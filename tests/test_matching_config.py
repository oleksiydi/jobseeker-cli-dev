from __future__ import annotations

import pytest

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
                "token_aware_enabled": False,
            },
        }
    }
    monkeypatch.setenv("MATCHING_RUN_MODE", "on_demand")
    monkeypatch.setenv("MATCHING_ALLOW_MANUAL_RUN", "false")
    monkeypatch.setenv("MATCHING_SEMANTIC_SIMILARITY_THRESHOLD", "0.81")
    monkeypatch.setenv("MATCHING_SEMANTIC_SENTENCE_WINDOW", "3")
    monkeypatch.setenv("MATCHING_CHUNKING_HARD_MAX_CHARS", "2600")
    monkeypatch.setenv("MATCHING_SENTENCE_SPLITTER", "spacy")
    monkeypatch.setenv("MATCHING_SPACY_MODEL", "de_core_news_md")
    monkeypatch.setenv("MATCHING_TOKEN_AWARE_ENABLED", "true")
    monkeypatch.setenv("MATCHING_TOKEN_SPLITTER", "char")
    monkeypatch.setenv("MATCHING_TOKENIZER_MODEL_NAME", "intfloat/multilingual-e5-large")
    monkeypatch.setenv("MATCHING_MAX_TOKENS_PER_CHUNK", "256")
    monkeypatch.setenv("MATCHING_OVERLAP_TOKENS", "32")
    monkeypatch.setenv("MATCHING_RERANK_WEIGHT_CHUNK_TOP1", "0.5")
    monkeypatch.setenv("MATCHING_RERANK_WEIGHT_CHUNK_TOP2", "0.3")
    monkeypatch.setenv("MATCHING_RERANK_WEIGHT_CHUNK_TOP3", "0.2")
    monkeypatch.setenv("MATCHING_RERANK_ROLE_MISMATCH_THRESHOLD", "0.1")
    monkeypatch.setenv("MATCHING_RERANK_ROLE_MISMATCH_PENALTY", "0.7")

    resolved = resolve_matching_config(cfg)

    assert resolved["run_mode"] == "on_demand"
    assert resolved["allow_manual_run"] is False
    assert resolved["chunking"]["semantic_similarity_threshold"] == 0.81
    assert resolved["chunking"]["semantic_sentence_window"] == 3
    assert resolved["chunking"]["hard_max_chunk_chars"] == 2600
    assert resolved["chunking"]["sentence_splitter"] == "spacy"
    assert resolved["chunking"]["spacy_model"] == "de_core_news_md"
    assert resolved["chunking"]["token_aware_enabled"] is True
    assert resolved["chunking"]["token_splitter"] == "char"
    assert resolved["chunking"]["tokenizer_model_name"] == "intfloat/multilingual-e5-large"
    assert resolved["chunking"]["max_tokens_per_chunk"] == 256
    assert resolved["chunking"]["overlap_tokens"] == 32
    assert resolved["rerank"]["weights"]["chunk_top1"] == 0.5
    assert resolved["rerank"]["weights"]["chunk_top2"] == 0.3
    assert resolved["rerank"]["weights"]["chunk_top3"] == 0.2
    assert resolved["rerank"]["weights"]["role_mismatch_threshold"] == 0.1
    assert resolved["rerank"]["weights"]["role_mismatch_penalty"] == 0.7


def test_resolve_matching_config_raises_on_invalid_top_k(monkeypatch) -> None:
    cfg = {"matching": {"retrieval": {"top_k": 100}}}
    monkeypatch.setenv("MATCHING_RETRIEVAL_TOP_K", "0")
    with pytest.raises(ValueError, match="retrieval.top_k"):
        resolve_matching_config(cfg)


def test_resolve_matching_config_raises_on_invalid_semantic_threshold(monkeypatch) -> None:
    cfg = {"matching": {"chunking": {"semantic_similarity_threshold": 0.72}}}
    monkeypatch.setenv("MATCHING_SEMANTIC_SIMILARITY_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="semantic_similarity_threshold"):
        resolve_matching_config(cfg)


def test_resolve_matching_config_raises_on_invalid_overlap(monkeypatch) -> None:
    cfg = {"matching": {"chunking": {"max_tokens_per_chunk": 128, "overlap_tokens": 16}}}
    monkeypatch.setenv("MATCHING_MAX_TOKENS_PER_CHUNK", "64")
    monkeypatch.setenv("MATCHING_OVERLAP_TOKENS", "64")
    with pytest.raises(ValueError, match="overlap_tokens"):
        resolve_matching_config(cfg)


def test_resolve_matching_config_raises_on_negative_qdrant_retries(monkeypatch) -> None:
    cfg = {"matching": {"qdrant": {"max_retries": 2}}}
    monkeypatch.setenv("MATCHING_QDRANT_MAX_RETRIES", "-1")
    with pytest.raises(ValueError, match="qdrant.max_retries"):
        resolve_matching_config(cfg)


def test_resolve_matching_config_raises_on_invalid_role_mismatch_penalty(monkeypatch) -> None:
    cfg = {"matching": {"rerank": {"weights": {"role_mismatch_penalty": 0.65}}}}
    monkeypatch.setenv("MATCHING_RERANK_ROLE_MISMATCH_PENALTY", "1.5")
    with pytest.raises(ValueError, match="role_mismatch_penalty"):
        resolve_matching_config(cfg)
