import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.reviewer_agent import REVIEWER_SYSTEM_PROMPT
from app.logging.log_writer import append_runtime_log
from app.llm.ollama_client import OllamaError, query_llm, query_llm_async
from app.models.agent_decision import AgentDecision
from app.models.reviewer_verdict import ReviewerVerdict
from app.tools.tool_registry import TOOLS

logger = logging.getLogger(__name__)


class DecisionEngineError(Exception):
    """Raised when the LLM does not return valid structured JSON (reviewer path)."""

    pass


# Initial attempt + up to 2 retries = 3 LLM calls max for coder decisions.
MAX_CODER_DECISION_RETRIES = 2

# Per-LLM-call wall-clock cap (inner httpx may use longer read timeout; outer wait_for wins first).
LLM_CALL_TIMEOUT_SEC = 30.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_agent_memory_markdown() -> str:
    memory_path = _repo_root() / "MEMORY.md"
    if not memory_path.exists():
        return ""
    try:
        content = memory_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("Failed reading MEMORY.md: %s", e)
        return ""
    if not content:
        return ""
    return f"## Agent Memory\n{content}\n\n"


def _abort_if_task_killed(task: Any) -> None:
    if task is not None and getattr(task, "kill_requested", False):
        raise asyncio.CancelledError("task kill_requested")

_CORRECTION_PROMPT = """
Your previous response was invalid. You must return valid JSON in the following format:

{
"reasoning": "...",
"tool": "...",
"input": "...",
"done": false
}

Return JSON only.
""".strip()


