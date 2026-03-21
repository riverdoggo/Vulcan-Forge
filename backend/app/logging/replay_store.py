import json
import logging
import os
import re
from pathlib import Path

from app.config.settings import REPLAY_MAX_FILES

logger = logging.getLogger(__name__)
REPLAY_DIR = "replays"
_SAFE_ID = re.compile(r"^[\w.-]+$")


def _enforce_replay_retention(replay_dir: str, max_files: int) -> None:
    """Remove oldest replay files if over max_files."""
    try:
        files = list(Path(replay_dir).glob("*.json"))
        if len(files) <= max_files:
            return
        by_mtime = sorted(files, key=lambda p: p.stat().st_mtime)
        for p in by_mtime[: len(files) - max_files]:
            try:
                p.unlink()
                logger.debug("Removed old replay %s", p.name)
            except OSError as e:
                logger.warning("Failed to remove replay %s: %s", p, e)
    except OSError as e:
        logger.warning("Replay retention check failed: %s", e)


class ReplayStore:
    """Persists task replay JSON under `replays/`. `data["steps"]` includes coder tools and `reviewer_agent` steps."""

    def save(self, task_id: str, data: dict) -> None:
        if not _SAFE_ID.match(str(task_id)):
            logger.warning("Skipping replay save for invalid task_id: %s", task_id[:50])
            return
        os.makedirs(REPLAY_DIR, exist_ok=True)
        path = os.path.join(REPLAY_DIR, f"{task_id}.json")
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.exception("Failed to save replay %s: %s", task_id, e)
            return
        _enforce_replay_retention(REPLAY_DIR, REPLAY_MAX_FILES)

    def get(self, task_id: str) -> dict | None:
        if not _SAFE_ID.match(str(task_id)):
            return None
        path = os.path.join(REPLAY_DIR, f"{task_id}.json")
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("Failed to read replay %s: %s", task_id, e)
            return None