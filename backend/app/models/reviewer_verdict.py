"""Structured output from the reviewer LLM."""

from typing import Any, Literal

from pydantic import BaseModel, Field

ReviewerVerdictType = Literal["approved", "needs_changes", "escalate_to_human"]


class ReviewerVerdict(BaseModel):
    verdict: ReviewerVerdictType
    reason: str = Field(default="", description="Justification for the verdict")
    suggestions: str = Field(default="", description="Actionable feedback for the coder when needs_changes")

    @classmethod
    def from_llm_raw(cls, raw: dict[str, Any]) -> "ReviewerVerdict":
        v = raw.get("verdict")
        if v is None:
            raise ValueError("missing verdict")
        vs = str(v).strip()
        if vs not in ("approved", "needs_changes", "escalate_to_human"):
            raise ValueError(f"invalid verdict: {vs!r}")
        return cls(
            verdict=vs,  # type: ignore[arg-type]
            reason="" if raw.get("reason") is None else str(raw["reason"]),
            suggestions="" if raw.get("suggestions") is None else str(raw["suggestions"]),
        )
