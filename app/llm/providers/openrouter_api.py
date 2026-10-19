from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if False:  # pragma: no cover
    from app.llm.client import LLMChatRequest


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv_list(name: str) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return []
    items = [item.strip() for item in raw.split(",")]
    return [item for item in items if item]


def _build_provider_preferences(*, request_requires_strict_parameters: bool) -> dict[str, Any]:
    provider_payload: dict[str, Any] = {}

    sort = (os.getenv("OPENROUTER_PROVIDER_SORT") or "").strip()
    if sort:
        provider_payload["sort"] = sort

    data_collection = (os.getenv("OPENROUTER_PROVIDER_DATA_COLLECTION") or "").strip()
    if data_collection:
        provider_payload["data_collection"] = data_collection

    zdr_raw = os.getenv("OPENROUTER_PROVIDER_ZDR")
    if zdr_raw is not None:
        provider_payload["zdr"] = _env_flag("OPENROUTER_PROVIDER_ZDR", False)

    allow_fallbacks_raw = os.getenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS")
    if allow_fallbacks_raw is not None:
        provider_payload["allow_fallbacks"] = allow_fallbacks_raw.strip().lower() in {"1", "true", "yes", "on"}

    provider_only = _env_csv_list("OPENROUTER_PROVIDER_ONLY")
    provider_order = _env_csv_list("OPENROUTER_PROVIDER_ORDER")
    if provider_only:
        provider_payload["only"] = provider_only
    elif provider_order:
        provider_payload["order"] = provider_order

    if request_requires_strict_parameters and _env_flag("OPENROUTER_REQUIRE_PARAMETERS", True):
        provider_payload["require_parameters"] = True

    return provider_payload


def _normalize_response_format_for_openrouter(response_format: dict[str, Any] | None) -> dict[str, Any] | None:
    if response_format is None:
        return None
    if not isinstance(response_format, dict):
        return response_format
    # Keep parity with OpenAI-compatible schema format expected by OpenRouter.
    if response_format.get("type") == "json_object" and isinstance(response_format.get("schema"), dict):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "profile_response",
                "strict": True,
                "schema": response_format["schema"],
            },
        }
    return response_format


def _raise_for_status_with_body(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    raise requests.HTTPError(
        f"OpenRouter HTTP status {response.status_code}",
        response=response,
    )


@lru_cache(maxsize=1)
def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1, allowed_methods={"POST"},
                  status_forcelist=(429, 500, 502, 503, 504), raise_on_status=False,
                  respect_retry_after_header=False, backoff_max=5)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def openrouter_chat_completion(request: "LLMChatRequest") -> dict[str, Any]:
    base_url = (request.base_url or os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    api_key = request.api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for provider=openrouter.")

    url = f"{base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": request.model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "messages": request.messages,
    }
    normalized_response_format = _normalize_response_format_for_openrouter(request.response_format)
    strict_parameters = False
    if normalized_response_format is not None:
        payload["response_format"] = normalized_response_format
        # Fail fast when downstream provider does not support requested parameters.
        # This avoids silent fallback where schema is ignored and output becomes non-JSON text.
        strict_parameters = True

    provider_preferences = _build_provider_preferences(
        request_requires_strict_parameters=strict_parameters,
    )
    if provider_preferences:
        payload["provider"] = provider_preferences

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    title = os.getenv("OPENROUTER_X_TITLE", "").strip()
    if title:
        headers["X-Title"] = title

    timeout = None if request.timeout_seconds is None or request.timeout_seconds <= 0 else request.timeout_seconds
    if timeout is None:
        raise ValueError("OpenRouter requests require a positive timeout")
    # Повторно используем HTTP-соединение; retry и задержка ограничены.
    response = _session().post(url, json=payload, headers=headers, timeout=timeout)
    _raise_for_status_with_body(response)
    result = response.json()
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        code = result["error"].get("code", "unknown")
        raise ValueError(f"OpenRouter upstream error code {code}")
    return result
