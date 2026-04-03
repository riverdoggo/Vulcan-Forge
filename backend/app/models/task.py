from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal[
    "pending",
    "running",
    "awaiting_approval",
    "completed",
    "rejected",
    "error",
    "max_steps_reached",
    "killed",
]


def _utc_iso() -> str:
    return datetime.utcnow().isoformat()


class Task(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    status: TaskStatus = "pending"
    workspace: dict | None = None
    created_at: str = Field(default_factory=_utc_iso)
    repo_url: str = ""
    repo_type: str = ""  # "github", "local", or "default"
    diff_output: str = ""
    rejection_reason: str = ""
    error_message: str = ""  # Set when status is error (e.g. workspace/Docker failure)
    review_iterations: int = 0
    reviewer_feedback: list[dict] = Field(default_factory=list)
    reviewer_status: str = ""
    escalation_reason: str = ""
    transcript: list[dict] = Field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens_used: int = 0

    # --- Safety / planning state (optional; persisted in DB) ---
    last_plan: dict | None = None
    touched_files: list[str] = Field(default_factory=list)

    # Latest observed pytest counts (best-effort parsed from output).
    last_test_counts: dict | None = None  # {"passed": int|None, "failed": int|None, "errors": int|None, "skipped": int|None}

    # Baseline captured right before an accepted write_file, used for regression detection.
    regression_baseline: dict | None = None  # same shape as last_test_counts

    # Last successful state (green tests) so we can revert/compare progress.
    last_green: dict | None = None  # {"counts": {...}, "files": {path: content}, "timestamp": str}

    # Per-write preimage for quick revert on regression.
    pre_write_files: dict[str, str] = Field(default_factory=dict)

    # User-initiated stop: agent loop checks this each step and exits cleanly.
    kill_requested: bool = False

    # After repeated read_file loop guard: only write_file until a modify succeeds.
    read_loop_guard_active: bool = False

    # Paths (normalized /workspace/...) blocked from read_file after repeated reads.
    read_blocked_paths: list[str] = Field(default_factory=list)

    # Per-task read cache: path -> {content, mtime, size, last_read_step}
    file_read_cache: dict[str, Any] = Field(default_factory=dict)

    # Telemetry lines for logs/last_run.log (cache hits, guards, reviewer retries, etc.).
    runtime_log_lines: list[str] = Field(default_factory=list)

    # Injected once per coder phase when step budget is almost exhausted.
    step_budget_warning_shown: bool = False

    # Asyncio task running this agent (in-memory only; not persisted).
    agent_runtime_task: asyncio.Task | None = Field(default=None, exclude=True)
