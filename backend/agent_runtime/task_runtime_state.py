"""In-memory per-task state (not persisted). Used for scope lock, regression blocks, messages."""

from __future__ import annotations

from typing import Any

_RUNTIME: dict[str, dict[str, Any]] = {}


def runtime_state(task_id: str) -> dict[str, Any]:
    return _RUNTIME.setdefault(task_id, {})


def clear_runtime_state(task_id: str) -> None:
    _RUNTIME.pop(task_id, None)
