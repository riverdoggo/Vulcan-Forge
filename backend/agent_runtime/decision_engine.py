import json
import logging
from typing import Any

from app.llm.ollama_client import OllamaError, query_llm
from app.models.agent_decision import AgentDecision
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
- Once run_tests passes, call git_diff immediately
- After git_diff succeeds, set done=true and stop
- Do NOT call git_commit — committing is handled by the human approval system
- Do NOT run tests again after they already passed
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