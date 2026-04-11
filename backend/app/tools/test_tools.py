import re
from typing import Any

from app.memory.compression import strip_pytest_output
from app.models.tool_result import ToolResult
from app.tools.docker_terminal import run_in_container_argv

# Pytest short / classic failure lines
_FAILED_LINE = re.compile(
    r"^FAILED\s+(?P<loc>\S+::(?P<name>\S+))\s*(?:-\s*(?P<msg>.*))?$",
    re.MULTILINE,
)
_E_ASSERT = re.compile(r"^\s*E\s+assert\s+(\S+)\s*==\s*(\S+)\s*$", re.MULTILINE)
_ASSERTION_ERR = re.compile(
    r"AssertionError:\s*assert\s+(\S+)\s*==\s*(\S+)",
    re.IGNORECASE,
)


def _summarize_pytest_failures(output: str) -> str:
    """
    Build a short human-readable list of failing tests and expected vs actual
    when parseable from pytest output (for LLM context).
    """
    if not output or ("FAILED" not in output and "FAILURES" not in output and "= ERRORS =" not in output):
        return ""

    blocks: list[str] = []
    failed_tests: list[str] = []
    for m in _FAILED_LINE.finditer(output):
        name = m.group("name") or m.group("loc")
        if name and name not in failed_tests:
            failed_tests.append(name)

    # Pair E-lines with test names by order (pytest usually lists failures in order)
    e_matches = list(_E_ASSERT.finditer(output)) + list(_ASSERTION_ERR.finditer(output))
    used_pairs: set[tuple[str, str]] = set()

    for i, m in enumerate(e_matches):
        left, right = m.group(1), m.group(2)
        key = (left, right)
        if key in used_pairs:
            continue
        used_pairs.add(key)
        test_label = failed_tests[i] if i < len(failed_tests) else f"(failure {i + 1})"
        blocks.append(
            f"* {test_label}\n  expected: {right}\n  actual: {left}",
        )

    # If we had FAILED lines but no assert pairs, still list test names
    if not blocks and failed_tests:
        for n in failed_tests[:20]:
            blocks.append(f"* {n}\n  (see raw output for details)")

    if not blocks:
        return ""

    return "Failing tests:\n\n" + "\n\n".join(blocks)


def failed_test_names_from_pytest_output(output: str) -> list[str]:
    """Collect failing test identifiers from pytest or stripped FAIL: lines."""
    names: list[str] = []
    if not output:
        return names
    for m in _FAILED_LINE.finditer(output):
        name = m.group("name") or m.group("loc")
        if name and name not in names:
            names.append(name)
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("FAIL:"):
            rest = s[5:].strip()
            seg = rest.split(" - ", 1)[0].strip()
            if seg and seg not in names:
                names.append(seg)
    return names


def run_tests(container: str, path: str | None = None) -> dict[str, Any]:
    """Run pytest in the workspace container. Merges stderr into stdout so pytest errors are visible."""
    argv = ["python", "-m", "pytest", "--tb=short", "-q"]
    if path and str(path).strip():
        argv.append(str(path).strip())
    raw = run_in_container_argv(container, argv)
    out = (raw.get("stdout") or "").rstrip()
    err = (raw.get("stderr") or "").rstrip()
    if out and err:
        combined = out + "\n" + err
    else:
        combined = out or err
    if not combined:
        combined = ""

    failure_summary = _summarize_pytest_failures(combined)
    compact_stdout = strip_pytest_output(combined)
    result = ToolResult.from_subprocess(
        returncode=raw.get("exit_code", -1),
        stdout=compact_stdout,
        stderr="",
    ).to_dict()
    if failure_summary:
        result["failure_summary"] = failure_summary
    return result
