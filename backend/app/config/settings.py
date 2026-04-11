import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

for _p in (_BACKEND_DIR / ".env", _PROJECT_ROOT / ".env"):
    if _p.exists():
        load_dotenv(_p, override=False)
load_dotenv(override=False)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-coder:free")
VULCAN_API_KEY = os.getenv("VULCAN_API_KEY", "")
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "30"))
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "agent-sandbox")
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "workspaces")
AGENT_TASK_TIMEOUT = int(os.getenv("AGENT_TASK_TIMEOUT", "600"))
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
OLLAMA_TIMEOUT_SEC = int(os.getenv("OLLAMA_TIMEOUT_SEC", "120"))
DOCKER_EXEC_TIMEOUT_SEC = int(os.getenv("DOCKER_EXEC_TIMEOUT_SEC", "60"))
REPLAY_MAX_FILES = int(os.getenv("REPLAY_MAX_FILES", "100"))

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def validate_config() -> None:
    """
    Called on startup. Exits with a clear message if required config is missing.
    """
    errors: list[str] = []

    if not GROQ_API_KEY:
        errors.append(
            "GROQ_API_KEY is not set.\n"
            "  Set it in backend/.env or project root .env, or as an environment variable.\n"
            "  Get a free key at https://console.groq.com"
        )

    if MAX_AGENT_STEPS < 1 or MAX_AGENT_STEPS > 500:
        errors.append("MAX_AGENT_STEPS must be between 1 and 500")

    if not OLLAMA_URL.startswith(("http://", "https://")):
        errors.append("OLLAMA_URL must be http or https")

    if OLLAMA_TIMEOUT_SEC < 5 or OLLAMA_TIMEOUT_SEC > 600:
        errors.append("OLLAMA_TIMEOUT_SEC must be between 5 and 600")

    if REPLAY_MAX_FILES < 1:
        errors.append("REPLAY_MAX_FILES must be >= 1")

    if errors:
        print("\n" + "=" * 60)
        print("VULCAN FORGE — STARTUP CONFIGURATION ERROR")
        print("=" * 60)
        for e in errors:
            print(f"\n  [X] {e}")
        print("\n  Copy backend/.env.example to backend/.env and fill in values.")
        print("=" * 60 + "\n")
        sys.exit(1)

    print(
        f"Config OK - model={GROQ_MODEL}, sandbox={SANDBOX_IMAGE}, "
        f"max_steps={MAX_AGENT_STEPS}, timeout={AGENT_TASK_TIMEOUT}s, "
        f"auth={'enabled' if VULCAN_API_KEY else 'disabled'}",
        flush=True,
    )
