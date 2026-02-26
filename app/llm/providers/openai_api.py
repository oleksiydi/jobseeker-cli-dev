from __future__ import annotations

import os
from typing import Any

import requests

if False:  # pragma: no cover
    from app.llm.client import LLMChatRequest


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
    if request.response_format is not None:
        payload["response_format"] = request.response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with requests.Session() as session:
        response = session.post(url, json=payload, headers=headers, timeout=request.timeout_seconds)
        response.raise_for_status()
    return response.json()

