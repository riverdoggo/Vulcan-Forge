import base64
import re
import shlex
from typing import Any

from app.tools.docker_terminal import run_in_container_argv

# Paths must be under /workspace and not contain shell metacharacters
_PATH_SAFE = re.compile(r"^[\w./\-]+$")


def _safe_path(path: str) -> str:
    if not path or ".." in path or not _PATH_SAFE.match(path):
        raise ValueError(f"Invalid or unsafe path: {path!r}")
    return path if path.startswith("/") else f"/workspace/{path}"


def read_file(container: str, path: str | None) -> dict[str, Any]:
    """Read file inside container; path is validated and not passed through shell."""
    if not path:
        return {"status": "error", "stderr": "path required", "exit_code": -1, "stdout": ""}
    try:
        safe = _safe_path(path.strip())
    except ValueError as e:
        return {"status": "error", "stderr": str(e), "exit_code": -1, "stdout": ""}
    return run_in_container_argv(container, ["cat", "--", safe])


def write_file(container: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Write content to path in container; content is base64-encoded to avoid shell injection."""
    if not args:
        return {"status": "error", "stderr": "args required", "exit_code": -1, "stdout": ""}
    path = args.get("path")
    content = args.get("content")
    if path is None:
        return {"status": "error", "stderr": "path required", "exit_code": -1, "stdout": ""}
    try:
        safe_path = _safe_path(str(path).strip())
    except ValueError as e:
        return {"status": "error", "stderr": str(e), "exit_code": -1, "stdout": ""}
    raw = content if isinstance(content, bytes) else (content or "").encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    # Pass content via env to avoid any shell interpretation
    argv = ["env", f"B64={b64}", "sh", "-c", f"echo \"$B64\" | base64 -d > {shlex.quote(safe_path)}"]
    return run_in_container_argv(container, argv)