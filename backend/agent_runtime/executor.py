from app.tools.tool_registry import TOOLS


# executor dispatches tool calls chosen by the LLM
class Executor:
    def execute(self, decision, task):

        tool_name = decision["tool"]

        if tool_name not in TOOLS:
            raise Exception(f"Unknown tool: {tool_name}")

        tool = TOOLS[tool_name]

        tool_input = decision.get("input")

        result = tool(task.workspace["container"], tool_input)

        if result["exit_code"] == 5:
            result["status"] = "no_tests_found"

        return result
