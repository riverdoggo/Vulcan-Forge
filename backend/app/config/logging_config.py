"""Centralized logging configuration."""
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# Same project-root logs/ as log_writer (…/backend/app/config → parents[3] = repo root).
LOGS_DIR = Path(__file__).resolve().parents[3] / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


class VulcanJSONFormatter(logging.Formatter):
    """Emits one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "task_id"):
            obj["task_id"] = record.task_id
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """
    Call once at startup. Console (human-readable) + rotating JSON file.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)

    log_file = LOGS_DIR / "vulcan.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(VulcanJSONFormatter())
    root.addHandler(file_handler)

    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)


def get_task_logger(task_id: str) -> logging.LoggerAdapter:
    """Logger that injects task_id into log records (see VulcanJSONFormatter)."""
    base = logging.getLogger("vulcan.agent")
    return logging.LoggerAdapter(base, {"task_id": task_id})
