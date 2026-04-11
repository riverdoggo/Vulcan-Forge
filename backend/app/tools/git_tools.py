import shlex
from typing import Any

from app.tools.docker_terminal import run_in_container_argv

# Basic git operations inside sandbox container; message is escaped to prevent injection.

# Unstage compiled / cache artifacts so they never appear in diffs or commits.
_GIT_SANITIZE_STAGED = r"""
git add -A
git diff --cached --name-only -z | while IFS= read -r -d '' f; do
  if [[ "$f" == *__pycache__* ]] || [[ "$f" == *.pyc ]] || [[ "$f" == *.pyo ]] || [[ "$f" == *.pyd ]] \
     || [[ "$f" == .pytest_cache/* ]] || [[ "$f" == */.pytest_cache/* ]]; then
    git reset HEAD -- "$f" 2>/dev/null || true
  fi
done
exit 0
"""


def _stage_workspace_sanitized(container: str) -> dict[str, Any]:
    return run_in_container_argv(container, ["bash", "-lc", _GIT_SANITIZE_STAGED])


def git_diff(container: str, path: str | None = None) -> dict[str, Any]:
    stage = _stage_workspace_sanitized(container)
    if stage.get("exit_code") != 0:
        return stage
    status_res = run_in_container_argv(container, ["git", "status", "--porcelain"])
    if status_res.get("exit_code") != 0:
        return status_res
    if not (status_res.get("stdout") or "").strip():
        last_commit = run_in_container_argv(container, ["git", "log", "--oneline", "-1"])
        last_commit_line = (last_commit.get("stdout") or "").strip()
        return {
            "status": "success",
            "stdout": (
                "No uncommitted changes found. "
                + (f"Last commit: {last_commit_line}\n" if last_commit_line else "")
                + "Changes may have already been committed in a previous cycle."
            ),
            "stderr": "",
            "exit_code": 0,
        }
    return run_in_container_argv(container, ["git", "diff", "--cached"])


def git_commit(container: str, message: str | None) -> dict[str, Any]:
    if not message or not str(message).strip():
        return {"status": "error", "stderr": "commit message required", "exit_code": -1, "stdout": ""}
    safe_msg = shlex.quote(str(message).strip())
    stage = _stage_workspace_sanitized(container)
    if stage.get("exit_code") != 0:
        return stage
    return run_in_container_argv(container, ["sh", "-c", f"git commit -m {safe_msg}"])
