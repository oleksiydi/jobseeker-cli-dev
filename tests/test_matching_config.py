from __future__ import annotations

from app.core.matching_config import resolve_matching_config


def test_resolve_matching_config_reads_defaults() -> None:
    cfg = {
        "matching": {
            "enabled": False,
            "run_mode": "nightly",
            "embeddings": {"model_name": "intfloat/multilingual-e5-large", "vector_size": 1024},
            "qdrant": {"url": "http://127.0.0.1:6333"},
        }
    }

    resolved = resolve_matching_config(cfg)

    assert resolved["enabled"] is False
    assert resolved["run_mode"] == "nightly"
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


def test_resolve_matching_config_applies_run_mode_and_semantic_overrides(monkeypatch) -> None:
    cfg = {
        "matching": {
            "run_mode": "nightly",
            "allow_manual_run": True,
            "chunking": {
                "semantic_similarity_threshold": 0.72,
                "semantic_sentence_window": 2,
                "hard_max_chunk_chars": 2200,
            },
        }
    }
    monkeypatch.setenv("MATCHING_RUN_MODE", "on_demand")
    monkeypatch.setenv("MATCHING_ALLOW_MANUAL_RUN", "false")
    monkeypatch.setenv("MATCHING_SEMANTIC_SIMILARITY_THRESHOLD", "0.81")
    monkeypatch.setenv("MATCHING_SEMANTIC_SENTENCE_WINDOW", "3")
    monkeypatch.setenv("MATCHING_CHUNKING_HARD_MAX_CHARS", "2600")

    resolved = resolve_matching_config(cfg)

    assert resolved["run_mode"] == "on_demand"
    assert resolved["allow_manual_run"] is False
    assert resolved["chunking"]["semantic_similarity_threshold"] == 0.81
    assert resolved["chunking"]["semantic_sentence_window"] == 3
    assert resolved["chunking"]["hard_max_chunk_chars"] == 2600
