import asyncio
import logging
import httpx
import requests
from requests import HTTPError as RequestsHTTPError

from app.config.settings import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    GROQ_URL,
    LLM_REQUEST_TIMEOUT,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
)

logger = logging.getLogger(__name__)


def _fmt_api_error(status_code: int, body_preview: str, *, provider_name: str = "LLM provider") -> str:
    hint = ""
    if status_code == 401:
        hint = f" Provide a valid API key for {provider_name}."
    return f"{provider_name} API error: HTTP {status_code}{hint}: {body_preview}"


class OllamaError(Exception):
    pass


def _abort_if_task_killed(task: object | None) -> None:
    if task is not None and getattr(task, "kill_requested", False):
        raise asyncio.CancelledError("task kill_requested")


def _is_rate_limited(status_code: int, body_preview: str) -> bool:
    if status_code == 429:
        return True
    txt = (body_preview or "").lower()
    return (
        "rate limit" in txt
        or "too many requests" in txt
        or "quota exceeded" in txt
        or "daily limit" in txt
    )


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


def _chat_url(full_url: str, base_default: str) -> str:
    if "/chat/completions" in full_url:
        return full_url
    return f"{base_default.rstrip('/')}/chat/completions"


def _mask_api_key(api_key: str) -> str:
    return api_key[-4:] if len(api_key) > 4 else "****"


def _is_openrouter_target(llm_override: dict | None) -> bool:
    base = str((llm_override or {}).get("base_url") or "").lower()
    return "openrouter" in base


def _fallback_to_openrouter_sync(*, prompt: str, timeout: float, task: object | None) -> str:
    if not OPENROUTER_API_KEY:
        raise OllamaError(
            "LLM rate limited and OPENROUTER_API_KEY is not set for fallback (set it in .env)."
        )
    logger.warning(
        "[llm] Primary provider rate limited — falling back to OpenRouter (%s)",
        OPENROUTER_MODEL,
    )
    try:
        data = _call_provider_sync(
            prompt=prompt,
            timeout=timeout,
            model=OPENROUTER_MODEL,
            default_api_key=OPENROUTER_API_KEY,
            default_url=OPENROUTER_URL,
            default_base_url=OPENROUTER_BASE_URL,
            provider_name="OpenRouter",
        )
    except RequestsHTTPError as e2:
        resp2 = e2.response
        raise OllamaError(
            _fmt_api_error(
                resp2.status_code if resp2 is not None else 0,
                (resp2.text[:500] if resp2 is not None else str(e2)),
                provider_name="OpenRouter",
            )
        ) from e2
    _accumulate_task_usage(task, data)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e3:
        raise OllamaError(f"Unexpected OpenRouter response format: {data}") from e3


async def _fallback_to_openrouter_async(
    *, prompt: str, timeout: float, task: object | None
) -> str:
    if not OPENROUTER_API_KEY:
        raise OllamaError(
            "LLM rate limited and OPENROUTER_API_KEY is not set for fallback (set it in .env)."
        )
    logger.warning(
        "[llm] Primary provider rate limited — falling back to OpenRouter (%s)",
        OPENROUTER_MODEL,
    )
    _abort_if_task_killed(task)
    try:
        data = await _call_provider_async(
            prompt=prompt,
            task=task,
            timeout=timeout,
            model=OPENROUTER_MODEL,
            default_api_key=OPENROUTER_API_KEY,
            default_url=OPENROUTER_URL,
            default_base_url=OPENROUTER_BASE_URL,
            provider_name="OpenRouter",
        )
    except httpx.HTTPStatusError as e2:
        r2 = e2.response
        raise OllamaError(
            _fmt_api_error(
                r2.status_code if r2 is not None else 0,
                r2.text[:500] if r2 is not None else "",
                provider_name="OpenRouter",
            )
        ) from e2
    except httpx.RequestError as e2:
        raise OllamaError(f"OpenRouter request failed after rate limit: {e2}") from e2
    _accumulate_task_usage(task, data)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e3:
        raise OllamaError(f"Unexpected OpenRouter response format: {data}") from e3


