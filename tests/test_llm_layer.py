from app.llm.client import LLMChatRequest, _build_log_event, chat_completion
from app.llm.factory import get_provider, resolve_provider_name
from app.llm.providers.openai_api import (
    _build_temperature_payload,
    _build_token_limit_payload,
    _finish_reason_from_responses_payload,
    _is_gpt5_model,
    _messages_to_responses_input,
    _normalize_response_format_for_openai,
    _normalize_response_format_for_openai_responses,
    _normalize_responses_to_chat_shape,
)
from app.llm.providers.openrouter_api import (
    _build_provider_preferences,
    _normalize_response_format_for_openrouter,
)


def test_resolve_provider_name_aliases():
    assert resolve_provider_name("llama_cpp") == "llama_cpp"
    assert resolve_provider_name("local") == "llama_cpp"
    assert resolve_provider_name("openai_api") == "openai"
    assert resolve_provider_name("openrouter_api") == "openrouter"


def test_get_provider_openrouter_available():
    provider = get_provider("openrouter")
    assert callable(provider)


def test_chat_completion_with_stub_provider(monkeypatch):
    def _stub_provider(_: LLMChatRequest) -> dict:
        return {
            "id": "chatcmpl-test",
            "model": "stub-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": '{"key":"default","target_role":"Engineer","summary":"ok","skills":[],"tools":[],"stack":[],"experience":"","seniority":"mid","industries":[],"locations":[],"languages":[]}',
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "timings": {"prompt_ms": 1.2},
        }

    monkeypatch.setattr("app.llm.client.get_provider", lambda _: _stub_provider)
    request = LLMChatRequest(
        provider="llama_cpp",
        model="stub",
        timeout_seconds=10,
        messages=[{"role": "user", "content": "x"}],
    )
    result = chat_completion(request)
    assert result.finish_reason == "stop"
    assert result.response_id == "chatcmpl-test"
    assert result.model == "stub-model"
    assert result.content.startswith('{"key":"default"')
    assert result.usage["completion_tokens"] == 20


def test_chat_completion_logs_structured_success(monkeypatch, caplog):
    caplog.set_level("INFO", logger="llm.client")

    def _stub_provider(_: LLMChatRequest) -> dict:
        return {
            "id": "chatcmpl-ok",
            "model": "stub-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok":true}'},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            "timings": {"predicted_ms": 4.2},
        }

    monkeypatch.setattr("app.llm.client.get_provider", lambda _: _stub_provider)
    request = LLMChatRequest(
        provider="llama_cpp",
        model="stub",
        timeout_seconds=10,
        messages=[{"role": "user", "content": "secret resume content"}],
        log_events=True,
    )
    chat_completion(request)
    assert any('"status": "ok"' in rec.message for rec in caplog.records)
    # We only log message count, never message content.
    assert not any("secret resume content" in rec.message for rec in caplog.records)


def test_chat_completion_logs_structured_error(monkeypatch, caplog):
    caplog.set_level("INFO", logger="llm.client")

    def _failing_provider(_: LLMChatRequest) -> dict:
        raise RuntimeError("network unavailable")

    monkeypatch.setattr("app.llm.client.get_provider", lambda _: _failing_provider)
    request = LLMChatRequest(
        provider="llama_cpp",
        model="stub",
        timeout_seconds=10,
        messages=[{"role": "user", "content": "x"}],
        log_events=True,
    )
    try:
        chat_completion(request)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected RuntimeError")
    assert any('"status": "error"' in rec.message for rec in caplog.records)
    assert any('"error_type": "RuntimeError"' in rec.message for rec in caplog.records)


def test_build_log_event_for_openai_gpt5_uses_responses_fields():
    request = LLMChatRequest(
        provider="openai",
        model="gpt-5-nano",
        timeout_seconds=10,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=321,
        temperature=0.0,
    )
    event = _build_log_event(
        request=request,
        status="ok",
        started_at="2026-03-17T00:00:00+00:00",
        elapsed_ms=12.3,
        response_json={"id": "resp_1", "model": "gpt-5-nano", "choices": [{"finish_reason": "stop"}]},
    )
    assert event["api_mode"] == "responses"
    assert event["temperature_mode"] == "provider_default"
    assert event["max_completion_tokens"] == 321
    assert "temperature" not in event
    assert "max_tokens" not in event


def test_build_log_event_for_non_gpt5_keeps_chat_completion_fields():
    request = LLMChatRequest(
        provider="openai",
        model="gpt-4o-mini",
        timeout_seconds=10,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=321,
        temperature=0.0,
    )
    event = _build_log_event(
        request=request,
        status="ok",
        started_at="2026-03-17T00:00:00+00:00",
        elapsed_ms=12.3,
        response_json={"id": "resp_1", "model": "gpt-4o-mini", "choices": [{"finish_reason": "stop"}]},
    )
    assert event["api_mode"] == "chat_completions"
    assert event["temperature"] == 0.0
    assert event["max_tokens"] == 321
    assert "max_completion_tokens" not in event


