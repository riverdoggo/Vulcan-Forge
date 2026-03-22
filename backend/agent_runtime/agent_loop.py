import logging
from typing import Any

from app.config.settings import MAX_AGENT_STEPS
from app.logging.log_writer import write_last_run_log
from app.logging.replay_store import ReplayStore
from app.memory.memory_store import MemoryStore
from app.models.agent_decision import AgentDecision
from app.models.task import Task
from app.tools.filesystem_tools import read_file as read_file_tool
from app.tools.git_tools import git_commit
from app.tools.docker_terminal import run_in_container_argv
from agent_runtime.decision_engine import DecisionEngine, DecisionEngineError
from agent_runtime.executor import Executor, ExecutorError

logger = logging.getLogger(__name__)

MAX_REVIEW_CYCLES = 3


def _should_force_run_tests_after_double_write(steps: list[dict[str, Any]]) -> bool:
    """True if the last 3 steps contain two write_file calls on the same path with no run_tests between them."""
    w = steps[-3:]
    if len(w) < 2:
        return False
    n = len(w)
    for i in range(n):
        for j in range(i + 1, n):
            di, dj = w[i].get("decision", {}), w[j].get("decision", {})
            if not isinstance(di, dict) or not isinstance(dj, dict):
                continue
            if di.get("tool") != "write_file" or dj.get("tool") != "write_file":
                continue
            path = di.get("input")
            if not path or path != dj.get("input"):
                continue
            between = w[i + 1 : j]
            if any((b.get("decision") or {}).get("tool") == "run_tests" for b in between):
                continue
            return True
    return False


def _collect_staged_file_contents(container: str) -> dict[str, str]:
    """Read full contents of every path in the staged diff."""
    out = run_in_container_argv(container, ["git", "diff", "--cached", "--name-only"])
    if out.get("exit_code") != 0:
        return {}
    raw = (out.get("stdout") or "").strip()
    if not raw:
        return {}
    contents: dict[str, str] = {}
    for rel in raw.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = rel if rel.startswith("/") else f"/workspace/{rel}"
        r = read_file_tool(container, path)
        if r.get("exit_code") == 0:
            contents[path] = r.get("stdout") or ""
        else:
            contents[path] = f"<read failed: {r.get('stderr', '')}>"
    return contents


def _reviewer_decision_dict() -> dict[str, Any]:
    return {
        "tool": "reviewer_agent",
        "input": "diff + file contents + test results",
        "content": None,
        "done": False,
    }


