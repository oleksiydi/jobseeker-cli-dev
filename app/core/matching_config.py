from __future__ import annotations

import os
from copy import deepcopy
from typing import Any


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_int(raw_value: str) -> int:
    return int(raw_value.strip())


def _parse_float(raw_value: str) -> float:
    return float(raw_value.strip())


def _set_nested(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = config
    for key in path[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[path[-1]] = value


def _get_matching_defaults(app_cfg: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(app_cfg.get("matching", {}))


def _get_nested(config: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _validate_matching_config(matching_cfg: dict[str, Any]) -> None:
    """
    Fail-fast валидация matching-конфига после ENV override.

    Важно:
    - проверяем только критичные инварианты, влияющие на стабильность pipeline;
    - используем безопасные default-значения, чтобы минимальные конфиги не ломались.
    """
    retrieval_top_k = int(_get_nested(matching_cfg, ("retrieval", "top_k"), 100))
    if retrieval_top_k <= 0:
        raise ValueError("matching.retrieval.top_k must be > 0")

    rerank_top_n = int(_get_nested(matching_cfg, ("rerank", "top_n"), 50))
    if rerank_top_n <= 0:
        raise ValueError("matching.rerank.top_n must be > 0")

    semantic_threshold = float(
        _get_nested(matching_cfg, ("chunking", "semantic_similarity_threshold"), 0.72)
    )
    if not 0.0 <= semantic_threshold <= 1.0:
        raise ValueError("matching.chunking.semantic_similarity_threshold must be in [0, 1]")

    semantic_min_sentences = int(_get_nested(matching_cfg, ("chunking", "semantic_min_sentences"), 2))
    semantic_max_sentences = int(_get_nested(matching_cfg, ("chunking", "semantic_max_sentences"), 14))
    if semantic_min_sentences < 1:
        raise ValueError("matching.chunking.semantic_min_sentences must be >= 1")
    if semantic_max_sentences < semantic_min_sentences:
        raise ValueError(
            "matching.chunking.semantic_max_sentences must be >= semantic_min_sentences"
        )

    max_tokens_per_chunk = int(_get_nested(matching_cfg, ("chunking", "max_tokens_per_chunk"), 320))
    overlap_tokens = int(_get_nested(matching_cfg, ("chunking", "overlap_tokens"), 48))
    if max_tokens_per_chunk <= 0:
        raise ValueError("matching.chunking.max_tokens_per_chunk must be > 0")
    if overlap_tokens < 0:
        raise ValueError("matching.chunking.overlap_tokens must be >= 0")
    if overlap_tokens >= max_tokens_per_chunk:
        raise ValueError(
            "matching.chunking.overlap_tokens must be < matching.chunking.max_tokens_per_chunk"
        )

    qdrant_timeout = int(_get_nested(matching_cfg, ("qdrant", "timeout_seconds"), 10))
    qdrant_max_retries = int(_get_nested(matching_cfg, ("qdrant", "max_retries"), 2))
    qdrant_backoff = float(_get_nested(matching_cfg, ("qdrant", "retry_backoff_seconds"), 0.5))
    if qdrant_timeout <= 0:
        raise ValueError("matching.qdrant.timeout_seconds must be > 0")
    if qdrant_max_retries < 0:
        raise ValueError("matching.qdrant.max_retries must be >= 0")
    if qdrant_backoff < 0:
        raise ValueError("matching.qdrant.retry_backoff_seconds must be >= 0")

    score_weight_keys = (
        ("role", 0.40),
        ("chunk", 0.30),
        ("profile", 0.20),
        ("metadata", 0.10),
        ("chunk_top1", 0.60),
        ("chunk_top2", 0.30),
        ("chunk_top3", 0.10),
        ("rerank_pre_llm", 0.75),
        ("rerank_lexical", 0.25),
        ("role_mismatch_threshold", 0.08),
        ("role_mismatch_penalty", 0.65),
    )
    for weight_key, default in score_weight_keys:
        value = float(_get_nested(matching_cfg, ("rerank", "weights", weight_key), default))
        if value < 0:
            raise ValueError(f"matching.rerank.weights.{weight_key} must be >= 0")
        if weight_key in {"role_mismatch_threshold", "role_mismatch_penalty"} and value > 1:
            raise ValueError(f"matching.rerank.weights.{weight_key} must be <= 1")


def resolve_matching_config(app_cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Вернуть итоговый matching-конфиг с учетом ENV override.

    Почему это вынесено в отдельный модуль:
    - чтобы все скрипты matching-слоя читали настройки одинаково;
    - чтобы новичку было понятно, где искать "источник истины" по параметрам.
    """
    matching_cfg = _get_matching_defaults(app_cfg)

    # Базовые флаги запуска matching-контура.
    core_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_ENABLED": (("enabled",), _parse_bool),
        "MATCHING_RUN_MODE": (("run_mode",), str),
        "MATCHING_ALLOW_MANUAL_RUN": (("allow_manual_run",), _parse_bool),
        "MATCHING_NIGHTLY_SCHEDULE_CRON": (("nightly_schedule_cron",), str),
        "MATCHING_PREPROCESS_STRATEGY": (("preprocess", "strategy"), str),
        "MATCHING_PREPROCESS_VERSION": (("preprocess", "version"), str),
    }

    # Параметры chunking: сегментация, ограничения длины и token-aware split.
    chunking_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_CHUNKING_STRATEGY": (("chunking", "strategy"), str),
        "MATCHING_CHUNKING_VERSION": (("chunking", "version"), str),
        "MATCHING_SEMANTIC_SIMILARITY_THRESHOLD": (
            ("chunking", "semantic_similarity_threshold"),
            _parse_float,
        ),
        "MATCHING_SEMANTIC_SENTENCE_WINDOW": (
            ("chunking", "semantic_sentence_window"),
            _parse_int,
        ),
        "MATCHING_SEMANTIC_MIN_SENTENCES": (("chunking", "semantic_min_sentences"), _parse_int),
        "MATCHING_SEMANTIC_MAX_SENTENCES": (("chunking", "semantic_max_sentences"), _parse_int),
        "MATCHING_SENTENCE_SPLITTER": (("chunking", "sentence_splitter"), str),
        "MATCHING_SPACY_MODEL": (("chunking", "spacy_model"), str),
        "MATCHING_CHUNKING_HARD_MAX_CHARS": (("chunking", "hard_max_chunk_chars"), _parse_int),
        "MATCHING_CHUNKING_HARD_MIN_CHARS": (("chunking", "hard_min_chunk_chars"), _parse_int),
        "MATCHING_CHUNKING_FALLBACK_OVERLAP_CHARS": (
            ("chunking", "fallback_overlap_chars"),
            _parse_int,
        ),
        "MATCHING_TOKEN_AWARE_ENABLED": (("chunking", "token_aware_enabled"), _parse_bool),
        "MATCHING_TOKEN_SPLITTER": (("chunking", "token_splitter"), str),
        "MATCHING_TOKENIZER_MODEL_NAME": (("chunking", "tokenizer_model_name"), str),
        "MATCHING_MAX_TOKENS_PER_CHUNK": (("chunking", "max_tokens_per_chunk"), _parse_int),
        "MATCHING_OVERLAP_TOKENS": (("chunking", "overlap_tokens"), _parse_int),
    }

    # Настройки embeddings и retrieval шага.
    retrieval_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_EMBEDDING_MODEL": (("embeddings", "model_name"), str),
        "MATCHING_EMBEDDING_VECTOR_SIZE": (("embeddings", "vector_size"), _parse_int),
        "MATCHING_RETRIEVAL_TOP_K": (("retrieval", "top_k"), _parse_int),
    }

    # Rerank-конфиг и веса итогового скоринга.
    rerank_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_RERANK_ENABLED": (("rerank", "enabled"), _parse_bool),
        "MATCHING_RERANK_MODEL": (("rerank", "model_name"), str),
        "MATCHING_RERANK_TOP_N": (("rerank", "top_n"), _parse_int),
        "MATCHING_RERANK_WEIGHT_ROLE": (("rerank", "weights", "role"), _parse_float),
        "MATCHING_RERANK_WEIGHT_CHUNK": (("rerank", "weights", "chunk"), _parse_float),
        "MATCHING_RERANK_WEIGHT_PROFILE": (("rerank", "weights", "profile"), _parse_float),
        "MATCHING_RERANK_WEIGHT_METADATA": (("rerank", "weights", "metadata"), _parse_float),
        "MATCHING_RERANK_WEIGHT_CHUNK_TOP1": (("rerank", "weights", "chunk_top1"), _parse_float),
        "MATCHING_RERANK_WEIGHT_CHUNK_TOP2": (("rerank", "weights", "chunk_top2"), _parse_float),
        "MATCHING_RERANK_WEIGHT_CHUNK_TOP3": (("rerank", "weights", "chunk_top3"), _parse_float),
        "MATCHING_RERANK_WEIGHT_PRE_LLM": (("rerank", "weights", "rerank_pre_llm"), _parse_float),
        "MATCHING_RERANK_WEIGHT_LEXICAL": (("rerank", "weights", "rerank_lexical"), _parse_float),
        "MATCHING_RERANK_ROLE_MISMATCH_THRESHOLD": (
            ("rerank", "weights", "role_mismatch_threshold"),
            _parse_float,
        ),
        "MATCHING_RERANK_ROLE_MISMATCH_PENALTY": (
            ("rerank", "weights", "role_mismatch_penalty"),
            _parse_float,
        ),
    }

    # Параметры selection-cache (reuse top-N выдачи).
    cache_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_CACHE_ENABLED": (("selection_cache", "enabled"), _parse_bool),
        "MATCHING_CACHE_TTL_HOURS": (("selection_cache", "ttl_hours"), _parse_int),
        "MATCHING_CACHE_TOP_BASE": (("selection_cache", "top_base"), _parse_int),
        "MATCHING_CACHE_EXTRA_MIN": (("selection_cache", "extra_min"), _parse_int),
        "MATCHING_CACHE_TOP_CAP": (("selection_cache", "top_cap"), _parse_int),
    }

    # Режимы провайдера/формата входа для LLM handoff.
    llm_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_LLM_PROVIDER_MODE": (("llm", "provider_mode"), str),
        "MATCHING_LLM_INPUT_MODE": (("llm", "input_mode"), str),
        "MATCHING_LLM_FALLBACK_PROVIDER_MODE": (("llm", "fallback_provider_mode"), str),
    }

    # Подключение к Qdrant и политика retry.
    qdrant_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_QDRANT_URL": (("qdrant", "url"), str),
        "MATCHING_QDRANT_COLLECTION": (("qdrant", "collection_name"), str),
        "MATCHING_QDRANT_DISTANCE": (("qdrant", "distance"), str),
        "MATCHING_QDRANT_HNSW_M": (("qdrant", "hnsw_m"), _parse_int),
        "MATCHING_QDRANT_HNSW_EF_CONSTRUCT": (("qdrant", "hnsw_ef_construct"), _parse_int),
        "MATCHING_QDRANT_TIMEOUT_SECONDS": (("qdrant", "timeout_seconds"), _parse_int),
        "MATCHING_QDRANT_MAX_RETRIES": (("qdrant", "max_retries"), _parse_int),
        "MATCHING_QDRANT_RETRY_BACKOFF_SECONDS": (
            ("qdrant", "retry_backoff_seconds"),
            _parse_float,
        ),
    }

    # Единая таблица override для цикла применения ниже.
    env_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        **core_overrides,
        **chunking_overrides,
        **retrieval_overrides,
        **rerank_overrides,
        **cache_overrides,
        **llm_overrides,
        **qdrant_overrides,
    }

    for env_name, (path, parser) in env_overrides.items():
        env_value = os.getenv(env_name)
        if env_value is None or env_value == "":
            continue
        _set_nested(matching_cfg, path, parser(env_value))

    _validate_matching_config(matching_cfg)
    return matching_cfg
