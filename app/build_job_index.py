from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import load_app_config
from app.core.matching_config import resolve_matching_config
from app.db.mongo import get_database, resolve_collection_name
from app.matching.models import ChunkRecord, MergedVacancyNormalized, MergedVacancyRaw
from app.matching.rule_engine import MatchingRuleEngine
from app.vector.qdrant_client import QdrantRestClient, parse_qdrant_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexVersions:
    embedding_model: str
    embedding_vector_size: int
    chunking_version: str
    preprocess_version: str
    ruleset_version: str


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
        }

    return {
        "hash_changed": state_doc.get("embedding_input_hash") != new_hash,
        "embedding_model_changed": state_doc.get("embedding_model") != versions.embedding_model,
        "chunking_version_changed": state_doc.get("chunking_version") != versions.chunking_version,
        "preprocess_version_changed": state_doc.get("preprocess_version")
        != versions.preprocess_version,
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
) -> list[ChunkRecord]:
    """
    Baseline chunk builder для первого инкрементального индексатора.

    Важно:
    - semantic/spacy chunking будет отдельным шагом следующей итерации;
    - здесь делаем надежный стартовый вариант с fallback поведением.
    """
    if bool(rule_flags.get("create_synthetic_chunk", False)):
        text = str(rule_flags.get("synthetic_chunk_text", "")).strip()
        return [
            ChunkRecord(
                referenznummer=vacancy.referenznummer,
                chunk_id=f"{vacancy.referenznummer}:0",
                chunk_index=0,
                text=text,
                section_label="unknown",
                section_confidence=0.0,
                description_missing=vacancy.description_missing,
            )
        ]

    source_text = vacancy.description.strip() or vacancy.canonical_text()
    label, confidence, _reason = engine.infer_section_label(source_text)
    return [
        ChunkRecord(
            referenznummer=vacancy.referenznummer,
            chunk_id=f"{vacancy.referenznummer}:0",
            chunk_index=0,
            text=source_text,
            section_label=label,
            section_confidence=confidence,
            description_missing=vacancy.description_missing,
        )
    ]


def _now_utc() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

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

    versions = _build_index_versions(matching_cfg)
    engine = MatchingRuleEngine(cfg)
    run_id = f"build-job-index-{_now_utc().strftime('%Y%m%dT%H%M%SZ')}"

    scanned = 0
    reindexed = 0
    skipped = 0
    failed_quality = 0

    cursor = col_list.find({"is_active": True})
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

        chunks = _build_chunk_records(vacancy=vacancy, rule_flags=decision.flags, engine=engine)
        quality = run_quality_checks(chunks)
        if not quality.hard_passed:
            failed_quality += 1
            col_state.update_one(
                {"referenznummer": ref},
                {
                    "$set": {
                        "referenznummer": ref,
                        "index_status": "quality_failed",
                        "quality_hard_errors": quality.hard_errors,
                        "quality_soft_warnings": quality.soft_warnings,
                        "updated_at": _now_utc(),
                    }
                },
                upsert=True,
            )
            continue

        # В этой версии индексатора фиксируем state и quality.
        # Отдельный шаг: подключение реального embedding расчета и upsert points в Qdrant.
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
                    "index_status": "ready_for_vector_upsert",
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
        "build_job_index finished: run_id=%s scanned=%s reindexed=%s skipped=%s failed_quality=%s",
        run_id,
        scanned,
        reindexed,
        skipped,
        failed_quality,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
