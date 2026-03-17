import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load from project root .env if present (do not commit .env; use .env.example as template)
_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()  # fallback: cwd or env

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "workspaces")
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "agent-sandbox")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
OLLAMA_TIMEOUT_SEC = int(os.getenv("OLLAMA_TIMEOUT_SEC", "120"))
DOCKER_EXEC_TIMEOUT_SEC = int(os.getenv("DOCKER_EXEC_TIMEOUT_SEC", "60"))
REPLAY_MAX_FILES = int(os.getenv("REPLAY_MAX_FILES", "100"))


def validate_config() -> None:
    """Validate required config; raises ValueError if invalid."""
    if MAX_AGENT_STEPS < 1 or MAX_AGENT_STEPS > 500:
        raise ValueError("MAX_AGENT_STEPS must be between 1 and 500")
    if not OLLAMA_URL.startswith(("http://", "https://")):
        raise ValueError("OLLAMA_URL must be http or https")
    if OLLAMA_TIMEOUT_SEC < 5 or OLLAMA_TIMEOUT_SEC > 600:
        raise ValueError("OLLAMA_TIMEOUT_SEC must be between 5 and 600")
    if REPLAY_MAX_FILES < 1:
        raise ValueError("REPLAY_MAX_FILES must be >= 1")