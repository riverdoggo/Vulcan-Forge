import json
import sqlite3
from pathlib import Path

from app.models.task import Task

DB_PATH = Path(__file__).resolve().parents[2] / "orchestrator.db"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT,
                goal TEXT,
                repo_url TEXT,
                created_at TEXT,
                data TEXT,
                transcript TEXT
            )
            """
        )
        cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
        col_names = {row["name"] for row in cols}
        if "transcript" not in col_names:
            conn.execute("ALTER TABLE tasks ADD COLUMN transcript TEXT")
        conn.commit()


def save_task(task: Task) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks (id, status, goal, repo_url, created_at, data, transcript)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.status,
                task.goal,
                getattr(task, "repo_url", "") or "",
                getattr(task, "created_at", "") or "",
                task.model_dump_json(),
                json.dumps(getattr(task, "transcript", []) or []),
            ),
        )
        conn.commit()


def load_all_tasks():
    with get_conn() as conn:
        rows = conn.execute("SELECT data FROM tasks ORDER BY created_at DESC").fetchall()
    return [json.loads(row["data"]) for row in rows]


def load_task_transcript(task_id: str) -> list[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT transcript, data FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if not row:
        raise KeyError(task_id)
    if row["transcript"]:
        try:
            val = json.loads(row["transcript"])
            if isinstance(val, list):
                return val
        except Exception:
            pass
    try:
        data = json.loads(row["data"] or "{}")
        t = data.get("transcript")
        if isinstance(t, list):
            return t
    except Exception:
        pass
    return []
