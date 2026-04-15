import difflib
import hashlib
import logging
import re
from typing import Any

from app.logging.log_writer import append_runtime_log
from app.models.agent_decision import AgentDecision
from app.models.tool_result import ToolResult
from app.tools.docker_terminal import run_in_container_argv
from app.tools.filesystem_tools import read_file as read_file_tool
from app.tools.tool_registry import TOOLS
from agent_runtime.scope_guard import scope_violation_warning
from agent_runtime.task_runtime_state import runtime_state

logger = logging.getLogger(__name__)

_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<passed>\d+)\s+passed|(?P<failed>\d+)\s+failed|(?P<errors>\d+)\s+error[s]?|(?P<skipped>\d+)\s+skipped",
    re.IGNORECASE,
)


def _goal_is_bugfix_mode(goal: str | None) -> bool:
    g = (goal or "").strip().lower()
    if not g:
        return False
    bugfix_tokens = ("fix", "bug", "failing", "failure", "regression", "error", "exception")
    return any(tok in g for tok in bugfix_tokens)


def _goal_explicitly_allows_new_files(goal: str | None) -> bool:
    g = (goal or "").strip().lower()
    if not g:
        return False
    allow_tokens = ("create", "add ", "new file", "scaffold", "generate", "write a ")
    return any(tok in g for tok in allow_tokens)


def _parse_pytest_counts(output: str) -> dict[str, int | None]:
    counts: dict[str, int | None] = {"passed": None, "failed": None, "errors": None, "skipped": None}
    if not output:
        return counts
    for m in _PYTEST_SUMMARY_RE.finditer(output):
        gd = m.groupdict()
        for k, v in gd.items():
            if v is None:
                continue
            try:
                counts[k] = int(v)
            except ValueError:
                continue
    return counts


def _looks_like_unified_diff(text: str) -> bool:
    if not text or not text.strip():
        return False
    lines = text.splitlines()
    return (
        any(l.startswith("--- ") for l in lines)
        and any(l.startswith("+++ ") for l in lines)
        and any(l.startswith("@@") for l in lines)
    )


def _apply_patch_text_from_decision(decision: AgentDecision) -> str | None:
    inp = decision.input if decision.input is None else str(decision.input).strip()
    ct = decision.content if decision.content is None else str(decision.content).strip()
    if _looks_like_unified_diff(ct):
        return ct
    if _looks_like_unified_diff(inp):
        return inp
    if ct and not inp:
        return ct
    if inp and not ct:
        return inp
    if ct and inp:
        return ct if len(ct) >= len(inp) else inp
    return None


def _abs_workspace_path(path: str | None) -> str | None:
    if path is None:
        return None
    p = str(path).strip()
    if not p:
        return None
    return p if p.startswith("/") else f"/workspace/{p}"


def _stat_workspace_file(container: str, abs_path: str) -> tuple[int, int] | None:
    r = run_in_container_argv(container, ["stat", "-c", "%Y %s", abs_path])
    if r.get("exit_code") != 0:
        return None
    parts = (r.get("stdout") or "").strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _unblock_read_path(task: Any, abs_path: str) -> None:
    lst = list(getattr(task, "read_blocked_paths", None) or [])
    if abs_path in lst:
        task.read_blocked_paths = [p for p in lst if p != abs_path]


def _invalidate_file_cache(task: Any, abs_path: str) -> None:
    cache = getattr(task, "file_read_cache", None)
    if isinstance(cache, dict) and abs_path in cache:
        del cache[abs_path]


def get_max_diff_ratio(file_size_bytes: int) -> float:
    """
    Stricter caps for larger files; small files allow higher ratios so targeted edits are not rejected.
    """
    if file_size_bytes < 2000:
        return 0.80
    if file_size_bytes < 5000:
        return 0.60
    if file_size_bytes < 15000:
        return 0.45
    if file_size_bytes < 50000:
        return 0.35
    return 0.25


def allow_full_rewrite_for_small_file(file_size_bytes: int) -> bool:
    """
    For very small files, allow complete rewrites.
    This avoids over-constraining simple fixtures/config files.
    """
    return file_size_bytes <= 2500


def _compute_line_diff_ratio(old: str, new: str) -> dict[str, Any]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    total = max(len(old_lines), 1)
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        changed += (i2 - i1) + (j2 - j1)
    ratio = changed / total
    return {"changed_lines": changed, "total_lines": total, "diff_ratio": ratio}


class ExecutorError(Exception):
    """Raised when tool execution fails (unknown tool or tool raised)."""

    pass


