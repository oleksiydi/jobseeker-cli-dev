from __future__ import annotations

import os
from typing import Any

import requests

if False:  # pragma: no cover
    from app.llm.client import LLMChatRequest


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_provider_preferences(*, request_requires_strict_parameters: bool) -> dict[str, Any]:
    provider_payload: dict[str, Any] = {}
    # Prefer the cheapest route by default; can be overridden via env.
    provider_payload["sort"] = (os.getenv("OPENROUTER_PROVIDER_SORT") or "price").strip() or "price"
    # Prevent data collection by default for privacy.
    provider_payload["data_collection"] = (
        os.getenv("OPENROUTER_PROVIDER_DATA_COLLECTION") or "deny"
    ).strip() or "deny"
    # Prefer zero-retention routing by default when available.
    provider_payload["zdr"] = _env_flag("OPENROUTER_PROVIDER_ZDR", True)

    allow_fallbacks_raw = os.getenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS")
    if allow_fallbacks_raw is not None:
        provider_payload["allow_fallbacks"] = allow_fallbacks_raw.strip().lower() in {"1", "true", "yes", "on"}

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
    body = response.text.strip()
    body_preview = body[:1500] if body else "<empty>"
    raise requests.HTTPError(
        f"{response.status_code} Client Error for url: {response.url}. body={body_preview}",
        response=response,
    )


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

    payload["provider"] = _build_provider_preferences(
        request_requires_strict_parameters=strict_parameters,
    )

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

    with requests.Session() as session:
        response = session.post(url, json=payload, headers=headers, timeout=request.timeout_seconds)
        _raise_for_status_with_body(response)
    return response.json()
