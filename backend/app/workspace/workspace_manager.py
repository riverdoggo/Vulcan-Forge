import logging
import os
import subprocess
from pathlib import Path

from app.config.settings import WORKSPACE_ROOT, SANDBOX_IMAGE

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = "1.0"


def _sanitize_task_id(task_id: str) -> str:
    """Return a safe substring for use in container/disk names."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    return safe[:64] or "default"


def _container_name(task_id: str) -> str:
    return f"agent_ws_{_sanitize_task_id(task_id)}"


class WorkspaceManager:
    def create_workspace(self, task_id: str) -> dict[str, str]:
        safe_id = _sanitize_task_id(task_id)
        path = f"{WORKSPACE_ROOT}/{safe_id}"
        os.makedirs(path, exist_ok=True)
        container = _container_name(task_id)
        abs_path = str(Path(os.getcwd()).resolve() / path)
        cmd = [
            "docker",
            "run",
            "-d",
            "--memory", DEFAULT_MEMORY_LIMIT,
            "--cpus", DEFAULT_CPU_LIMIT,
            "--network", "none",
            "--name", container,
            "-v", f"{abs_path}:/workspace",
            SANDBOX_IMAGE,
            "sleep", "infinity",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                stderr = result.stderr or ""
                if "already in use" in stderr or "Conflict" in stderr:
                    self.cleanup(container)
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    raise RuntimeError(f"docker run failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("docker run timed out after 30s")
        logger.info("Created workspace container %s for task %s", container, task_id)
        return {"path": path, "container": container}

    def cleanup(self, container: str) -> None:
        """Stop and remove a workspace container. Idempotent."""
        for action, args in [("stop", ["docker", "stop", "-t", "2", container]), ("rm", ["docker", "rm", "-f", container])]:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if r.returncode != 0 and "No such" not in (r.stderr or ""):
                logger.warning("%s %s: %s", action, container, r.stderr)