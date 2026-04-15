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
from agent_runtime.reviewer_diff import (
    all_tests_passed_from_results,
    clean_diff_for_reviewer,
    green_tests_reviewer_instruction,
    truncate_at_line_boundary,
)

logger = logging.getLogger(__name__)


class DecisionEngineError(Exception):
    """Raised when the LLM does not return valid structured JSON (reviewer path)."""

    pass


# Initial attempt + up to 2 retries = 3 LLM calls max for coder decisions.
MAX_CODER_DECISION_RETRIES = 2

# Per-LLM-call wall-clock cap (inner httpx may use longer read timeout; outer wait_for wins first).
LLM_CALL_TIMEOUT_SEC = 30.0
REVIEWER_DIFF_MAX_CHARS = 12000
REVIEWER_FILE_MAX_CHARS = 3000
REVIEWER_TEST_MAX_CHARS = 1000


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
"content": null,
"done": false
}

Rules:
- Return JSON only — no markdown code fences (no ```), no prose before or after the object.
- Default editing tool is write_file: "content" must be ONE JSON string with the complete file. Escape every double quote inside the file as \\" and every newline as \\n. Never use Python \"\"\" triple quotes — that is not valid JSON.
- apply_patch (fallback only): put the unified diff in "content"; use only when write_file is unsuitable. Prefer write_file.
- For other tools: use "content": null unless the tool needs content.

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


def _strip_markdown_json_fence(text: str) -> str:
    """Remove leading ```json / ``` and trailing ``` so brace matching sees raw JSON."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    first_nl = s.find("\n")
    if first_nl == -1:
        return s
    s = s[first_nl + 1 :].rstrip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """
    Find the outermost JSON object and parse it. Brace matching respects JSON double-quoted
    strings so file content with { } does not break extraction.
    """
    text = _strip_markdown_json_fence(text.strip())
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
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
        return f"unknown tool {name!r}; must be one of: {', '.join(TOOLS)}"
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


def query_llm_structured_coder(prompt: str, task: Any | None = None) -> CoderDecisionOutcome:
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
            raw = query_llm(
                current_prompt,
                task=task,
                llm_override=getattr(task, "llm_override", None),
            )
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
                query_llm_async(
                    current_prompt,
                    task=task,
                    llm_override=getattr(task, "llm_override", None),
                ),
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


def _reviewer_base_prompt(user_payload: str, extra_system: str = "") -> str:
    sys = REVIEWER_SYSTEM_PROMPT
    if extra_system.strip():
        sys = f"{REVIEWER_SYSTEM_PROMPT}\n\n{extra_system.strip()}"
    return f"{sys}\n\n---\n\n{user_payload}"


def _truncate_at_line_boundary(text: str, max_chars: int, marker: str) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    return f"{truncated}\n{marker}"


def query_llm_reviewer_verdict(
    user_payload: str,
    task: Any | None = None,
    *,
    extra_system: str = "",
    reviewer_system: str | None = None,
) -> ReviewerVerdict:
    """Parse reviewer JSON; one retry with correction, then deterministic fallback."""
    if reviewer_system is not None:
        base = f"{reviewer_system}\n\n---\n\n{user_payload}"
    else:
        base = _reviewer_base_prompt(user_payload, extra_system)
    prompt = base
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw = query_llm(
                prompt,
                task=task,
                llm_override=getattr(task, "llm_override", None),
            )
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


async def query_llm_reviewer_verdict_async(
    task: Any,
    user_payload: str,
    *,
    extra_system: str = "",
    reviewer_system: str | None = None,
) -> ReviewerVerdict:
    """Async reviewer: one retry with correction, then fallback (never raises for bad JSON)."""
    if reviewer_system is not None:
        base = f"{reviewer_system}\n\n---\n\n{user_payload}"
    else:
        base = _reviewer_base_prompt(user_payload, extra_system)
    prompt = base
    last_error: Exception | None = None
    for attempt in range(2):
        _abort_if_task_killed(task)
        try:
            raw = await asyncio.wait_for(
                query_llm_async(
                    prompt,
                    task=task,
                    llm_override=getattr(task, "llm_override", None),
                ),
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
You MUST use one of these exact tool names (write_file is the default way to change code):
- list_directory
- read_file
- write_file
- run_tests
- run_command
- git_diff
- git_commit
- apply_patch (fallback only — avoid; use write_file for essentially all edits)

Rules:
- You are modifying an existing codebase.
- Do NOT rewrite entire files. Only change the minimal parts required.
- Preserve all existing logic and structure.
- Add missing methods instead of removing features.
- If a fix makes tests worse, revert and try another approach.
- Do NOT repeat an action you already took with the same input
- When fixing code, prefer small localized edits instead of rewriting entire files.
- Use write_file for all file edits unless you have a rare, specific reason not to: put the complete updated file in the content field (change only what is needed vs what you read).
- apply_patch is a last resort (unified diff in content). Do not choose it when write_file would work.
- For write_file you MUST provide the complete file content in the content field as a single JSON string: escape newlines as \\n and internal double-quotes as \\". Never wrap the whole answer in ``` markdown fences. Never use Python \"\"\" triple quotes — invalid JSON and the runtime will reject the step.
- Use strict JSON syntax only: booleans are true/false, empty values are null. NEVER use Python None.
- After EVERY write_file (or apply_patch if you used that fallback) you MUST immediately call run_tests next — the runtime enforces this after writes; still plan for it.
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
- You MUST follow: read_file → write_file → run_tests.
- After read_file succeeds for a path, NEVER call read_file on that same path again in this task (the full file is in <latest_read_file> and memory). The next step is write_file with the complete updated file, then run_tests — unless read/write failed or the runtime explicitly said the file changed.
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
  "tool": "write_file",
  "input": "/workspace/path/to/file.py",
  "content": "full file as one JSON string — escape newlines as \\\\n and quotes as \\\\\"",
  "done": false
}}
"""
        logger.info("PROMPT SENT TO LLM:\n%s", prompt)
        return query_llm_structured_coder(prompt, task=None)

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
You MUST use one of these exact tool names (write_file is the default way to change code):
- list_directory
- read_file
- write_file
- run_tests
- run_command
- git_diff
- git_commit
- apply_patch (fallback only — avoid; use write_file for essentially all edits)

Rules:
- You are modifying an existing codebase.
- Do NOT rewrite entire files. Only change the minimal parts required.
- Preserve all existing logic and structure.
- Add missing methods instead of removing features.
- If a fix makes tests worse, revert and try another approach.
- Do NOT repeat an action you already took with the same input
- When fixing code, prefer small localized edits instead of rewriting entire files.
- Use write_file for all file edits unless you have a rare, specific reason not to: put the complete updated file in the content field (change only what is needed vs what you read).
- apply_patch is a last resort (unified diff in content). Do not choose it when write_file would work.
- For write_file you MUST provide the complete file content in the content field as a single JSON string: escape newlines as \\n and internal double-quotes as \\". Never wrap the whole answer in ``` markdown fences. Never use Python \"\"\" triple quotes — invalid JSON and the runtime will reject the step.
- Use strict JSON syntax only: booleans are true/false, empty values are null. NEVER use Python None.
- After EVERY write_file (or apply_patch if you used that fallback) you MUST immediately call run_tests next — the runtime enforces this after writes; still plan for it.
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
- You MUST follow: read_file → write_file → run_tests.
- After read_file succeeds for a path, NEVER call read_file on that same path again in this task (the full file is in <latest_read_file> and memory). The next step is write_file with the complete updated file, then run_tests — unless read/write failed or the runtime explicitly said the file changed.
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
  "tool": "write_file",
  "input": "/workspace/path/to/file.py",
  "content": "full file as one JSON string — escape newlines as \\\\n and quotes as \\\\\"",
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
                query_llm_async(
                    prompt,
                    task=task,
                    llm_override=getattr(task, "llm_override", None),
                ),
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
        all_passed, n_pass = all_tests_passed_from_results(test_results)
        logger.debug(
            "[reviewer] all_tests_passed=%s, test_counts=%s",
            all_passed,
            test_results.get("test_counts") if isinstance(test_results, dict) else None,
        )
        reviewer_system = REVIEWER_SYSTEM_PROMPT
        if all_passed:
            reviewer_system = (
                reviewer_system
                + "\n\n"
                + green_tests_reviewer_instruction(n_pass)
            )

        diff_for_llm = clean_diff_for_reviewer(diff)
        diff_for_llm = truncate_at_line_boundary(diff_for_llm, REVIEWER_DIFF_MAX_CHARS)

        files_block_parts: list[str] = []
        for path, body in file_contents.items():
            body = _truncate_at_line_boundary(
                body,
                REVIEWER_FILE_MAX_CHARS,
                "... [truncated for brevity]",
            )
            files_block_parts.append(f"### {path}\n```\n{body}\n```")
        files_block = "\n\n".join(files_block_parts) if files_block_parts else "(no file contents collected)"

        stdout = test_results.get("stdout") or ""
        stderr = test_results.get("stderr") or ""
        fs = test_results.get("failure_summary") or ""
        if fs:
            stdout = f"{fs}\n\n--- raw pytest output ---\n{stdout}"
        stdout = _truncate_at_line_boundary(stdout, REVIEWER_TEST_MAX_CHARS, "... [truncated]")
        stderr = _truncate_at_line_boundary(stderr, REVIEWER_TEST_MAX_CHARS, "... [truncated]")
        exit_code = test_results.get("exit_code")
        user_payload = f"""## Git diff (staged)

