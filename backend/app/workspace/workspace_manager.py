import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.config.settings import WORKSPACE_ROOT, SANDBOX_IMAGE

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = "1.0"

# Resolve project root as 4 levels up from this file:
# backend/app/workspace/workspace_manager.py -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# WORKSPACE_ROOT is a name like "workspaces"; keep env override but resolve to absolute base dir
WORKSPACES_BASE = PROJECT_ROOT / WORKSPACE_ROOT
TEST_REPO_DIR = WORKSPACES_BASE / "test_repo"


def _sanitize_task_id(task_id: str) -> str:
    """Return a safe substring for use in container/disk names."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    return safe[:64] or "default"


def _container_name(task_id: str) -> str:
    return f"agent_ws_{_sanitize_task_id(task_id)}"


class WorkspaceManager:
    def create_workspace(self, task_id: str) -> dict[str, str]:
        safe_id = _sanitize_task_id(task_id)
        # Path relative to project root (for returning/storing), and absolute on disk.
        rel_path = f"{WORKSPACE_ROOT}/{safe_id}"
        workspace_path = WORKSPACES_BASE / safe_id
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Temporary: copy test_repo contents into workspace for Phase 2 testing.
        try:
            if TEST_REPO_DIR.exists() and TEST_REPO_DIR.is_dir():
                for item in TEST_REPO_DIR.iterdir():
                    if item.is_file():
                        shutil.copy2(item, workspace_path / item.name)
            else:
                logger.warning("test_repo directory not found at %s; workspace will start empty", TEST_REPO_DIR)
        except Exception as e:
            logger.warning("Failed to copy test_repo into workspace %s: %s", workspace_path, e)

        container = _container_name(task_id)
        abs_path = str(workspace_path.resolve())
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
        logger.info("Created workspace container %s for task %s (workspace=%s)", container, task_id, abs_path)

        # initialize git so diff and commit work
        try:
            init = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "bash",
                    "-c",
                    "cd /workspace && git init && git add -A && git commit -m 'initial' --allow-empty",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if init.returncode == 0:
                logger.info("Initialized git in workspace %s", container)
            else:
                logger.warning("Git init failed in workspace %s: %s", container, (init.stderr or "").strip())
        except Exception as e:
            logger.warning("Git init error in workspace %s: %s", container, e)

        return {"path": rel_path, "container": container}

    def cleanup(self, container: str) -> None:
        """Stop and remove a workspace container. Idempotent."""
        for action, args in [("stop", ["docker", "stop", "-t", "2", container]), ("rm", ["docker", "rm", "-f", container])]:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if r.returncode != 0 and "No such" not in (r.stderr or ""):
                logger.warning("%s %s: %s", action, container, r.stderr)