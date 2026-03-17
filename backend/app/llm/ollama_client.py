import logging
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


class OllamaError(Exception):
    pass


def query_llm(prompt: str, model: str = GROQ_MODEL, timeout_sec: int | None = None) -> str:
    if not GROQ_API_KEY:
        raise OllamaError("GROQ_API_KEY is not set")
    
    timeout = timeout_sec or 120
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    try:
        response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise OllamaError(f"Groq request failed: {e}") from e
    if not response.ok:
        raise OllamaError(f"Groq returned {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise OllamaError(f"Unexpected Groq response format: {data}") from e