class AgentLoop:
    def run(self, task: Task) -> str:
        logger.info("AgentLoop started for task %s", task.id)
        review_prefix = ""
        decision_engine = DecisionEngine()
        executor = Executor()
        replay = ReplayStore()
        steps: list[dict[str, Any]] = []

        while True:
            broke_for_review = False
            memory = MemoryStore()
            if review_prefix:
                memory.goal = f"{review_prefix}\n\n---\n\nOriginal goal:\n{task.goal}"
            else:
                memory.goal = task.goal

            bootstrap_actions = [
                AgentDecision(tool="list_directory", input="/workspace", content=None, done=False),
                AgentDecision(tool="run_tests", input=None, content=None, done=False),
            ]

            step = len(steps)
            for boot_decision in bootstrap_actions:
                result = executor.execute(boot_decision, task)
                step_data = {"step": step, "decision": boot_decision.model_dump(), "result": result}
                steps.append(step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps})
                memory.add_step(boot_decision.model_dump())
                memory.add_observation(result)
                logger.info("Bootstrap step %s | tool=%s", step, boot_decision.tool)
                step += 1

            # Per-phase step budget so review restarts get a full coder budget (global `step` is only for replay).
            phase_steps = 0
            while phase_steps < MAX_AGENT_STEPS:
                phase_steps += 1
                force_run_tests = _should_force_run_tests_after_double_write(steps)
                forced_write = False
                if len(steps) >= 3:
                    recent_decisions = [s.get("decision", {}) for s in steps[-3:] if isinstance(s.get("decision"), dict)]
                    if len(recent_decisions) >= 2:
                        last_d = recent_decisions[-1]
                        for past_d in recent_decisions[:-1]:
                            if last_d.get("tool") == past_d.get("tool") and last_d.get("input") == past_d.get("input"):
                                forced_write = True
                                break

                try:
                    if force_run_tests:
                        logger.warning(
                            "Repeated write_file on same path without run_tests in between; forcing run_tests.",
                            extra={"task_id": task.id},
                        )
                        decision = AgentDecision(tool="run_tests", input=None, content=None, done=False)
                    elif forced_write:
                        logger.warning("Agent is looping. Forcing write_file prompt.", extra={"task_id": task.id})
                        decision = decision_engine.decide(
                            memory,
                            override_prompt=(
                                "You are looping. You have already read the file and seen the bug. "
                                "You must call write_file now with the fixed content. Do not call any other tool. "
                                "Set done to false. After this write, the runtime will run tests and review; "
                                "you must not finish the task yourself."
                            ),
                        )
                    else:
                        decision = decision_engine.decide(memory)
                except DecisionEngineError as e:
                    logger.exception("Decision engine failed for task %s: %s", task.id, e)
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

                # The LLM often sets done=true after write_file (legacy prompt). That must not end the run:
                # completion is only run_tests → git_diff → reviewer → awaiting_approval (or max steps).
                if decision.done:
                    logger.info(
                        "Ignoring LLM done=true; task completes only via tests, diff, and reviewer.",
                        extra={"task_id": task.id, "step": step},
                    )
                    decision = decision.model_copy(update={"done": False})

                try:
                    result = executor.execute(decision, task)
                except ExecutorError as e:
                    logger.exception("Executor failed for task %s: %s", task.id, e)
                    result = {"status": "error", "stderr": str(e), "exit_code": -1}

                step_data = {"step": step, "decision": decision.model_dump(), "result": result}
                steps.append(step_data)
                replay.save(task.id, {"goal": task.goal, "steps": steps})

                if decision.tool == "run_tests" and result.get("exit_code") == 0:
                    diff_decision = AgentDecision(tool="git_diff", input=None, content=None, done=False)
                    diff_result = executor.execute(diff_decision, task)

                    task.diff_output = diff_result.get("stdout", "")

                    step += 1
                    steps.append({"step": step, "decision": diff_decision.model_dump(), "result": diff_result})
                    replay.save(task.id, {"goal": task.goal, "steps": steps})

                    container = task.workspace["container"] if task.workspace else None
                    file_contents: dict[str, str] = {}
                    if container:
                        file_contents = _collect_staged_file_contents(container)

                    try:
                        verdict = decision_engine.get_reviewer_decision(
                            task.diff_output,
                            file_contents,
                            result,
                        )
                    except DecisionEngineError as e:
                        logger.exception("Reviewer decision engine failed for task %s: %s", task.id, e)
                        task.reviewer_status = "escalate_to_human"
                        task.escalation_reason = f"Automated reviewer failed to return valid JSON: {e}"
                        task.reviewer_feedback.append(
                            {
                                "verdict": "escalate_to_human",
                                "reason": task.escalation_reason,
                                "suggestions": "",
                                "iteration": None,
                            }
                        )
                        step += 1
                        steps.append(
                            {
                                "step": step,
                                "decision": _reviewer_decision_dict(),
                                "result": {
                                    "status": "escalate_to_human",
                                    "stdout": "",
                                    "exit_code": -1,
                                    "verdict": "escalate_to_human",
                                    "reason": task.escalation_reason,
                                    "suggestions": "",
                                    "iteration": "—",
                                    "error": str(e),
                                },
                            }
                        )
                        task.status = "awaiting_approval"
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                        logger.warning(
                            "Reviewer LLM failed; escalating to human.",
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "awaiting_approval"

                    v = str(verdict.verdict)
                    if v == "needs_changes":
                        task.review_iterations += 1

                    iter_label = f"{task.review_iterations}/{MAX_REVIEW_CYCLES}" if v == "needs_changes" else "—"

                    task.reviewer_feedback.append(
                        {
                            "verdict": v,
                            "reason": verdict.reason,
                            "suggestions": verdict.suggestions,
                            "iteration": task.review_iterations if v == "needs_changes" else None,
                        }
                    )
                    task.reviewer_status = v

                    step += 1
                    steps.append(
                        {
                            "step": step,
                            "decision": _reviewer_decision_dict(),
                            "result": {
                                "status": v,
                                "stdout": "",
                                "exit_code": 0,
                                "verdict": v,
                                "reason": verdict.reason,
                                "suggestions": verdict.suggestions,
                                "iteration": iter_label,
                            },
                        }
                    )
                    replay.save(task.id, {"goal": task.goal, "steps": steps})

                    # Set task.status before any write_last_run_log so logs never show running/completed incorrectly.
                    if v == "approved":
                        commit_msg = "Auto-committed: reviewer approved"
                        container = task.workspace["container"] if task.workspace else None
                        if not container:
                            commit_result: dict[str, Any] = {
                                "status": "error",
                                "stderr": "no workspace container",
                                "exit_code": -1,
                                "stdout": "",
                            }
                        else:
                            commit_result = git_commit(container, commit_msg)

                        step += 1
                        steps.append(
                            {
                                "step": step,
                                "decision": {
                                    "tool": "git_commit",
                                    "input": commit_msg,
                                    "content": None,
                                    "done": False,
                                },
                                "result": commit_result,
                            }
                        )
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "completed"})

                        if commit_result.get("exit_code") != 0:
                            task.status = "error"
                            logger.error(
                                "Auto-commit after reviewer approval failed: %s",
                                commit_result,
                                extra={"task_id": task.id},
                            )
                            write_last_run_log(task, steps)
                            return "error"

                        task.status = "completed"
                        task.escalation_reason = ""
                        logger.info(
                            "Reviewer approved; changes committed automatically.",
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "completed"

                    if v == "escalate_to_human":
                        task.status = "awaiting_approval"
                        task.escalation_reason = verdict.reason or "Reviewer requested human review."
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                        logger.info(
                            "Reviewer escalated to human. Awaiting approval.",
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "awaiting_approval"

                    if v == "needs_changes" and task.review_iterations >= MAX_REVIEW_CYCLES:
                        task.status = "awaiting_approval"
                        task.escalation_reason = (
                            "The automated reviewer could not approve the changes after "
                            f"{MAX_REVIEW_CYCLES} review cycles. See reviewer_feedback for the full history."
                        )
                        replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
                        logger.warning(
                            "Reviewer needs_changes after %s cycles; escalating to human with warning.",
                            MAX_REVIEW_CYCLES,
                            extra={"task_id": task.id, "step": step},
                        )
                        write_last_run_log(task, steps)
                        return "awaiting_approval"

                    if v == "needs_changes":
                        review_prefix = (
                            f"Reviewer feedback (iteration {task.review_iterations}/{MAX_REVIEW_CYCLES}) — "
                            f"you must address this before finishing:\n"
                            f"{verdict.suggestions}\n\n"
                            f"Reviewer reason: {verdict.reason}"
                        )
                        logger.info(
                            "Reviewer requested changes; restarting coder from scratch.",
                            extra={"task_id": task.id, "iteration": task.review_iterations},
                        )
                        broke_for_review = True
                        break

                    logger.error("Unexpected reviewer verdict %r; escalating to human.", v)
                    task.status = "awaiting_approval"
                    task.escalation_reason = f"Unexpected reviewer verdict: {v!r}"
                    replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "awaiting_approval"})
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

            if broke_for_review:
                continue

            replay.save(task.id, {"goal": task.goal, "steps": steps, "status": "max_steps_reached"})
            task.status = "max_steps_reached"
            write_last_run_log(task, steps)
            return "max_steps_reached"
