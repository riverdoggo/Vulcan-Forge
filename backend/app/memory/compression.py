"""
Token-oriented compression for LLM context (observations, reasoning).
Does not replace verbatim file or diff content elsewhere in the pipeline.
"""

from __future__ import annotations

import re

# Words to strip — articles, conjunctions, auxiliary verbs, filler phrases
_STRIP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "can",
    "need",
    "to",
    "of",
    "in",
    "that",
    "this",
    "it",
    "we",
    "you",
    "they",
    "i",
    "my",
    "our",
    "your",
    "its",
    "there",
    "here",
    "so",
    "and",
    "but",
    "or",
    "if",
    "then",
    "also",
    "just",
    "very",
    "really",
    "actually",
    "basically",
    "simply",
    "currently",
    "already",
    "now",
    "additionally",
    "furthermore",
    "however",
    "therefore",
    "thus",
    "hence",
    "as",
    "for",
    "with",
    "on",
    "at",
    "by",
    "from",
    "about",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "each",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "not",
    "only",
    "same",
    "than",
    "too",
    "s",
    "re",
    "ll",
    "ve",
    "d",
    "m",
}

_PHRASE_MAP: list[tuple[str, str]] = [
    (r"in order to", "to"),
    (r"we need to", ""),
    (r"we have to", ""),
    (r"it is important to", ""),
    (r"make sure (?:to|that)", "ensure"),
    (r"take a look at", "check"),
    (r"the reason (?:why|for this) is", "reason:"),
    (r"at this point in time", "now"),
    (r"due to the fact that", "because"),
    (r"in the event that", "if"),
    (r"for the purpose of", "for"),
    (r"with regard to", "re:"),
    (r"as a result of", "from"),
    (r"prior to", "before"),
    (r"subsequent to", "after"),
    (r"in addition to", "plus"),
    (r"in spite of", "despite"),
    (r"a large number of", "many"),
    (r"the majority of", "most"),
    (r"on a regular basis", "regularly"),
    (r"at the present time", "now"),
    (r"in close proximity to", "near"),
    (r"in the near future", "soon"),
    (r"has the ability to", "can"),
    (r"is able to", "can"),
    (r"bootstrap:?\s*", ""),
    (r"runtime:?\s*", ""),
    (r"to advance the goal of", ""),
    (r"before making any changes", ""),
    (r"it(?:'s| is) essential (?:to|that)", ""),
    (r"the recent observations show that", ""),
    (r"this will (?:allow|enable|help)", ""),
]

_FAILURE_HEADER_RE = re.compile(r"^_{5,}\s*(.+?)\s*_{5,}$")
_FILE_LINE_RE = re.compile(r"^(\S+\.py):(\d+):\s+in\s+\S+")
_ASSERT_ERR_RE = re.compile(r"^E\s+AssertionError:\s*(.+)$")
_E_ASSERT_RE = re.compile(r"^E\s+assert\s+(.+)$")
_PROGRESS_RE = re.compile(r"^[\.\sFEsxX]+\[\s*\d+%\]\s*$")
_SUMMARY_STATS_RE = re.compile(
    r"(?P<n>\d+)\s+(?:passed|failed|error[s]?|skipped|warnings?|deselected)",
    re.IGNORECASE,
)
_SHORT_FAIL_RE = re.compile(r"^FAILED\s+(\S+)\s*-\s*(.*)$")


def caveman_compress(text: str, aggressive: bool = False) -> str:
    """
    Remove grammatical filler while preserving factual content (names, values, paths).
    """
    if not text:
        return text

    result = text.strip()
    if len(result) < 12:
        return result

    for pattern, replacement in _PHRASE_MAP:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    if aggressive:
        sentences = re.split(r"(?<=[.!?])\s+", result)
        compressed_sentences: list[str] = []
        for sentence in sentences:
            words = sentence.split()
            while words and words[0].lower() in _STRIP_WORDS:
                words = words[1:]
            if words:
                compressed_sentences.append(" ".join(words))
        result = ". ".join(compressed_sentences)

    result = re.sub(r"  +", " ", result)
    result = re.sub(r"\s+\.", ".", result)
    return result.strip()


