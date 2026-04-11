#!/usr/bin/env python3
"""Run a SWE-bench Lite sample against Vulcan Forge API."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from datasets import load_dataset


TERMINAL_STATUSES = {"completed", "rejected", "error", "max_steps_reached", "killed"}


@dataclass
class RunResult:
    instance_id: str
    task_id: str
    status: str
    elapsed_sec: float
    repo: str
    base_commit: str
    error_message: str = ""
    escalation_reason: str = ""
    reviewer_status: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SWE-bench Lite runner for Vulcan Forge")
    p.add_argument("--api", default="http://localhost:8000", help="Base API URL")
    p.add_argument("--split", default="test", help="Dataset split (default: test)")
    p.add_argument("--start", type=int, default=0, help="Start index in split")
    p.add_argument("--limit", type=int, default=20, help="Number of instances to run")
    p.add_argument("--poll-sec", type=float, default=5.0, help="Task polling interval seconds")
    p.add_argument("--task-timeout-sec", type=int, default=3600, help="Per-task timeout in seconds")
    p.add_argument(
        "--output-json",
        default="swebench_results.json",
        help="Path to write structured results",
    )
    p.add_argument(
        "--max-submit-errors",
        type=int,
        default=1,
        help="Stop run after this many consecutive submit/poll errors",
    )
    return p.parse_args()


def _post_task(api: str, goal: str, repo: str, base_commit: str) -> str:
    r = requests.post(
        f"{api.rstrip('/')}/tasks",
        json={"goal": goal, "repo_url": repo, "base_commit": base_commit},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    return str(payload["id"])


def _poll_task(api: str, task_id: str, poll_sec: float, timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    url = f"{api.rstrip('/')}/tasks/{task_id}"
    while True:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        task = r.json()
        status = str(task.get("status") or "")
        if status in TERMINAL_STATUSES:
            return task
        if time.time() >= deadline:
            return {"status": "timeout", "id": task_id}
        time.sleep(poll_sec)


def _preflight_or_exit(api: str) -> None:
    api_url = api.rstrip("/")
    try:
        r = requests.get(f"{api_url}/tasks", timeout=10)
        r.raise_for_status()
    except Exception as exc:
        raise SystemExit(
            f"Preflight failed: API is not reachable at {api_url}. "
            f"Start the backend first. Details: {exc}"
        )

    try:
        res = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise SystemExit(
            "Preflight failed: docker CLI not found. Install Docker Desktop and ensure "
            "'docker' is available in PATH."
        )
    except Exception as exc:
        raise SystemExit(f"Preflight failed while checking Docker: {exc}")

    if res.returncode != 0:
        details = (res.stderr or res.stdout or "").strip()
        raise SystemExit(
            "Preflight failed: Docker daemon is not ready. Start Docker Desktop and wait "
            f"until it is running.\nDetails: {details[:500]}"
        )


def main() -> int:
    args = parse_args()
    _preflight_or_exit(args.api)
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split=args.split)

    start = max(args.start, 0)
    end = min(start + max(args.limit, 0), len(ds))
    subset = list(ds.select(range(start, end)))
    if not subset:
        print("No instances selected. Check --start/--limit.")
        return 1

    print(f"Loaded SWE-bench Lite split={args.split} total={len(ds)}")
    print(f"Running instances [{start}:{end}) => {len(subset)} tasks")

    results: list[RunResult] = []
    consecutive_submit_errors = 0
    for idx, instance in enumerate(subset, start=1):
        instance_id = str(instance.get("instance_id", ""))
        repo_name = str(instance["repo"])
        repo_url = f"https://github.com/{repo_name}.git"
        goal = str(instance.get("problem_statement") or "")
        base_commit = str(instance.get("base_commit") or "")

        print(f"[{idx}/{len(subset)}] submit {instance_id} repo={repo_name}")
        submitted_at = time.time()
        try:
            task_id = _post_task(args.api, goal, repo_url, base_commit)
            task = _poll_task(args.api, task_id, args.poll_sec, args.task_timeout_sec)
            status = str(task.get("status") or "unknown")
            error_message = str(task.get("error_message") or "")
            escalation_reason = str(task.get("escalation_reason") or "")
            reviewer_status = str(task.get("reviewer_status") or "")
        except Exception as exc:
            task_id = ""
            status = f"submit_or_poll_error: {exc}"
            error_message = str(exc)
            escalation_reason = ""
            reviewer_status = ""
            consecutive_submit_errors += 1
        else:
            if status.startswith("submit_or_poll_error"):
                consecutive_submit_errors += 1
            else:
                consecutive_submit_errors = 0
        elapsed = time.time() - submitted_at
        print(f"  -> task={task_id or '<none>'} status={status} elapsed={elapsed:.1f}s")
        results.append(
            RunResult(
                instance_id=instance_id,
                task_id=task_id,
                status=status,
                elapsed_sec=elapsed,
                repo=repo_name,
                base_commit=base_commit,
                error_message=error_message,
                escalation_reason=escalation_reason,
                reviewer_status=reviewer_status,
            )
        )
        if consecutive_submit_errors >= max(args.max_submit_errors, 1):
            print(
                "\nStopping early: repeated submit/poll errors detected "
                f"({consecutive_submit_errors} in a row)."
            )
            print("Check backend health at http://localhost:8000/tasks and restart API if needed.")
            break

    resolved = sum(1 for r in results if r.status == "completed")
    total = len(results)
    print(f"\nResolved: {resolved}/{total} ({(resolved / total * 100.0):.1f}%)")

    output_path = Path(args.output_json).resolve()
    payload = {
        "api": args.api,
        "split": args.split,
        "start": start,
        "limit": args.limit,
        "resolved": resolved,
        "total": total,
        "results": [r.__dict__ for r in results],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved results to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
