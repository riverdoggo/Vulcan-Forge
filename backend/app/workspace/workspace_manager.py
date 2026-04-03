import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config.settings import WORKSPACE_ROOT, SANDBOX_IMAGE
from app.models.task import Task

logger = logging.getLogger(__name__)

# Must match sandbox image Python version (see sandbox/docker/Dockerfile)
CONTAINER_SITE_PACKAGES = "/usr/local/lib/python3.11/site-packages"

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


def _copy_top_level_files_only(src: Path, dst: Path) -> None:
    """Match legacy test_repo behavior: copy only top-level files."""
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists() or not src.is_dir():
        return
    for item in src.iterdir():
        if item.is_file():
            shutil.copy2(item, dst / item.name)


def _copy_tree_into_workspace(src: Path, dst: Path, *, skip_git: bool) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in src.iterdir():
        if skip_git and item.name == ".git":
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def prepare_workspace(task: Task, workspace_path: Path) -> None:
    if task.repo_type == "github":
        clone_dir = Path(tempfile.gettempdir()) / f"orch_clone_{task.id}"
        if clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        clone_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.info("Git clone starting: url=%s dest=%s", task.repo_url, clone_dir)
            result = subprocess.run(
                ["git", "clone", "--depth", "1", task.repo_url, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip() or "(no stderr)"
                logger.error("Git clone failed (rc=%s): %s", result.returncode, err)
                raise ValueError(f"Git clone failed: {err}")
            logger.info("Git clone succeeded for %s", task.repo_url)
            _copy_tree_into_workspace(clone_dir, workspace_path, skip_git=True)
        finally:
            shutil.rmtree(clone_dir, ignore_errors=True)
    elif task.repo_type == "local":
        local_path = Path(os.path.expanduser(str(task.repo_url))).resolve()
        if not local_path.exists():
            raise ValueError(f"Local path does not exist: {local_path}")
        _copy_tree_into_workspace(local_path, workspace_path, skip_git=True)
    else:
        try:
            if TEST_REPO_DIR.exists() and TEST_REPO_DIR.is_dir():
                _copy_top_level_files_only(TEST_REPO_DIR, workspace_path)
            else:
                logger.warning(
                    "test_repo directory not found at %s; workspace will start empty",
                    TEST_REPO_DIR,
                )
        except Exception as e:
            logger.warning("Failed to copy test_repo into workspace %s: %s", workspace_path, e)


def install_dependencies_to_container(container_id: str, repo_path: str) -> None:
    """
    Install dependencies on the host (network available), then copy into the container site-packages.
    Keeps the sandbox container on --network none.
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        logger.warning("install_dependencies_to_container: not a directory: %s", repo_path)
        return

    if not any((repo / name).exists() for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        logger.info(
            "Skipping host deps install: no pyproject.toml, setup.py, setup.cfg, or requirements.txt in %s",
            repo,
        )
        return

    site_packages_dir = repo / "_deps"
    if site_packages_dir.exists():
        shutil.rmtree(site_packages_dir, ignore_errors=True)
    site_packages_dir.mkdir(parents=True, exist_ok=True)

    def _pip(extra: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(site_packages_dir), "--quiet", *extra],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=600,
        )

    try:
        result: subprocess.CompletedProcess[str] | None = None
        has_project = any(
            (repo / name).exists() for name in ("pyproject.toml", "setup.py", "setup.cfg")
        )
        if has_project:
            for extra in (["-e", "."], ["."]):
                result = _pip(extra)
                logger.info(
                    "Host pip install: extra=%s returncode=%s stderr=%s",
                    extra,
                    result.returncode,
                    (result.stderr or "")[:200],
                )
                if result.returncode == 0:
                    break

        if (result is None or result.returncode != 0) and (repo / "requirements.txt").exists():
            result = _pip(["-r", str(repo / "requirements.txt")])
            logger.info(
                "Host pip install (requirements.txt): returncode=%s stderr=%s",
                result.returncode,
                (result.stderr or "")[:200],
            )

        if result is None or result.returncode != 0:
            out = ((result.stderr or "") + (result.stdout or "")).strip() if result else ""
            logger.warning(
                "Host pip install failed for %s: %s",
                repo,
                out[:800] or "(no output)",
            )
            return

        src = str(site_packages_dir).replace("\\", "/") + "/."
        dest = f"{container_id}:{CONTAINER_SITE_PACKAGES}/"
        cp = subprocess.run(
            ["docker", "cp", src, dest],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cp.returncode != 0:
            logger.error(
                "docker cp deps failed: %s %s",
                (cp.stderr or "")[:500],
                (cp.stdout or "")[:200],
            )
        else:
            logger.info("Copied host-installed dependencies into container %s", container_id)
    finally:
        shutil.rmtree(site_packages_dir, ignore_errors=True)


class WorkspaceManager:
    def create_workspace(self, task: Task) -> dict[str, str]:
        task_id = task.id
        safe_id = _sanitize_task_id(task_id)
        rel_path = f"{WORKSPACE_ROOT}/{safe_id}"
        workspace_path = WORKSPACES_BASE / safe_id
        workspace_path.mkdir(parents=True, exist_ok=True)

        container = _container_name(task_id)
        abs_path = str(workspace_path.resolve())
        cmd = [
            "docker",
            "run",
            "-d",
            "--memory",
            DEFAULT_MEMORY_LIMIT,
            "--cpus",
            DEFAULT_CPU_LIMIT,
            "--network",
            "none",
            "--name",
            container,
            "-v",
            f"{abs_path}:/workspace",
            SANDBOX_IMAGE,
            "sleep",
            "infinity",
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
        logger.info(
            "Created workspace container %s for task %s (workspace=%s)",
            container,
            task_id,
            abs_path,
        )

        try:
            prepare_workspace(task, workspace_path)
        except Exception as e:
            logger.exception("prepare_workspace failed for task %s: %s", task_id, e)
            self.cleanup(container)
            raise

        try:
            install_dependencies_to_container(container, abs_path)
        except Exception as e:
            logger.warning("install_dependencies_to_container failed for %s: %s", container, e)

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
                logger.warning(
                    "Git init failed in workspace %s: %s",
                    container,
                    (init.stderr or "").strip(),
                )
        except Exception as e:
            logger.warning("Git init error in workspace %s: %s", container, e)

        return {"path": rel_path, "container": container}

    def cleanup(self, container: str) -> None:
        """Stop and remove a workspace container. Idempotent."""
        for action, args in [
            ("stop", ["docker", "stop", "-t", "2", container]),
            ("rm", ["docker", "rm", "-f", container]),
        ]:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if r.returncode != 0 and "No such" not in (r.stderr or ""):
                logger.warning("%s %s: %s", action, container, r.stderr)


def terminate_workspace_container(task_id: str, *, remove_workspace_dir: bool = False) -> None:
    """
    Stop/remove the sandbox container for a task (name: agent_ws_<sanitized_task_id>).
    Optionally delete the on-disk workspace copy. Safe if the container is already gone.
    """
    container = _container_name(task_id)
    mgr = WorkspaceManager()
    mgr.cleanup(container)
    if remove_workspace_dir:
        safe_id = _sanitize_task_id(task_id)
        path = WORKSPACES_BASE / safe_id
        if path.is_dir():
            try:
                shutil.rmtree(path, ignore_errors=True)
                logger.info("Removed workspace directory %s for task %s", path, task_id)
            except Exception as e:
                logger.warning("Could not remove workspace %s: %s", path, e)
