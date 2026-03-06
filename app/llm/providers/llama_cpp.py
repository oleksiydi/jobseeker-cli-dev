from __future__ import annotations

import os
from typing import Any

import requests

if False:  # pragma: no cover
    from app.llm.client import LLMChatRequest


def llama_cpp_chat_completion(request: "LLMChatRequest") -> dict[str, Any]:
    base_url = (request.base_url or os.getenv("LLM_BASE_URL") or "http://127.0.0.1:8081").rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": request.model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "messages": request.messages,
    }
    if request.response_format is not None:
        payload["response_format"] = request.response_format
    if request.chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = request.chat_template_kwargs

    timeout = None if request.timeout_seconds is None or request.timeout_seconds <= 0 else request.timeout_seconds
    with requests.Session() as session:
        response = session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    return response.json()
