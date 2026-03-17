from typing import Any


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
        # Truncate history and observations to keep LLM prompt compact.
        truncated_obs: list[dict[str, Any]] = []
        for obs in self.observations[-3:]:
            if isinstance(obs, dict):
                truncated = {
                    "status": obs.get("status"),
                    "stdout": (obs.get("stdout") or "")[:500],
                    "stderr": (obs.get("stderr") or "")[:200],
                }
                truncated_obs.append(truncated)
        return {
            "goal": self.goal,
            "history": self.history[-3:],
            "observations": truncated_obs,
        }