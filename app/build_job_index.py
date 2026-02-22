from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.core.config import load_app_config
from app.core.matching_config import resolve_matching_config
from app.db.mongo import get_database, resolve_collection_name
from app.matching.models import ChunkRecord, MergedVacancyNormalized, MergedVacancyRaw
from app.matching.rule_engine import MatchingRuleEngine
from app.vector.embedder import TextEmbedder, build_text_embedder
from app.vector.qdrant_client import QdrantRestClient, parse_qdrant_settings

log = logging.getLogger(__name__)
_SPACY_PIPELINES: dict[str, Any] = {}
_TOKENIZERS: dict[str, Any] = {}
_UNAVAILABLE_SPACY_MODELS: set[str] = set()
_UNAVAILABLE_TOKENIZER_MODELS: set[str] = set()
_WARNED_KEYS: set[str] = set()
_SPACY_IMPORT_FAILED = False
_TRANSFORMERS_IMPORT_FAILED = False


@dataclass(frozen=True)
class IndexVersions:
    embedding_model: str
    embedding_vector_size: int
    chunking_version: str
    preprocess_version: str
    ruleset_version: str


@dataclass
class ChunkingRuntimeInfo:
    sentence_splitter_used: str = "unknown"
    token_splitter_used: str = "disabled"
    token_aware_applied: bool = False
    tokenizer_model_used: str | None = None
    fallback_reasons: list[str] | None = None

    def __post_init__(self) -> None:
        if self.fallback_reasons is None:
            self.fallback_reasons = []


def _compute_embedding_input_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_change_flags(
    state_doc: dict[str, Any] | None,
    *,
    new_hash: str,
    versions: IndexVersions,
) -> dict[str, bool]:
    """
    Вычислить флаги изменений для rule-engine.

    Почему отдельной функцией:
    - вся логика skip/reindex в одном месте;
    - проще тестировать и расширять.
    """
    if not state_doc:
        return {
            "hash_changed": True,
            "embedding_model_changed": True,
            "chunking_version_changed": True,
            "preprocess_version_changed": True,
            # Для новых вакансий индекса еще нет, значит нужен реальный reindex.
            "not_indexed_yet": True,
        }

    return {
        "hash_changed": state_doc.get("embedding_input_hash") != new_hash,
        "embedding_model_changed": state_doc.get("embedding_model") != versions.embedding_model,
        "chunking_version_changed": state_doc.get("chunking_version") != versions.chunking_version,
        "preprocess_version_changed": state_doc.get("preprocess_version")
        != versions.preprocess_version,
        # Важно: normalized_only/quality_failed/qdrant_failed не должны "зависать" в skip.
        # Если статус не indexed, вакансию нужно догонять до реального upsert в Qdrant.
        "not_indexed_yet": state_doc.get("index_status") != "indexed",
    }


def _build_index_versions(matching_cfg: dict[str, Any]) -> IndexVersions:
    embeddings = matching_cfg.get("embeddings", {})
    chunking = matching_cfg.get("chunking", {})
    preprocess = matching_cfg.get("preprocess", {})
    rules = matching_cfg.get("rules", {})
    return IndexVersions(
        embedding_model=str(embeddings.get("model_name", "")),
        embedding_vector_size=int(embeddings.get("vector_size", 1024)),
        chunking_version=str(chunking.get("version", "v1")),
        preprocess_version=str(preprocess.get("version", "v1")),
        ruleset_version=str(rules.get("ruleset_version", "v1")),
    )


def _build_chunk_records(
    *,
    vacancy: MergedVacancyNormalized,
    rule_flags: dict[str, Any],
    engine: MatchingRuleEngine,
    chunking_cfg: dict[str, Any] | None = None,
    embedder: TextEmbedder | None = None,
    embedding_model: str | None = None,
) -> list[ChunkRecord]:
    records, _runtime = _build_chunk_records_with_runtime(
        vacancy=vacancy,
        rule_flags=rule_flags,
        engine=engine,
        chunking_cfg=chunking_cfg,
        embedder=embedder,
        embedding_model=embedding_model,
    )
    return records


