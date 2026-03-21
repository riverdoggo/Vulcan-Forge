import os
from pathlib import Path
from datetime import datetime

# Resolves to: project_root / 'logs'
LOGS_DIR = Path(__file__).resolve().parents[3] / "logs"

def write_last_run_log(task, steps: list) -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = LOGS_DIR / "last_run.log"
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"Task ID: {task.id}\n")
        f.write(f"Status: {task.status}\n")
        f.write(f"Timestamp: {datetime.utcnow().isoformat()}\n")
        f.write(f"--- {len(steps)} steps ---\n\n")
        
        for step_data in steps:
            step_idx = step_data.get("step", "?")
            decision = step_data.get("decision", {})
            result = step_data.get("result", {})
            
            # handle case where decision is a string ("error" fallback) or dict
            if isinstance(decision, dict):
                tool = decision.get("tool", "None")
                input_val = decision.get("input", "None")
            else:
                tool = "None"
                input_val = "None"

            # handle case where result is a string
            if isinstance(result, dict):
                res_status = result.get("status", "None")
                stdout = result.get("stdout", "")
            else:
                res_status = str(result)
                stdout = ""

            f.write(f"Step {step_idx} | Tool: {tool} | Input: {input_val}\n")
            f.write(f"  Result status: {res_status}\n")
            if isinstance(decision, dict) and decision.get("tool") == "reviewer_agent" and isinstance(result, dict):
                reason = result.get("reason", "") or ""
                suggestions = result.get("suggestions", "") or ""
                iteration = result.get("iteration", "—")
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
