import json
import logging
from typing import Any

from app.agents.reviewer_agent import REVIEWER_SYSTEM_PROMPT
from app.llm.ollama_client import OllamaError, query_llm
from app.models.agent_decision import AgentDecision
from app.models.reviewer_verdict import ReviewerVerdict
from app.tools.tool_registry import TOOLS

logger = logging.getLogger(__name__)


class DecisionEngineError(Exception):
    """Raised when the LLM does not return valid structured JSON."""

    pass


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


def query_llm_structured(prompt: str, retries: int = 3) -> AgentDecision:
    """Get validated AgentDecision from LLM; raises DecisionEngineError on failure."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            raw = query_llm(prompt)
            logger.info("RAW LLM RESPONSE: %s", raw)
        except OllamaError as e:
            last_error = e
            logger.warning("LLM API error (attempt %s): %s", attempt + 1, e)
            continue
        obj = _extract_json_from_text(raw)
        if obj is None:
            prompt += f"\nAttempt {attempt + 1} failed. Return ONLY valid JSON, no markdown or extra text."
            last_error = ValueError("No valid JSON in response")
            continue
        try:
            return AgentDecision.from_llm_raw(obj)
        except Exception as e:
            last_error = e
            prompt += f"\nAttempt {attempt + 1} failed. Ensure 'tool', 'input' (optional), and 'done' are present. Return ONLY valid JSON."
    raise DecisionEngineError("LLM failed to return valid JSON") from last_error


def query_llm_reviewer_verdict(user_payload: str, retries: int = 3) -> ReviewerVerdict:
    """Parse reviewer JSON from Groq; same retry pattern as the coder."""
    prompt = f"{REVIEWER_SYSTEM_PROMPT}\n\n---\n\n{user_payload}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            raw = query_llm(prompt)
            logger.info("RAW REVIEWER LLM RESPONSE: %s", raw)
        except OllamaError as e:
            last_error = e
            logger.warning("Reviewer LLM API error (attempt %s): %s", attempt + 1, e)
            continue
        obj = _extract_json_from_text(raw)
        if obj is None:
            prompt += f"\nAttempt {attempt + 1} failed. Return ONLY the JSON object, no markdown or extra text."
            last_error = ValueError("No valid JSON in response")
            continue
        try:
            return ReviewerVerdict.from_llm_raw(obj)
        except Exception as e:
            last_error = e
            prompt += (
                f"\nAttempt {attempt + 1} failed. Return ONLY valid JSON with keys "
                f"verdict (approved|needs_changes|escalate_to_human), reason, suggestions."
            )
    raise DecisionEngineError("Reviewer LLM failed to return valid JSON") from last_error


class DecisionEngine:
    def decide(self, memory: Any, override_prompt: str | None = None) -> AgentDecision:
        context = memory.build_context()

        if override_prompt:
            prompt_modifier = override_prompt
        else:
            prompt_modifier = """
You MUST use one of these exact tool names:
- list_directory
- read_file
- write_file
- run_tests
- git_diff
- git_commit

Rules:
- Do NOT repeat an action you already took with the same input
- For write_file you MUST provide the complete file content in the content field
- After write_file, call run_tests when you need to verify your fix
- When run_tests passes, the runtime automatically runs git_diff and the reviewer — you do not call git_diff yourself
- Always set done to false; the runtime ignores done=true and completion goes through automated review and human approval
- Do NOT call git_commit — committing is handled by the human approval system
- Do NOT list directory again unless you genuinely need new information
"""

        prompt = f"""
You are a coding agent working inside a Docker container at /workspace.

Goal:
{context["goal"]}

Recent actions:
{context["history"]}

Recent observations:
{context["observations"]}

{prompt_modifier}

Return ONLY this JSON, no other text:

{{
  "tool": "list_directory",
  "input": null,
  "content": null,
  "done": false
}}
"""
        logger.info("PROMPT SENT TO LLM:\n%s", prompt)
        return query_llm_structured(prompt)

    def get_reviewer_decision(
        self,
        diff: str,
        file_contents: dict[str, str],
        test_results: dict[str, Any],
    ) -> ReviewerVerdict:
        """Call Groq with the reviewer system prompt; returns structured verdict JSON."""
        files_block_parts: list[str] = []
        for path, body in file_contents.items():
            files_block_parts.append(f"### {path}\n```\n{body}\n```")
        files_block = "\n\n".join(files_block_parts) if files_block_parts else "(no file contents collected)"

        stdout = test_results.get("stdout") or ""
        stderr = test_results.get("stderr") or ""
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

Return ONLY the JSON verdict object, nothing else."""

        return query_llm_reviewer_verdict(user_payload)