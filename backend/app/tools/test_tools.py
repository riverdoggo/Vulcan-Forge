from typing import Any

from app.tools.docker_terminal import run_in_container_argv


def run_tests(container: str, path: str | None = None) -> dict[str, Any]:
    """Run pytest in the workspace container. path is optional (passed as single arg to avoid injection)."""
    if path and str(path).strip():
        return run_in_container_argv(container, ["pytest", str(path).strip()])
    return run_in_container_argv(container, ["pytest"])