def _build_chunk_records_with_runtime(
    *,
    vacancy: MergedVacancyNormalized,
    rule_flags: dict[str, Any],
    engine: MatchingRuleEngine,
    chunking_cfg: dict[str, Any] | None = None,
    embedder: TextEmbedder | None = None,
    embedding_model: str | None = None,
) -> tuple[list[ChunkRecord], ChunkingRuntimeInfo]:
    """
    Построить chunk records для индексации.

    Поведение:
    - synthetic chunk из rule-engine всегда имеет приоритет;
    - при `strategy=semantic` используем смысловое объединение предложений;
    - если semantic шаг не сработал, откатываемся к baseline (один стабильный chunk).
    """
    runtime = ChunkingRuntimeInfo()
    if bool(rule_flags.get("create_synthetic_chunk", False)):
        runtime.fallback_reasons.append("synthetic_chunk_by_rule_engine")
        text = str(rule_flags.get("synthetic_chunk_text", "")).strip()
        return (
            [
                ChunkRecord(
                    referenznummer=vacancy.referenznummer,
                    chunk_id=f"{vacancy.referenznummer}:0",
                    chunk_index=0,
                    text=text,
                    section_label="unknown",
                    section_confidence=0.0,
                    description_missing=vacancy.description_missing,
                )
            ],
            runtime,
        )

    source_text = vacancy.description.strip() or vacancy.canonical_text()
    strategy = str((chunking_cfg or {}).get("strategy", "baseline")).strip().lower()

    chunk_texts = [source_text]
    if strategy == "semantic":
        chunk_texts = _build_semantic_chunk_texts(
            source_text=source_text,
            chunking_cfg=chunking_cfg or {},
            embedder=embedder,
            embedding_model=embedding_model,
            runtime=runtime,
        )
    else:
        runtime.fallback_reasons.append("strategy_not_semantic")

    records: list[ChunkRecord] = []
    for chunk_index, chunk_text in enumerate(chunk_texts):
        label, confidence, _reason = engine.infer_section_label(chunk_text)
        records.append(
            ChunkRecord(
                referenznummer=vacancy.referenznummer,
                chunk_id=f"{vacancy.referenznummer}:{chunk_index}",
                chunk_index=chunk_index,
                text=chunk_text,
                section_label=label,
                section_confidence=confidence,
                description_missing=vacancy.description_missing,
            )
        )
    return records, runtime


def _build_semantic_chunk_texts(
    *,
    source_text: str,
    chunking_cfg: dict[str, Any],
    embedder: TextEmbedder | None,
    embedding_model: str | None = None,
    runtime: ChunkingRuntimeInfo | None = None,
) -> list[str]:
    """
    Собрать смысловые chunks из текста вакансии.

    Важно для новичка:
    - если embedding недоступен или предложений слишком мало, делаем безопасный fallback;
    - hard-* параметры ограничивают только "крайние" случаи длины chunk.
    """
    text = source_text.strip()
    if not text:
        return [source_text]

    sentences = _split_text_to_sentences(text, chunking_cfg=chunking_cfg, runtime=runtime)
    if len(sentences) <= 1 or embedder is None:
        if len(sentences) <= 1 and runtime is not None:
            runtime.fallback_reasons.append("too_few_sentences_for_semantic")
        if embedder is None and runtime is not None:
            runtime.fallback_reasons.append("embedder_unavailable_for_semantic")
        token_limited = _enforce_token_aware_chunk_limits(
            [text],
            chunking_cfg=chunking_cfg,
            embedding_model=embedding_model,
            runtime=runtime,
        )
        return _enforce_hard_chunk_limits(token_limited, chunking_cfg)

    threshold = float(chunking_cfg.get("semantic_similarity_threshold", 0.72))
    window = max(1, int(chunking_cfg.get("semantic_sentence_window", 2)))
    min_sentences = max(1, int(chunking_cfg.get("semantic_min_sentences", 2)))
    max_sentences = max(min_sentences, int(chunking_cfg.get("semantic_max_sentences", 14)))

    vectors = embedder.embed_texts([f"passage: {sentence}" for sentence in sentences])
    if len(vectors) != len(sentences):
        if runtime is not None:
            runtime.fallback_reasons.append("vector_sentence_count_mismatch")
        token_limited = _enforce_token_aware_chunk_limits(
            [text],
            chunking_cfg=chunking_cfg,
            embedding_model=embedding_model,
            runtime=runtime,
        )
        return _enforce_hard_chunk_limits(token_limited, chunking_cfg)

    vectors_np = np.asarray(vectors, dtype=np.float32)
    chunks: list[list[str]] = []
    current_chunk: list[str] = [sentences[0]]
    current_vector_window: list[np.ndarray] = [vectors_np[0]]

    for sentence, vector in zip(sentences[1:], vectors_np[1:], strict=True):
        if len(current_chunk) >= max_sentences:
            chunks.append(current_chunk)
            current_chunk = [sentence]
            current_vector_window = [vector]
            continue

        similarity = _mean_similarity_vectorized(vector, current_vector_window[-window:])
        # Держим минимум предложений в chunk, чтобы не дробить слишком агрессивно.
        if similarity >= threshold or len(current_chunk) < min_sentences:
            current_chunk.append(sentence)
            current_vector_window.append(vector)
            continue

        chunks.append(current_chunk)
        current_chunk = [sentence]
        current_vector_window = [vector]

    chunks.append(current_chunk)
    chunk_texts = [" ".join(sentences_group).strip() for sentences_group in chunks if sentences_group]
    token_limited = _enforce_token_aware_chunk_limits(
        chunk_texts,
        chunking_cfg=chunking_cfg,
        embedding_model=embedding_model,
        runtime=runtime,
    )
    return _enforce_hard_chunk_limits(token_limited, chunking_cfg)