@dataclass
class CoderDecisionOutcome:
    """Result of coder LLM decision with retry metadata for logging."""

    decision: AgentDecision | None
    attempt_count: int
    retry_count: int
    raw_responses: list[str] = field(default_factory=list)
    last_error: str | None = None
    last_raw_response: str | None = None


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Try to find a JSON object in the response (between { and })."""
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _required_keys_present(obj: dict[str, Any]) -> bool:
    return all(k in obj for k in ("reasoning", "tool", "input", "done"))


def _validate_tool_registered(raw: dict[str, Any]) -> str | None:
    """Return error message if invalid; None if OK."""
    done = bool(raw.get("done", False))
    tool = raw.get("tool")
    if done:
        # Completion path: executor accepts missing/unknown tool when done=True.
        return None
    if tool is None or (isinstance(tool, str) and not str(tool).strip()):
        return "tool is required and must be non-empty when done is false"
    name = str(tool).strip()
    if name not in TOOLS:
        return f"unknown tool {name!r}; must be one of: {', '.join(sorted(TOOLS))}"
    return None


def _validate_and_build_decision(obj: dict[str, Any]) -> tuple[AgentDecision | None, str | None]:
    """Validate JSON shape and registry; return (decision, error_message)."""
    if not _required_keys_present(obj):
        return None, "JSON must include keys: reasoning, tool, input, done"
    err = _validate_tool_registered(obj)
    if err:
        return None, err
    try:
        return AgentDecision.from_llm_raw(obj), None
    except Exception as e:
        return None, str(e)


def query_llm_structured_coder(prompt: str) -> CoderDecisionOutcome:
    """
    Get validated AgentDecision from LLM with up to 2 retries on parse/validation failure.
    Does not raise: returns decision=None if all attempts fail (task stays alive).
    """
    base_prompt = prompt
    raw_responses: list[str] = []
    last_error: str | None = None
    last_raw: str | None = None

    total_attempts = MAX_CODER_DECISION_RETRIES + 1
    current_prompt = base_prompt

    for attempt in range(total_attempts):
        try:
            raw = query_llm(current_prompt)
            last_raw = raw
            raw_responses.append(raw)
            logger.info("RAW LLM RESPONSE (coder attempt %s): %s", attempt + 1, raw)
        except OllamaError as e:
            last_error = f"LLM API error: {e}"
            logger.warning("LLM API error (coder attempt %s): %s", attempt + 1, e)
            if attempt < total_attempts - 1:
                current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT
            continue

        obj = _extract_json_from_text(raw)
        if obj is None:
            last_error = "No valid JSON in response"
            logger.warning(
                "Coder LLM JSON parse failed (attempt %s). Raw response: %s",
                attempt + 1,
                raw,
            )
            if attempt < total_attempts - 1:
                current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT
            continue

        decision, verr = _validate_and_build_decision(obj)
        if decision is not None:
            logger.info(
                "Coder decision OK | reasoning=%s | tool=%s | attempt=%s",
                decision.reasoning[:200] + ("..." if len(decision.reasoning) > 200 else ""),
                decision.tool,
                attempt + 1,
            )
            return CoderDecisionOutcome(
                decision=decision,
                attempt_count=attempt + 1,
                retry_count=attempt,
                raw_responses=raw_responses,
                last_error=None,
                last_raw_response=None,
            )

        last_error = verr or "validation failed"
        logger.warning(
            "Coder decision invalid (attempt %s): %s. Raw JSON object: %s",
            attempt + 1,
            last_error,
            obj,
        )
        if attempt < total_attempts - 1:
            current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT

    logger.error(
        "Coder LLM failed after %s attempts. Last error: %s. Last raw: %s",
        total_attempts,
        last_error,
        last_raw,
    )
    return CoderDecisionOutcome(
        decision=None,
        attempt_count=total_attempts,
        retry_count=MAX_CODER_DECISION_RETRIES,
        raw_responses=raw_responses,
        last_error=last_error,
        last_raw_response=last_raw,
    )


async def query_llm_structured_coder_async(task: Any, prompt: str) -> CoderDecisionOutcome:
    """Async coder LLM with kill checks, per-call timeout, and cancellation."""
    base_prompt = prompt
    raw_responses: list[str] = []
    last_error: str | None = None
    last_raw: str | None = None

    total_attempts = MAX_CODER_DECISION_RETRIES + 1
    current_prompt = base_prompt

    for attempt in range(total_attempts):
        _abort_if_task_killed(task)
        try:
            raw = await asyncio.wait_for(
                query_llm_async(current_prompt, task=task),
                timeout=LLM_CALL_TIMEOUT_SEC,
            )
            last_raw = raw
            raw_responses.append(raw)
            logger.info("RAW LLM RESPONSE (coder attempt %s): %s", attempt + 1, raw)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            last_error = f"LLM call timed out after {LLM_CALL_TIMEOUT_SEC}s"
            logger.warning("Coder LLM timeout (attempt %s)", attempt + 1)
            if attempt < total_attempts - 1:
                current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT
            continue
        except OllamaError as e:
            last_error = f"LLM API error: {e}"
            logger.warning("LLM API error (coder attempt %s): %s", attempt + 1, e)
            if attempt < total_attempts - 1:
                current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT
            continue

        obj = _extract_json_from_text(raw)
        if obj is None:
            last_error = "No valid JSON in response"
            logger.warning(
                "Coder LLM JSON parse failed (attempt %s). Raw response: %s",
                attempt + 1,
                raw,
            )
            if attempt < total_attempts - 1:
                current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT
            continue

        decision, verr = _validate_and_build_decision(obj)
        if decision is not None:
            logger.info(
                "Coder decision OK | reasoning=%s | tool=%s | attempt=%s",
                decision.reasoning[:200] + ("..." if len(decision.reasoning) > 200 else ""),
                decision.tool,
                attempt + 1,
            )
            return CoderDecisionOutcome(
                decision=decision,
                attempt_count=attempt + 1,
                retry_count=attempt,
                raw_responses=raw_responses,
                last_error=None,
                last_raw_response=None,
            )

        last_error = verr or "validation failed"
        logger.warning(
            "Coder decision invalid (attempt %s): %s. Raw JSON object: %s",
            attempt + 1,
            last_error,
            obj,
        )
        if attempt < total_attempts - 1:
            current_prompt = base_prompt + "\n\n" + _CORRECTION_PROMPT

    logger.error(
        "Coder LLM failed after %s attempts. Last error: %s. Last raw: %s",
        total_attempts,
        last_error,
        last_raw,
    )
    return CoderDecisionOutcome(
        decision=None,
        attempt_count=total_attempts,
        retry_count=MAX_CODER_DECISION_RETRIES,
        raw_responses=raw_responses,
        last_error=last_error,
        last_raw_response=last_raw,
    )


_REVIEWER_JSON_CORRECTION = """
Your previous response was invalid.
Return ONLY JSON in the required schema.
""".strip()


def _reviewer_fallback_verdict() -> ReviewerVerdict:
    return ReviewerVerdict(
        verdict="escalate_to_human",
        reason="reviewer returned invalid output",
        confidence=0.0,
        suggestions="",
        lesson="",
    )


def query_llm_reviewer_verdict(user_payload: str, task: Any | None = None) -> ReviewerVerdict:
    """Parse reviewer JSON; one retry with correction, then deterministic fallback."""
    base = f"{REVIEWER_SYSTEM_PROMPT}\n\n---\n\n{user_payload}"
    prompt = base
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw = query_llm(prompt)
            logger.info("RAW REVIEWER LLM RESPONSE: %s", raw)
        except OllamaError as e:
            last_error = e
            logger.warning("Reviewer LLM API error (attempt %s): %s", attempt + 1, e)
            if attempt == 0:
                prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
        obj = _extract_json_from_text(raw)
        if obj is None:
            last_error = ValueError("No valid JSON in response")
            if task is not None:
                append_runtime_log(task, "reviewer_retry: invalid_json")
            prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
        try:
            return ReviewerVerdict.from_llm_raw(obj)
        except Exception as e:
            last_error = e
            if task is not None:
                append_runtime_log(task, "reviewer_retry: invalid_json")
            prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
    logger.error("Reviewer LLM failed after retries: %s", last_error)
    if task is not None:
        append_runtime_log(task, "reviewer_fallback: invalid_output")
    return _reviewer_fallback_verdict()


async def query_llm_reviewer_verdict_async(task: Any, user_payload: str) -> ReviewerVerdict:
    """Async reviewer: one retry with correction, then fallback (never raises for bad JSON)."""
    base = f"{REVIEWER_SYSTEM_PROMPT}\n\n---\n\n{user_payload}"
    prompt = base
    last_error: Exception | None = None
    for attempt in range(2):
        _abort_if_task_killed(task)
        try:
            raw = await asyncio.wait_for(
                query_llm_async(prompt, task=task),
                timeout=LLM_CALL_TIMEOUT_SEC,
            )
            logger.info("RAW REVIEWER LLM RESPONSE: %s", raw)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            last_error = asyncio.TimeoutError("reviewer LLM timed out")
            logger.warning("Reviewer LLM timeout (attempt %s)", attempt + 1)
            if task is not None:
                append_runtime_log(task, "reviewer_retry: invalid_json")
            prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
        except OllamaError as e:
            last_error = e
            logger.warning("Reviewer LLM API error (attempt %s): %s", attempt + 1, e)
            if attempt == 0:
                prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
        obj = _extract_json_from_text(raw)
        if obj is None:
            last_error = ValueError("No valid JSON in response")
            if task is not None:
                append_runtime_log(task, "reviewer_retry: invalid_json")
            prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
        try:
            return ReviewerVerdict.from_llm_raw(obj)
        except Exception as e:
            last_error = e
            if task is not None:
                append_runtime_log(task, "reviewer_retry: invalid_json")
            prompt = base + "\n\n" + _REVIEWER_JSON_CORRECTION
            continue
    logger.error("Reviewer LLM failed after retries: %s", last_error)
    append_runtime_log(task, "reviewer_fallback: invalid_output")
    return _reviewer_fallback_verdict()


class DecisionEngine:
    def decide(self, memory: Any, override_prompt: str | None = None) -> CoderDecisionOutcome:
        context = memory.build_context()
        memory_block = _read_agent_memory_markdown()

        if override_prompt:
            prompt_modifier = override_prompt
        else:
            prompt_modifier = """
