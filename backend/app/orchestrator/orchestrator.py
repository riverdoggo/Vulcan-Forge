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