import shlex
from typing import Any

from app.tools.docker_terminal import run_in_container_argv

# Basic git operations inside sandbox container; message is escaped to prevent injection.


def git_diff(container: str, path: str | None = None) -> dict[str, Any]:
    return run_in_container_argv(container, ["git", "diff"])


def git_commit(container: str, message: str | None) -> dict[str, Any]:
    if not message or not str(message).strip():
        return {"status": "error", "stderr": "commit message required", "exit_code": -1, "stdout": ""}
    safe_msg = shlex.quote(str(message).strip())
    run_in_container_argv(container, ["git", "add", "."])
    return run_in_container_argv(container, ["sh", "-c", f"git commit -m {safe_msg}"])