from app.llm.client import LLMChatRequest, chat_completion
from app.llm.factory import get_provider, resolve_provider_name
from app.llm.providers.openai_api import _normalize_response_format_for_openai
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
    monkeypatch.delenv("OPENROUTER_REQUIRE_PARAMETERS", raising=False)

    provider = _build_provider_preferences(request_requires_strict_parameters=True)
    assert provider["sort"] == "price"
    assert provider["data_collection"] == "deny"
    assert provider["zdr"] is True
    assert provider["require_parameters"] is True


def test_openrouter_provider_preferences_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PROVIDER_SORT", "latency")
    monkeypatch.setenv("OPENROUTER_PROVIDER_DATA_COLLECTION", "allow")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ZDR", "false")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ALLOW_FALLBACKS", "true")
    monkeypatch.setenv("OPENROUTER_REQUIRE_PARAMETERS", "false")

    provider = _build_provider_preferences(request_requires_strict_parameters=True)
    assert provider["sort"] == "latency"
    assert provider["data_collection"] == "allow"
    assert provider["zdr"] is False
    assert provider["allow_fallbacks"] is True
    assert "require_parameters" not in provider
