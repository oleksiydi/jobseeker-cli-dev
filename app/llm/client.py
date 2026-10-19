from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any

from app.core.logging import get_logger
from app.llm.factory import get_provider

LOGGER = get_logger("llm.client")


class LLMResponseError(ValueError):
    """Ответ модели нельзя использовать; отличаем его от ошибок доступа/API."""


@dataclass(frozen=True)
class LLMChatRequest:
    provider: str
    model: str
    messages: list[dict[str, str]]
    timeout_seconds: int | None
    temperature: float = 0.0
    max_tokens: int = 700
    base_url: str | None = None
    api_key: str | None = None
    response_format: dict[str, Any] | None = None
    chat_template_kwargs: dict[str, Any] | None = None
    log_events: bool = True


@dataclass(frozen=True)
class LLMChatResponse:
    provider: str
    model: str
    response_id: str
    finish_reason: str
    content: str
    usage: dict[str, Any]
    timings: dict[str, Any]
    started_at: str
    elapsed_ms: float
    raw_response: dict[str, Any]


def _extract_content(response_json: dict[str, Any]) -> tuple[str, str]:
    try:
        choice0 = response_json["choices"][0]
        message = choice0["message"]
        content = message["content"]
        finish_reason = str(choice0.get("finish_reason") or "")
    except (KeyError, TypeError, IndexError) as exc:
        raise LLMResponseError("Unexpected LLM response format (choices[0].message.content missing).") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("LLM response content is empty.")
    return content.strip(), finish_reason


def _build_log_event(
    *,
    request: LLMChatRequest,
    status: str,
    started_at: str,
    elapsed_ms: float,
    response_json: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    usage = {}
    timings = {}
    response_id = ""
    model = request.model
    finish_reason = ""
    if response_json is not None:
        response_id = str(response_json.get("id") or "")
        model = str(response_json.get("model") or request.model)
        usage = response_json.get("usage", {}) if isinstance(response_json.get("usage", {}), dict) else {}
        timings = response_json.get("timings", {}) if isinstance(response_json.get("timings", {}), dict) else {}
        try:
            finish_reason = str(response_json["choices"][0].get("finish_reason") or "")
        except (KeyError, TypeError, IndexError):
            finish_reason = ""

    is_openai_gpt5 = request.provider == "openai" and request.model.strip().lower().startswith("gpt-5")

    event = {
        "event": "llm_chat_completion",
        "status": status,
        "provider": request.provider,
        "model": model,
        "response_id": response_id,
        "finish_reason": finish_reason,
        "elapsed_ms": elapsed_ms,
        "started_at": started_at,
        "timeout_seconds": request.timeout_seconds,
        # Log only count of messages to avoid leaking prompt content.
        "messages_count": len(request.messages),
        "usage": usage,
        "timings": timings,
    }
    if is_openai_gpt5:
        event["api_mode"] = "responses"
        event["temperature_mode"] = "provider_default"
        event["max_completion_tokens"] = request.max_tokens
    else:
        event["api_mode"] = "chat_completions"
        event["temperature"] = request.temperature
        event["max_tokens"] = request.max_tokens
    if error is not None:
        event["error_type"] = error.__class__.__name__
        event["error_message"] = str(error)
    return event


def _log_structured(level: str, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if level == "error":
        LOGGER.error(line)
    else:
        LOGGER.info(line)


def chat_completion(request: LLMChatRequest) -> LLMChatResponse:
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    response_json: dict[str, Any] | None = None
    try:
        provider = get_provider(request.provider)
        response_json = provider(request)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        content, finish_reason = _extract_content(response_json)
        if request.log_events:
            _log_structured(
                "info",
                _build_log_event(
                    request=request,
                    status="ok",
                    started_at=started_at,
                    elapsed_ms=elapsed_ms,
                    response_json=response_json,
                ),
            )
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        if request.log_events:
            _log_structured(
                "error",
                _build_log_event(
                    request=request,
                    status="error",
                    started_at=started_at,
                    elapsed_ms=elapsed_ms,
                    response_json=response_json,
                    error=exc,
                ),
            )
        raise

    return LLMChatResponse(
        provider=request.provider,
        model=str(response_json.get("model") or request.model),
        response_id=str(response_json.get("id") or ""),
        finish_reason=finish_reason,
        content=content,
        usage=response_json.get("usage", {}) if isinstance(response_json.get("usage", {}), dict) else {},
        timings=response_json.get("timings", {}) if isinstance(response_json.get("timings", {}), dict) else {},
        started_at=started_at,
        elapsed_ms=elapsed_ms,
        raw_response=response_json,
    )