You MUST use one of these exact tool names:
- list_directory
- read_file
- apply_patch
- write_file
- run_tests
- run_command
- git_diff
- git_commit

Rules:
- You are modifying an existing codebase.
- Do NOT rewrite entire files. Only change the minimal parts required.
- Preserve all existing logic and structure.
- Add missing methods instead of removing features.
- If a fix makes tests worse, revert and try another approach.
- Do NOT repeat an action you already took with the same input
- When fixing code, prefer small localized edits instead of rewriting entire files.
- Prefer apply_patch over write_file for small, localized edits (unified diff in input or content).
- Use write_file when you must replace full file content or apply_patch is impractical.
- For write_file you MUST provide the complete file content in the content field
- For apply_patch put the unified diff in input OR content (both are accepted)
- After EVERY write_file or apply_patch call you MUST immediately call run_tests next — no exceptions
- Never call write_file twice in a row on the same file
- When run_tests passes, the runtime automatically runs git_diff and the reviewer — you do not call git_diff yourself
- Always set done to false; the runtime ignores done=true and completion goes through automated review and human approval
- Do NOT call git_commit — committing is handled by the human approval system
- Do NOT list directory again unless you genuinely need new information
- If run_tests fails with ModuleNotFoundError, use run_command to install the missing package with: pip install <package_name>
- If the repo has a pyproject.toml or setup.py, run: pip install -e .
- Only use run_command for pip install or similar setup commands, not for arbitrary system changes
- NEVER delete existing functions, methods, or tests to make tests pass
- NEVER remove test cases from test files
- NEVER simplify code by removing functionality
- The goal is to FIX bugs in the existing code, not rewrite or delete it
- If a test is failing, fix the SOURCE CODE that the test is testing, not the test itself
- All original functions and methods must remain present in your final solution

