from __future__ import annotations

import os
from typing import Any

import requests

if False:  # pragma: no cover
    from app.llm.client import LLMChatRequest


def _normalize_response_format_for_openai(response_format: dict[str, Any] | None) -> dict[str, Any] | None:
    if response_format is None:
        return None
    if not isinstance(response_format, dict):
        return response_format
    # llama.cpp uses {"type":"json_object","schema":{...}}.
    # OpenAI expects {"type":"json_schema","json_schema":{"name":...,"schema":{...}}}.
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


def openai_chat_completion(request: "LLMChatRequest") -> dict[str, Any]:
    base_url = (request.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
    api_key = request.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for provider=openai.")

    url = f"{base_url}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": request.model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "messages": request.messages,
    }
    normalized_response_format = _normalize_response_format_for_openai(request.response_format)
    if normalized_response_format is not None:
        payload["response_format"] = normalized_response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = None if request.timeout_seconds is None or request.timeout_seconds <= 0 else request.timeout_seconds
    with requests.Session() as session:
        response = session.post(url, json=payload, headers=headers, timeout=timeout)
        _raise_for_status_with_body(response)
    return response.json()
