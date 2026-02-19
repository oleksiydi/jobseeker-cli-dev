from __future__ import annotations

from app.core.matching_config import resolve_matching_config


def test_resolve_matching_config_reads_defaults() -> None:
    cfg = {
        "matching": {
            "enabled": False,
            "embeddings": {"model_name": "intfloat/multilingual-e5-large", "vector_size": 1024},
            "qdrant": {"url": "http://127.0.0.1:6333"},
        }
    }

    resolved = resolve_matching_config(cfg)

    assert resolved["enabled"] is False
    assert resolved["embeddings"]["model_name"] == "intfloat/multilingual-e5-large"
    assert resolved["qdrant"]["url"] == "http://127.0.0.1:6333"


def test_resolve_matching_config_applies_env_overrides(monkeypatch) -> None:
    cfg = {
        "matching": {
            "enabled": False,
            "retrieval": {"top_k": 100},
            "qdrant": {"url": "http://127.0.0.1:6333"},
        }
    }
    monkeypatch.setenv("MATCHING_ENABLED", "true")
    monkeypatch.setenv("MATCHING_RETRIEVAL_TOP_K", "250")
    monkeypatch.setenv("MATCHING_QDRANT_URL", "http://qdrant:6333")

    resolved = resolve_matching_config(cfg)

    assert resolved["enabled"] is True
    assert resolved["retrieval"]["top_k"] == 250
    assert resolved["qdrant"]["url"] == "http://qdrant:6333"

