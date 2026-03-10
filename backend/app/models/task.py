from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    status: str = "pending"
    workspace: dict | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)