import logging
from typing import Any

from app.config.settings import MAX_AGENT_STEPS
from app.logging.replay_store import ReplayStore
from app.memory.memory_store import MemoryStore
from app.models.agent_decision import AgentDecision
from app.models.task import Task
from agent_runtime.decision_engine import DecisionEngine, DecisionEngineError
from agent_runtime.executor import Executor, ExecutorError
from app.logging.log_writer import write_last_run_log

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
            # Detect repeated tool+input within the last 3 steps and force write_file
            forced_write = False
            if len(steps) >= 3:
                recent_decisions = [s.get("decision", {}) for s in steps[-3:] if isinstance(s.get("decision"), dict)]
                if len(recent_decisions) >= 2:
                    last_d = recent_decisions[-1]
                    # check if the last decision's tool/input appears earlier in the 3-step window
                    for past_d in recent_decisions[:-1]:
                        if last_d.get("tool") == past_d.get("tool") and last_d.get("input") == past_d.get("input"):
                            forced_write = True
                            break
            
            try:
                if forced_write:
                    logger.warning("Agent is looping. Forcing write_file prompt.", extra={"task_id": task.id})
                    decision = decision_engine.decide(memory, override_prompt="You are looping. You have already read the file and seen the bug. You must call write_file now with the fixed content. Do not call any other tool.")
                else:
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
                task.status = "error"
                write_last_run_log(task, steps)
                return "error"

            if decision.done:
                step_data = {"step": step, "decision": decision.model_dump(), "result": "success"}
                steps.append(step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "completed"})
                logger.info("Agent decided task is complete", extra={"task_id": task.id, "step": step})
                task.status = "completed"
                write_last_run_log(task, steps)
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
                diff_decision = AgentDecision(
                    tool="git_diff",
                    input=None,
                    content=None,
                    done=False
                )
                diff_result = executor.execute(diff_decision, task)
                
                # capture diff and update task
                task.diff_output = diff_result.get("stdout", "")
                task.status = "awaiting_approval"
                
                # record diff action and final paused state
                step += 1
                steps.append({"step": step, "decision": diff_decision.model_dump(), "result": diff_result})
                replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                
                logger.info("Tests passed. Captured diff and pausing for approval. Agent loop stopping.", extra={"task_id": task.id, "step": step})
                write_last_run_log(task, steps)
                return "awaiting_approval"

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
        task.status = "max_steps_reached"
        write_last_run_log(task, steps)
        return "max_steps_reached"