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


def _is_gpt5_model(model: str) -> bool:
    return (model or "").strip().lower().startswith("gpt-5")


def _normalize_response_format_for_openai_responses(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert chat-style response_format into Responses API text.format shape."""
    normalized = _normalize_response_format_for_openai(response_format)
    if not isinstance(normalized, dict):
        return None
    if normalized.get("type") == "json_schema" and isinstance(normalized.get("json_schema"), dict):
        json_schema = normalized["json_schema"]
        return {
            "format": {
                "type": "json_schema",
                "name": str(json_schema.get("name") or "response"),
                "schema": json_schema.get("schema") or {},
                "strict": bool(json_schema.get("strict", True)),
            }
        }
    return None


def _raise_for_status_with_body(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    body = response.text.strip()
    body_preview = body[:1500] if body else "<empty>"
    raise requests.HTTPError(
        f"{response.status_code} Client Error for url: {response.url}. body={body_preview}",
        response=response,
    )


def _build_token_limit_payload(model: str, max_tokens: int) -> dict[str, int]:
    """OpenAI token limit field differs across model families.

    gpt-5* models use `max_completion_tokens`, while gpt-4* chat-completions
    models continue to use `max_tokens`.
    """
    if _is_gpt5_model(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _build_temperature_payload(model: str, temperature: float) -> dict[str, float]:
    """Normalize temperature for model families with stricter constraints."""
    if _is_gpt5_model(model):
        # gpt-5 Responses path does not require explicit temperature here.
        return {}
    return {"temperature": temperature}


def _messages_to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        result.append(
            {
                "role": role,
                "content": [{"type": "input_text", "text": content}],
            }
        )
    return result


def _extract_text_from_responses_payload(response_json: dict[str, Any]) -> str:
    output_text = response_json.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    fragments: list[str] = []
    output = response_json.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                if not isinstance(chunk, dict):
                    continue
                if str(chunk.get("type") or "").strip() in {"output_text", "text"}:
                    text = chunk.get("text")
                    if isinstance(text, str) and text.strip():
                        fragments.append(text.strip())
    return "\n".join(fragments).strip()


def _finish_reason_from_responses_payload(response_json: dict[str, Any]) -> str:
    status = str(response_json.get("status") or "").strip().lower()
    if status == "completed":
        return "stop"
    if status == "incomplete":
        incomplete = response_json.get("incomplete_details")
        if isinstance(incomplete, dict):
            reason = str(incomplete.get("reason") or "").strip().lower()
            if "max_output_tokens" in reason or "max_tokens" in reason:
                return "length"
        return "incomplete"
    return ""


def _usage_from_responses_payload(response_json: dict[str, Any]) -> dict[str, Any]:
    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens_details": usage.get("input_tokens_details", {}),
        "completion_tokens_details": usage.get("output_tokens_details", {}),
    }


def _normalize_responses_to_chat_shape(response_json: dict[str, Any], request_model: str) -> dict[str, Any]:
    content = _extract_text_from_responses_payload(response_json)
    finish_reason = _finish_reason_from_responses_payload(response_json)
    return {
        "id": str(response_json.get("id") or ""),
        "model": str(response_json.get("model") or request_model),
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
            }
        ],
        "usage": _usage_from_responses_payload(response_json),
    }


def _openai_chat_completions_request(
    *,
    url: str,
    request: "LLMChatRequest",
    headers: dict[str, str],
    timeout: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": request.messages,
    }
    payload.update(_build_temperature_payload(request.model, request.temperature))
    payload.update(_build_token_limit_payload(request.model, request.max_tokens))
    normalized_response_format = _normalize_response_format_for_openai(request.response_format)
    if normalized_response_format is not None:
        payload["response_format"] = normalized_response_format
    with requests.Session() as session:
        response = session.post(url, json=payload, headers=headers, timeout=timeout)
        _raise_for_status_with_body(response)
    return response.json()


def _openai_responses_request(
    *,
    url: str,
    request: "LLMChatRequest",
    headers: dict[str, str],
    timeout: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "input": _messages_to_responses_input(request.messages),
        "max_output_tokens": request.max_tokens,
    }
    text_cfg = _normalize_response_format_for_openai_responses(request.response_format)
    if text_cfg is not None:
        payload["text"] = text_cfg
    with requests.Session() as session:
        response = session.post(url, json=payload, headers=headers, timeout=timeout)
        _raise_for_status_with_body(response)
    return _normalize_responses_to_chat_shape(response.json(), request.model)


def openai_chat_completion(request: "LLMChatRequest") -> dict[str, Any]:
    base_url = (request.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
    api_key = request.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for provider=openai.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = None if request.timeout_seconds is None or request.timeout_seconds <= 0 else request.timeout_seconds
    if _is_gpt5_model(request.model):
        return _openai_responses_request(
            url=f"{base_url}/v1/responses",
            request=request,
            headers=headers,
            timeout=timeout,
        )
    return _openai_chat_completions_request(
        url=f"{base_url}/v1/chat/completions",
        request=request,
        headers=headers,
        timeout=timeout,
    )
