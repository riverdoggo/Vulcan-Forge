import json
from app.llm.ollama_client import query_llm
from app.tools.tool_registry import TOOLS

# structured retry for bad JSON responses from local models
def query_llm_structured(prompt, retries=3):

    for attempt in range(retries):

        raw = query_llm(prompt)

        try:
            return json.loads(raw)

        except Exception:
            prompt += f"\nAttempt {attempt+1} failed. Return ONLY valid JSON."

    raise Exception("LLM failed to return valid JSON")


class DecisionEngine:

    def decide(self, memory):

        context = memory.build_context()

        prompt = f"""
You are a coding agent.

Goal:
{context["goal"]}

Recent actions:
{context["history"]}

Recent observations:
{context["observations"]}

Available tools:
{list(TOOLS.keys())}

You MUST choose exactly one tool.

Return ONLY this JSON format:

{{
 "tool": "tool name from the available tools list",
 "input": "argument for the tool",
 "done": false
}}

No text before or after JSON.
"""

        return query_llm_structured(prompt)