Process requirement (STRICT):
- You MUST follow: read_file → apply_patch or write_file → run_tests.
- If you see AttributeError in test output, prioritize ADDING the missing method/attribute with minimal changes.

Think step: Always set "reasoning" to a short explanation of why you chose this tool and arguments (for logs only).
"""

        prompt = f"""
{memory_block}You are a coding agent working inside a Docker container at /workspace.

Goal:
{context["goal"]}

Recent actions:
{context["history"]}

Recent observations:
{context["observations"]}

{prompt_modifier}

Return ONLY this JSON, no other text:

{{
  "reasoning": "why this action advances the goal",
  "tool": "list_directory",
  "input": null,
  "content": null,
  "done": false
}}
"""
        logger.info("PROMPT SENT TO LLM:\n%s", prompt)
        return query_llm_structured_coder(prompt)

    async def decide_async(
        self, task: Any, memory: Any, override_prompt: str | None = None
    ) -> CoderDecisionOutcome:
        _abort_if_task_killed(task)
        context = memory.build_context()
        memory_block = _read_agent_memory_markdown()

        if override_prompt:
            prompt_modifier = override_prompt
        else:
            prompt_modifier = """
You MUST use one of these exact tool names:
- list_directory
- read_file
- apply_patch
- write_file
- run_tests
- run_command
- git_diff
- git_commit

Rules:
- You are modifying an existing codebase.
- Do NOT rewrite entire files. Only change the minimal parts required.
- Preserve all existing logic and structure.
- Add missing methods instead of removing features.
- If a fix makes tests worse, revert and try another approach.
- Do NOT repeat an action you already took with the same input
- When fixing code, prefer small localized edits instead of rewriting entire files.
- Prefer apply_patch over write_file for small, localized edits (unified diff in input or content).
- Use write_file when you must replace full file content or apply_patch is impractical.
- For write_file you MUST provide the complete file content in the content field
- For apply_patch put the unified diff in input OR content (both are accepted)
- After EVERY write_file or apply_patch call you MUST immediately call run_tests next — no exceptions
- Never call write_file twice in a row on the same file
- When run_tests passes, the runtime automatically runs git_diff and the reviewer — you do not call git_diff yourself
- Always set done to false; the runtime ignores done=true and completion goes through automated review and human approval
- Do NOT call git_commit — committing is handled by the human approval system
- Do NOT list directory again unless you genuinely need new information
- If run_tests fails with ModuleNotFoundError, use run_command to install the missing package with: pip install <package_name>
- If the repo has a pyproject.toml or setup.py, run: pip install -e .
- Only use run_command for pip install or similar setup commands, not for arbitrary system changes
- NEVER delete existing functions, methods, or tests to make tests pass
- NEVER remove test cases from test files
- NEVER simplify code by removing functionality
- The goal is to FIX bugs in the existing code, not rewrite or delete it
- If a test is failing, fix the SOURCE CODE that the test is testing, not the test itself
- All original functions and methods must remain present in your final solution

Process requirement (STRICT):
- You MUST follow: read_file → apply_patch or write_file → run_tests.
- If you see AttributeError in test output, prioritize ADDING the missing method/attribute with minimal changes.

Think step: Always set "reasoning" to a short explanation of why you chose this tool and arguments (for logs only).
"""

        prompt = f"""
{memory_block}You are a coding agent working inside a Docker container at /workspace.

Goal:
{context["goal"]}

Recent actions:
{context["history"]}

Recent observations:
{context["observations"]}

{prompt_modifier}

Return ONLY this JSON, no other text:

{{
  "reasoning": "why this action advances the goal",
  "tool": "list_directory",
  "input": null,
  "content": null,
  "done": false
}}
"""
        logger.info("PROMPT SENT TO LLM:\n%s", prompt)
        return await query_llm_structured_coder_async(task, prompt)

    async def generate_reviewer_lesson_async(
        self,
        task: Any,
        goal: str,
        verdict: str,
        review_iterations: int,
        reason: str,
        suggestions: str,
    ) -> str:
        _abort_if_task_killed(task)
        prompt = f"""{REVIEWER_SYSTEM_PROMPT}

