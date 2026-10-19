#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build term/domain taxonomy artifacts from real vacancies in MongoDB.

Goal of this stage:
- extract terms from vacancies with LLM (LLM-first approach);
- keep domain hint per term;
- produce two artifacts:
  1) term rows JSONL (one row per extracted term);
  2) aggregated JSON with votes by category/domain.

This script is intentionally additive and does not mutate production collections.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from jsonschema import Draft202012Validator
from pathlib import Path
from typing import Any

# Allow "python tools/..." execution with imports from project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.llm.factory import resolve_provider_name
from app.matching.models import MergedVacancyRaw, MergedVacancyNormalized
from app.normalization.loader import (
    DEFAULT_SYNONYMS_AUTO_PATH,
    DEFAULT_SYNONYMS_RELEASE_PATH,
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    load_runtime_taxonomy,
)
from tools.dictionary_settings import dictionary_settings
from tools.taxonomy_extract import (build_system_prompt, build_user_prompt, load_schema, request_extraction)
from tools.llm_artifact_cache import ArtifactCache, input_hash
from tools.taxonomy_io import append_checkpoint, append_jsonl, append_process_log, write_json, write_jsonl
from tools.taxonomy_transform import (
    DEFAULT_DOMAIN_LIST,
    ExtractedTerm,
    apply_runtime_taxonomy_to_row,
    build_aggregate,
    extract_vacancy_domain_top,
    load_domains,
    normalize_domain,
    normalize_surface_text,
    now_utc_iso,
    sanitize_term_record,
)

LOGGER = logging.getLogger("tools.build_taxonomy_from_vacancies")

# Backward-compatible aliases for existing tests/internal imports.
_normalize_domain = normalize_domain
_load_domains = load_domains
_sanitize_term_record = sanitize_term_record
_apply_runtime_taxonomy_to_row = apply_runtime_taxonomy_to_row
_build_aggregate = build_aggregate


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_dry_run(*, dry_run: bool, allow_learning: bool) -> bool:
    return dry_run or (not allow_learning)


def _apply_preset_limits(*, preset: str, max_terms_per_vacancy: int, max_input_chars: int) -> tuple[int, int]:
    if preset == "cpu_safe":
        return min(max_terms_per_vacancy, 10), min(max_input_chars, 3000)
    if preset == "quality_pass":
        return min(max_terms_per_vacancy, 15), min(max_input_chars, 3000)
    return max_terms_per_vacancy, max_input_chars


