"""Heuristic scope check for write_file (warn only; does not block writes)."""

from __future__ import annotations

import difflib
import re
from typing import Any

_DEF_RE = re.compile(r"^\s*def\s+(\w+)\s*\(")


def _changed_defs_from_unified_diff_lines(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        if not line.startswith("+") and not line.startswith("-"):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        body = line[1:]
        m = _DEF_RE.match(body)
        if m:
            names.add(m.group(1))
    return names


def scope_violation_warning(
    new_content: str,
    existing_content: str,
    locked_failing_tests: list[str],
) -> str | None:
    """
    If locked failing tests exist and changed defs seem unrelated, return a warning
    string for stdout; otherwise None.
    """
    if not locked_failing_tests:
        return None

    diff_lines = list(
        difflib.unified_diff(
            existing_content.splitlines(),
            new_content.splitlines(),
            lineterm="",
        )
    )
    changed_functions = _changed_defs_from_unified_diff_lines(diff_lines)
    if not changed_functions:
        return None

    for func in changed_functions:
        fl = func.lower()
        for test in locked_failing_tests:
            tl = test.lower()
            if fl in tl:
                return None

    return (
        f"WARNING: You are modifying function(s) {{{', '.join(sorted(changed_functions))}}} "
        f"which do not obviously relate to the locked failing tests: {locked_failing_tests}.\n"
        "Only change code directly required to fix those tests.\n"
        "If this edit is necessary, say so in your reasoning."
    )