def test_openai_response_format_conversion_from_llama_style_schema():
    response_format = {
        "type": "json_object",
        "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    normalized = _normalize_response_format_for_openai(response_format)
    assert normalized["type"] == "json_schema"
    assert normalized["json_schema"]["name"] == "profile_response"
    assert normalized["json_schema"]["strict"] is True
    assert normalized["json_schema"]["schema"]["type"] == "object"


def test_openai_token_payload_for_gpt4_models_uses_max_tokens():
    payload = _build_token_limit_payload("gpt-4o-mini", 321)
    assert payload == {"max_tokens": 321}


def test_openai_token_payload_for_gpt5_models_uses_max_completion_tokens():
    payload = _build_token_limit_payload("gpt-5-nano", 321)
    assert payload == {"max_completion_tokens": 321}


def test_openai_temperature_payload_for_gpt4_models_keeps_value():
    payload = _build_temperature_payload("gpt-4o-mini", 0.0)
    assert payload == {"temperature": 0.0}


def test_openai_temperature_payload_for_gpt5_models_uses_default_one():
    payload = _build_temperature_payload("gpt-5-nano", 0.0)
    assert payload == {}


def test_openai_gpt5_detector():
    assert _is_gpt5_model("gpt-5-nano")
    assert _is_gpt5_model("gpt-5")
    assert not _is_gpt5_model("gpt-4o-mini")


def test_messages_to_responses_input_shape():
    payload = _messages_to_responses_input([{"role": "user", "content": "ping"}])
    assert payload == [{"role": "user", "content": [{"type": "input_text", "text": "ping"}]}]


def test_openai_responses_format_conversion_from_json_schema():
    response_format = {
        "type": "json_object",
        "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    normalized = _normalize_response_format_for_openai_responses(response_format)
    assert normalized is not None
    assert normalized["format"]["type"] == "json_schema"
    assert normalized["format"]["name"] == "profile_response"
    assert normalized["format"]["strict"] is True
    assert normalized["format"]["schema"]["type"] == "object"


def test_finish_reason_from_responses_payload():
    assert _finish_reason_from_responses_payload({"status": "completed"}) == "stop"
    assert (
        _finish_reason_from_responses_payload(
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        )
        == "length"
    )


def test_normalize_responses_to_chat_shape_extracts_output_text():
    raw = {
        "id": "resp_1",
        "model": "gpt-5-nano",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "{\"ok\":true}"}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    normalized = _normalize_responses_to_chat_shape(raw, "gpt-5-nano")
    assert normalized["id"] == "resp_1"
    assert normalized["choices"][0]["finish_reason"] == "stop"
    assert normalized["choices"][0]["message"]["content"] == "{\"ok\":true}"
    assert normalized["usage"]["prompt_tokens"] == 10
    assert normalized["usage"]["completion_tokens"] == 20


def test_openrouter_response_format_conversion_from_llama_style_schema():
    response_format = {
        "type": "json_object",
        "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    normalized = _normalize_response_format_for_openrouter(response_format)
    assert normalized["type"] == "json_schema"
    assert normalized["json_schema"]["name"] == "profile_response"
    assert normalized["json_schema"]["strict"] is True
    assert normalized["json_schema"]["schema"]["type"] == "object"


def test_openrouter_provider_preferences_defaults(monkeypatch):
    monkeypatch.delenv("OPENROUTER_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_DATA_COLLECTION", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ZDR", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ONLY", raising=False)
    monkeypatch.delenv("OPENROUTER_REQUIRE_PARAMETERS", raising=False)

    provider = _build_provider_preferences(request_requires_strict_parameters=True)
    assert len(provider) == 1
    assert provider["require_parameters"] is True


def test_openrouter_provider_preferences_neutral_when_not_requested(monkeypatch):
    monkeypatch.delenv("OPENROUTER_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_DATA_COLLECTION", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ZDR", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ONLY", raising=False)
    monkeypatch.delenv("OPENROUTER_REQUIRE_PARAMETERS", raising=False)

    provider = _build_provider_preferences(request_requires_strict_parameters=False)
    assert provider == {}


def test_openrouter_provider_preferences_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER_SORT", "latency")
    monkeypatch.setenv("OPENROUTER_PROVIDER_DATA_COLLECTION", "allow")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ZDR", "false")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS", "true")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "deepinfra, together")
    monkeypatch.setenv("OPENROUTER_REQUIRE_PARAMETERS", "false")

    provider = _build_provider_preferences(request_requires_strict_parameters=True)
    assert provider["sort"] == "latency"
    assert provider["data_collection"] == "allow"
    assert provider["zdr"] is False
    assert provider["allow_fallbacks"] is True
    assert provider["order"] == ["deepinfra", "together"]
    assert "require_parameters" not in provider


def test_openrouter_provider_only_takes_precedence_over_order(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "deepinfra, together")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ONLY", "novita")

    provider = _build_provider_preferences(request_requires_strict_parameters=False)
    assert provider["only"] == ["novita"]
    assert "order" not in provider
