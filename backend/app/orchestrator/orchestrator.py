from app.workspace.workspace_manager import WorkspaceManager
from agent_runtime.agent_loop import AgentLoop

class Orchestrator:

    def __init__(self):
        self.tasks = {}
        self.workspace_manager = WorkspaceManager()
        self.agent_loop = AgentLoop()

    def create_task(self, task):
        workspace = self.workspace_manager.create_workspace(task.id)
        task.workspace = workspace
        task.status = "running"
        self.tasks[task.id] = task
        return task

    def run_agent(self, task):
        result = self.agent_loop.run(task)
        task.status = result

    def list_tasks(self):
        return list(self.tasks.values())