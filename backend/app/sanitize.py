import re

MAX_GOAL_LENGTH = 2000
MAX_REPO_URL_LENGTH = 500

GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?(\.git)?/?$"
)

LOCAL_PATH_RE = re.compile(r"^[A-Za-z0-9_./ :\\\-]+$")

GOAL_STRIP_RE = re.compile(r"[`$|;&><{}]")


def sanitize_goal(goal: str) -> str:
    """
    Strip shell metacharacters and enforce length limit on task goal.
    The goal goes into LLM prompts — strip characters that could be
    used for prompt injection or shell injection.
    """
    if not goal:
        raise ValueError("Goal cannot be empty")
    goal = goal.strip()
    goal = GOAL_STRIP_RE.sub("", goal)
    if len(goal) > MAX_GOAL_LENGTH:
        goal = goal[:MAX_GOAL_LENGTH]
    if not goal:
        raise ValueError("Goal is empty after sanitization")
    return goal


def _normalize_github_ssh(url: str) -> str:
    if url.startswith("git@github.com:"):
        rest = url[len("git@github.com:") :]
        return f"https://github.com/{rest}"
    return url


def sanitize_repo_url(repo_url: str | None) -> str | None:
    """
    Validate and sanitize repo_url before passing to git clone or
    workspace copy. Reject anything that looks like a shell injection.
    """
    if not repo_url:
        return None
    repo_url = repo_url.strip()
    if len(repo_url) > MAX_REPO_URL_LENGTH:
        raise ValueError(f"repo_url too long (max {MAX_REPO_URL_LENGTH} chars)")

    if repo_url.startswith(("http://", "https://")):
        if not repo_url.startswith("https://github.com/"):
            raise ValueError(
                "Invalid repo_url: only https://github.com/ URLs are allowed for HTTP(S) remotes"
            )
        normalized = repo_url.rstrip("/")
        if not GITHUB_URL_RE.match(normalized):
            raise ValueError(f"Invalid GitHub URL format: {repo_url}")
        return repo_url

    if repo_url.startswith("git@github.com:"):
        normalized = _normalize_github_ssh(repo_url).rstrip("/")
        if not GITHUB_URL_RE.match(normalized):
            raise ValueError(f"Invalid GitHub URL format: {repo_url}")
        return repo_url

    if ".." in repo_url:
        raise ValueError("repo_url must not contain '..'")

    if not LOCAL_PATH_RE.match(repo_url):
        raise ValueError(
            "repo_url contains invalid characters. "
            "Must be a GitHub HTTPS / SSH URL or a local filesystem path."
        )
    return repo_url