def _split_text_to_sentences(
    text: str,
    *,
    chunking_cfg: dict[str, Any],
    runtime: ChunkingRuntimeInfo | None = None,
) -> list[str]:
    """
    Разбить описание на предложения.

    Основной путь: spaCy sentence segmentation.
    Fallback: regex-деление, чтобы пайплайн оставался рабочим даже в легком/offline окружении.
    """
    splitter = str(chunking_cfg.get("sentence_splitter", "spacy")).strip().lower()
    if splitter == "spacy":
        model_name = str(chunking_cfg.get("spacy_model", "de_core_news_md")).strip()
        sentences = _split_text_to_sentences_spacy(text, model_name=model_name)
        if sentences:
            if runtime is not None:
                runtime.sentence_splitter_used = "spacy"
            return sentences
        if runtime is not None:
            runtime.fallback_reasons.append("spacy_unavailable_or_failed")
    elif splitter == "syntok":
        sentences = _split_text_to_sentences_syntok(text)
        if sentences:
            if runtime is not None:
                runtime.sentence_splitter_used = "syntok"
            return sentences
        if runtime is not None:
            runtime.fallback_reasons.append("syntok_unavailable_or_failed")
    elif splitter == "regex":
        if runtime is not None:
            runtime.sentence_splitter_used = "regex"
        return _split_text_to_sentences_regex(text)

    if runtime is not None:
        runtime.sentence_splitter_used = "regex"
    return _split_text_to_sentences_regex(text)


def _split_text_to_sentences_spacy(text: str, *, model_name: str) -> list[str]:
    nlp = _get_spacy_pipeline(model_name)
    if nlp is None:
        return []
    doc = nlp(text)
    sentences = [sentence.text.strip() for sentence in doc.sents if sentence.text.strip()]
    return sentences


def _split_text_to_sentences_syntok(text: str) -> list[str]:
    try:
        from syntok import segmenter
    except ModuleNotFoundError:
        _warn_once("syntok_missing", "syntok is not installed; sentence split fallback=regex")
        return []

    try:
        sentences: list[str] = []
        for paragraph in segmenter.process(text):
            for sentence in paragraph:
                sentence_text = "".join(token.spacing + token.value for token in sentence).strip()
                if sentence_text:
                    sentences.append(sentence_text)
        return sentences
    except Exception:
        _warn_once("syntok_failed", "syntok processing failed; sentence split fallback=regex")
        return []


def _split_text_to_sentences_regex(text: str) -> list[str]:
    raw_parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = [part.strip() for part in raw_parts if part and part.strip()]
    return sentences if sentences else [text.strip()]


