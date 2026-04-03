import shlex
from typing import Any

from app.models.tool_result import ToolResult
from app.tools.docker_terminal import run_in_container_argv


def run_command(container: str, command: str | None = None) -> dict[str, Any]:
    """Run a argv-style command inside the container (e.g. pip install click)."""
    if not command or not str(command).strip():
        return ToolResult(
            status="error",
            stderr="No command provided",
            exit_code=-1,
        ).to_dict()

    input_str = str(command).strip()
    blocked = ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:"]
    if any(b in input_str for b in blocked):
        return ToolResult(
            status="error",
            stderr="Command blocked for safety",
            exit_code=-1,
        ).to_dict()

    try:
        parts = shlex.split(input_str)
    except ValueError as e:
        return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
    if not parts:
        return ToolResult(status="error", stderr="No command provided", exit_code=-1).to_dict()

    raw = run_in_container_argv(container, parts)
    out = (raw.get("stdout") or "").rstrip()
    err = (raw.get("stderr") or "").rstrip()
    if out and err:
        combined = out + "\n" + err
    else:
        combined = out or err
    return ToolResult.from_subprocess(
        returncode=raw.get("exit_code", -1),
        stdout=combined,
        stderr="",
    ).to_dict()
