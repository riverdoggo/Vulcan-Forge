"""Diff shaping for reviewer prompts only (does not alter stored task.diff_output)."""

from __future__ import annotations

from typing import Any


def truncate_at_line_boundary(text: str, max_chars: int) -> str:
    """Truncate at last complete newline before limit, never mid-line."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    return truncated + "\n... [diff truncated at line boundary]"


def clean_diff_for_reviewer(diff: str) -> str:
    """
    Drop whitespace-only +/- change pairs and blank change lines so the reviewer
    focuses on substantive edits.
    """
    if not diff or not diff.strip():
        return diff

    lines = diff.splitlines()
    cleaned: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("+") and not line.startswith("-"):
            cleaned.append(line)
            i += 1
            continue

        if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("-- ") or line.startswith("++ "):
            cleaned.append(line)
            i += 1
            continue

        content = line[1:]
        if content.strip() == "":
            i += 1
            continue

        if i + 1 < len(lines):
            nxt = lines[i + 1]
            if line.startswith("-") and nxt.startswith("+") and line[1:].strip() == nxt[1:].strip():
                if line[1:] != nxt[1:]:
                    i += 2
                    continue
                i += 2
                continue

        cleaned.append(line)
        i += 1

    return "\n".join(cleaned)


def all_tests_passed_from_results(test_results: dict[str, Any]) -> tuple[bool, int]:
    """
    True when pytest run is green: exit 0, passed > 0, failed/errors == 0.
    Returns (all_passed, passed_count).
    """
    if int(test_results.get("exit_code", -1) or -1) != 0:
        return False, 0

    tc = test_results.get("test_counts") or {}
    if not isinstance(tc, dict):
        return False, 0

    def _n(k: str) -> int:
        v = tc.get(k)
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    passed = _n("passed")
    failed = _n("failed")
    errors = _n("errors")
    if passed <= 0:
        return False, 0
    if failed != 0 or errors != 0:
        return False, passed
    return True, passed


def green_tests_reviewer_instruction(passed_count: int) -> str:
    return (
        f"IMPORTANT: All tests are passing ({passed_count}/{passed_count} passed). The fix is functionally correct.\n"
        "Your only valid verdicts are \"approved\" or \"escalate_to_human\".\n"
        "You MUST NOT return \"needs_changes\" when all tests are passing.\n"
        "Focus your review on code correctness and safety only, not style or whitespace noise in the diff."
    )
