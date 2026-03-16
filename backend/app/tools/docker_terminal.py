import logging
import subprocess
from typing import Any

from app.config.settings import DOCKER_EXEC_TIMEOUT_SEC
from app.models.tool_result import ToolResult

logger = logging.getLogger(__name__)


def run_in_container(
    container: str,
    command: str,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Run a shell command in the container. Returns ToolResult as dict. Use run_in_container_argv for untrusted args."""
    timeout = timeout_sec or DOCKER_EXEC_TIMEOUT_SEC
    try:
        result = subprocess.run(
            ["docker", "exec", container, "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.warning("Docker exec timed out after %ss: %s", timeout, e)
        return ToolResult(
            status="error",
            stderr=f"Command timed out after {timeout}s",
            exit_code=-1,
        ).to_dict()
    except Exception as e:
        logger.exception("Docker exec failed: %s", e)
        return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
    return ToolResult.from_subprocess(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    ).to_dict()


def run_in_container_argv(
    container: str,
    argv: list[str],
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Run argv in container without shell; safe for untrusted path/args."""
    timeout = timeout_sec or DOCKER_EXEC_TIMEOUT_SEC
    try:
        result = subprocess.run(
            ["docker", "exec", container] + argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.warning("Docker exec (argv) timed out after %ss: %s", timeout, e)
        return ToolResult(
            status="error",
            stderr=f"Command timed out after {timeout}s",
            exit_code=-1,
        ).to_dict()
    except Exception as e:
        logger.exception("Docker exec (argv) failed: %s", e)
        return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
    return ToolResult.from_subprocess(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    ).to_dict()