from typing import Any, Callable

from app.tools.docker_terminal import run_in_container
from app.tools.filesystem_tools import read_file, write_file
from app.tools.git_tools import git_commit, git_diff
from app.tools.test_tools import run_tests


def list_directory(container: str, path: str | None = None) -> dict[str, Any]:
    p = path.strip() if path else "/workspace"
    return run_in_container(container, f"ls -la {p}")


# Map tool name -> callable(container, input) -> dict
TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "run_tests": run_tests,
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "git_diff": git_diff,
    "git_commit": git_commit,
}