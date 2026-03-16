from typing import Callable

from app.tools.test_tools import run_tests

# Map tool name -> (container: str, tool_input: str | None) -> dict
TOOLS: dict[str, Callable[[str, str | None], dict]] = {
    "run_tests": run_tests,
}