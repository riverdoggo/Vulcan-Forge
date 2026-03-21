from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime
from typing import Literal

TaskStatus = Literal["pending", "running", "awaiting_approval", "completed", "rejected", "error", "max_steps_reached"]

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    status: TaskStatus = "pending"
    workspace: dict | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    diff_output: str = ""
    rejection_reason: str = ""
    review_iterations: int = 0
    reviewer_feedback: list[dict] = Field(default_factory=list)
    reviewer_status: str = ""
    escalation_reason: str = ""