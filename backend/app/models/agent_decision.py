"""Pydantic model for validated LLM agent decisions."""
from typing import Any

from pydantic import BaseModel, Field


class AgentDecision(BaseModel):
    """Schema for structured LLM decision output."""

    tool: str | None = Field(default=None, description="Tool name from the available tools list")
    input: str | None = Field(default=None, description="Argument for the tool")
    content: str | None = Field(default=None, description="Full file content (only for write_file)")
    done: bool = Field(default=False, description="Whether the agent is finished")

    @classmethod
    def from_llm_raw(cls, raw: dict[str, Any]) -> "AgentDecision":
        """Build from raw LLM JSON; normalizes types."""
        return cls(
            tool=None if raw.get("tool") is None else str(raw["tool"]).strip(),
            input=raw.get("input") if raw.get("input") is None else str(raw["input"]),
            content=raw.get("content") if raw.get("content") is None else str(raw["content"]),
            done=bool(raw.get("done", False)),
        )