```
{diff_for_llm}
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

        verdict = query_llm_reviewer_verdict(
            user_payload,
            task=None,
            reviewer_system=reviewer_system,
        )
        if all_passed and verdict.verdict == "needs_changes":
            logger.warning(
                "[reviewer_coerce] overriding needs_changes -> approved (tests=%s)",
                n_pass,
            )
            verdict = verdict.model_copy(
                update={
                    "verdict": "approved",
                    "reason": f"All tests passed; auto-approved despite whitespace/style noise. Reviewer said: {verdict.reason}",
                    "confidence": max(float(verdict.confidence), 0.85),
                    "suggestions": "",
                }
            )
        return verdict

    async def get_reviewer_decision_async(
        self,
        task: Any,
        diff: str,
        file_contents: dict[str, str],
        test_results: dict[str, Any],
    ) -> ReviewerVerdict:
        _abort_if_task_killed(task)
        all_passed, n_pass = all_tests_passed_from_results(test_results)
        logger.debug(
            "[reviewer] all_tests_passed=%s, test_counts=%s",
            all_passed,
            test_results.get("test_counts") if isinstance(test_results, dict) else None,
        )
        reviewer_system = REVIEWER_SYSTEM_PROMPT
        if all_passed:
            reviewer_system = (
                reviewer_system
                + "\n\n"
                + green_tests_reviewer_instruction(n_pass)
            )

        diff_for_llm = clean_diff_for_reviewer(diff)
        diff_for_llm = truncate_at_line_boundary(diff_for_llm, REVIEWER_DIFF_MAX_CHARS)

        files_block_parts: list[str] = []
        for path, body in file_contents.items():
            body = _truncate_at_line_boundary(
                body,
                REVIEWER_FILE_MAX_CHARS,
                "... [truncated for brevity]",
            )
            files_block_parts.append(f"### {path}\n```\n{body}\n```")
        files_block = "\n\n".join(files_block_parts) if files_block_parts else "(no file contents collected)"

        stdout = test_results.get("stdout") or ""
        stderr = test_results.get("stderr") or ""
        fs = test_results.get("failure_summary") or ""
        if fs:
            stdout = f"{fs}\n\n--- raw pytest output ---\n{stdout}"
        stdout = _truncate_at_line_boundary(stdout, REVIEWER_TEST_MAX_CHARS, "... [truncated]")
        stderr = _truncate_at_line_boundary(stderr, REVIEWER_TEST_MAX_CHARS, "... [truncated]")
        exit_code = test_results.get("exit_code")
        user_payload = f"""## Git diff (staged)

```
{diff_for_llm}
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

        verdict = await query_llm_reviewer_verdict_async(
            task,
            user_payload,
            reviewer_system=reviewer_system,
        )
        if all_passed and verdict.verdict == "needs_changes":
            logger.warning(
                "[reviewer_coerce] overriding needs_changes -> approved (tests=%s)",
                n_pass,
            )
            append_runtime_log(task, "reviewer_coerce: all tests green; overriding needs_changes to approved")
            verdict = verdict.model_copy(
                update={
                    "verdict": "approved",
                    "reason": f"All tests passed; auto-approved despite whitespace/style noise. Reviewer said: {verdict.reason}",
                    "confidence": max(float(verdict.confidence), 0.85),
                    "suggestions": "",
                }
            )
        return verdict
