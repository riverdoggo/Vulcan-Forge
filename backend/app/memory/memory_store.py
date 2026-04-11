import html
import logging
from typing import Any

from app.memory.compression import (
    caveman_compress,
    compress_directory_listing,
    compress_git_commit_output,
    strip_pytest_output,
)

logger = logging.getLogger(__name__)

# Cap tool output injected into LLM prompts (full detail remains in step transcript / logs).
_MAX_RUN_TESTS_COMBINED_CHARS = 6500
_MAX_RUN_TESTS_STDERR_CHARS = 1200


def _normalize_obs_path(p: str) -> str:
    s = str(p).strip()
    if not s:
        return ""
    if s.startswith("/workspace/"):
        return s
    if s.startswith("/"):
        return s
    return f"/workspace/{s}"


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 200 or len(text) <= max_chars:
        return text
    head = (max_chars * 55) // 100
    tail = max_chars - head - 70
    if tail < 120:
        tail = 120
        head = max(0, max_chars - tail - 70)
    omitted = len(text) - head - tail
    return (
        text[:head]
        + f"\n\n[... {omitted} characters omitted for token budget ...]\n\n"
        + text[-tail:]
    )


class MemoryStore:
    def __init__(self) -> None:
        self.goal: str | None = None
        self.history: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []

    def add_step(self, step: dict[str, Any]) -> None:
        s = dict(step)
        br = s.get("reasoning")
        if isinstance(br, str) and br:
            s["reasoning"] = caveman_compress(br, aggressive=False)
        self.history.append(s)

    def add_observation(self, obs: dict[str, Any]) -> None:
        o = dict(obs)
        tool = str(o.get("tool") or "")

        reason = o.get("reasoning")
        if isinstance(reason, str) and reason:
            o["reasoning"] = caveman_compress(reason, aggressive=False)

        if tool == "run_tests":
            o["stdout"] = strip_pytest_output(str(o.get("stdout") or ""))
        elif tool == "list_directory":
            o["stdout"] = compress_directory_listing(str(o.get("stdout") or ""))
        elif tool == "git_commit":
            o["stdout"] = compress_git_commit_output(str(o.get("stdout") or ""))
        elif tool == "write_file":
            if o.get("status") == "success" and not str(o.get("stdout") or "").strip():
                dr = o.get("diff_ratio")
                if dr is not None:
                    try:
                        o["stdout"] = f"written. diff_ratio={float(dr):.3f}"
                    except (TypeError, ValueError):
                        o["stdout"] = "written."
                else:
                    o["stdout"] = "written."

        self.observations.append(o)

    def _merge_observation_for_prompt(self, obs: dict[str, Any]) -> dict[str, Any]:
        merged = dict(obs)
        if merged.get("tool") == "run_tests":
            merged["stdout"] = merged.get("stdout") or ""
            merged["stderr"] = merged.get("stderr") or ""
            merged["stdout"] = _truncate_middle(merged["stdout"] or "", _MAX_RUN_TESTS_COMBINED_CHARS)
            merged["stderr"] = _truncate_middle(merged["stderr"] or "", _MAX_RUN_TESTS_STDERR_CHARS)
            return merged

        fs = merged.get("failure_summary") if isinstance(merged.get("failure_summary"), str) else ""
        stdout = merged.get("stdout") or ""
        if fs:
            merged["failure_summary"] = fs
            stdout = f"{fs}\n\n--- raw test output ---\n{stdout}"
        merged["stdout"] = stdout
        merged["stderr"] = merged.get("stderr") or ""
        return merged

    def _last_read_file_for_prompt(self) -> tuple[str, str] | None:
        """Most recent successful read_file (path + full stdout) so the model always retains file context."""
        for obs in reversed(self.observations):
            if not isinstance(obs, dict):
                continue
            if obs.get("tool") != "read_file":
                continue
            if int(obs.get("exit_code", -1) or -1) != 0:
                continue
            raw_inp = obs.get("input")
            if raw_inp is None:
                continue
            path = str(raw_inp).strip()
            if not path:
                continue
            body = str(obs.get("stdout") or "")
            return (_normalize_obs_path(path), body)
        return None

    def _format_observations_for_prompt(
        self,
        observations: list[dict[str, Any]],
        *,
        dedupe_read_path: str | None = None,
    ) -> str:
        """
        Last 3 observations: structured verbatim blocks for the LLM.
        Older: one line per step inside <history>.
        """
        if not observations:
            return ""

        merged_list: list[dict[str, Any]] = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            merged_list.append(self._merge_observation_for_prompt(obs))

        if not merged_list:
            return ""

        recent = merged_list[-3:]
        older = merged_list[:-3]

        parts: list[str] = []

        if older:
            summary_lines: list[str] = []
            for obs in older:
                tool = str(obs.get("tool") or obs.get("action") or "?")
                inp = str(obs.get("input") or "")
                if len(inp) > 60:
                    inp = inp[:57] + "..."

                status = str(obs.get("status") or obs.get("result_status") or "")

                tc = obs.get("test_counts") or {}
                if isinstance(tc, dict):
                    p_ct = tc.get("passed")
                    f_ct = tc.get("failed")
                    if p_ct is not None or f_ct is not None:
                        passed = int(p_ct) if p_ct is not None else 0
                        failed = int(f_ct) if f_ct is not None else 0
                        errs = tc.get("errors")
                        if errs is not None:
                            status = f"{passed}pass/{failed}fail/{int(errs)}err"
                        else:
                            status = f"{passed}pass/{failed}fail"

                dr = obs.get("diff_ratio")
                if dr is not None:
                    try:
                        status = f"{status} diff={float(dr):.2f}" if status else f"diff={float(dr):.2f}"
                    except (TypeError, ValueError):
                        pass

                line = f"[{tool}] {inp}"
                if status:
                    line += f" → {status}"
                summary_lines.append(line)

            parts.append(f"<history>\n" + "\n".join(summary_lines) + "\n</history>")

        for obs in recent:
            tool = str(obs.get("tool") or obs.get("action") or "?")
            inp = str(obs.get("input") or "")
            stdout = str(obs.get("stdout") or obs.get("result") or "")
            stderr = str(obs.get("stderr") or "")
            reasoning = str(obs.get("reasoning") or "")

            if (
                dedupe_read_path
                and tool == "read_file"
                and int(obs.get("exit_code", -1) or -1) == 0
                and _normalize_obs_path(inp) == dedupe_read_path
            ):
                stdout = "[Omitted: full file is in <latest_read_file> above.]"
                stderr = ""
            elif stderr:
                stdout = f"{stdout}\n--- stderr ---\n{stderr}" if stdout else stderr

            tool_a = html.escape(tool, quote=True)
            inp_a = html.escape(inp, quote=True)
            section = f"<step tool='{tool_a}'"
            if inp:
                section += f" input='{inp_a}'"
            section += ">"
            if reasoning:
                section += f"\nreasoning: {html.escape(reasoning)}"
            if stdout:
                section += f"\nresult:\n{html.escape(stdout)}"
            section += "\n</step>"
            parts.append(section)

        return "\n\n".join(parts)

    def build_context(self) -> dict[str, Any]:
        latest = self._last_read_file_for_prompt()
        dedupe = latest[0] if latest else None
        obs_text = self._format_observations_for_prompt(self.observations, dedupe_read_path=dedupe)
        if latest:
            path_esc = html.escape(latest[0], quote=True)
            obs_text = (
                f"<latest_read_file path={path_esc}>\n{latest[1]}\n</latest_read_file>\n\n" + obs_text
            )

        context = {
            "goal": self.goal,
            "history": self.history[-3:],
            "observations": obs_text,
        }
        logger.debug(
            "MEMORY CONTEXT (sizes): goal=%s history=%s obs_chars=%s",
            self.goal,
            len(context["history"]),
            len(obs_text),
        )
        return context
