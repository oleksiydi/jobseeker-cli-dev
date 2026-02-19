from __future__ import annotations

import os
from copy import deepcopy
from typing import Any


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_int(raw_value: str) -> int:
    return int(raw_value.strip())


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


def resolve_matching_config(app_cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Вернуть итоговый matching-конфиг с учетом ENV override.

    Почему это вынесено в отдельный модуль:
    - чтобы все скрипты matching-слоя читали настройки одинаково;
    - чтобы новичку было понятно, где искать "источник истины" по параметрам.
    """
    matching_cfg = _get_matching_defaults(app_cfg)

    env_overrides: dict[str, tuple[tuple[str, ...], Any]] = {
        "MATCHING_ENABLED": (("enabled",), _parse_bool),
        "MATCHING_NIGHTLY_MODE": (("nightly_mode",), _parse_bool),
        "MATCHING_PREPROCESS_STRATEGY": (("preprocess", "strategy"), str),
        "MATCHING_PREPROCESS_VERSION": (("preprocess", "version"), str),
        "MATCHING_CHUNKING_STRATEGY": (("chunking", "strategy"), str),
        "MATCHING_CHUNKING_VERSION": (("chunking", "version"), str),
        "MATCHING_CHUNKING_MAX_CHARS": (("chunking", "max_chunk_chars"), _parse_int),
        "MATCHING_CHUNKING_MIN_CHARS": (("chunking", "min_chunk_chars"), _parse_int),
        "MATCHING_CHUNKING_OVERLAP_CHARS": (("chunking", "overlap_chars"), _parse_int),
        "MATCHING_EMBEDDING_MODEL": (("embeddings", "model_name"), str),
        "MATCHING_EMBEDDING_VECTOR_SIZE": (("embeddings", "vector_size"), _parse_int),
        "MATCHING_RETRIEVAL_TOP_K": (("retrieval", "top_k"), _parse_int),
        "MATCHING_RERANK_ENABLED": (("rerank", "enabled"), _parse_bool),
        "MATCHING_RERANK_MODEL": (("rerank", "model_name"), str),
        "MATCHING_RERANK_TOP_N": (("rerank", "top_n"), _parse_int),
        "MATCHING_CACHE_ENABLED": (("selection_cache", "enabled"), _parse_bool),
        "MATCHING_CACHE_TTL_HOURS": (("selection_cache", "ttl_hours"), _parse_int),
        "MATCHING_CACHE_TOP_BASE": (("selection_cache", "top_base"), _parse_int),
        "MATCHING_CACHE_EXTRA_MIN": (("selection_cache", "extra_min"), _parse_int),
        "MATCHING_CACHE_TOP_CAP": (("selection_cache", "top_cap"), _parse_int),
        "MATCHING_LLM_PROVIDER_MODE": (("llm", "provider_mode"), str),
        "MATCHING_LLM_INPUT_MODE": (("llm", "input_mode"), str),
        "MATCHING_LLM_FALLBACK_PROVIDER_MODE": (("llm", "fallback_provider_mode"), str),
        "MATCHING_QDRANT_URL": (("qdrant", "url"), str),
        "MATCHING_QDRANT_COLLECTION": (("qdrant", "collection_name"), str),
        "MATCHING_QDRANT_DISTANCE": (("qdrant", "distance"), str),
        "MATCHING_QDRANT_HNSW_M": (("qdrant", "hnsw_m"), _parse_int),
        "MATCHING_QDRANT_HNSW_EF_CONSTRUCT": (("qdrant", "hnsw_ef_construct"), _parse_int),
        "MATCHING_QDRANT_TIMEOUT_SECONDS": (("qdrant", "timeout_seconds"), _parse_int),
    }

    for env_name, (path, parser) in env_overrides.items():
        env_value = os.getenv(env_name)
        if env_value is None or env_value == "":
            continue
        _set_nested(matching_cfg, path, parser(env_value))

    return matching_cfg
