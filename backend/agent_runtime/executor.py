from typing import Any

from app.models.agent_decision import AgentDecision
from app.models.tool_result import ToolResult
from app.tools.tool_registry import TOOLS


class ExecutorError(Exception):
    """Raised when tool execution fails (unknown tool or tool raised)."""

    pass


class Executor:
    """Dispatches tool calls chosen by the LLM; returns typed ToolResult."""

    def execute(self, decision: AgentDecision, task: Any) -> dict[str, Any]:
        tool_name = decision.tool.strip()
        if not tool_name or tool_name not in TOOLS:
            raise ExecutorError(f"Unknown tool: {tool_name!r}")
        tool = TOOLS[tool_name]
        try:
            result = tool(task.workspace["container"], decision.input)
        except Exception as e:
            raise ExecutorError(f"Tool {tool_name} failed: {e}") from e
        if isinstance(result, ToolResult):
            return result.to_dict()
        if isinstance(result, dict) and "exit_code" in result:
            return ToolResult.from_subprocess(
                returncode=result["exit_code"],
                stdout=result.get("stdout", "") or "",
                stderr=result.get("stderr", "") or "",
            ).to_dict()
        return ToolResult(status="error", stderr=str(result), exit_code=-1).to_dict()
