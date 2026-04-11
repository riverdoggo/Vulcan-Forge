import asyncio
import hashlib
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.settings import MAX_AGENT_STEPS
from app.database import save_task
from app.logging.log_writer import append_runtime_log, write_last_run_log
from app.logging.replay_store import ReplayStore
from app.memory.memory_store import MemoryStore
from app.models.agent_decision import AgentDecision
from app.models.task import Task
from app.tools.filesystem_tools import read_file as read_file_tool
from app.tools.git_tools import git_commit
from app.tools.docker_terminal import run_in_container_argv
from app.tools.test_tools import failed_test_names_from_pytest_output
from app.workspace.workspace_manager import terminate_workspace_container
from agent_runtime.decision_engine import (
    CoderDecisionOutcome,
    DecisionEngine,
)
from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.task_runtime_state import clear_runtime_state, runtime_state

logger = logging.getLogger(__name__)

MAX_REVIEW_CYCLES = 3

_KILL_USER_MESSAGE = "Task terminated by user."


def _maybe_lock_failing_scope(task_id: str, result: dict[str, Any]) -> None:
    """On first pytest run with failures, lock failing test names for coder scope."""
    st = runtime_state(task_id)
    if st.get("locked_failing_tests"):
        return
    ec = int(result.get("exit_code", -1) or -1)
    tc = result.get("test_counts") if isinstance(result.get("test_counts"), dict) else {}
    failed = tc.get("failed")
    errors = tc.get("errors")
    fn = int(failed) if failed is not None else 0
    en = int(errors) if errors is not None else 0
    if ec == 0 and fn == 0 and en == 0:
        return
    raw = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
    names = failed_test_names_from_pytest_output(raw)
    if not names:
        names = [f"(pytest: failed={fn} errors={en} exit={ec}; use observations for names)"]
    st["locked_failing_tests"] = names


def _scope_constraint_text(task_id: str) -> str:
    tests = runtime_state(task_id).get("locked_failing_tests") or []
    if not tests:
        return ""
    test_list = "\n".join(f"  - {t}" for t in tests)
    return (
        "SCOPE CONSTRAINT — MANDATORY:\n"
        f"Your ONLY job is to fix these specific failing tests:\n{test_list}\n"
        "DO NOT modify other methods, classes, or logic except as required for those tests.\n"
        "DO NOT add features, refactor for atomicity, or add error handling unless required to fix those tests.\n"
        "Unrelated edits will be treated as scope violations."
    )


def _build_coder_memory_goal(task: Task, review_prefix: str) -> str:
    st = runtime_state(task.id)
    parts: list[str] = []
    if st.get("regression_message"):
        parts.append(str(st["regression_message"]))
    if review_prefix:
        parts.append(review_prefix)
    sc = _scope_constraint_text(task.id)
    if sc:
        parts.append(sc)
    if parts:
        return "\n\n---\n\n".join(parts) + f"\n\n---\n\nOriginal goal:\n{task.goal}"
    return task.goal


def _proposed_content_before_regression(steps: list[dict[str, Any]]) -> str | None:
    if len(steps) < 2:
        return None
    for j in range(len(steps) - 2, -1, -1):
        d = steps[j].get("decision") or {}
        if not isinstance(d, dict):
            continue
        t = d.get("tool")
        if t == "write_file" and d.get("content") is not None:
            return str(d["content"])
        if t == "apply_patch" and (d.get("content") or d.get("input")):
            return str(d.get("content") or d.get("input") or "")
    return None

def _normalize_read_file_input(inp: str | None) -> str:
    """Canonical key for read_file path (workspace-relative vs absolute)."""
    if inp is None:
        return ""
    s = str(inp).strip()
    if not s:
        return ""
    if s.startswith("/workspace/"):
        return s
    if s.startswith("/"):
        return s
    return f"/workspace/{s}"


def _successful_write_with_positive_diff(result: dict[str, Any]) -> bool:
    """True when write_file reported success and the line-diff ratio indicates a real edit."""
    if int(result.get("exit_code", -1) or -1) != 0:
        return False
    st = result.get("status")
    if st is not None and st != "success":
        return False
    dr = result.get("diff_ratio")
    if dr is None:
        return True
    try:
        return float(dr) > 0.0
    except (TypeError, ValueError):
        return True


def _forced_run_tests_decision_after_write(decision: AgentDecision, result: dict[str, Any]) -> AgentDecision | None:
    """Hard chain: always run_tests after a successful write (diff > 0) or identical-content rejection."""
    if decision.tool != "write_file":
        return None
    if _successful_write_with_positive_diff(result):
        return AgentDecision(
            reasoning="Runtime: auto run_tests after successful write",
            tool="run_tests",
            input=None,
            content=None,
            done=False,
        )
    if result.get("rejected_reason") == "identical_content":
        return AgentDecision(
            reasoning=(
                "Runtime: auto run_tests after identical-content rejection — "
                "checking if previous write already fixed the issue"
            ),
            tool="run_tests",
            input=None,
            content=None,
            done=False,
        )
    return None


def _forced_run_tests_decision_after_apply_patch(decision: AgentDecision, result: dict[str, Any]) -> AgentDecision | None:
    if decision.tool != "apply_patch" or int(result.get("exit_code", -1) or -1) != 0:
        return None
    return AgentDecision(
        reasoning="Runtime: auto run_tests after apply_patch (fallback edit)",
        tool="run_tests",
        input=None,
        content=None,
        done=False,
    )


