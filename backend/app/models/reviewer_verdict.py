"""Structured output from the reviewer LLM."""

from typing import Any, Literal

from pydantic import BaseModel, Field

ReviewerVerdictType = Literal["approved", "needs_changes", "escalate_to_human"]


class ReviewerVerdict(BaseModel):
    verdict: ReviewerVerdictType
    reason: str = Field(default="", description="Justification for the verdict")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence in the verdict (0.0–1.0)",
    )
    suggestions: str = Field(default="", description="Actionable feedback when needs_changes")
    lesson: str = Field(
        default="",
        description="One-line memory lesson for approved/escalated outcomes",
    )

    @classmethod
    def from_llm_raw(cls, raw: dict[str, Any]) -> "ReviewerVerdict":
        v = raw.get("verdict")
        if v is None:
            raise ValueError("missing verdict")
        vs = str(v).strip().strip('"').strip("'")
        if "|" in vs:
            vs = vs.split("|")[0].strip()
        if vs not in ("approved", "needs_changes", "escalate_to_human"):
            raise ValueError(f"invalid verdict: {v!r}")
        vs_clean = vs

        conf_raw = raw.get("confidence")
        if conf_raw is None:
            raise ValueError("missing confidence")
        try:
            cf = float(conf_raw)
        except (TypeError, ValueError) as e:
            raise ValueError("invalid confidence") from e
        if not 0.0 <= cf <= 1.0:
            raise ValueError("confidence out of range")

        return cls(
            verdict=vs_clean,  # type: ignore[arg-type]
            reason="" if raw.get("reason") is None else str(raw["reason"]),
            confidence=cf,
            suggestions="" if raw.get("suggestions") is None else str(raw["suggestions"]),
            lesson="" if raw.get("lesson") is None else str(raw["lesson"]),
        )
