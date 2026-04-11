from typing import Any, Callable

from app.memory.compression import compress_directory_listing
from app.tools.docker_terminal import run_in_container
from app.tools.filesystem_tools import apply_patch, read_file, write_file
from app.tools.git_tools import git_commit, git_diff
from app.tools.run_command import run_command
from app.tools.test_tools import run_tests


def list_directory(container: str, path: str | None = None) -> dict[str, Any]:
    p = path.strip() if path else "/workspace"
    r = run_in_container(container, f"ls -la {p}")
    if isinstance(r, dict):
        r = dict(r)
        out = r.get("stdout")
        if isinstance(out, str) and out.strip():
            r["stdout"] = compress_directory_listing(out)
    return r


# Map tool name -> callable(container, input) -> dict
# write_file is the primary editor; apply_patch is optional fallback (listed after write_file).
TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "list_directory": list_directory,
    "read_file": read_file,
    "write_file": write_file,
    "apply_patch": apply_patch,
    "run_tests": run_tests,
    "run_command": run_command,
    "git_diff": git_diff,
    "git_commit": git_commit,
}
