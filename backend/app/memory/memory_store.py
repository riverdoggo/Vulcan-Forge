class MemoryStore:

    def __init__(self):
        self.goal = None
        self.history = []
        self.observations = []

    def add_step(self, step):
        self.history.append(step)

    def add_observation(self, obs):
        self.observations.append(obs)

    def build_context(self):
        return {
            "goal": self.goal,
            "history": self.history[-5:],
            "observations": self.observations[-5:]
        }