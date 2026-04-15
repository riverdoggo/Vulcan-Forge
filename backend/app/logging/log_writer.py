import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any

# Resolves to: project_root / 'logs'
LOGS_DIR = Path(__file__).resolve().parents[3] / "logs"


def append_runtime_log(task: Any, line: str) -> None:
    """Append a single telemetry line for inclusion in last_run.log."""
    text = (line or "").strip()
    if not text:
        return
    lst = getattr(task, "runtime_log_lines", None)
    if not isinstance(lst, list):
        task.runtime_log_lines = []
        lst = task.runtime_log_lines
    lst.append(text)

def write_last_run_log(task, steps: list) -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = LOGS_DIR / "last_run.log"
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Task ID: {task.id}\n")
        f.write(f"Status: {task.status}\n")
        f.write(f"Timestamp: {datetime.utcnow().isoformat()}\n")
        rt = getattr(task, "runtime_log_lines", None) or []
        if isinstance(rt, list) and rt:
            f.write("--- runtime telemetry ---\n")
            for line in rt:
                f.write(f"{line}\n")
            f.write("\n")
        f.write(f"--- {len(steps)} steps ---\n\n")
        
        for step_data in steps:
            step_idx = step_data.get("step", "?")
            decision = step_data.get("decision", {})
            result = step_data.get("result", {})
            
            # handle case where decision is a string ("error" fallback) or dict
            if isinstance(decision, dict):
                tool = decision.get("tool", "None")
                input_val = decision.get("input", "None")
                reasoning = decision.get("reasoning", "")
                if decision.get("error") == "invalid_llm_decision":
                    reasoning = f"[invalid LLM decision] {decision.get('last_error', '')}"
            else:
                tool = "None"
                input_val = "None"
                reasoning = ""

            # handle case where result is a string
            if isinstance(result, dict):
                res_status = result.get("status", "None")
                stdout = result.get("stdout", "")
                diff_ratio = result.get("diff_ratio")
                rejected_reason = result.get("rejected_reason")
                test_counts = result.get("test_counts")
                plan_payload = result.get("plan")
                failure_summary = result.get("failure_summary")
                raw_llm_fail = result.get("raw_llm_on_failure")
                decision_retry_count = result.get("decision_retry_count")
            else:
                res_status = str(result)
                stdout = ""
                diff_ratio = None
                rejected_reason = None
                test_counts = None
                plan_payload = None
                failure_summary = None
                raw_llm_fail = None
                decision_retry_count = None

            f.write(f"Step {step_idx} | Tool: {tool} | Input: {input_val}\n")
            if isinstance(result, dict):
                if result.get("from_cache"):
                    f.write(f"  cache_hit: {input_val}\n")
                if result.get("patch_applied_target"):
                    f.write(f"  patch_applied: {result.get('patch_applied_target')}\n")
                if result.get("loop_guard"):
                    f.write(f"  loop_guard_triggered: {result.get('loop_guard')}\n")
            if reasoning:
                f.write(f"  Reasoning: {str(reasoning).strip()[:2000]}\n")
            dr = step_data.get("decision_retries")
            if dr is not None:
                f.write(f"  Decision retries (LLM): {dr}\n")
            if decision_retry_count is not None:
                f.write(f"  Decision retries (recorded on step): {decision_retry_count}\n")
            f.write(f"  Result status: {res_status}\n")
            if diff_ratio is not None:
                f.write(f"  Diff ratio: {diff_ratio}\n")
            if rejected_reason:
                f.write(f"  Rejected: {rejected_reason}\n")
            if isinstance(test_counts, dict) and any(v is not None for v in test_counts.values()):
                f.write(f"  Test counts: {test_counts}\n")
            if failure_summary:
                f.write(f"  Failure summary:\n{str(failure_summary).strip()[:2000]}\n")
            if raw_llm_fail:
                f.write(f"  Raw LLM (parse failure): {str(raw_llm_fail).strip()[:4000]}\n")
            if tool == "plan" and plan_payload is not None:
                f.write(f"  Plan output: {str(plan_payload)[:2000]}\n")
            if isinstance(decision, dict) and decision.get("tool") == "reviewer_agent" and isinstance(result, dict):
                reason = result.get("reason", "") or ""
                suggestions = result.get("suggestions", "") or ""
                iteration = result.get("iteration", "—")
                conf = result.get("confidence")
                if conf is not None:
                    f.write(f"  Confidence: {conf}\n")
                f.write(f"  Verdict: {reason}\n")
                if suggestions.strip():
                    f.write(f"  Suggestions: {suggestions.strip()[:2000]}\n")
                f.write(f"  Iteration: {iteration}\n")
            else:
                # truncate stdout to keep logs readable if it's huge, though prompt doesn't strictly say it
                f.write(f"  Stdout: {str(stdout).strip()[:1000]}\n")
            f.write("\n")
            
        f.write(f"Final status: {task.status}\n")
        if task.status == "awaiting_approval":
            f.write(f"Awaiting human approval. Run POST /tasks/{task.id}/approve or /reject to continue.\n")
            er = getattr(task, "escalation_reason", "") or ""
            if er:
                f.write(f"Escalation reason: {er}\n")
            if "3 review cycles" in er:
                f.write(
                    "NOTE: This task reached the human gate because the automated reviewer "
                    "returned needs_changes three times and could not be satisfied within the review budget.\n"
                )
    # Keep a dedicated Azure latest-run log in sync.
    azure_log_path = LOGS_DIR / "last_run_azure.log"
    try:
        shutil.copyfile(log_path, azure_log_path)
    except Exception:
        pass
