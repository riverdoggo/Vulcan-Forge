import base64
import logging
import re
import shlex
from pathlib import Path
from typing import Any

from app.tools.docker_terminal import run_in_container_argv
from app.workspace.workspace_manager import WORKSPACES_BASE

logger = logging.getLogger(__name__)

# Paths must be under /workspace and not contain shell metacharacters
_PATH_SAFE = re.compile(r"^[\w./\-]+$")


def _host_file_for_workspace_path(container: str, workspace_abs: str) -> Path | None:
    """
    Map container name + /workspace/... path to the host bind-mounted file, if present.
    Avoids docker exec stdout limits on Windows Docker Desktop for normal task workspaces.
    """
    if not container.startswith("agent_ws_"):
        return None
    safe_id = container[len("agent_ws_") :]
    if not safe_id:
        return None
    w = workspace_abs.strip()
    if w != "/workspace" and not w.startswith("/workspace/"):
        return None
    rel = w[len("/workspace") :].lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    try:
        base = (WORKSPACES_BASE / safe_id).resolve()
        candidate = (base / rel).resolve()
        candidate.relative_to(base)
    except (ValueError, OSError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _safe_path(path: str) -> str:
    if not path or ".." in path or not _PATH_SAFE.match(path):
        raise ValueError(f"Invalid or unsafe path: {path!r}")
    resolved = path if path.startswith("/") else f"/workspace/{path}"
    if resolved != "/workspace" and not resolved.startswith("/workspace/"):
        raise ValueError(f"Path must be under /workspace: {path!r}")
    return resolved


def read_file(container: str, path: str | None) -> dict[str, Any]:
    """Read file inside container; path is validated and not passed through shell."""
    if not path:
        return {"status": "error", "stderr": "path required", "exit_code": -1, "stdout": ""}
    try:
        safe = _safe_path(path.strip())
    except ValueError as e:
        return {"status": "error", "stderr": str(e), "exit_code": -1, "stdout": ""}

    host_fp = _host_file_for_workspace_path(container, safe)
    if host_fp is not None:
        try:
            raw = host_fp.read_bytes()
        except OSError as e:
            logger.warning("Host workspace read failed, falling back to docker exec: %s", e)
        else:
            logger.debug("read_file via host bind mount: %s", host_fp)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            return {"status": "success", "stdout": text, "stderr": "", "exit_code": 0}

    # Fallback: base64 inside container (docker exec); docker_terminal uses binary pipe reads + ``-i``.
    result = run_in_container_argv(container, ["base64", "-w", "0", safe])
    if result.get("exit_code") != 0:
        return result
    b64_raw = (result.get("stdout") or "").strip()
    try:
        raw = base64.b64decode(b64_raw, validate=False)
    except Exception as e:
        return {"status": "error", "stderr": f"read_file base64 decode failed: {e}", "exit_code": -1, "stdout": ""}
    try:
        result["stdout"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        result["stdout"] = raw.decode("utf-8", errors="replace")
    return result


def write_file(container: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Write content to path in container; content is base64-encoded to avoid shell injection."""
    if not args:
        return {"status": "error", "stderr": "args required", "exit_code": -1, "stdout": ""}
    path = args.get("path")
    content = args.get("content")
    if path is None:
        return {"status": "error", "stderr": "path required", "exit_code": -1, "stdout": ""}
    try:
        safe_path = _safe_path(str(path).strip())
    except ValueError as e:
        return {"status": "error", "stderr": str(e), "exit_code": -1, "stdout": ""}
    raw = content if isinstance(content, bytes) else (content or "").encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    # Pass content via env to avoid any shell interpretation
    argv = ["env", f"B64={b64}", "sh", "-c", f"echo \"$B64\" | base64 -d > {shlex.quote(safe_path)}"]
    return run_in_container_argv(container, argv)


def _rewrite_diff_path_header(line: str, prefix: str) -> str:
    if not line.startswith(prefix):
        return line
    body = line[len(prefix) :]
    stripped = body.lstrip()
    if stripped.startswith("/dev/null"):
        return line
    tab_suffix = ""
    main = body
    if "\t" in body:
        main, rest = body.split("\t", 1)
        tab_suffix = "\t" + rest
    path_raw = main.strip()
    if path_raw.startswith("a/") or path_raw.startswith("b/"):
        path_raw = path_raw[2:]
    if not path_raw:
        return line
    try:
        full = _safe_path(path_raw)
    except ValueError:
        return line
    return prefix + full + tab_suffix


def _normalize_unified_diff_paths(patch: str) -> str:
    out: list[str] = []
    for line in patch.splitlines():
        if line.startswith("--- "):
            out.append(_rewrite_diff_path_header(line, "--- "))
        elif line.startswith("+++ "):
            out.append(_rewrite_diff_path_header(line, "+++ "))
        else:
            out.append(line)
    if patch.endswith("\n"):
        return "\n".join(out) + "\n"
    return "\n".join(out)


def _path_from_diff_header(line: str, prefix: str) -> str:
    rest = line[len(prefix) :]
    path_part = rest.split("\t", 1)[0].strip()
    if path_part.startswith("a/") or path_part.startswith("b/"):
        path_part = path_part[2:]
    return path_part


def apply_patch(container: str, patch_text: str | None) -> dict[str, Any]:
    """
    Apply a unified diff patch under /workspace.
    Validates structure; uses git apply --check then apply, with patch(1) fallback.
    """
    if patch_text is None or not str(patch_text).strip():
        return {"status": "error", "stderr": "patch text required", "exit_code": -1, "stdout": ""}
    patch = str(patch_text)
    lines = patch.splitlines()
    if not any(l.startswith("--- ") for l in lines) or not any(l.startswith("+++ ") for l in lines):
        return {
            "status": "error",
            "stderr": "Invalid unified diff: missing ---/+++ headers",
            "exit_code": -1,
            "stdout": "",
        }
    if not any(l.startswith("@@") for l in lines):
        return {
            "status": "error",
            "stderr": "Invalid unified diff: missing @@ hunk markers",
            "exit_code": -1,
            "stdout": "",
        }

    patch = _normalize_unified_diff_paths(patch)
    lines = patch.splitlines()

    minus_h = [l for l in lines if l.startswith("--- ")]
    plus_h = [l for l in lines if l.startswith("+++ ")]
    if not minus_h or not plus_h:
        return {"status": "error", "stderr": "Invalid patch headers", "exit_code": -1, "stdout": ""}

    target = _path_from_diff_header(plus_h[0], "+++ ")
    if target == "/dev/null":
        return {
            "status": "error",
            "stderr": "apply_patch does not support creating or deleting files in this runtime",
            "exit_code": -1,
            "stdout": "",
        }
    try:
        _safe_path(target)
    except ValueError as e:
        return {"status": "error", "stderr": f"Unsafe patch target: {e}", "exit_code": -1, "stdout": ""}

    b64 = base64.b64encode(patch.encode("utf-8")).decode("ascii")
    write_patch = run_in_container_argv(
        container,
        ["env", f"B64={b64}", "sh", "-c", "echo \"$B64\" | base64 -d > /tmp/agent_patch.diff"],
    )
    if write_patch.get("exit_code") != 0:
        return write_patch

    check = run_in_container_argv(container, ["git", "apply", "--check", "/tmp/agent_patch.diff"])
    if check.get("exit_code") == 0:
        apply_result = run_in_container_argv(container, ["git", "apply", "/tmp/agent_patch.diff"])
        if apply_result.get("exit_code") != 0:
            return {
                "status": "error",
                "stderr": apply_result.get("stderr", "git apply failed"),
                "stdout": apply_result.get("stdout", "") or "git apply failed after successful --check.",
                "exit_code": apply_result.get("exit_code", 1),
            }
        return {
            "status": "success",
            "stdout": f"Patch applied via git apply to {target}",
            "stderr": "",
            "exit_code": 0,
            "target": target,
            "method": "git_apply",
        }

    git_err = (check.get("stderr") or "") + (check.get("stdout") or "")
    last_patch_err = ""
    for strip in ("1", "0"):
        pr = run_in_container_argv(
            container,
            [
                "patch",
                f"-p{strip}",
                "-d",
                "/workspace",
                "-i",
                "/tmp/agent_patch.diff",
                "--forward",
                "--batch",
            ],
        )
        if pr.get("exit_code") == 0:
            return {
                "status": "success",
                "stdout": f"Patch applied via patch -p{strip} to {target}",
                "stderr": "",
                "exit_code": 0,
                "target": target,
                "method": f"patch_p{strip}",
            }
        last_patch_err = (pr.get("stderr") or "") + (pr.get("stdout") or "")

    return {
        "status": "error",
        "stderr": (
            "Could not apply patch. git apply --check failed:\n"
            f"{git_err.strip()}\n"
            "---\n"
            "patch command also failed:\n"
            f"{last_patch_err.strip()}"
        ),
        "stdout": "Patch rejected: re-read the file and produce an accurate unified diff (paths under /workspace).",
        "exit_code": check.get("exit_code", 1),
    }