def _enrich_observation_for_memory(result: Any, decision: AgentDecision | None) -> dict[str, Any]:
    """Attach tool/input so memory condensing and prompts show which path each observation refers to."""
    if not isinstance(result, dict):
        return {
            "status": "error",
            "stdout": str(result),
            "stderr": "",
            "exit_code": -1,
        }
    out = dict(result)
    if decision is not None and getattr(decision, "tool", None):
        out.setdefault("tool", decision.tool)
    if decision is not None and decision.input is not None:
        out.setdefault("input", str(decision.input))
    if decision is not None and getattr(decision, "reasoning", None) is not None:
        out.setdefault("reasoning", str(decision.reasoning))
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _append_lesson_to_memory_md(lesson_text: str) -> None:
    memory_path = _repo_root() / "MEMORY.md"
    if not memory_path.exists():
        return
    lesson = (lesson_text or "").strip().replace("\n", " ")
    if not lesson:
        return

    date_tag = datetime.utcnow().strftime("%Y-%m-%d")
    line = f"- [{date_tag}] {lesson}"
    try:
        content = memory_path.read_text(encoding="utf-8")
    except Exception:
        return

    if "## Lessons" in content:
        insert_at = content.find("## Lessons") + len("## Lessons")
        tail = content[insert_at:]
        newline_count = 0
        while newline_count < 2 and tail.startswith("\n"):
            tail = tail[1:]
            newline_count += 1
        updated = content[:insert_at] + "\n" + line + "\n\n" + tail
    else:
        updated = f"## Lessons\n{line}\n\n## Context\n"
        if content.strip():
            updated += content

    try:
        memory_path.write_text(updated, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed writing MEMORY.md lesson: %s", e)


def _replay_has_kill_step(steps: list[dict[str, Any]]) -> bool:
    for s in steps:
        r = s.get("result") or {}
        if isinstance(r, dict) and (r.get("stdout") or "").strip() == _KILL_USER_MESSAGE:
            return True
    return False


def _finalize_task_killed(task: Task, steps: list[dict[str, Any]], replay: ReplayStore) -> str:
    """Record kill in replay/logs, tear down sandbox. Idempotent if already recorded."""
    if not _replay_has_kill_step(steps):
        _append_step_with_transcript(
            task,
            steps,
            {
                "step": len(steps),
                "decision": {
                    "reasoning": "Runtime: user requested task termination",
                    "tool": "_user_kill",
                    "input": None,
                    "content": None,
                    "done": False,
                },
                "result": {
                    "status": "killed",
                    "stdout": _KILL_USER_MESSAGE,
                    "stderr": "",
                    "exit_code": -1,
                },
            },
        )
    append_runtime_log(task, "kill_switch_activated: user_requested")
    task.status = "killed"
    save_task(task)
    replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "killed"})
    write_last_run_log(task, steps)
    try:
        terminate_workspace_container(task.id, remove_workspace_dir=True)
    except Exception as e:
        logger.warning("terminate_workspace_container during finalize kill: %s", e)
    logger.info("Task %s finalized as killed/cancelled (replay + Docker cleanup)", task.id)
    clear_runtime_state(task.id)
    return "killed"


def _append_step_with_transcript(task: Task, steps: list[dict[str, Any]], step_data: dict[str, Any]) -> None:
    steps.append(step_data)
    decision = step_data.get("decision") or {}
    result = step_data.get("result")
    tool = decision.get("tool") if isinstance(decision, dict) else None
    inp = decision.get("input") if isinstance(decision, dict) else None
    entry = {
        "step": step_data.get("step"),
        "tool": tool,
        "input": inp,
        "output": result,
        "timestamp": datetime.utcnow().isoformat(),
    }
    task.transcript.append(entry)
    save_task(task)


