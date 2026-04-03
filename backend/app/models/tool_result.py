"""Typed result contract for tools (replaces ad-hoc dicts and magic exit codes)."""
from typing import Any

from pydantic import ConfigDict
from pydantic import BaseModel, Field

# Pytest exit code for "no tests collected" (documented, not magic)
PYTEST_EXIT_NO_TESTS_COLLECTED = 5


class ToolResult(BaseModel):
    """Standard result from a tool run."""

    # Tools may attach additional metadata (e.g. diff ratios, test counts). Preserve it.
    model_config = ConfigDict(extra="allow")

    status: str = Field(..., description="success | error | no_tests_found")
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    exit_code: int = Field(default=0)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_subprocess(cls, returncode: int, stdout: str, stderr: str) -> "ToolResult":
        status = "success" if returncode == 0 else "error"
        if returncode == PYTEST_EXIT_NO_TESTS_COLLECTED:
            status = "no_tests_found"
        return cls(status=status, stdout=stdout or "", stderr=stderr or "", exit_code=returncode)
