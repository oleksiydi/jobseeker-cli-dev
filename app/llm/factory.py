from __future__ import annotations

import os
from typing import Any, Callable

from app.llm.providers.llama_cpp import llama_cpp_chat_completion
from app.llm.providers.openai_api import openai_chat_completion
from app.llm.providers.openrouter_api import openrouter_chat_completion

ProviderFn = Callable[[Any], dict]


def resolve_provider_name(explicit_provider: str | None = None) -> str:
    provider = (explicit_provider or os.getenv("LLM_PROVIDER") or "llama_cpp").strip().lower()
    if provider in {"llama_cpp", "local"}:
        return "llama_cpp"
    if provider in {"openai", "openai_api"}:
        return "openai"
    if provider in {"openrouter", "openrouter_api"}:
        return "openrouter"
    raise ValueError(f"Unsupported LLM provider: {provider}")


def get_provider(provider_name: str) -> ProviderFn:
    normalized = resolve_provider_name(provider_name)
    if normalized == "llama_cpp":
        return llama_cpp_chat_completion
    if normalized == "openai":
        return openai_chat_completion
    if normalized == "openrouter":
        return openrouter_chat_completion
    raise ValueError(f"Unsupported LLM provider: {provider_name}")