class Executor:
    """Dispatches tool calls chosen by the LLM; returns typed ToolResult."""

    def execute(self, decision: AgentDecision, task: Any, *, step: int = 0) -> dict[str, Any]:
        tool_name = decision.tool.strip() if decision.tool else None
        if not tool_name or tool_name not in TOOLS:
            if decision.done:
                return {"status": "success", "stdout": "task complete", "exit_code": 0}
            return ToolResult(status="error", stderr=f"Unknown tool: {tool_name}", exit_code=-1).to_dict()
        tool = TOOLS[tool_name]
        try:
            container = task.workspace["container"]
            if tool_name == "read_file":
                path = _abs_workspace_path(decision.input)
                if not path:
                    return ToolResult(status="error", stderr="read_file requires a path", exit_code=-1).to_dict()
                cache = getattr(task, "file_read_cache", None)
                if not isinstance(cache, dict):
                    task.file_read_cache = {}
                    cache = task.file_read_cache
                fp = _stat_workspace_file(container, path)
                entry = cache.get(path)
                if (
                    entry
                    and fp is not None
                    and entry.get("mtime") == fp[0]
                    and entry.get("size") == fp[1]
                ):
                    append_runtime_log(task, f"cache_hit: {path}")
                    return {
                        "status": "success",
                        "stdout": entry.get("content", ""),
                        "stderr": "",
                        "exit_code": 0,
                        "from_cache": True,
                    }
                result = read_file_tool(container, decision.input)
                if int(result.get("exit_code", -1) or -1) == 0:
                    fp2 = fp if fp is not None else _stat_workspace_file(container, path)
                    if fp2 is not None:
                        cache[path] = {
                            "content": result.get("stdout") or "",
                            "mtime": fp2[0],
                            "size": fp2[1],
                            "last_read_step": step,
                        }
                    else:
                        cache[path] = {
                            "content": result.get("stdout") or "",
                            "mtime": -1,
                            "size": -1,
                            "last_read_step": step,
                        }
                return result

            if tool_name == "write_file":
                path = _abs_workspace_path(decision.input)
                if not path:
                    return ToolResult(status="error", stderr="write_file requires a path", exit_code=-1).to_dict()
                path_exists = _stat_workspace_file(container, path) is not None
                if (
                    not path_exists
                    and _goal_is_bugfix_mode(getattr(task, "goal", ""))
                    and not _goal_explicitly_allows_new_files(getattr(task, "goal", ""))
                ):
                    return ToolResult(
                        status="error",
                        stderr="new_file_blocked_in_bugfix_mode",
                        stdout=(
                            "Write rejected: creating a brand-new file is blocked for this bug-fix task.\n"
                            "Use existing project files (for example, files already present in /workspace) "
                            "and apply minimal targeted fixes."
                        ),
                        exit_code=2,
                    ).to_dict()

                proposed = decision.content or ""
                tid = str(getattr(task, "id", "") or "")
                blocked = list(runtime_state(tid).get("blocked_content_hashes") or [])
                if proposed.strip():
                    nh = hashlib.md5(proposed.encode("utf-8")).hexdigest()
                    if nh in blocked:
                        return ToolResult(
                            status="error",
                            stderr="blocked_reapplied_content",
                            stdout=(
                                "Write rejected: this exact content was previously reverted by the "
                                "regression guard because it broke tests or caused a collection error. "
                                "You cannot re-apply it. Read the current file and make a different fix."
                            ),
                            exit_code=1,
                        ).to_dict()

                current_r = read_file_tool(container, path)
                current = current_r.get("stdout", "") if isinstance(current_r, dict) else ""

                scope_note = scope_violation_warning(
                    proposed,
                    current,
                    list(runtime_state(tid).get("locked_failing_tests") or []),
                )

                if (proposed or "").strip() == (current or "").strip():
                    return ToolResult(
                        status="error",
                        stderr="identical_content",
                        stdout=(
                            "Write rejected: file content is identical to what is already on disk.\n"
                            "The file was not changed. This means either:\n"
                            "  (a) Your previous write already applied this fix correctly, OR\n"
                            "  (b) You are attempting the same incorrect change again.\n"
                            "Run the tests now to check whether the current file state passes. "
                            "Do not write again until you have run tests and seen the results."
                        ),
                        exit_code=1,
                        rejected_reason="identical_content",
                    ).to_dict()

                stats = _compute_line_diff_ratio(current, proposed)
                diff_ratio = float(stats["diff_ratio"])
                file_size_bytes = len(current.encode("utf-8"))
                max_ratio = get_max_diff_ratio(file_size_bytes)
                logger.info(
                    "diff size path=%s changed_lines=%s total_lines=%s diff_ratio=%.3f max_ratio=%.3f bytes=%s",
                    path,
                    stats["changed_lines"],
                    stats["total_lines"],
                    diff_ratio,
                    max_ratio,
                    file_size_bytes,
                    extra={"task_id": getattr(task, "id", None)},
                )

                if diff_ratio > max_ratio:
                    if allow_full_rewrite_for_small_file(file_size_bytes):
                        logger.info(
                            "Allowing full rewrite for small file path=%s bytes=%s diff_ratio=%.3f",
                            path,
                            file_size_bytes,
                            diff_ratio,
                            extra={"task_id": getattr(task, "id", None)},
                        )
                    else:
                        logger.warning(
                            "Full rewrite detected",
                            extra={"task_id": getattr(task, "id", None), "path": path, "diff_ratio": diff_ratio},
                        )
                        guidance = (
                            "Write rejected: full rewrite detected.\n"
                            "Your change modified too much of the file.\n"
                            "Only edit the minimal lines necessary to fix the failing tests.\n"
                            "Use write_file again with a smaller, targeted change (full file content, but only change what you must).\n"
                            f"Diff ratio: {diff_ratio:.3f} exceeds limit {max_ratio:.3f} "
                            f"for a file of this size ({file_size_bytes} bytes)."
                        )
                        return ToolResult(
                            status="error",
                            stderr="Full rewrite detected",
                            stdout=guidance,
                            exit_code=2,
                            diff_ratio=diff_ratio,
                            changed_lines=stats["changed_lines"],
                            total_lines=stats["total_lines"],
                            rejected_reason="full_rewrite_detected",
                        ).to_dict()

                if not getattr(task, "pre_write_files", None):
                    task.pre_write_files = {}
                task.pre_write_files[path] = current

                if getattr(task, "regression_baseline", None) is None:
                    task.regression_baseline = getattr(task, "last_test_counts", None)

                if hasattr(task, "touched_files") and path not in getattr(task, "touched_files", []):
                    task.touched_files.append(path)

                result = tool(container, {"path": decision.input, "content": decision.content})
                if isinstance(result, dict):
                    result.setdefault("diff_ratio", diff_ratio)
                    result.setdefault("changed_lines", stats["changed_lines"])
                    result.setdefault("total_lines", stats["total_lines"])
                if isinstance(result, dict) and int(result.get("exit_code", -1) or -1) == 0:
                    _invalidate_file_cache(task, path)
                    _unblock_read_path(task, path)
                    if scope_note:
                        result = dict(result)
                        prev = (result.get("stdout") or "").strip()
                        result["stdout"] = (prev + "\n\n" + scope_note).strip() if prev else scope_note
                return result if isinstance(result, dict) else result

            if tool_name == "apply_patch":
                if getattr(task, "regression_baseline", None) is None:
                    task.regression_baseline = getattr(task, "last_test_counts", None)
                patch_text = _apply_patch_text_from_decision(decision)
                result = tool(container, patch_text)
                if not isinstance(result, dict):
                    return ToolResult(status="error", stderr=str(result), exit_code=-1).to_dict()
                if int(result.get("exit_code", -1) or -1) == 0:
                    target = result.get("target")
                    if target:
                        abs_target = _abs_workspace_path(str(target))
                        if abs_target:
                            _invalidate_file_cache(task, abs_target)
                            _unblock_read_path(task, abs_target)
                        append_runtime_log(task, f"patch_applied: {target}")
                        result = dict(result)
                        result["patch_applied_target"] = target
                    if hasattr(task, "touched_files") and target:
                        abs_t = _abs_workspace_path(str(target))
                        if abs_t and abs_t not in getattr(task, "touched_files", []):
                            task.touched_files.append(abs_t)
                return result

            else:
                result = tool(container, decision.input)
        except Exception as e:
            return ToolResult(status="error", stderr=str(e), exit_code=-1).to_dict()
        if isinstance(result, ToolResult):
            return result.to_dict()
        if isinstance(result, dict) and "exit_code" in result:
            tr = ToolResult.from_subprocess(
                returncode=result["exit_code"],
                stdout=result.get("stdout", "") or "",
                stderr=result.get("stderr", "") or "",
            ).to_dict()

            if tool_name == "run_tests":
                combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
                counts = _parse_pytest_counts(combined)
                if hasattr(task, "last_test_counts"):
                    task.last_test_counts = counts
                tr["test_counts"] = counts
                fs = result.get("failure_summary")
                if fs:
                    tr["failure_summary"] = fs
            return tr
        return ToolResult(status="error", stderr=str(result), exit_code=-1).to_dict()
