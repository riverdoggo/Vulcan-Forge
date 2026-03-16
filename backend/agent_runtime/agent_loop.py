import logging
from typing import Any

from app.memory.memory_store import MemoryStore
from app.models.agent_decision import AgentDecision
from app.models.task import Task
from agent_runtime.decision_engine import DecisionEngine, DecisionEngineError
from agent_runtime.executor import Executor, ExecutorError
from app.config.settings import MAX_AGENT_STEPS

logger = logging.getLogger(__name__)


class AgentLoop:
    def run(self, task: Task) -> str:
        memory = MemoryStore()
        memory.goal = task.goal
        decision_engine = DecisionEngine()
        executor = Executor()
        step = 0
        done = False

        while not done and step < MAX_AGENT_STEPS:
            try:
                decision = decision_engine.decide(memory)
            except DecisionEngineError as e:
                logger.exception("Decision engine failed for task %s: %s", task.id, e)
                return "error"
            if decision.done:
                logger.info("Agent decided task is complete", extra={"task_id": task.id, "step": step})
                return "completed"
            try:
                result = executor.execute(decision, task)
            except ExecutorError as e:
                logger.exception("Executor failed for task %s: %s", task.id, e)
                memory.add_step(decision.model_dump())
                memory.add_observation({"status": "error", "stderr": str(e), "exit_code": -1})
                step += 1
                continue
            logger.info(
                "Step %s | tool=%s | result_status=%s",
                step,
                decision.tool,
                result.get("status", "?"),
                extra={"task_id": task.id},
            )
            memory.add_step(decision.model_dump())
            memory.add_observation(result)
            step += 1
        return "max_steps_reached"