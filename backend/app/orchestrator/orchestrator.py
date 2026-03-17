import logging
from typing import Any

from app.logging.replay_store import ReplayStore
from app.models.task import Task
from app.workspace.workspace_manager import WorkspaceManager
from agent_runtime.agent_loop import AgentLoop

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.workspace_manager = WorkspaceManager()
        self.agent_loop = AgentLoop()
        self.replay_store = ReplayStore()

    def create_task(self, task: Task) -> Task:
        try:
            workspace = self.workspace_manager.create_workspace(task.id)
        except Exception as e:
            logger.exception("Failed to create workspace for task %s: %s", task.id, e)
            task.status = "error"
            self.tasks[task.id] = task
            return task
        task.workspace = workspace
        task.status = "running"
        self.tasks[task.id] = task
        logger.info("Created task %s", task.id, extra={"goal": task.goal[:80]})
        return task

    def run_agent(self, task: Task) -> None:
        try:
            result = self.agent_loop.run(task)
            task.status = result
        except Exception as e:
            logger.exception("Agent loop crashed for task %s: %s", task.id, e)
            task.status = "error"

    def list_tasks(self) -> list[Task]:
        return list(self.tasks.values())

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
        from app.tools.git_tools import git_commit # import here to avoid circular dep
        result = git_commit(container, "Approved by human")
        
        task.status = "completed"
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
        logs = self.get_logs(task_id)
        if logs and "steps" in logs:
            logs["status"] = "rejected"
            self.replay_store.save(task_id, logs)
            
        from app.logging.log_writer import write_last_run_log
        write_last_run_log(task, logs.get("steps", []))