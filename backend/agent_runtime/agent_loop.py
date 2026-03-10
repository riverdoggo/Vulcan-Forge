from app.memory.memory_store import MemoryStore
from agent_runtime.decision_engine import DecisionEngine
from agent_runtime.executor import Executor
from app.config.settings import MAX_AGENT_STEPS

class AgentLoop:

    def run(self, task):

        memory = MemoryStore()
        memory.goal = task.goal

        decision_engine = DecisionEngine()
        executor = Executor()

        step = 0
        done = False

        while not done and step < MAX_AGENT_STEPS:

            decision = decision_engine.decide(memory)

            if decision["done"]:
                print("Agent decided task is complete")
                return "completed"

            result = executor.execute(decision, task)

            print(f"Step {step} | Decision: {decision} | Result: {result}")

            memory.add_step(decision)
            memory.add_observation(result)

            step += 1

        return "max_steps_reached"