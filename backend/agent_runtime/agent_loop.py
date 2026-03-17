import logging
from typing import Any

from app.config.settings import MAX_AGENT_STEPS
from app.logging.replay_store import ReplayStore
from app.memory.memory_store import MemoryStore
from app.models.agent_decision import AgentDecision
from app.models.task import Task
from agent_runtime.decision_engine import DecisionEngine, DecisionEngineError
from agent_runtime.executor import Executor, ExecutorError

logger = logging.getLogger(__name__)


class AgentLoop:
    def run(self, task: Task) -> str:
        logger.info("AgentLoop started for task %s", task.id)
        memory = MemoryStore()
        memory.goal = task.goal
        decision_engine = DecisionEngine()
        executor = Executor()
        replay = ReplayStore()
        steps: list[dict[str, Any]] = []
        # HARDCODE bootstrap steps so LLM starts with real context.
        bootstrap_actions = [
            AgentDecision(tool="list_directory", input="/workspace", content=None, done=False),
            AgentDecision(tool="run_tests", input=None, content=None, done=False),
        ]

        step = 0
        for boot_decision in bootstrap_actions:
            result = executor.execute(boot_decision, task)
            step_data = {"step": step, "decision": boot_decision.model_dump(), "result": result}
            steps.append(step_data)
            replay.save(task.id, {"goal": task.goal, "steps": steps})
            memory.add_step(boot_decision.model_dump())
            memory.add_observation(result)
            logger.info("Bootstrap step %s | tool=%s", step, boot_decision.tool)
            step += 1

        done = False
        while not done and step < MAX_AGENT_STEPS:
            # Detect repeated tool+input and inject an error observation instead of blindly looping.
            if len(steps) >= 2:
                last = steps[-1].get("decision", {})
                second_last = steps[-2].get("decision", {})
                if (
                    isinstance(last, dict)
                    and isinstance(second_last, dict)
                    and last.get("tool") == second_last.get("tool")
                    and last.get("input") == second_last.get("input")
                ):
                    memory.add_observation(
                        {
                            "status": "error",
                            "stdout": "",
                            "stderr": f"You already called {last.get('tool')} with the same input. You MUST choose a different tool now.",
                        }
                    )

            try:
                decision = decision_engine.decide(memory)
            except DecisionEngineError as e:
                logger.exception("Decision engine failed for task %s: %s", task.id, e)
                # log the failure as a step so UI can see it
                steps.append(
                    {
                        "step": step,
                        "decision": {"error": "decision_engine_failed"},
                        "result": {"status": "error", "stderr": str(e), "exit_code": -1},
                    }
                )
                replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "error"})
                return "error"

            if decision.done:
                step_data = {"step": step, "decision": decision.model_dump(), "result": "agent_done"}
                steps.append(step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "completed"})
                logger.info("Agent decided task is complete", extra={"task_id": task.id, "step": step})
                return "completed"

            try:
                result = executor.execute(decision, task)
            except ExecutorError as e:
                logger.exception("Executor failed for task %s: %s", task.id, e)
                result = {"status": "error", "stderr": str(e), "exit_code": -1}

            step_data = {"step": step, "decision": decision.model_dump(), "result": result}
            steps.append(step_data)
            replay.save(task.id, {"goal": task.goal, "steps": steps})

            if decision.tool == "run_tests" and result.get("exit_code") == 0:
                commit_decision = AgentDecision(
                    tool="git_commit",
                    input="Fixed failing tests",
                    content=None,
                    done=False
                )
                commit_result = executor.execute(commit_decision, task)
                steps.append({"step": step, "decision": commit_decision.model_dump(), "result": commit_result})
                replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "completed"})
                logger.info("Hardcoded commit applied. Agent decided task is complete", extra={"task_id": task.id, "step": step})
                return "completed"

            logger.info(
                "Step %s | tool=%s | result_status=%s",
                step,
                getattr(decision, "tool", "?"),
                result.get("status", "?"),
                extra={"task_id": task.id},
            )
            memory.add_step(decision.model_dump())
            memory.add_observation(result)
            step += 1

        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "max_steps_reached"})
        return "max_steps_reached"