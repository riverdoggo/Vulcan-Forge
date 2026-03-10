import json
import os

REPLAY_DIR = "replays"

class ReplayStore:

    def save(self, task_id, data):

        os.makedirs(REPLAY_DIR, exist_ok=True)

        path = f"{REPLAY_DIR}/{task_id}.json"

        with open(path, "w") as f:
            json.dump(data, f, indent=2)