def _get_spacy_pipeline(model_name: str) -> Any | None:
    global _SPACY_IMPORT_FAILED
    if model_name in _SPACY_PIPELINES:
        return _SPACY_PIPELINES[model_name]
    if model_name in _UNAVAILABLE_SPACY_MODELS:
        return None
    if _SPACY_IMPORT_FAILED:
        return None

    try:
        import spacy
    except ModuleNotFoundError:
        _SPACY_IMPORT_FAILED = True
        _warn_once("spacy_missing", "spaCy is not installed; sentence split fallback=regex")
        _UNAVAILABLE_SPACY_MODELS.add(model_name)
        return None

    try:
        nlp = spacy.load(
            model_name,
            disable=[
                "ner",
                "tagger",
                "lemmatizer",
                "attribute_ruler",
            ],
        )
    except Exception:
        _warn_once(
            f"spacy_model_unavailable:{model_name}",
            "spaCy model '%s' unavailable; fallback to lightweight sentencizer",
            model_name,
        )
        nlp = spacy.blank("de")

    if "parser" not in nlp.pipe_names and "senter" not in nlp.pipe_names:
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")

    _SPACY_PIPELINES[model_name] = nlp
    return nlp


def _mean_similarity(vector: list[float], others: list[list[float]]) -> float:
    if not others:
        return 1.0
    values = np.array([_cosine_similarity(vector, other) for other in others], dtype=np.float32)
    return float(values.mean()) if values.size else 0.0


def _mean_similarity_vectorized(vector: np.ndarray, others: list[np.ndarray]) -> float:
    if not others:
        return 1.0
    target_norm = float(np.linalg.norm(vector))
    if target_norm == 0.0:
        return 0.0

    others_matrix = np.vstack(others)
    others_norm = np.linalg.norm(others_matrix, axis=1)
    denominator = target_norm * others_norm
    safe = denominator > 0
    scores = np.zeros_like(others_norm, dtype=np.float32)
    if np.any(safe):
        scores[safe] = np.dot(others_matrix[safe], vector) / denominator[safe]
    return float(scores.mean())


