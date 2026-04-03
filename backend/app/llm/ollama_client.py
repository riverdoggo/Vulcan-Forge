import asyncio
import logging
import os
from pathlib import Path

import httpx
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def _fmt_api_error(status_code: int, body_preview: str) -> str:
    hint = ""
    if status_code == 401:
        hint = " Set a valid GROQ_API_KEY in .env (https://console.groq.com)."
    return f"LLM API error: HTTP {status_code}{hint}: {body_preview}"


class OllamaError(Exception):
    pass


def _abort_if_task_killed(task: object | None) -> None:
    if task is not None and getattr(task, "kill_requested", False):
        raise asyncio.CancelledError("task kill_requested")


def _accumulate_task_usage(task: object | None, data: dict) -> None:
    if task is None:
        return
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return

    def _n(v: object) -> int:
        try:
            return int(v) if v is not None else 0
        except Exception:
            return 0

    prompt_tokens = _n(usage.get("prompt_tokens"))
    completion_tokens = _n(usage.get("completion_tokens"))
    total_tokens = _n(usage.get("total_tokens"))
    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
        return

    try:
        task.total_prompt_tokens = int(getattr(task, "total_prompt_tokens", 0) or 0) + prompt_tokens
        task.total_completion_tokens = int(getattr(task, "total_completion_tokens", 0) or 0) + completion_tokens
        if total_tokens > 0:
            task.total_tokens_used = int(getattr(task, "total_tokens_used", 0) or 0) + total_tokens
        else:
            task.total_tokens_used = (
                int(getattr(task, "total_tokens_used", 0) or 0) + prompt_tokens + completion_tokens
            )
        from app.database import save_task

        save_task(task)
    except Exception as e:
        logger.warning("Failed to persist token usage: %s", e)


def query_llm(
    prompt: str, model: str = GROQ_MODEL, timeout_sec: int | None = None, task: object | None = None
) -> str:
    if not GROQ_API_KEY:
        raise OllamaError(
            "GROQ_API_KEY is not set. Add it to your project root .env (see .env.example)."
        )

    import time

    time.sleep(2)  # rate limit buffer — 2 seconds between calls

    timeout = timeout_sec or 120
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    max_retries = 20
    response = None
    for attempt in range(max_retries):
        try:
            response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise OllamaError(f"Groq request failed: {e}") from e
        if response.status_code == 429:
            logger.warning(
                "Groq rate limit (429), waiting 5s before retry %s/%s",
                attempt + 1,
                max_retries,
            )
            time.sleep(5)
            continue
        break
    if response is None:
        raise OllamaError("Groq request returned no response")
    if not response.ok:
        raise OllamaError(_fmt_api_error(response.status_code, response.text[:500]))
    data = response.json()
    _accumulate_task_usage(task, data)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise OllamaError(f"Unexpected Groq response format: {data}") from e


async def query_llm_async(
    prompt: str,
    *,
    task: object | None = None,
    model: str = GROQ_MODEL,
    per_request_timeout: float = 120.0,
) -> str:
    """
    Async Groq chat completion. Respects task.kill_requested between waits.
    Cancellable: a cancelled asyncio task aborts in-flight httpx I/O.
    """
    if not GROQ_API_KEY:
        raise OllamaError(
            "GROQ_API_KEY is not set. Add it to your project root .env (see .env.example)."
        )

    _abort_if_task_killed(task)
    await asyncio.sleep(2)
    _abort_if_task_killed(task)

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    max_retries = 20

    async with httpx.AsyncClient() as client:
        response: httpx.Response | None = None
        for attempt in range(max_retries):
            _abort_if_task_killed(task)
            try:
                response = await client.post(
                    GROQ_URL,
                    json=payload,
                    headers=headers,
                    timeout=per_request_timeout,
                )
            except httpx.RequestError as e:
                raise OllamaError(f"Groq request failed: {e}") from e
            if response.status_code == 429:
                logger.warning(
                    "Groq rate limit (429), waiting 5s before retry %s/%s",
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(5)
                continue
            break

        if response is None:
            raise OllamaError("Groq request returned no response")
        if not response.is_success:
            raise OllamaError(_fmt_api_error(response.status_code, response.text[:500]))
        data = response.json()
        _accumulate_task_usage(task, data)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise OllamaError(f"Unexpected Groq response format: {data}") from e
