from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

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
        # Локальные личные данные не должны проходить через ENV-прокси.
        loopback = urlsplit(base_url).hostname in {'127.0.0.1', 'localhost', '::1'}
        if loopback:
            session.trust_env = False
        response = session.post(url, json=payload, timeout=timeout, allow_redirects=not loopback)
        response.raise_for_status()
        if loopback and 300 <= response.status_code < 400:
            raise ValueError('Local LLM redirects are not allowed')
    return response.json()
