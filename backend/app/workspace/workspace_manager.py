import os
import subprocess
from app.config.settings import WORKSPACE_ROOT, SANDBOX_IMAGE

class WorkspaceManager:

    def create_workspace(self, task_id):
        path = f"{WORKSPACE_ROOT}/{task_id}"
        os.makedirs(path, exist_ok=True)

        container = f"agent_ws_{task_id}"

        subprocess.run([
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "-v",
            f"{os.getcwd()}/{path}:/workspace",
            SANDBOX_IMAGE,
            "sleep",
            "infinity"
        ])

        return {
            "path": path,
            "container": container
        }