def _worse_tests(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    if not before or not after:
        return False
    def n(x: Any) -> int:
        try:
            return int(x) if x is not None else 0
        except Exception:
            return 0
    bp, bf, be = n(before.get("passed")), n(before.get("failed")), n(before.get("errors"))
    ap, af, ae = n(after.get("passed")), n(after.get("failed")), n(after.get("errors"))
    # Regression if fewer passes OR more failures/errors.
    return (ap < bp) or (af > bf) or (ae > be)


def _contains_attribute_error(test_stdout: str) -> bool:
    return "AttributeError" in (test_stdout or "")


def _write_file_step_succeeded(step: dict[str, Any]) -> bool:
    """True only if write_file actually changed the file (not rejected / error)."""
    d = step.get("decision") or {}
    r = step.get("result") or {}
    if not isinstance(d, dict) or d.get("tool") != "write_file":
        return False
    if not isinstance(r, dict):
        return False
    if r.get("rejected_reason") in ("full_rewrite_detected", "identical_content"):
        return False
    if r.get("status") == "error":
        return False
    if int(r.get("exit_code", -1) or -1) != 0:
        return False
    return True


def _should_force_run_tests_after_double_write(steps: list[dict[str, Any]]) -> bool:
    """True if two successful write_file calls on the same path with no run_tests between."""
    w = steps[-5:]
    if len(w) < 2:
        return False
    n = len(w)
    for i in range(n):
        for j in range(i + 1, n):
            di, dj = w[i].get("decision", {}), w[j].get("decision", {})
            if not isinstance(di, dict) or not isinstance(dj, dict):
                continue
            if di.get("tool") != "write_file" or dj.get("tool") != "write_file":
                continue
            path = di.get("input")
            if not path or path != dj.get("input"):
                continue
            if not _write_file_step_succeeded(w[i]) or not _write_file_step_succeeded(w[j]):
                continue
            between = w[i + 1 : j]
            if any((b.get("decision") or {}).get("tool") == "run_tests" for b in between):
                continue
            return True
    return False


def _bump_stall_counters(stall: dict[str, Any], step: dict[str, Any]) -> None:
    """Track consecutive read_file per path and list_directory for idle-loop breaks."""
    d = step.get("decision") or {}
    t = d.get("tool") if isinstance(d, dict) else None
    reads: dict[str, int] = stall["reads"]
    if t == "read_file":
        stall["lists"] = 0
        rp = _normalize_read_file_input(d.get("input"))
        if rp:
            reads[rp] = reads.get(rp, 0) + 1
    elif t == "list_directory":
        reads.clear()
        stall["lists"] = int(stall["lists"]) + 1
    else:
        reads.clear()
        stall["lists"] = 0


def _stall_override_prompt(stall: dict[str, Any], last_test_failure_summary: str) -> str | None:
    if int(stall["lists"]) >= 2:
        stall["lists"] = 0
        stall["reads"].clear()
        msg = (
            "Loop detected: you have listed the directory multiple times without making progress. "
            "You already know the workspace layout. Use write_file on the file that needs fixing, then run_tests.\n\n"
        )
        if last_test_failure_summary.strip():
            msg += f"Last known test failures:\n{last_test_failure_summary}\n"
        return msg
    stuck_path = next((p for p, c in stall["reads"].items() if c >= 2), None)
    if stuck_path:
        stall["reads"].clear()
        stall["lists"] = 0
        msg = (
            f"Loop detected: read_file was used repeatedly on the same path ({stuck_path}) with no write. "
            "Use write_file with that path and a targeted fix; do not read that file again.\n\n"
        )
        if last_test_failure_summary.strip():
            msg += f"Last known test failures:\n{last_test_failure_summary}\n"
        return msg
    return None


def _should_force_after_double_run_command_error(steps: list[dict[str, Any]]) -> bool:
    """True if the last two steps are run_command with the same input and both failed."""
    if len(steps) < 2:
        return False
    a, b = steps[-2], steps[-1]
    da = a.get("decision") or {}
    db = b.get("decision") or {}
    if not isinstance(da, dict) or not isinstance(db, dict):
        return False
    if da.get("tool") != "run_command" or db.get("tool") != "run_command":
        return False
    if da.get("input") != db.get("input"):
        return False

    def _failed(r: dict[str, Any]) -> bool:
        if r.get("status") == "error":
            return True
        code = r.get("exit_code")
        return code is not None and code != 0

    ra = a.get("result") or {}
    rb = b.get("result") or {}
    if not isinstance(ra, dict) or not isinstance(rb, dict):
        return False
    return _failed(ra) and _failed(rb)


def _collect_staged_file_contents(container: str) -> dict[str, str]:
    """Read full contents of every path in the staged diff."""
    out = run_in_container_argv(container, ["git", "diff", "--cached", "--name-only"])
    if out.get("exit_code") != 0:
        return {}
    raw = (out.get("stdout") or "").strip()
    if not raw:
        return {}
    contents: dict[str, str] = {}
    for rel in raw.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = rel if rel.startswith("/") else f"/workspace/{rel}"
        r = read_file_tool(container, path)
        if r.get("exit_code") == 0:
            contents[path] = r.get("stdout") or ""
        else:
            contents[path] = f"<read failed: {r.get('stderr', '')}>"
    return contents


def _reviewer_decision_dict() -> dict[str, Any]:
    return {
        "reasoning": "Runtime: automated reviewer step (not from coder LLM)",
        "tool": "reviewer_agent",
        "input": "diff + file contents + test results",
        "content": None,
        "done": False,
    }


def _normalize_tool_input_pair(decision: dict[str, Any]) -> tuple[str | None, str]:
    tool = decision.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        return None, ""
    inp = decision.get("input")
    if inp is None:
        return tool.strip(), ""
    return tool.strip(), str(inp).strip()


def _consecutive_duplicate_tool_input(steps: list[dict[str, Any]]) -> tuple[str, str] | None:
    """If the last two steps used the same tool with the same input, return (tool, input) to forbid."""
    if len(steps) < 2:
        return None
    da = steps[-2].get("decision")
    db = steps[-1].get("decision")
    if not isinstance(da, dict) or not isinstance(db, dict):
        return None
    ta, ia = _normalize_tool_input_pair(da)
    tb, ib = _normalize_tool_input_pair(db)
    if not ta or not tb:
        return None
    if ta == tb and ia == ib:
        return (ta, ia)
    return None


def _same_read_file_three_in_five(steps: list[dict[str, Any]]) -> str | None:
    """Return read_file path when same read_file input appears >=3 times in last 5 steps."""
    window = steps[-5:]
    counts: dict[str, int] = {}
    for s in window:
        d = s.get("decision") or {}
        if not isinstance(d, dict):
            continue
        if d.get("tool") != "read_file":
            continue
        inp = d.get("input")
        key = _normalize_read_file_input(inp)
        counts[key] = counts.get(key, 0) + 1
    for path, n in counts.items():
        if n >= 3:
            return path
    return None


def _loop_breaker_prompt(forbidden_tool: str, forbidden_input: str) -> str:
    return (
        "LOOP DETECTED: You executed the same tool with the same input twice in a row. "
        f"You must NOT use tool {forbidden_tool!r} with input {forbidden_input!r} again. "
        "Choose a DIFFERENT tool or different arguments that advance the task "
        "(for example: write_file, run_tests, run_command, list_directory, read_file with a different path)."
    )


def _record_invalid_decision_in_memory(memory: MemoryStore, outcome: CoderDecisionOutcome) -> None:
    """So the next Think step sees that the LLM output was invalid."""
    memory.add_step(
        {
            "error": "invalid_llm_decision",
            "last_error": outcome.last_error,
            "decision_retries": outcome.retry_count,
            "decision_attempts": outcome.attempt_count,
        }
    )
    memory.add_observation(
        {
            "status": "error",
            "stderr": outcome.last_error or "",
            "stdout": (outcome.last_raw_response or "")[:4000],
        }
    )


def _append_invalid_decision_step(
    steps: list[dict[str, Any]],
    step: int,
    outcome: CoderDecisionOutcome,
    task: Task,
    replay: ReplayStore,
    memory: MemoryStore,
) -> None:
    """Record a failed coder decision without terminating the task."""
    _record_invalid_decision_in_memory(memory, outcome)
    _append_step_with_transcript(
        task,
        steps,
        {
            "step": step,
            "decision": {
                "error": "invalid_llm_decision",
                "last_error": outcome.last_error,
                "attempt_count": outcome.attempt_count,
                "retry_count": outcome.retry_count,
                "raw_llm_responses": outcome.raw_responses,
            },
            "result": {
                "status": "error",
                "stderr": outcome.last_error or "LLM returned no valid decision",
                "stdout": "",
                "exit_code": -1,
                "raw_llm_on_failure": outcome.last_raw_response,
                "decision_retry_count": outcome.retry_count,
                "decision_attempt_count": outcome.attempt_count,
            },
        },
    )
    replay.save(task.id, {"goal": task.goal, "steps": steps})
    logger.error(
        "Coder decision invalid after retries | task=%s | error=%s | last_raw=%s",
        task.id,
        outcome.last_error,
        outcome.last_raw_response,
    )


class AgentLoop:
    async def run_async(self, task: Task) -> str:
        logger.info("AgentLoop started for task %s", task.id)
        review_prefix = ""
        decision_engine = DecisionEngine()
        executor = Executor()
        replay = ReplayStore()
        steps: list[dict[str, Any]] = []

        def exit_if_killed() -> str | None:
            if getattr(task, "kill_requested", False):
                return _finalize_task_killed(task, steps, replay)
            return None

        while True:
            hit = exit_if_killed()
            if hit:
                return hit

            broke_for_review = False
            memory = MemoryStore()
            memory.goal = _build_coder_memory_goal(task, review_prefix)

            bootstrap_actions: list[AgentDecision] = []
            if getattr(task, "_skip_bootstrap", False):
                logger.info(
                    "Skipping bootstrap after reviewer feedback",
                    extra={"task_id": task.id},
                )
                task._skip_bootstrap = False
            else:
                bootstrap_actions = [
                    AgentDecision(
                        reasoning="Bootstrap: inspect workspace layout",
                        tool="list_directory",
                        input="/workspace",
                        content=None,
                        done=False,
                    ),
                    AgentDecision(
                        reasoning="Bootstrap: run test suite to see current failures",
                        tool="run_tests",
                        input=None,
                        content=None,
                        done=False,
                    ),
                ]

            stall: dict[str, Any] = {"reads": {}, "lists": 0}
            step = len(steps)
            for boot_decision in bootstrap_actions:
                hit = exit_if_killed()
                if hit:
                    return hit

                result = await asyncio.to_thread(executor.execute, boot_decision, task, step=step)
                step_data = {"step": step, "decision": boot_decision.model_dump(), "result": result}
                _append_step_with_transcript(task, steps, step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps})
                memory.add_step(boot_decision.model_dump())
                memory.add_observation(_enrich_observation_for_memory(result, boot_decision))
                if boot_decision.tool == "run_tests" and isinstance(result, dict):
                    _maybe_lock_failing_scope(task.id, result)
                logger.info(
                    "Bootstrap step %s | reasoning=%s | tool=%s",
                    step,
                    boot_decision.reasoning,
                    boot_decision.tool,
                )
                _bump_stall_counters(stall, steps[-1])
                step += 1

            # Per-phase step budget so review restarts get a full coder budget (global `step` is only for replay).
            phase_steps = 0
            task.step_budget_warning_shown = False
            last_test_failure_summary = ""

            async def run_tests_outcome_pipeline(
                rt_decision: AgentDecision,
                rt_result: dict[str, Any],
                *,
                rt_step: int,
            ) -> str | None:
                """
                Regression guard, then git_diff + reviewer + commit on green.
                Returns None if the caller should record memory for this run_tests and continue.
                Returns "__continue__" if the outer loop should continue (regression).
                Otherwise returns a terminal status for run_async.
                """
                nonlocal step, last_test_failure_summary, review_prefix, broke_for_review

                counts_after = rt_result.get("test_counts") if isinstance(rt_result, dict) else None
                if hasattr(task, "last_test_counts") and counts_after:
                    task.last_test_counts = counts_after

                if isinstance(rt_result, dict) and rt_result.get("exit_code") == 0:
                    files_snapshot: dict[str, str] = {}
                    for p in getattr(task, "touched_files", []) or []:
                        r = read_file_tool(task.workspace["container"], p)
                        if r.get("exit_code") == 0:
                            files_snapshot[p] = r.get("stdout") or ""
                    task.last_green = {"counts": counts_after, "files": files_snapshot, "timestamp": time.time()}
                    save_task(task)

                baseline = getattr(task, "regression_baseline", None)
                if _worse_tests(baseline, counts_after):
                    logger.warning("Regression detected, reverting", extra={"task_id": task.id})
                    proposed_bad = _proposed_content_before_regression(steps)
                    if proposed_bad:
                        bh = hashlib.md5(proposed_bad.encode("utf-8")).hexdigest()
                        rs = runtime_state(task.id)
                        blocked = rs.setdefault("blocked_content_hashes", [])
                        if bh not in blocked:
                            blocked.append(bh)
                    reg_msg = (
                        "REGRESSION DETECTED: Your last change made tests worse (or caused errors). "
                        "It was reverted. DO NOT re-apply the same edit — that content is now blocked.\n"
                        "Get back to passing tests on the original failing cases without breaking what worked.\n"
                        "Follow the SCOPE CONSTRAINT: only fix the listed failing tests."
                    )
                    runtime_state(task.id)["regression_message"] = reg_msg
                    try:
                        memory.add_observation(
                            {
                                "tool": "runtime",
                                "status": "warning",
                                "stdout": reg_msg,
                                "stderr": "regression_guard",
                                "exit_code": 0,
                            }
                        )
                    except Exception:
                        pass
                    container = task.workspace["container"] if task.workspace else None
                    if container:
                        run_in_container_argv(container, ["git", "checkout", "--", "."])
                        run_in_container_argv(container, ["git", "clean", "-fd"])
                    task.last_plan = None
                    task.regression_baseline = None
                    task.file_read_cache = {}
                    save_task(task)
                    _append_step_with_transcript(
                        task,
                        steps,
                        {
                            "step": rt_step + 1,
                            "decision": {
                                "reasoning": "Runtime: regression guard reverted workspace after worse test results",
                                "tool": "regression_guard",
                                "input": None,
                                "content": None,
                                "done": False,
                            },
                            "result": {
                                "status": "error",
                                "stdout": "Regression detected, reverting",
                                "stderr": "",
                                "exit_code": 2,
                            },
                        },
                    )
                    replay.save(task.id, {"goal": task.goal, "steps": steps})
                    review_prefix = (
                        "Regression detected (tests got worse) and changes were reverted. "
                        "Try a different minimal fix. Do NOT rewrite files."
                    )
                    step = rt_step + 2
                    return "__continue__"

                if rt_result.get("exit_code") == 0:
                    diff_decision = AgentDecision(
                        reasoning="Runtime: collect staged diff after tests passed",
                        tool="git_diff",
                        input=None,
                        content=None,
                        done=False,
                    )
                    diff_result = await asyncio.to_thread(executor.execute, diff_decision, task, step=rt_step)

                    task.diff_output = diff_result.get("stdout", "")

                    step = rt_step + 1
                    _append_step_with_transcript(
                        task,
                        steps,
                        {"step": step, "decision": diff_decision.model_dump(), "result": diff_result},
                    )
                    replay.save(task.id, {"goal": task.goal, "steps": steps})

                    container = task.workspace["container"] if task.workspace else None
                    file_contents: dict[str, str] = {}
                    if container:
                        file_contents = _collect_staged_file_contents(container)

                    hit = exit_if_killed()
                    if hit:
                        return hit

                    verdict = await decision_engine.get_reviewer_decision_async(
                        task,
                        task.diff_output,
                        file_contents,
                        rt_result,
                    )

                    hit = exit_if_killed()
                    if hit:
                        return hit

                    v = str(verdict.verdict)
                    if v == "needs_changes":
                        tc = counts_after if isinstance(counts_after, dict) else {}
                        try:
                            passed = int((tc or {}).get("passed") or 0)
                            failed = int((tc or {}).get("failed") or 0)
                            errors = int((tc or {}).get("errors") or 0)
                        except Exception:
                            passed, failed, errors = 0, 0, 0
                        if passed > 0 and failed == 0 and errors == 0:
                            logger.warning(
                                "[agent_loop coerce] reviewer said needs_changes but all tests pass (%s/%s) - overriding to approved",
                                passed,
                                passed,
                            )
                            append_runtime_log(
                                task,
                                "agent_loop_reviewer_coerce: all tests green; overriding needs_changes to approved",
                            )
                            v = "approved"
                            verdict = verdict.model_copy(
                                update={
                                    "verdict": "approved",
                                    "reason": (
                                        "All tests passed; overridden in agent loop safety net. "
                                        f"Reviewer said: {verdict.reason}"
                                    ),
                                    "confidence": max(float(verdict.confidence), 0.85),
                                    "suggestions": "",
                                }
                            )
                    if v in {"approved", "escalate_to_human"}:
                        lesson = (verdict.lesson or "").strip()
                        if not lesson:
                            lesson = await decision_engine.generate_reviewer_lesson_async(
                                task=task,
                                goal=task.goal,
                                verdict=v,
                                review_iterations=task.review_iterations,
                                reason=verdict.reason,
                                suggestions=verdict.suggestions,
                            )
                        _append_lesson_to_memory_md(lesson)
                    if v == "needs_changes":
                        task.review_iterations += 1

                    iter_label = f"{task.review_iterations}/{MAX_REVIEW_CYCLES}" if v == "needs_changes" else "—"

                    task.reviewer_feedback.append(
                        {
                            "verdict": v,
                            "reason": verdict.reason,
                            "confidence": verdict.confidence,
                            "suggestions": verdict.suggestions,
                            "iteration": task.review_iterations if v == "needs_changes" else None,
                        }
                    )
                    task.reviewer_status = v

                    step = rt_step + 2
                    _append_step_with_transcript(
                        task,
                        steps,
                        {
                            "step": step,
                            "decision": _reviewer_decision_dict(),
                            "result": {
                                "status": v,
                                "stdout": "",
                                "exit_code": 0,
                                "verdict": v,
                                "reason": verdict.reason,
                                "confidence": verdict.confidence,
                                "suggestions": verdict.suggestions,
                                "iteration": iter_label,
                            },
                        },
                    )
                    replay.save(task.id, {"goal": task.goal, "steps": steps})

                    if v == "approved":
                        commit_msg = "Auto-committed: reviewer approved"
                        container = task.workspace["container"] if task.workspace else None
                        if not container:
                            commit_result: dict[str, Any] = {
                                "status": "error",
                                "stderr": "no workspace container",
                                "exit_code": -1,
                                "stdout": "",
                            }
                        else:
                            commit_result = git_commit(container, commit_msg)

                        step = rt_step + 3
                        _append_step_with_transcript(
                            task,
                            steps,
                            {
                                "step": step,
                                "decision": {
                                    "reasoning": "Runtime: auto-commit after reviewer approval",
                                    "tool": "git_commit",
                                    "input": commit_msg,
                                    "content": None,
                                    "done": False,
                                },
                                "result": commit_result,
                            },
                        )
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "completed"})

                        if commit_result.get("exit_code") != 0:
                            task.status = "error"
                            save_task(task)
                            logger.error(
                                "Auto-commit after reviewer approval failed: %s",
                                commit_result,
                                extra={"task_id": task.id},
                            )
                            write_last_run_log(task, steps)
                            clear_runtime_state(task.id)
                            return "error"

                        task.status = "completed"
                        task.escalation_reason = ""
                        save_task(task)
                        logger.info(
                            "Reviewer approved; changes committed automatically.",
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        clear_runtime_state(task.id)
                        return "completed"

                    if v == "escalate_to_human":
                        task.status = "awaiting_approval"
                        task.escalation_reason = verdict.reason or "Reviewer requested human review."
                        save_task(task)
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                        logger.info(
                            "Reviewer escalated to human. Awaiting approval.",
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "awaiting_approval"

                    if v == "needs_changes" and task.review_iterations >= MAX_REVIEW_CYCLES:
                        task.status = "awaiting_approval"
                        task.escalation_reason = (
                            "The automated reviewer could not approve the changes after "
                            f"{MAX_REVIEW_CYCLES} review cycles. See reviewer_feedback for the full history."
                        )
                        save_task(task)
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                        logger.warning(
                            "Reviewer needs_changes after %s cycles; escalating to human with warning.",
                            MAX_REVIEW_CYCLES,
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "awaiting_approval"

                    if v == "needs_changes":
                        tc = counts_after if isinstance(counts_after, dict) else {}
                        last_passed = int((tc.get("passed") or 0) if isinstance(tc, dict) else 0)
                        review_prefix = (
                            f"Reviewer feedback (iteration {task.review_iterations}/{MAX_REVIEW_CYCLES}) — "
                            f"you must address this before finishing:\n"
                            f"{verdict.suggestions}\n\n"
                            f"Reviewer reason: {verdict.reason}\n\n"
                            f"CURRENT STATE: {last_passed} tests are passing.\n"
                            "Do NOT re-run tests or list_directory at the start of this retry.\n"
                            "Do NOT rewrite already-working code.\n"
                            "Prefer targeted actions only (read_file if needed, write_file targeted edit, git_diff when required)."
                        )
                        logger.info(
                            "Reviewer requested changes; restarting coder with targeted loop and no bootstrap.",
                            extra={"task_id": task.id, "iteration": task.review_iterations},
                        )
                        task._reviewer_feedback_pending = True
                        task._skip_bootstrap = True
                        task.read_loop_guard_active = False
                        task.read_blocked_paths = []
                        task.file_read_cache = {}
                        task.step_budget_warning_shown = False
                        broke_for_review = True
                        step = rt_step + 2
                        return "__break_phase__"

                    logger.error("Unexpected reviewer verdict %r; escalating to human.", v)
                    task.status = "awaiting_approval"
                    task.escalation_reason = f"Unexpected reviewer verdict: {v!r}"
                    save_task(task)
                    replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                    write_last_run_log(task, steps)
                    return "awaiting_approval"

                return None

            while phase_steps < MAX_AGENT_STEPS:
                phase_steps += 1
                hit = exit_if_killed()
                if hit:
                    return hit

                rem_steps = MAX_AGENT_STEPS - phase_steps
                if rem_steps <= 5 and not getattr(task, "step_budget_warning_shown", False):
                    task.step_budget_warning_shown = True
                    memory.add_observation(
                        {
                            "status": "warning",
                            "stdout": (
                                "Warning: step limit approaching.\n"
                                "Focus on making a concrete code change to solve the failing tests."
                            ),
                            "stderr": "",
                        }
                    )

                stall_override = _stall_override_prompt(stall, last_test_failure_summary)
                force_run_tests = _should_force_run_tests_after_double_write(steps)
                force_run_command_break = _should_force_after_double_run_command_error(steps)
                loop_dup = _consecutive_duplicate_tool_input(steps)
                repeated_read_path = _same_read_file_three_in_five(steps)
                if repeated_read_path:
                    rb = list(getattr(task, "read_blocked_paths", None) or [])
                    if repeated_read_path not in rb:
                        task.read_blocked_paths = rb + [repeated_read_path]
                    append_runtime_log(task, "loop_guard_triggered: repeated_file_read")
                    logger.warning(
                        "loop_guard_triggered: repeated_file_read",
                        extra={"task_id": task.id, "path": repeated_read_path},
                    )
                last_obs = memory.observations[-1] if memory.observations else {}
                last_out = last_obs.get("stdout") if isinstance(last_obs, dict) else ""
                attr_err = _contains_attribute_error(str(last_out or ""))
                rewrite_rejected_last_step = False
                if steps:
                    last_step = steps[-1]
                    last_dec = last_step.get("decision") or {}
                    last_res = last_step.get("result") or {}
                    if (
                        isinstance(last_dec, dict)
                        and isinstance(last_res, dict)
                        and last_dec.get("tool") == "write_file"
                        and last_res.get("rejected_reason") == "full_rewrite_detected"
                    ):
                        rewrite_rejected_last_step = True

                outcome: CoderDecisionOutcome | None = None
                decision: AgentDecision | None = None
                runtime_forced_call = False

                if stall_override is not None:
                    outcome = await decision_engine.decide_async(
                        task,
                        memory,
                        override_prompt=stall_override,
                    )
                elif force_run_tests:
                    runtime_forced_call = True
                    logger.warning(
                        "Repeated write_file on same path without run_tests in between; forcing run_tests.",
                        extra={"task_id": task.id},
                    )
                    decision = AgentDecision(
                        reasoning="Runtime: enforce run_tests after repeated writes on the same path",
                        tool="run_tests",
                        input=None,
                        content=None,
                        done=False,
                    )
                elif force_run_command_break:
                    logger.warning(
                        "Repeated run_command with same input failed twice; forcing a different approach.",
                        extra={"task_id": task.id},
                    )
                    outcome = await decision_engine.decide_async(
                        task,
                        memory,
                        override_prompt=(
                            "run_command failed twice with the same command. Do NOT repeat that exact command. "
                            "The sandbox has no network; use list_directory, read_file, write_file, or run_tests. "
                            "Dependencies may already be installed from the host — focus on code and tests."
                        ),
                    )
                elif rewrite_rejected_last_step:
                    memory.add_observation(
                        {
                            "status": "error",
                            "stdout": (
                                "The previous attempt rewrote the entire file and was rejected. "
                                "Modify only the specific lines related to the failing tests."
                            ),
                            "stderr": "",
                        }
                    )
                    outcome = await decision_engine.decide_async(
                        task,
                        memory,
                        override_prompt=(
                            "The previous attempt rewrote the entire file and was rejected. "
                            "Modify only the specific lines related to the failing tests. "
                            "Use write_file with the full file content (minimal targeted change, not a full rewrite). Do not use apply_patch."
                        ),
                    )
                elif loop_dup:
                    ft, fi = loop_dup
                    logger.warning(
                        "Consecutive duplicate tool+input (%s, %r); applying loop breaker.",
                        ft,
                        fi,
                        extra={"task_id": task.id},
                    )
                    outcome = await decision_engine.decide_async(
                        task, memory, override_prompt=_loop_breaker_prompt(ft, fi)
                    )
                elif attr_err:
                    outcome = await decision_engine.decide_async(
                        task,
                        memory,
                        override_prompt=(
                            "Tests show AttributeError. Prioritize ADDING the missing method/attribute with minimal changes. "
                            "Do NOT refactor or rewrite files. Follow read_file → write_file → run_tests."
                        ),
                    )
                else:
                    outcome = await decision_engine.decide_async(task, memory)

                if decision is None:
                    assert outcome is not None
                    if outcome.decision is None:
                        _append_invalid_decision_step(steps, step, outcome, task, replay, memory)
                        _bump_stall_counters(stall, steps[-1])
                        step += 1
                        continue
                    decision = outcome.decision
                    logger.info(
                        "Observe→Think→Act | reasoning=%s | tool=%s | input=%s | decision_retries=%s | attempts=%s",
                        decision.reasoning,
                        decision.tool,
                        decision.input,
                        outcome.retry_count,
                        outcome.attempt_count,
                        extra={"task_id": task.id, "step": step},
                    )
                elif decision is not None:
                    logger.info(
                        "Observe→Think→Act (runtime forced) | reasoning=%s | tool=%s | input=%s",
                        decision.reasoning,
                        decision.tool,
                        decision.input,
                        extra={"task_id": task.id, "step": step},
                    )

                # The LLM often sets done=true after write_file (legacy prompt). That must not end the run:
                # completion is only run_tests → git_diff → reviewer → awaiting_approval (or max steps).
                if decision.done:
                    logger.info(
                        "Ignoring LLM done=true; task completes only via tests, diff, and reviewer.",
                        extra={"task_id": task.id, "step": step},
                    )
                    decision = decision.model_copy(update={"done": False})

                if (not decision.tool or not str(decision.tool).strip()) and not decision.done:
                    logger.warning("LLM returned no tool — retrying decision", extra={"task_id": task.id})
                    await asyncio.sleep(5)
                    continue

                if decision.tool == "read_file" and not runtime_forced_call:
                    rp = _normalize_read_file_input(decision.input)
                    if rp:
                        if rp in (getattr(task, "read_blocked_paths", None) or []):
                            append_runtime_log(task, "loop_guard_triggered: repeated_file_read")
                            result = {
                                "status": "error",
                                "exit_code": 2,
                                "stdout": (
                                    "This path is temporarily blocked from read_file after repeated reads. "
                                    "Use write_file to change the file, or read a different path."
                                ),
                                "stderr": "repeated_file_read",
                                "loop_guard": "repeated_file_read",
                            }
                            step_data = {
                                "step": step,
                                "decision": decision.model_dump(),
                                "result": result,
                                "decision_retries": (outcome.retry_count if outcome is not None else 0),
                                "decision_attempts": (outcome.attempt_count if outcome is not None else 1),
                            }
                            _append_step_with_transcript(task, steps, step_data)
                            replay.save(task.id, {"goal": task.goal, "steps": steps})
                            memory.add_step(decision.model_dump())
                            memory.add_observation(_enrich_observation_for_memory(result, decision))
                            _bump_stall_counters(stall, steps[-1])
                            step += 1
                            continue

                        cache = getattr(task, "file_read_cache", None)
                        if (
                            isinstance(cache, dict)
                            and rp in cache
                            and isinstance(cache.get(rp), dict)
                            and "content" in cache[rp]
                        ):
                            append_runtime_log(task, "redundant_read_file_blocked")
                            result = {
                                "status": "error",
                                "exit_code": 2,
                                "stdout": (
                                    f"You already read {rp} successfully earlier in this task; the full content is in "
                                    "<latest_read_file> and recent observations. Do NOT read_file this path again. "
                                    "Next step: write_file with input set to this path and content = the complete "
                                    "updated file, then run_tests."
                                ),
                                "stderr": "redundant_read_file",
                            }
                            step_data = {
                                "step": step,
                                "decision": decision.model_dump(),
                                "result": result,
                                "decision_retries": (outcome.retry_count if outcome is not None else 0),
                                "decision_attempts": (outcome.attempt_count if outcome is not None else 1),
                            }
                            _append_step_with_transcript(task, steps, step_data)
                            replay.save(task.id, {"goal": task.goal, "steps": steps})
                            memory.add_step(decision.model_dump())
                            memory.add_observation(_enrich_observation_for_memory(result, decision))
                            _bump_stall_counters(stall, steps[-1])
                            step += 1
                            continue

                hit = exit_if_killed()
                if hit:
                    return hit

                try:
                    result = await asyncio.to_thread(executor.execute, decision, task, step=step)
                except ExecutorError as e:
                    logger.exception("Executor failed for task %s: %s", task.id, e)
                    result = {"status": "error", "stderr": str(e), "exit_code": -1}

                step_data: dict[str, Any] = {"step": step, "decision": decision.model_dump(), "result": result}
                if outcome is not None:
                    step_data["decision_retries"] = outcome.retry_count
                    step_data["decision_attempts"] = outcome.attempt_count
                else:
                    step_data["decision_retries"] = 0
                    step_data["decision_attempts"] = 1
                _append_step_with_transcript(task, steps, step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps})

                if (
                    decision.tool in ("write_file", "apply_patch")
                    and isinstance(result, dict)
                    and int(result.get("exit_code", -1) or -1) == 0
                ):
                    task.read_loop_guard_active = False

                chain_rt: AgentDecision | None = None
                if isinstance(result, dict):
                    chain_rt = _forced_run_tests_decision_after_write(decision, result)
                    if chain_rt is None:
                        chain_rt = _forced_run_tests_decision_after_apply_patch(decision, result)

                if decision.tool == "run_tests" and isinstance(result, dict):
                    pipe = await run_tests_outcome_pipeline(decision, result, rt_step=step)
                    if pipe == "__continue__":
                        continue
                    if pipe == "__break_phase__":
                        break
                    if pipe is not None:
                        return pipe

                logger.info(
                    "Step %s | reasoning=%s | tool=%s | result_status=%s",
                    step,
                    (getattr(decision, "reasoning", "") or "")[:300],
                    getattr(decision, "tool", "?"),
                    result.get("status", "?"),
                    extra={"task_id": task.id},
                )
                memory.add_step(decision.model_dump())
                memory.add_observation(_enrich_observation_for_memory(result, decision))
                _bump_stall_counters(stall, steps[-1])
                if decision.tool == "run_tests" and isinstance(result, dict):
                    _maybe_lock_failing_scope(task.id, result)
                    fs = result.get("failure_summary")
                    if fs:
                        last_test_failure_summary = str(fs)
                    elif int(result.get("exit_code", 0) or 0) != 0:
                        last_test_failure_summary = (result.get("stdout") or "")[:8000]
                step += 1

                if chain_rt is not None:
                    td = chain_rt
                    try:
                        tr = await asyncio.to_thread(executor.execute, td, task, step=step)
                    except ExecutorError as e:
                        logger.exception("Executor failed for task %s: %s", task.id, e)
                        tr = {"status": "error", "stderr": str(e), "exit_code": -1}
                    _append_step_with_transcript(
                        task,
                        steps,
                        {
                            "step": step,
                            "decision": td.model_dump(),
                            "result": tr,
                            "decision_retries": 0,
                            "decision_attempts": 1,
                        },
                    )
                    replay.save(task.id, {"goal": task.goal, "steps": steps})
                    if isinstance(tr, dict):
                        pipe2 = await run_tests_outcome_pipeline(td, tr, rt_step=step)
                        if pipe2 == "__continue__":
                            continue
                        if pipe2 == "__break_phase__":
                            break
                        if pipe2 is not None:
                            return pipe2
                    logger.info(
                        "Step %s | reasoning=%s | tool=%s | result_status=%s",
                        step,
                        (getattr(td, "reasoning", "") or "")[:300],
                        getattr(td, "tool", "?"),
                        tr.get("status", "?") if isinstance(tr, dict) else "?",
                        extra={"task_id": task.id},
                    )
                    memory.add_step(td.model_dump())
                    memory.add_observation(_enrich_observation_for_memory(tr, td))
                    _bump_stall_counters(stall, steps[-1])
                    if isinstance(tr, dict):
                        _maybe_lock_failing_scope(task.id, tr)
                        fs2 = tr.get("failure_summary")
                        if fs2:
                            last_test_failure_summary = str(fs2)
                        elif int(tr.get("exit_code", 0) or 0) != 0:
                            last_test_failure_summary = (tr.get("stdout") or "")[:8000]
                    step += 1

            if broke_for_review:
                continue

            replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "max_steps_reached"})
            task.status = "max_steps_reached"
            save_task(task)
            write_last_run_log(task, steps)
            return "max_steps_reached"
