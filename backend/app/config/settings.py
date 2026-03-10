import os
from dotenv import load_dotenv

load_dotenv()

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "workspaces")
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "agent-sandbox")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", 30))