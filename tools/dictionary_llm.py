"""Общий вызов моделей словаря с локальной проверкой JSON Schema."""

from __future__ import annotations

import copy
import os
from typing import Any
from collections.abc import Callable

from jsonschema import Draft202012Validator

from app.llm.client import LLMChatRequest, LLMResponseError, chat_completion
from app.llm.factory import resolve_provider_name
from tools.llm_artifact_cache import ArtifactCache, input_hash
from tools.taxonomy_extract import extract_json_object


def _provider_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Nemo providers поддерживают grammar subset; полную schema проверяем локально."""
    result = copy.deepcopy(schema)

    def remove_unsupported(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("uniqueItems", None)
            for value in node.values():
                remove_unsupported(value)
        elif isinstance(node, list):
            for value in node:
                remove_unsupported(value)

    remove_unsupported(result)
    return result


def validated_completion(
    *, provider: str, model: str, messages: list[dict[str, str]],
    schema: dict[str, Any], cache: ArtifactCache,
    timeout_seconds: int = 120, max_tokens: int = 6000,
    validate: Callable[[dict[str, Any]], None] | None = None,
    base_url: str | None = None, api_key: str | None = None,
    metrics: dict | None = None,
) -> dict[str, Any]:
    provider = resolve_provider_name(provider)
    if provider == "openrouter" and (model.endswith(":free") or model == "openrouter/free"):
        raise ValueError("Dictionary pipeline requires a paid OpenRouter model")
    url_env = "LLM_BASE_URL" if provider == "llama_cpp" else f"{provider.upper()}_BASE_URL"
    key = input_hash({"provider": provider, "model": model, "messages": messages,
                      "base_url": base_url or os.getenv(url_env, ""),
                      "schema": schema, "max_tokens": max_tokens})
    payload = cache.get(key)

    def validate_payload(result: dict[str, Any]) -> None:
        Draft202012Validator(schema).validate(result)
        if validate is not None:
            try:
                validate(result)
            except ValueError as exc:
                raise LLMResponseError(str(exc)) from exc

    if payload is None:
        if metrics is not None:
            metrics['api_calls'] = metrics.get('api_calls', 0) + 1
        response = chat_completion(LLMChatRequest(
            provider=provider, model=model, messages=messages,
            timeout_seconds=timeout_seconds, max_tokens=max_tokens,
            base_url=base_url or "", api_key=api_key,
            response_format={"type": "json_object", "schema": _provider_schema(schema)},
        ))
        if metrics is not None:
            for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                value = (response.usage or {}).get(field)
                if type(value) is int and value >= 0:
                    metrics[field] = metrics.get(field, 0) + value
        if response.finish_reason != "stop":
            raise LLMResponseError("Incomplete dictionary response")
        try:
            payload = extract_json_object(response.content)
        except ValueError as exc:
            raise LLMResponseError(str(exc)) from exc
        validate_payload(payload)
        cache.put(key, payload)
    else:
        if metrics is not None:
            metrics['cache_hits'] = metrics.get('cache_hits', 0) + 1
        validate_payload(payload)
    return payload
