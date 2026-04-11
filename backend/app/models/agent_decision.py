"""Pydantic model for validated LLM agent decisions."""
import json
from typing import Any

from pydantic import BaseModel, Field


class AgentDecision(BaseModel):
    """Schema for structured LLM decision output."""

    reasoning: str = Field(
        ...,
        description="Short explanation of the decision (debugging/logging only; not used for execution)",
    )
    tool: str | None = Field(default=None, description="Tool name from the available tools list")
    input: str | None = Field(default=None, description="Argument for the tool")
    content: str | None = Field(
        default=None,
        description="Primary: full file body for write_file. Fallback: unified diff text only if tool is apply_patch (avoid unless necessary).",
    )
    done: bool = Field(default=False, description="Whether the agent is finished")

    @classmethod
    def from_llm_raw(cls, raw: dict[str, Any]) -> "AgentDecision":
        """Build from raw LLM JSON; normalizes types. Caller must ensure required keys exist."""
        inp = raw.get("input")
        if isinstance(inp, (dict, list)):
            # Preserve structured payloads as actual JSON when possible.
            inp = json.dumps(inp)
        reasoning = raw.get("reasoning")
        if reasoning is None:
            raise ValueError("missing required field: reasoning")
        reasoning_str = str(reasoning).strip()
        if not reasoning_str:
            raise ValueError("reasoning must be a non-empty string")

        return cls(
            reasoning=reasoning_str,
            tool=None if raw.get("tool") is None else str(raw["tool"]).strip(),
            input=inp if inp is None else str(inp),
            content=raw.get("content") if raw.get("content") is None else str(raw["content"]),
            done=bool(raw.get("done", False)),
        )
