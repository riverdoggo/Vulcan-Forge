import asyncio
import logging

from app.database import save_task
from app.logging.replay_store import ReplayStore
from app.models.task import Task
from app.workspace.workspace_manager import WorkspaceManager, terminate_workspace_container
from agent_runtime.agent_loop import AgentLoop, _finalize_task_killed

logger = logging.getLogger(__name__)

_DOCKER_DAEMON_HINT = (
    "Docker is not running or not reachable. On Windows, start Docker Desktop and wait until "
    "it is fully started, then try again. The agent needs a running Docker engine for the sandbox container."
)


def _workspace_failure_user_message(exc: BaseException) -> str:
    msg = str(exc)
    if any(
        s in msg
        for s in (
            "dockerDesktopLinuxEngine",
            "dockerDesktopEngine",
            "docker API",
            "Cannot connect to the Docker daemon",
            "connection refused",
        )
    ):
        return _DOCKER_DAEMON_HINT
    return msg[:4000]


class Orchestrator:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.session_order: list[str] = []
        self.workspace_manager = WorkspaceManager()
        self.agent_loop = AgentLoop()
        self.replay_store = ReplayStore()

    def create_task(self, task: Task) -> Task:
        try:
            workspace = self.workspace_manager.create_workspace(task)
        except Exception as e:
            logger.exception("Failed to create workspace for task %s: %s", task.id, e)
            task.status = "error"
            task.error_message = _workspace_failure_user_message(e)
            self.tasks[task.id] = task
            self.session_order.insert(0, task.id)
            save_task(task)
            return task
        task.workspace = workspace
        task.status = "running"
        self.tasks[task.id] = task
        self.session_order.insert(0, task.id)
        save_task(task)
        logger.info("Created task %s", task.id, extra={"goal": task.goal[:80]})
        return task

    async def kill_task_async(self, task_id: str) -> dict[str, str]:
        """
        Set kill_requested, cancel the running asyncio agent task (aborts in-flight LLM),
        remove Docker sandbox. Idempotent.
        """
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError("Task not found")

        if task.status == "killed" and task.kill_requested:
            try:
                terminate_workspace_container(task_id, remove_workspace_dir=True)
            except Exception as e:
                logger.warning("Repeat kill cleanup for %s: %s", task_id, e)
            return {"status": "ok", "message": "Task already killed."}

        task.kill_requested = True
        save_task(task)
        logger.info("Kill flag set for task %s", task_id)

        rt = task.agent_runtime_task
        if rt is not None and not rt.done():
            rt.cancel()
            logger.info("Cancelled asyncio agent task for %s", task_id)

        task.status = "killed"
        save_task(task)

        try:
            terminate_workspace_container(task_id, remove_workspace_dir=True)
        except Exception as e:
            logger.warning("Docker/workspace cleanup during kill for %s: %s", task_id, e)

        return {"status": "ok", "message": "Agent stopped; in-flight LLM requests cancelled."}

    async def run_agent_async(self, task: Task) -> None:
        try:
            result = await self.agent_loop.run_async(task)
            if getattr(task, "kill_requested", False):
                task.status = "killed"
            else:
                task.status = result
        except asyncio.CancelledError:
            logger.info("Agent asyncio task cancelled for task %s", task.id)
            task.kill_requested = True
            task.status = "killed"
            try:
                log_data = self.replay_store.get(task.id) or {"goal": task.goal, "steps": []}
                steps = list(log_data.get("steps", []))
                _finalize_task_killed(task, steps, self.replay_store)
            except Exception as ex:
                logger.warning("Finalize kill after cancel failed for %s: %s", task.id, ex)
        except Exception as e:
            logger.exception("Agent loop crashed for task %s: %s", task.id, e)
            if getattr(task, "kill_requested", False):
                task.status = "killed"
                try:
                    log_data = self.replay_store.get(task.id) or {"goal": task.goal, "steps": []}
                    steps = list(log_data.get("steps", []))
                    _finalize_task_killed(task, steps, self.replay_store)
                except Exception as ex:
                    logger.warning("Finalize kill after crash failed for %s: %s", task.id, ex)
            else:
                task.status = "error"
        finally:
            task.agent_runtime_task = None
            save_task(task)
            st = task.status
            if task.workspace and st in ("completed", "error", "max_steps_reached", "killed"):
                try:
                    terminate_workspace_container(
                        task.id,
                        remove_workspace_dir=(st == "killed"),
                    )
                except Exception as e:
                    logger.warning("Post-run workspace cleanup failed for %s: %s", task.id, e)

    def list_tasks(self) -> list[Task]:
        ordered = [self.tasks[i] for i in self.session_order if i in self.tasks]
        return sorted(
            ordered,
            key=lambda t: t.created_at or "",
            reverse=True,
        )

    def get_logs(self, task_id: str) -> dict:
        data = self.replay_store.get(task_id)
        if not data:
            return {"status": "no_logs_yet", "steps": []}
        return data

    def approve_task(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if not task or task.status != "awaiting_approval":
            raise ValueError("Task not found or not awaiting approval")

        container = task.workspace["container"]
        from app.tools.git_tools import git_commit  # import here to avoid circular dep

        git_commit(container, "Approved by human")

        task.status = "completed"
        save_task(task)
        logs = self.get_logs(task_id)
        if logs and "steps" in logs:
            logs["status"] = "completed"
            self.replay_store.save(task_id, logs)

        from app.logging.log_writer import write_last_run_log

        write_last_run_log(task, logs.get("steps", []))

    def reject_task(self, task_id: str, reason: str = "") -> None:
        task = self.tasks.get(task_id)
        if not task or task.status != "awaiting_approval":
            raise ValueError("Task not found or not awaiting approval")

        container = task.workspace["container"]
        from app.tools.docker_terminal import run_in_container_argv

        run_in_container_argv(container, ["git", "checkout", "--", "."])
        run_in_container_argv(container, ["git", "clean", "-fd"])

        task.status = "rejected"
        task.rejection_reason = reason
        save_task(task)
        logs = self.get_logs(task_id)
        if logs and "steps" in logs:
            logs["status"] = "rejected"
            self.replay_store.save(task_id, logs)

        from app.logging.log_writer import write_last_run_log

        write_last_run_log(task, logs.get("steps", []))