def _cosine_similarity(first: list[float], second: list[float]) -> float:
    left = np.asarray(first, dtype=np.float32)
    right = np.asarray(second, dtype=np.float32)
    if left.shape != right.shape or left.size == 0:
        return 0.0
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def _enforce_token_aware_chunk_limits(
    chunks: list[str],
    *,
    chunking_cfg: dict[str, Any],
    embedding_model: str | None,
    runtime: ChunkingRuntimeInfo | None = None,
) -> list[str]:
    enabled = bool(chunking_cfg.get("token_aware_enabled", False))
    if not enabled:
        if runtime is not None:
            runtime.token_splitter_used = "disabled"
        return chunks

    token_splitter = str(chunking_cfg.get("token_splitter", "transformers")).strip().lower()
    if token_splitter != "transformers":
        if runtime is not None:
            runtime.token_splitter_used = token_splitter
        return chunks

    tokenizer_model_name = str(
        chunking_cfg.get("tokenizer_model_name") or embedding_model or ""
    ).strip()
    if not tokenizer_model_name:
        if runtime is not None:
            runtime.token_splitter_used = "transformers"
            runtime.fallback_reasons.append("tokenizer_model_not_set")
        return chunks

    tokenizer = _get_transformers_tokenizer(tokenizer_model_name)
    if tokenizer is None:
        if runtime is not None:
            runtime.token_splitter_used = "transformers"
            runtime.fallback_reasons.append("tokenizer_unavailable")
        return chunks
    if runtime is not None:
        runtime.token_splitter_used = "transformers"
        runtime.tokenizer_model_used = tokenizer_model_name

    max_tokens = max(1, int(chunking_cfg.get("max_tokens_per_chunk", 320)))
    overlap_tokens = max(0, int(chunking_cfg.get("overlap_tokens", 48)))

    result: list[str] = []
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue

        offset_chunks = _split_text_by_token_offsets(
            text=text,
            tokenizer=tokenizer,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        if not offset_chunks:
            if runtime is not None:
                runtime.fallback_reasons.append("token_offsets_unavailable")
            try:
                encoded = tokenizer(
                    text,
                    add_special_tokens=False,
                    truncation=False,
                    max_length=None,
                    verbose=False,
                )
                token_ids = encoded.get("input_ids", [])
            except Exception:
                token_ids = tokenizer.encode(text, add_special_tokens=False)
            if len(token_ids) <= max_tokens:
                result.append(text)
                continue
            pieces = _split_token_ids_by_budget(
                token_ids=token_ids,
                tokenizer=tokenizer,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            )
            if pieces:
                if runtime is not None:
                    runtime.token_aware_applied = True
                result.extend(pieces)
                continue
            result.append(text)
            continue

        if len(offset_chunks) <= 1:
            result.append(text)
            continue

        if runtime is not None:
            runtime.token_aware_applied = True
        result.extend(offset_chunks)

    return result if result else chunks


def _split_text_by_token_offsets(
    *,
    text: str,
    tokenizer: Any,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    try:
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
            max_length=None,
            verbose=False,
        )
    except Exception:
        return []

    offsets = encoded.get("offset_mapping")
    if not offsets:
        return []
    valid_offsets = [(start, end) for start, end in offsets if end > start]
    if not valid_offsets:
        return []

    step = max(1, max_tokens - overlap_tokens)
    chunks: list[str] = []
    start_idx = 0
    while start_idx < len(valid_offsets):
        end_idx = min(len(valid_offsets), start_idx + max_tokens)
        char_start = valid_offsets[start_idx][0]
        char_end = valid_offsets[end_idx - 1][1]
        piece = text[char_start:char_end].strip()
        if piece:
            chunks.append(piece)
        if end_idx >= len(valid_offsets):
            break
        start_idx += step
    return chunks


def _split_token_ids_by_budget(
    *,
    token_ids: list[int],
    tokenizer: Any,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    step = max(1, max_tokens - overlap_tokens)
    pieces: list[str] = []
    start = 0
    while start < len(token_ids):
        piece_ids = token_ids[start : start + max_tokens]
        piece = tokenizer.decode(piece_ids, skip_special_tokens=True).strip()
        if piece:
            pieces.append(piece)
        if start + max_tokens >= len(token_ids):
            break
        start += step
    return pieces


def _get_transformers_tokenizer(model_name: str) -> Any | None:
    global _TRANSFORMERS_IMPORT_FAILED
    if model_name in _TOKENIZERS:
        return _TOKENIZERS[model_name]
    if model_name in _UNAVAILABLE_TOKENIZER_MODELS:
        return None
    if _TRANSFORMERS_IMPORT_FAILED:
        return None

    try:
        from transformers import AutoTokenizer
    except ModuleNotFoundError:
        _TRANSFORMERS_IMPORT_FAILED = True
        _warn_once("transformers_missing", "transformers is not installed; token-aware split disabled")
        _UNAVAILABLE_TOKENIZER_MODELS.add(model_name)
        return None

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    except Exception:
        _warn_once(
            f"tokenizer_unavailable:{model_name}",
            "Tokenizer '%s' is unavailable in current environment; token-aware split disabled",
            model_name,
        )
        _UNAVAILABLE_TOKENIZER_MODELS.add(model_name)
        return None

    _TOKENIZERS[model_name] = tokenizer
    return tokenizer


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _WARNED_KEYS:
        return
    _WARNED_KEYS.add(key)
    if args:
        log.warning(message, *args)
        return
    log.warning(message)


def _enforce_hard_chunk_limits(chunks: list[str], chunking_cfg: dict[str, Any]) -> list[str]:
    max_chars = max(1, int(chunking_cfg.get("hard_max_chunk_chars", 2200)))
    min_chars = max(1, int(chunking_cfg.get("hard_min_chunk_chars", 300)))
    overlap = max(0, int(chunking_cfg.get("fallback_overlap_chars", 120)))
    step = max(1, max_chars - overlap)

    split_chunks: list[str] = []
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        if len(text) <= max_chars:
            split_chunks.append(text)
            continue

        start = 0
        while start < len(text):
            piece = text[start : start + max_chars].strip()
            if piece:
                split_chunks.append(piece)
            if start + max_chars >= len(text):
                break
            start += step

    if not split_chunks:
        return chunks

    merged_chunks: list[str] = []
    for chunk in split_chunks:
        if len(chunk) >= min_chars or not merged_chunks:
            merged_chunks.append(chunk)
            continue
        candidate = f"{merged_chunks[-1]} {chunk}".strip()
        if len(candidate) <= max_chars:
            merged_chunks[-1] = candidate
        else:
            merged_chunks.append(chunk)
    return merged_chunks


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _is_env_true(env_name: str, default: bool = False) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_collection_create_policy(ensure_result: str) -> tuple[bool, bool]:
    """
    Вернуть политику после ensure_collection.

    Возвращает:
    - force_full_reindex: нужно ли принудительно reindex для всех кандидатов в этом запуске;
    - should_exit: нужно ли завершить запуск с кодом 1.
    """
    if ensure_result != "created":
        return False, False

    auto_reindex = _is_env_true("AUTO_FULL_REINDEX_ON_COLLECTION_CREATE", default=False)
    if auto_reindex:
        log.warning(
            "Qdrant collection was recreated; AUTO_FULL_REINDEX_ON_COLLECTION_CREATE=true, forcing full reindex in this run."
        )
        return True, False

    log.error(
        "Qdrant collection was recreated. Refusing to continue to avoid false skips from stale matching_index_state. "
        "Rerun with AUTO_FULL_REINDEX_ON_COLLECTION_CREATE=true for forced full reindex "
        "(optionally with MATCHING_BUILD_MAX_DOCS for smoke)."
    )
    return False, True


def _build_point_id(referenznummer: str, chunk_id: str, embedding_input_hash: str) -> int:
    """
    Детерминированный uint64 id для Qdrant point.

    Qdrant стабильно принимает integer id; используем хеш-композицию вместо
    случайных значений, чтобы повторный upsert был предсказуемым.
    """
    source = f"{referenznummer}|{chunk_id}|{embedding_input_hash}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _build_qdrant_points(
    *,
    referenznummer: str,
    chunks: list[ChunkRecord],
    vectors: list[list[float]],
    embedding_input_hash: str,
    versions: IndexVersions,
) -> list[dict[str, Any]]:
    if len(chunks) != len(vectors):
        raise ValueError("chunks/vectors length mismatch")

    points: list[dict[str, Any]] = []
    indexed_at_iso = _now_utc().isoformat()
    for chunk, vector in zip(chunks, vectors, strict=True):
        point_id = _build_point_id(referenznummer, chunk.chunk_id, embedding_input_hash)
        points.append(
            {
                "id": point_id,
                "vector": vector,
                "payload": {
                    "referenznummer": referenznummer,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "section_label": chunk.section_label,
                    "section_confidence": chunk.section_confidence,
                    "description_missing": chunk.description_missing,
                    "chunk_text": chunk.text,
                    "embedding_input_hash": embedding_input_hash,
                    "embedding_model": versions.embedding_model,
                    "chunking_version": versions.chunking_version,
                    "preprocess_version": versions.preprocess_version,
                    "indexed_at": indexed_at_iso,
                },
            }
        )
    return points


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = load_app_config()
    matching_cfg = resolve_matching_config(cfg)
    db = get_database(cfg)
    from app.matching.quality_schema import run_quality_checks

    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    details_collection_name = resolve_collection_name(cfg, key="details", default="jobdetails")
    state_collection_name = "matching_index_state"
    audit_collection_name = "matching_audit"

    col_list = db[list_collection_name]
    col_details = db[details_collection_name]
    col_state = db[state_collection_name]
    col_audit = db[audit_collection_name]

    col_state.create_index("referenznummer", unique=True)
    col_state.create_index("indexed_at")
    col_audit.create_index("created_at")
    col_audit.create_index([("referenznummer", 1), ("run_id", 1)])

    # На первом шаге индексатора явно проверяем инфраструктуру Qdrant.
    qdrant_settings = parse_qdrant_settings(matching_cfg)
    qdrant_client = QdrantRestClient(qdrant_settings)
    if not qdrant_client.healthcheck():
        raise RuntimeError("Qdrant healthcheck failed")
    ensure_result = qdrant_client.ensure_collection()
    log.info(
        "Qdrant check: %s collection=%s url=%s",
        ensure_result,
        qdrant_settings.collection_name,
        qdrant_settings.url,
    )
    force_full_reindex, should_exit = _resolve_collection_create_policy(ensure_result)
    if should_exit:
        return 1

    versions = _build_index_versions(matching_cfg)
    engine = MatchingRuleEngine(cfg)
    embedder = build_text_embedder(
        versions.embedding_model,
        batch_size=int(matching_cfg.get("embeddings", {}).get("batch_size", 32)),
    )
    run_id = f"build-job-index-{_now_utc().strftime('%Y%m%dT%H%M%SZ')}"
    max_docs_raw = os.getenv("MATCHING_BUILD_MAX_DOCS", "").strip()
    max_docs = int(max_docs_raw) if max_docs_raw else None
    skip_embeddings = os.getenv("MATCHING_SKIP_EMBEDDINGS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if max_docs is not None:
        log.info("build_job_index smoke mode enabled: max_docs=%s", max_docs)
    if skip_embeddings:
        log.info("build_job_index normalization-only mode: embeddings and qdrant upsert are skipped")

    scanned = 0
    reindexed = 0
    skipped = 0
    failed_quality = 0
    failed_qdrant = 0
    normalized_only = 0
    splitter_usage_stats: dict[str, int] = {"spacy": 0, "syntok": 0, "regex": 0, "unknown": 0}
    token_splitter_usage_stats: dict[str, int] = {
        "transformers": 0,
        "char": 0,
        "disabled": 0,
        "unknown": 0,
    }
    token_aware_applied_count = 0
    fallback_reason_counts: dict[str, int] = {}

    cursor = col_list.find({"is_active": True})
    if max_docs is not None:
        cursor = cursor.limit(max_docs)
    for meta in cursor:
        scanned += 1
        ref = str(meta.get("referenznummer") or "").strip()
        if not ref:
            continue

        details = col_details.find_one({"referenznummer": ref}) or {}
        raw = MergedVacancyRaw.from_sources(meta, details)
        vacancy = MergedVacancyNormalized.from_raw(raw)

        hash_payload = {
            "canonical_text": vacancy.canonical_text(),
            "embedding_model": versions.embedding_model,
            "embedding_vector_size": versions.embedding_vector_size,
            "chunking_version": versions.chunking_version,
            "preprocess_version": versions.preprocess_version,
            "ruleset_version": versions.ruleset_version,
        }
        new_hash = _compute_embedding_input_hash(hash_payload)
        state_doc = col_state.find_one({"referenznummer": ref})
        change_flags = _build_change_flags(state_doc, new_hash=new_hash, versions=versions)
        if force_full_reindex:
            change_flags["not_indexed_yet"] = True

        decision = engine.evaluate_vacancy(
            vacancy,
            change_flags=change_flags,
            run_id=run_id,
            audit_collection=col_audit,
        )
        if not decision.needs_reindex:
            skipped += 1
            col_state.update_one(
                {"referenznummer": ref},
                {
                    "$set": {
                        "referenznummer": ref,
                        "last_seen_at": _now_utc(),
                        "skip_reason": decision.skip_reason,
                        "rule_set_version": decision.rule_set_version,
                    }
                },
                upsert=True,
            )
            continue

        chunks, runtime = _build_chunk_records_with_runtime(
            vacancy=vacancy,
            rule_flags=decision.flags,
            engine=engine,
            chunking_cfg=matching_cfg.get("chunking", {}),
            embedder=embedder,
            embedding_model=versions.embedding_model,
        )
        splitter_usage_stats[runtime.sentence_splitter_used] = (
            splitter_usage_stats.get(runtime.sentence_splitter_used, 0) + 1
        )
        token_splitter_usage_stats[runtime.token_splitter_used] = (
            token_splitter_usage_stats.get(runtime.token_splitter_used, 0) + 1
        )
        if runtime.token_aware_applied:
            token_aware_applied_count += 1
        for reason in runtime.fallback_reasons:
            fallback_reason_counts[reason] = fallback_reason_counts.get(reason, 0) + 1
        quality = run_quality_checks(chunks)
        if not quality.hard_passed:
            failed_quality += 1
            col_state.update_one(
                {"referenznummer": ref},
                {
                    "$set": {
                        "referenznummer": ref,
                        "index_status": "quality_failed",
                        "sentence_splitter_used": runtime.sentence_splitter_used,
                        "token_splitter_used": runtime.token_splitter_used,
                        "token_aware_applied": runtime.token_aware_applied,
                        "tokenizer_model_used": runtime.tokenizer_model_used,
                        "chunking_fallback_reasons": runtime.fallback_reasons,
                        "quality_hard_errors": quality.hard_errors,
                        "quality_soft_warnings": quality.soft_warnings,
                        "updated_at": _now_utc(),
                    }
                },
                upsert=True,
            )
            continue

        if skip_embeddings:
            normalized_only += 1
            col_state.update_one(
                {"referenznummer": ref},
                {
                    "$set": {
                        "referenznummer": ref,
                        "embedding_input_hash": new_hash,
                        "embedding_model": versions.embedding_model,
                        "embedding_vector_size": versions.embedding_vector_size,
                        "chunking_version": versions.chunking_version,
                        "preprocess_version": versions.preprocess_version,
                        "rule_set_version": decision.rule_set_version,
                        "indexed_at": _now_utc(),
                        "index_status": "normalized_only",
                        "sentence_splitter_used": runtime.sentence_splitter_used,
                        "token_splitter_used": runtime.token_splitter_used,
                        "token_aware_applied": runtime.token_aware_applied,
                        "tokenizer_model_used": runtime.tokenizer_model_used,
                        "chunking_fallback_reasons": runtime.fallback_reasons,
                        "chunks_count": len(chunks),
                        "quality_hard_passed": quality.hard_passed,
                        "quality_soft_passed": quality.soft_passed,
                        "quality_soft_warnings": quality.soft_warnings,
                        "skip_reason": None,
                    }
                },
                upsert=True,
            )
            continue

        try:
            vectors = embedder.embed_texts([chunk.text for chunk in chunks])
            points = _build_qdrant_points(
                referenznummer=ref,
                chunks=chunks,
                vectors=vectors,
                embedding_input_hash=new_hash,
                versions=versions,
            )
            qdrant_client.delete_points_by_referenznummer(ref)
            qdrant_client.upsert_points(points)
        except Exception as exc:
            failed_qdrant += 1
            col_state.update_one(
                {"referenznummer": ref},
                {
                    "$set": {
                        "referenznummer": ref,
                        "index_status": "qdrant_failed",
                        "sentence_splitter_used": runtime.sentence_splitter_used,
                        "token_splitter_used": runtime.token_splitter_used,
                        "token_aware_applied": runtime.token_aware_applied,
                        "tokenizer_model_used": runtime.tokenizer_model_used,
                        "chunking_fallback_reasons": runtime.fallback_reasons,
                        "qdrant_error": str(exc),
                        "updated_at": _now_utc(),
                    }
                },
                upsert=True,
            )
            log.exception("Qdrant upsert failed for ref=%s", ref)
            continue

        reindexed += 1
        col_state.update_one(
            {"referenznummer": ref},
            {
                "$set": {
                    "referenznummer": ref,
                    "embedding_input_hash": new_hash,
                    "embedding_model": versions.embedding_model,
                    "embedding_vector_size": versions.embedding_vector_size,
                    "chunking_version": versions.chunking_version,
                    "preprocess_version": versions.preprocess_version,
                    "rule_set_version": decision.rule_set_version,
                    "indexed_at": _now_utc(),
                    "index_status": "indexed",
                    "sentence_splitter_used": runtime.sentence_splitter_used,
                    "token_splitter_used": runtime.token_splitter_used,
                    "token_aware_applied": runtime.token_aware_applied,
                    "tokenizer_model_used": runtime.tokenizer_model_used,
                    "chunking_fallback_reasons": runtime.fallback_reasons,
                    "vector_upserted": True,
                    "chunks_count": len(chunks),
                    "quality_hard_passed": quality.hard_passed,
                    "quality_soft_passed": quality.soft_passed,
                    "quality_soft_warnings": quality.soft_warnings,
                    "skip_reason": None,
                }
            },
            upsert=True,
        )

    log.info(
        "build_job_index finished: run_id=%s scanned=%s reindexed=%s skipped=%s failed_quality=%s normalized_only=%s",
        run_id,
        scanned,
        reindexed,
        skipped,
        failed_quality,
        normalized_only,
    )
    log.info("build_job_index qdrant metrics: failed_qdrant=%s", failed_qdrant)
    log.info(
        "build_job_index chunking usage: sentence_splitter=%s token_splitter=%s token_aware_applied=%s fallback_reasons=%s",
        splitter_usage_stats,
        token_splitter_usage_stats,
        token_aware_applied_count,
        fallback_reason_counts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