def _resolve_provider_request(
    *,
    model: str,
    llm_override: dict | None = None,
    default_api_key: str,
    default_url: str,
    default_base_url: str,
) -> tuple[str, str, str]:
    if llm_override and llm_override.get("api_key"):
        api_key = str(llm_override["api_key"]).strip()
        resolved_model = str(llm_override.get("model") or model or GROQ_MODEL).strip() or GROQ_MODEL
        base_url = str(llm_override.get("base_url") or default_base_url).strip() or default_base_url
        return _chat_url(base_url, default_base_url), api_key, resolved_model
    if not default_api_key:
        raise OllamaError(
            "GROQ_API_KEY is not set. Add it to your project root .env (see .env.example)."
        )
    return _chat_url(default_url, default_base_url), default_api_key, model


async def _call_provider_async(
    *,
    prompt: str,
    task: object | None,
    timeout: float,
    model: str,
    llm_override: dict | None = None,
    default_api_key: str = GROQ_API_KEY,
    default_url: str = GROQ_URL,
    default_base_url: str = GROQ_BASE_URL,
    provider_name: str = "Groq",
    log_user_override: bool = False,
) -> dict:
    url, api_key, resolved_model = _resolve_provider_request(
        model=model,
        llm_override=llm_override,
        default_api_key=default_api_key,
        default_url=default_url,
        default_base_url=default_base_url,
    )
    if log_user_override and llm_override and llm_override.get("api_key"):
        logger.info(
            "[llm] User-provided key active - model=%s, base=%s, key=...%s",
            resolved_model,
            str(llm_override.get("base_url") or default_base_url).strip() or default_base_url,
            _mask_api_key(api_key),
        )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter" in url.lower():
        headers["HTTP-Referer"] = "https://github.com/riverdoggo/vulcan-forge"
        headers["X-Title"] = "Vulcan Forge"
    payload = {"model": resolved_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def _call_provider_sync(
    *,
    prompt: str,
    timeout: float,
    model: str,
    llm_override: dict | None = None,
    default_api_key: str = GROQ_API_KEY,
    default_url: str = GROQ_URL,
    default_base_url: str = GROQ_BASE_URL,
    provider_name: str = "Groq",
    log_user_override: bool = False,
) -> dict:
    url, api_key, resolved_model = _resolve_provider_request(
        model=model,
        llm_override=llm_override,
        default_api_key=default_api_key,
        default_url=default_url,
        default_base_url=default_base_url,
    )
    if log_user_override and llm_override and llm_override.get("api_key"):
        logger.info(
            "[llm] User-provided key active - model=%s, base=%s, key=...%s",
            resolved_model,
            str(llm_override.get("base_url") or default_base_url).strip() or default_base_url,
            _mask_api_key(api_key),
        )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter" in url.lower():
        headers["HTTP-Referer"] = "https://github.com/riverdoggo/vulcan-forge"
        headers["X-Title"] = "Vulcan Forge"
    payload = {"model": resolved_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def query_llm(
    prompt: str,
    model: str = GROQ_MODEL,
    timeout_sec: int | None = None,
    task: object | None = None,
    llm_override: dict | None = None,
) -> str:
    llm_override = llm_override or getattr(task, "llm_override", None)
    if not GROQ_API_KEY and not (llm_override and llm_override.get("api_key")):
        raise OllamaError(
            "GROQ_API_KEY is not set. Add it to your project root .env (see .env.example)."
        )

    import time

    time.sleep(2)

    timeout = float(timeout_sec if timeout_sec is not None else LLM_REQUEST_TIMEOUT)
    if llm_override and llm_override.get("api_key"):
        try:
            data = _call_provider_sync(
                prompt=prompt,
                timeout=timeout,
                model=model,
                llm_override=llm_override,
                provider_name="LLM provider",
                log_user_override=True,
            )
            _accumulate_task_usage(task, data)
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise OllamaError(f"Unexpected provider response format: {data}") from e
        except RequestsHTTPError as e:
            resp = e.response
            status = resp.status_code if resp is not None else 0
            body = (resp.text[:500] if resp is not None else "") or ""
            if _is_rate_limited(status, body) and not _is_openrouter_target(llm_override):
                return _fallback_to_openrouter_sync(prompt=prompt, timeout=timeout, task=task)
            raise OllamaError(_fmt_api_error(status, body, provider_name="LLM provider")) from e
        except requests.RequestException as e:
            raise OllamaError(f"LLM provider request failed: {e}") from e

    try:
        data = _call_provider_sync(
            prompt=prompt,
            timeout=timeout,
            model=model,
            provider_name="Groq",
        )
        _accumulate_task_usage(task, data)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise OllamaError(f"Unexpected Groq response format: {data}") from e
    except RequestsHTTPError as e:
        resp = e.response
        status = resp.status_code if resp is not None else 0
        body = (resp.text[:500] if resp is not None else "") or ""
        if _is_rate_limited(status, body):
            return _fallback_to_openrouter_sync(prompt=prompt, timeout=timeout, task=task)
        raise OllamaError(_fmt_api_error(status, body, provider_name="Groq")) from e
    except requests.RequestException as e:
        raise OllamaError(f"Groq request failed: {e}") from e


async def query_llm_async(
    prompt: str,
    *,
    task: object | None = None,
    model: str = GROQ_MODEL,
    per_request_timeout: float | None = None,
    llm_override: dict | None = None,
) -> str:
    """
    Async Groq chat completion. Respects task.kill_requested between waits.
    Cancellable: a cancelled asyncio task aborts in-flight httpx I/O.
    """
    llm_override = llm_override or getattr(task, "llm_override", None)
    if not GROQ_API_KEY and not (llm_override and llm_override.get("api_key")):
        raise OllamaError(
            "GROQ_API_KEY is not set. Add it to your project root .env (see .env.example)."
        )

    _abort_if_task_killed(task)
    await asyncio.sleep(2)
    _abort_if_task_killed(task)

    timeout = float(per_request_timeout if per_request_timeout is not None else LLM_REQUEST_TIMEOUT)
    if llm_override and llm_override.get("api_key"):
        try:
            data = await _call_provider_async(
                prompt=prompt,
                task=task,
                timeout=timeout,
                model=model,
                llm_override=llm_override,
                provider_name="LLM provider",
                log_user_override=True,
            )
            _accumulate_task_usage(task, data)
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise OllamaError(f"Unexpected provider response format: {data}") from e
        except httpx.HTTPStatusError as e:
            resp = e.response
            status = resp.status_code if resp is not None else 0
            body = resp.text[:500] if resp is not None else ""
            if _is_rate_limited(status, body) and not _is_openrouter_target(llm_override):
                return await _fallback_to_openrouter_async(prompt=prompt, timeout=timeout, task=task)
            raise OllamaError(_fmt_api_error(status, body, provider_name="LLM provider")) from e
        except httpx.RequestError as e:
            raise OllamaError(f"LLM provider request failed: {e}") from e

    try:
        data = await _call_provider_async(
            prompt=prompt,
            task=task,
            timeout=timeout,
            model=model,
            provider_name="Groq",
        )
        _accumulate_task_usage(task, data)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise OllamaError(f"Unexpected Groq response format: {data}") from e
    except httpx.HTTPStatusError as e:
        resp = e.response
        status = resp.status_code if resp is not None else 0
        body = resp.text[:500] if resp is not None else ""
        if _is_rate_limited(status, body):
            return await _fallback_to_openrouter_async(prompt=prompt, timeout=timeout, task=task)
        raise OllamaError(_fmt_api_error(status, body, provider_name="Groq")) from e
    except httpx.RequestError as e:
        raise OllamaError(f"Groq request failed: {e}") from e
