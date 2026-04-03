import logging
from typing import Any

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self.goal: str | None = None
        self.history: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []

    def add_step(self, step: dict[str, Any]) -> None:
        self.history.append(step)

    def add_observation(self, obs: dict[str, Any]) -> None:
        self.observations.append(obs)

    def build_context(self) -> dict[str, Any]:
        obs_for_prompt: list[dict[str, Any]] = []
        for obs in self.observations[-3:]:
            if not isinstance(obs, dict):
                continue
            merged = dict(obs)
            fs = merged.get("failure_summary") if isinstance(merged.get("failure_summary"), str) else ""
            stdout = merged.get("stdout") or ""
            if fs:
                merged["failure_summary"] = fs
                stdout = f"{fs}\n\n--- raw test output ---\n{stdout}"
            merged["stdout"] = stdout
            stderr = merged.get("stderr") or ""
            merged["stderr"] = stderr
            obs_for_prompt.append(merged)

        context = {
            "goal": self.goal,
            "history": self.history[-3:],
            "observations": obs_for_prompt,
        }
        logger.debug("MEMORY CONTEXT (sizes): goal=%s history=%s obs=%s", self.goal, len(context["history"]), len(context["observations"]))
        return context