def _iter_active_vacancies(db: Any, cfg: dict[str, Any], max_vacancies: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    details_collection_name = resolve_collection_name(cfg, key="details", default="jobdetails")
    col_list = db[list_collection_name]
    col_details = db[details_collection_name]

    cursor = col_list.find({"is_active": True}).sort("referenznummer", 1)
    if max_vacancies > 0:
        cursor = cursor.limit(max_vacancies)

    # Загружаем выбранные вакансии одним списком, затем детали небольшими
    # группами: это сохраняет порядок списка и заменяет запрос на каждую вакансию
    # несколькими запросами $in.
    vacancies: list[tuple[dict[str, Any], str]] = []
    for meta in cursor:
        ref = str(meta.get("referenznummer") or "").strip()
        if not ref:
            continue
        vacancies.append((meta, ref))

    details_by_ref: dict[str, dict[str, Any]] = {}
    batch_size = 200
    refs = [ref for _, ref in vacancies]
    for offset in range(0, len(refs), batch_size):
        batch_refs = refs[offset : offset + batch_size]
        for details in col_details.find({"referenznummer": {"$in": batch_refs}}):
            ref = str(details.get("referenznummer") or "").strip()
            if ref:
                details_by_ref.setdefault(ref, details)

    return [(meta, details_by_ref.get(ref, {})) for meta, ref in vacancies]


def parse_args() -> argparse.Namespace:
    settings = dictionary_settings()
    parser = argparse.ArgumentParser(
        description="Build term/domain taxonomy artifacts from Mongo vacancies via LLM."
    )
    parser.add_argument("--provider", default=settings["extraction_provider"], help="llama_cpp|openai|openrouter")
    parser.add_argument('--reference-first', action=argparse.BooleanOptionalAction,
                        default=settings['reference_first'], help='Local references first; send only residual context to LLM')
    parser.add_argument("--model", default=settings["extraction_model"], help="LLM model id")
    parser.add_argument("--base-url", default=None, help="Optional LLM base URL override")
    parser.add_argument("--api-key", default=None, help="Optional LLM API key override")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Positive HTTP timeout per vacancy.",
    )
    parser.add_argument("--max-vacancies", type=int, default=300, help="Max active vacancies to process (0=all)")
    parser.add_argument("--max-terms-per-vacancy", type=int, default=30, help="Max terms requested per vacancy")
    parser.add_argument("--llm-max-tokens", type=int, default=4096, help="Max LLM output tokens per vacancy")
    parser.add_argument("--max-input-chars", type=int, default=12000, help="Cap per-vacancy text before LLM call")
    parser.add_argument(
        "--preset",
        default="cloud",
        choices=("cloud", "cpu_safe", "quality_pass"),
        help="Preset for local runs. cpu_safe lowers term volume/retry risk.",
    )
    parser.add_argument(
        "--extraction-schema",
        type=Path,
        default=Path("conf/normalization/schemas/vacancy_term_extraction.schema.json"),
        help="JSON schema for LLM extraction response",
    )
    parser.add_argument(
        "--aggregate-schema",
        type=Path,
        default=Path("conf/normalization/schemas/taxonomy_domain_aggregate.schema.json"),
        help="Schema path for aggregate contract documentation",
    )
    parser.add_argument(
        "--out-terms-jsonl",
        type=Path,
        default=Path("logs/normalization/vacancy_terms.jsonl"),
        help="Output JSONL with extracted term rows",
    )
    parser.add_argument(
        "--out-aggregate-json",
        type=Path,
        default=Path("logs/normalization/taxonomy_domain_aggregate.json"),
        help="Output aggregate JSON path",
    )
    parser.add_argument(
        "--llm-calls-log",
        type=Path,
        default=Path("logs/normalization/vacancy_taxonomy_llm_calls.jsonl"),
        help="JSONL log for per-call LLM metadata",
    )
    parser.add_argument(
        "--process-log",
        type=Path,
        default=Path("logs/normalization/vacancy_taxonomy_process.jsonl"),
        help="JSONL process log (start/normalization/error/end).",
    )
    parser.add_argument(
        "--process-log-level",
        default="summary",
        choices=("summary", "full"),
        help="summary excludes per-term rows, full includes term_normalized events.",
    )
    parser.add_argument(
        "--retry-on-length",
        type=int,
        default=1,
        help="Additional attempts when finish_reason=length (prompt is compressed on retry).",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=0,
        help="Write aggregate checkpoint every N successful vacancies (0=only final write).",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Truncate output JSONL files before run.",
    )
    parser.add_argument(
        "--domains-file",
        type=Path,
        default=Path("conf/normalization/domains.level1.de.json"),
        help="Path to level-1 domain vocabulary JSON.",
    )
    parser.add_argument(
        "--domain-list",
        default="",
        help="Optional comma-separated domain override (if empty, use --domains-file).",
    )
    parser.add_argument(
        "--allow-learning",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("ALLOW_VACANCY_LEARNING", False),
        help="Allow writing taxonomy artifacts (default from ALLOW_VACANCY_LEARNING, off by default).",
    )
    parser.add_argument(
        "--checkpoint-path",
        type=Path,
        default=Path("logs/normalization/vacancy_taxonomy_checkpoint.jsonl"),
        help="Checkpoint JSONL to support resume.",
    )
    parser.add_argument(
        "--resume-from-checkpoint",
        action="store_true",
        help="Compatibility flag: validated content cache resumes automatically; ref-only checkpoint is ignored.",
    )
    parser.add_argument(
        "--apply-runtime-taxonomy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply merged runtime taxonomy (release+auto) to extracted vacancy terms.",
    )
    parser.add_argument(
        "--taxonomy-release-path",
        type=Path,
        default=DEFAULT_TAXONOMY_RELEASE_PATH,
        help="Path to release taxonomy JSON layer for runtime mapping.",
    )
    parser.add_argument(
        "--taxonomy-auto-path",
        type=Path,
        default=DEFAULT_TAXONOMY_AUTO_PATH,
        help="Path to auto taxonomy JSON layer for runtime mapping.",
    )
    parser.add_argument(
        "--synonyms-release-path",
        type=Path,
        default=DEFAULT_SYNONYMS_RELEASE_PATH,
        help="Path to release synonyms JSON layer for runtime mapping.",
    )
    parser.add_argument(
        "--synonyms-auto-path",
        type=Path,
        default=DEFAULT_SYNONYMS_AUTO_PATH,
        help="Path to auto synonyms JSON layer for runtime mapping.",
    )
    parser.add_argument("--cache-path", type=Path,
                        default=Path("logs/normalization/taxonomy_cache.sqlite3"),
                        help="Validated response cache; changing input/model/prompt invalidates cache")
    parser.add_argument("--dry-run", action="store_true", help="Do not write output files")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be > 0")
    if args.max_terms_per_vacancy <= 0:
        raise ValueError("--max-terms-per-vacancy must be > 0")
    if args.llm_max_tokens <= 0:
        raise ValueError("--llm-max-tokens must be > 0")
    if args.max_input_chars <= 200:
        raise ValueError("--max-input-chars must be > 200")
    if args.retry_on_length < 0:
        raise ValueError("--retry-on-length must be >= 0")
    effective_dry_run = _effective_dry_run(dry_run=args.dry_run, allow_learning=args.allow_learning)

    provider = resolve_provider_name(args.provider)
    args.max_terms_per_vacancy, args.max_input_chars = _apply_preset_limits(
        preset=args.preset,
        max_terms_per_vacancy=args.max_terms_per_vacancy,
        max_input_chars=args.max_input_chars,
    )

    if args.domain_list.strip():
        domain_list = [normalize_domain(item) for item in args.domain_list.split(",") if normalize_domain(item)]
    else:
        domain_list = load_domains(args.domains_file)
    if not domain_list:
        domain_list = DEFAULT_DOMAIN_LIST[:]

    extraction_schema = load_schema(args.extraction_schema)
    for field in ("vacancy_domains", "terms"):
        item_schema = extraction_schema.get("properties", {}).get(field, {}).get("items", {})
        domain_field = "domain" if field == "vacancy_domains" else "domain_hint"
        if domain_field in item_schema.get("properties", {}):
            item_schema["properties"][domain_field]["enum"] = domain_list
    aggregate_validator = Draft202012Validator(load_schema(args.aggregate_schema))

    if provider == "openrouter" and (args.model.endswith(":free") or args.model == "openrouter/free"):
        raise ValueError("Dictionary pipeline requires a paid OpenRouter model")
    if effective_dry_run:
        print(f"dry_run=true max_vacancies={args.max_vacancies} api_calls=0 writes=0")
        return
    cfg = load_app_config()
    db = get_database(cfg)
    vacancies = _iter_active_vacancies(db, cfg, max_vacancies=max(0, args.max_vacancies))
    scanned = len(vacancies)
    run_id = f"vac-taxo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    append_process_log(
        args.process_log,
        {
            "event": "run_started",
            "run_id": run_id,
            "provider": provider,
            "model": args.model,
            "max_vacancies": args.max_vacancies,
            "max_terms_per_vacancy": args.max_terms_per_vacancy,
            "llm_max_tokens": args.llm_max_tokens,
            "max_input_chars": args.max_input_chars,
            "timeout_seconds": args.timeout_seconds,
            "allow_learning": args.allow_learning,
            "dry_run": args.dry_run,
            "effective_dry_run": effective_dry_run,
        },
        level=args.process_log_level,
    )
    LOGGER.info(
        "run_started run_id=%s provider=%s model=%s max_vacancies=%s max_terms=%s llm_max_tokens=%s max_input_chars=%s timeout_seconds=%s effective_dry_run=%s",
        run_id,
        provider,
        args.model,
        args.max_vacancies,
        args.max_terms_per_vacancy,
        args.llm_max_tokens,
        args.max_input_chars,
        args.timeout_seconds,
        effective_dry_run,
    )
    runtime_taxonomy = (
        load_runtime_taxonomy(
            taxonomy_release_path=args.taxonomy_release_path,
            taxonomy_auto_path=args.taxonomy_auto_path,
            synonyms_release_path=args.synonyms_release_path,
            synonyms_auto_path=args.synonyms_auto_path,
        )
        if args.apply_runtime_taxonomy
        else None
    )

    # Кэш хранит проверенный сырой ответ. Runtime mapping повторяем с текущим словарём.
    reference_index = None
    reference_metrics = {}
    if getattr(args, 'reference_first', False):
        from tools.reference_prefilter import load_index
        reference_index = load_index(db, runtime_taxonomy)

    cache = ArtifactCache(getattr(
        args, "cache_path", args.out_terms_jsonl.with_suffix(".cache.sqlite3")))
    extracted_rows: list[ExtractedTerm] = []
    term_rows_payload: list[dict[str, Any]] = []
    processed = failed = skipped = cache_hits = rejected_terms = 0
    validator = Draft202012Validator(extraction_schema)

    def save_snapshot() -> None:
        aggregate = build_aggregate(
            extracted_rows=extracted_rows, provider=provider, model=args.model,
            scanned_vacancies=scanned, processed_vacancies=processed,
            failed_vacancies=failed,
        )
        aggregate["run_id"] = run_id
        aggregate["cache_hits"] = cache_hits
        if reference_index is not None:
            aggregate['reference_prefilter'] = {**reference_metrics, 'enabled': True,
                                               'reference_version': reference_index.version}
        aggregate_validator.validate(aggregate)
        write_jsonl(args.out_terms_jsonl, term_rows_payload)
        write_json(args.out_aggregate_json, aggregate)

    try:
        for meta, details in vacancies:
            vacancy = MergedVacancyNormalized.from_raw(MergedVacancyRaw.from_sources(meta, details))
            if vacancy.description_missing:
                skipped += 1
                continue
            if reference_index is not None:
                from tools.reference_extraction import extract
                try:
                    rows, metrics = extract(vacancy, reference_index, runtime_taxonomy, args,
                                            domain_list, extraction_schema, cache, run_id,
                                            totals=reference_metrics)
                    cache_hits += metrics['reference_cache_hits']
                    extracted_rows.extend(row for row, _ in rows)
                    term_rows_payload.extend(metadata for _, metadata in rows)
                    processed += 1
                    append_checkpoint(args.checkpoint_path, run_id=run_id,
                                      referenznummer=vacancy.referenznummer, status='ok',
                                      input_hash=reference_index.version)
                    LOGGER.info('Reference prefilter: local=%s removed_segments=%s api_calls=%s cache_hits=%s',
                                metrics['reference_local_terms'], metrics['reference_removed_segments'],
                                metrics['reference_api_calls'], metrics['reference_cache_hits'])
                    if args.checkpoint_every > 0 and processed % args.checkpoint_every == 0:
                        save_snapshot()
                except Exception as exc:
                    failed += 1
                    LOGGER.error('vacancy_failed ref=%s error_type=%s', vacancy.referenznummer, type(exc).__name__)
                    append_process_log(args.process_log, {
                        'event': 'vacancy_failed', 'run_id': run_id,
                        'referenznummer': vacancy.referenznummer, 'error_type': type(exc).__name__,
                        'stage': 'reference_residual'}, level=args.process_log_level)
                continue
            key = input_hash({
                "provider": provider, "model": args.model, "base_url": args.base_url,
                "schema": extraction_schema, "max_tokens": args.llm_max_tokens,
                "system": build_system_prompt(domain_list, args.max_terms_per_vacancy),
                "user": build_user_prompt(vacancy, args.max_input_chars),
            })
            response = None
            attempt = 0
            try:
                payload = None if args.overwrite_output else cache.get(key)
                if payload is not None:
                    cache_hits += 1
                else:
                    for attempt in range(1, args.retry_on_length + 2):
                        payload, response = request_extraction(
                            provider=provider, model=args.model, base_url=args.base_url,
                            api_key=args.api_key, timeout_seconds=args.timeout_seconds,
                            llm_max_tokens=args.llm_max_tokens, extraction_schema=extraction_schema,
                            domain_list=domain_list,
                            max_terms_per_vacancy=(max(3, args.max_terms_per_vacancy // 2)
                                                  if attempt > 1 else args.max_terms_per_vacancy),
                            max_input_chars=args.max_input_chars, vacancy=vacancy,
                        )
                        if response.finish_reason != "length":
                            break
                        append_process_log(args.process_log, {
                            "event": "retry_on_length", "run_id": run_id,
                            "referenznummer": vacancy.referenznummer, "attempt": attempt,
                        }, level=args.process_log_level)
                    if response.finish_reason != "stop":
                        raise ValueError("Incomplete taxonomy response")
                validator.validate(payload)
                source_text = normalize_surface_text(vacancy.canonical_text()[:args.max_input_chars])
                domain_top, domain_conf = extract_vacancy_domain_top(payload.get("vacancy_domains"))
                vacancy_rows = []
                seen_terms = set()
                for term_item in payload.get("terms", [])[:args.max_terms_per_vacancy]:
                    row = sanitize_term_record(
                        raw=term_item, referenznummer=vacancy.referenznummer,
                        vacancy_domain_top=domain_top, vacancy_domain_confidence=domain_conf,
                    )
                    # Проверяем цитату по настоящему входу, а не по словам самой модели.
                    if row is None or normalize_surface_text(row.context_phrase) not in source_text:
                        rejected_terms += 1
                        continue
                    if row.domain_hint not in domain_list:
                        rejected_terms += 1
                        continue
                    if runtime_taxonomy is not None:
                        row = apply_runtime_taxonomy_to_row(row, runtime_taxonomy)
                    identity = (row.term_key, row.category, row.facet, row.domain_hint)
                    if identity in seen_terms:
                        continue
                    seen_terms.add(identity)
                    vacancy_rows.append(row)
                if response is not None:
                    cache.put(key, payload)
                extracted_rows.extend(vacancy_rows)
                for row in vacancy_rows:
                    term_rows_payload.append({**row.__dict__, "run_id": run_id,
                        "provider": provider, "model": args.model,
                        "input_hash": key, "created_at": now_utc_iso(), "attempt": attempt})
                processed += 1
                if response is not None:
                    append_jsonl(args.llm_calls_log, [{
                        "run_id": run_id, "status": "ok", "referenznummer": vacancy.referenznummer,
                        "provider": response.provider, "model": response.model,
                        "finish_reason": response.finish_reason, "usage": response.usage,
                        "elapsed_ms": response.elapsed_ms,
                    }])
                if response is not None:
                    append_checkpoint(args.checkpoint_path, run_id=run_id,
                                      referenznummer=vacancy.referenznummer, status="ok", input_hash=key)
                if args.checkpoint_every > 0 and processed % args.checkpoint_every == 0:
                    save_snapshot()
            except Exception as exc:
                failed += 1
                LOGGER.error("vacancy_failed ref=%s error_type=%s", vacancy.referenznummer,
                             type(exc).__name__)
                append_process_log(args.process_log, {
                    "event": "vacancy_failed", "run_id": run_id,
                    "referenznummer": vacancy.referenznummer, "error_type": type(exc).__name__,
                }, level=args.process_log_level)
        save_snapshot()
    finally:
        cache.close()
    append_process_log(args.process_log, {
        "event": "run_finished", "run_id": run_id, "scanned": scanned,
        "processed": processed, "failed": failed, "skipped": skipped,
        "cache_hits": cache_hits, "rejected_terms": rejected_terms,
        "term_rows": len(extracted_rows), "effective_dry_run": effective_dry_run,
    }, level=args.process_log_level)
    print(f"DONE: scanned={scanned} processed={processed} failed={failed} skipped={skipped} "
          f"cache_hits={cache_hits} rejected_terms={rejected_terms} term_rows={len(extracted_rows)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