def strip_pytest_output(stdout: str) -> str:
    """
    Keep failure signal, pytest count lines, and important error keywords.
    Preserves lines that executor._parse_pytest_counts can match (e.g. '19 passed').
    """
    if not stdout:
        return stdout

    lines = stdout.splitlines()
    result_lines: list[str] = []
    in_failure_block = False
    current_test_name: str | None = None
    current_line_num: str | None = None
    pending_assert: str | None = None

    def flush_failure(msg: str | None) -> None:
        nonlocal current_test_name, current_line_num, pending_assert, in_failure_block
        if not current_test_name:
            return
        detail = (msg or pending_assert or "").strip()
        if current_line_num:
            line = f"FAIL: {current_test_name} - line {current_line_num}"
        else:
            line = f"FAIL: {current_test_name}"
        if detail:
            line = f"{line} - {detail}"
        result_lines.append(line)
        current_test_name = None
        current_line_num = None
        pending_assert = None
        in_failure_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("=====") or stripped.startswith("-----"):
            if _SUMMARY_STATS_RE.search(stripped):
                result_lines.append(stripped)
            continue

        if (
            stripped.startswith("platform ")
            or stripped.startswith("rootdir:")
            or stripped.startswith("collected ")
            or stripped.startswith("plugins:")
            or stripped.startswith("cacheprovider")
            or stripped.startswith("cache:")
            or stripped.startswith("short test summary")
            or stripped.startswith("warnings summary")
            or stripped.startswith("-- Docs:")
            or _PROGRESS_RE.match(stripped)
        ):
            continue

        if stripped == "" and not in_failure_block:
            continue

        m_head = _FAILURE_HEADER_RE.match(stripped)
        if m_head:
            if in_failure_block and current_test_name:
                flush_failure(None)
            in_failure_block = True
            current_test_name = m_head.group(1).strip()
            current_line_num = None
            pending_assert = None
            continue

        m_file = _FILE_LINE_RE.match(stripped)
        if in_failure_block and m_file:
            current_line_num = m_file.group(2)
            continue

        m_short = _SHORT_FAIL_RE.match(stripped)
        if m_short:
            result_lines.append(f"FAIL: {m_short.group(1)} - {m_short.group(2).strip()}")
            continue

        if stripped.startswith("FAILED ") and " - " not in stripped[7:30]:
            rest = stripped.replace("FAILED ", "", 1).strip()
            result_lines.append(f"FAIL: {rest}")
            continue

        if in_failure_block and current_test_name:
            m_ae = _ASSERT_ERR_RE.match(stripped)
            if m_ae:
                flush_failure(m_ae.group(1).strip())
                continue
            m_ea = _E_ASSERT_RE.match(stripped)
            if m_ea:
                pending_assert = m_ea.group(1).strip()
                continue
            if stripped.startswith("assert "):
                pending_assert = stripped
                continue
            if re.match(r"^E\s+\+\s+where", stripped):
                continue
            if re.match(r"^E\s+", stripped):
                flush_failure(re.sub(r"^E\s+", "", stripped, count=1))
                continue
            continue

        if _SUMMARY_STATS_RE.search(stripped):
            result_lines.append(stripped)
            continue

    if current_test_name:
        flush_failure(None)

    stripped_out = "\n".join(result_lines) if result_lines else stdout

    # Preserve high-signal errors stripped tracebacks might drop
    extra: list[str] = []
    keys = ("AttributeError", "ModuleNotFoundError", "ImportError", "TypeError", "NameError")
    if stripped_out:
        for key in keys:
            if key in stdout and key not in stripped_out:
                for raw_line in lines:
                    if key in raw_line:
                        t = raw_line.strip()
                        if t and t not in extra:
                            extra.append(t)
                break
    if extra:
        stripped_out = (stripped_out + "\n" + "\n".join(extra)).strip()

    if len(stripped_out) < 10:
        return stdout
    return stripped_out


def compress_directory_listing(stdout: str) -> str:
    """Convert ls -l style lines to bare names (dirs with trailing slash)."""
    lines = stdout.strip().splitlines()
    names: list[str] = []
    for line in lines:
        if " -> " in line:
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[-1]
        if name in (".", ".."):
            continue
        is_dir = line.startswith("d")
        names.append(name + "/" if is_dir else name)
    if not names:
        return stdout
    return "\n".join(names)


def compress_git_commit_output(stdout: str) -> str:
    """Keep the [branch hash] summary line from git commit output."""
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("["):
            return s
    return stdout
