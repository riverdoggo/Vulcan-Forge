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
        tool_name = decision.tool.strip() if decision.tool else None
        if not tool_name or tool_name not in TOOLS:
            if decision.done:
                return {"status": "success", "stdout": "task complete", "exit_code": 0}
            return ToolResult(status="error", stderr=f"Unknown tool: {tool_name}", exit_code=-1).to_dict()
        tool = TOOLS[tool_name]
        try:
            container = task.workspace["container"]
            if tool_name == "write_file":
                result = tool(
                    container,
                    {
                        "path": decision.input,
                        "content": decision.content,
                    },
                )
            else:
                result = tool(container, decision.input)
        except Exception as e:
            return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
        if isinstance(result, ToolResult):
            return result.to_dict()
        if isinstance(result, dict) and "exit_code" in result:
            return ToolResult.from_subprocess(
                returncode=result["exit_code"],
                stdout=result.get("stdout", "") or "",
                stderr=result.get("stderr", "") or "",
            ).to_dict()
        return ToolResult(status="error", stderr=str(result), exit_code=-1).to_dict()
