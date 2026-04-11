import logging
import subprocess
from typing import Any

from app.config.settings import DOCKER_EXEC_TIMEOUT_SEC
from app.models.tool_result import ToolResult

logger = logging.getLogger(__name__)


def _docker_exec_completed(
    docker_argv: list[str],
    *,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    """
    Run the docker CLI; read stdout/stderr as raw bytes (full ``communicate()``),
    then callers decode. Uses binary pipes and ``-i`` on exec so Docker Desktop on
    Windows reliably delivers large streamed output without truncating mid-payload.
    """
    proc = subprocess.Popen(
        docker_argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=-1,
    )
    try:
        out_b, err_b = proc.communicate(timeout=timeout)
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, out_b or b"", err_b or b""
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out_b, err_b = proc.communicate(timeout=10)
        except Exception:
            out_b, err_b = b"", b""
        raise subprocess.TimeoutExpired(
            docker_argv, timeout, output=out_b, stderr=err_b
        ) from None


def _bytes_result(
    returncode: int,
    out_b: bytes,
    err_b: bytes,
) -> dict[str, Any]:
    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    return ToolResult.from_subprocess(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    ).to_dict()


def run_in_container(
    container: str,
    command: str,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Run a shell command in the container. Returns ToolResult as dict. Use run_in_container_argv for untrusted args."""
    timeout = float(timeout_sec or DOCKER_EXEC_TIMEOUT_SEC)
    docker_argv = ["docker", "exec", "-i", container, "bash", "-c", command]
    try:
        rc, out_b, err_b = _docker_exec_completed(docker_argv, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        logger.warning("Docker exec timed out after %ss: %s", timeout, e)
        return ToolResult(
            status="error",
            stderr=f"Command timed out after {int(timeout)}s",
            exit_code=-1,
        ).to_dict()
    except Exception as e:
        logger.exception("Docker exec failed: %s", e)
        return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
    return _bytes_result(rc, out_b, err_b)


def run_in_container_argv(
    container: str,
    argv: list[str],
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Run argv in container without shell; safe for untrusted path/args."""
    timeout = float(timeout_sec or DOCKER_EXEC_TIMEOUT_SEC)
    docker_argv = ["docker", "exec", "-i", container, *argv]
    try:
        rc, out_b, err_b = _docker_exec_completed(docker_argv, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        logger.warning("Docker exec (argv) timed out after %ss: %s", timeout, e)
        return ToolResult(
            status="error",
            stderr=f"Command timed out after {int(timeout)}s",
            exit_code=-1,
        ).to_dict()
    except Exception as e:
        logger.exception("Docker exec (argv) failed: %s", e)
        return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
    return _bytes_result(rc, out_b, err_b)
