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
        return {
            "goal": self.goal,
            "history": self.history[-5:],
            "observations": self.observations[-5:],
        }