Generate one concise memory lesson for future tasks.
Return ONLY valid JSON with this shape:
{{
  "lesson": "<single line, no bullet prefix, no date>"
}}

Constraints:
- Keep it to one line and practical.
- Mention the task goal briefly and what to remember next time.
- Include reviewer cycle context when relevant.
- No markdown formatting, no extra keys.

Input:
goal: {goal}
verdict: {verdict}
review_iterations: {review_iterations}
reason: {reason}
suggestions: {suggestions}
"""
        try:
            raw = await asyncio.wait_for(
                query_llm_async(prompt, task=task),
                timeout=LLM_CALL_TIMEOUT_SEC,
            )
            obj = _extract_json_from_text(raw)
            if not isinstance(obj, dict):
                return ""
            lesson = obj.get("lesson")
            if lesson is None:
                return ""
            return str(lesson).strip().replace("\n", " ")
        except Exception as e:
            logger.warning("Failed to generate reviewer lesson: %s", e)
            return ""

    def get_reviewer_decision(
        self,
        diff: str,
        file_contents: dict[str, str],
        test_results: dict[str, Any],
    ) -> ReviewerVerdict:
        """Call Groq with the reviewer system prompt; returns structured verdict JSON."""
        MAX_DIFF_CHARS = 1000
        MAX_FILE_CHARS = 1500
        MAX_TEST_CHARS = 500

        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

        files_block_parts: list[str] = []
        for path, body in file_contents.items():
            if len(body) > MAX_FILE_CHARS:
                body = body[:MAX_FILE_CHARS] + "\n... [truncated for brevity]"
            files_block_parts.append(f"### {path}\n```\n{body}\n```")
        files_block = "\n\n".join(files_block_parts) if files_block_parts else "(no file contents collected)"

        stdout = test_results.get("stdout") or ""
        stderr = test_results.get("stderr") or ""
        fs = test_results.get("failure_summary") or ""
        if fs:
            stdout = f"{fs}\n\n--- raw pytest output ---\n{stdout}"
        if len(stdout) > MAX_TEST_CHARS:
            stdout = stdout[:MAX_TEST_CHARS] + "\n... [truncated]"
        if len(stderr) > MAX_TEST_CHARS:
            stderr = stderr[:MAX_TEST_CHARS] + "\n... [truncated]"
        exit_code = test_results.get("exit_code")
        user_payload = f"""## Git diff (staged)

```
{diff}
```

## Full file contents (staged changes)

{files_block}

## Test results

exit_code: {exit_code}
stdout:
```
{stdout}
```
stderr:
```
{stderr}
```

Return ONLY valid JSON in the schema from the system prompt (verdict, reason, confidence 0.0–1.0; optional suggestions, lesson). Nothing else."""

        return query_llm_reviewer_verdict(user_payload)

    async def get_reviewer_decision_async(
        self,
        task: Any,
        diff: str,
        file_contents: dict[str, str],
        test_results: dict[str, Any],
    ) -> ReviewerVerdict:
        _abort_if_task_killed(task)
        MAX_DIFF_CHARS = 1000
        MAX_FILE_CHARS = 1500
        MAX_TEST_CHARS = 500

        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

        files_block_parts: list[str] = []
        for path, body in file_contents.items():
            if len(body) > MAX_FILE_CHARS:
                body = body[:MAX_FILE_CHARS] + "\n... [truncated for brevity]"
            files_block_parts.append(f"### {path}\n```\n{body}\n```")
        files_block = "\n\n".join(files_block_parts) if files_block_parts else "(no file contents collected)"

        stdout = test_results.get("stdout") or ""
        stderr = test_results.get("stderr") or ""
        fs = test_results.get("failure_summary") or ""
        if fs:
            stdout = f"{fs}\n\n--- raw pytest output ---\n{stdout}"
        if len(stdout) > MAX_TEST_CHARS:
            stdout = stdout[:MAX_TEST_CHARS] + "\n... [truncated]"
        if len(stderr) > MAX_TEST_CHARS:
            stderr = stderr[:MAX_TEST_CHARS] + "\n... [truncated]"
        exit_code = test_results.get("exit_code")
        user_payload = f"""## Git diff (staged)

```
{diff}
```

## Full file contents (staged changes)

{files_block}

## Test results

exit_code: {exit_code}
stdout:
```
{stdout}
```
stderr:
```
{stderr}
```

Return ONLY valid JSON in the schema from the system prompt (verdict, reason, confidence 0.0–1.0; optional suggestions, lesson). Nothing else."""

        return await query_llm_reviewer_verdict_async(task, user_payload)
