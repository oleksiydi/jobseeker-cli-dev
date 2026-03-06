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
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    load_merged_taxonomy,
)
from tools.taxonomy_extract import content_preview, load_schema, request_extraction
from tools.taxonomy_io import append_checkpoint, append_jsonl, append_process_log, load_checkpoint, write_json
from tools.taxonomy_transform import (
    DEFAULT_DOMAIN_LIST,
    ExtractedTerm,
    apply_runtime_taxonomy_to_row,
    build_aggregate,
    extract_vacancy_domain_top,
    load_domains,
    normalize_domain,
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

    cursor = col_list.find({"is_active": True})
    if max_vacancies > 0:
        cursor = cursor.limit(max_vacancies)

    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for meta in cursor:
        ref = str(meta.get("referenznummer") or "").strip()
        if not ref:
            continue
        details = col_details.find_one({"referenznummer": ref}) or {}
        rows.append((meta, details))
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build term/domain taxonomy artifacts from Mongo vacancies via LLM."
    )
    parser.add_argument("--provider", default=os.getenv("LLM_PROVIDER", "openai"), help="llama_cpp|openai|openrouter")
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "gpt-4o-mini"), help="LLM model id")
    parser.add_argument("--base-url", default=None, help="Optional LLM base URL override")
    parser.add_argument("--api-key", default=None, help="Optional LLM API key override")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="HTTP timeout per vacancy; set 0 to disable timeout.",
    )
    parser.add_argument("--max-vacancies", type=int, default=300, help="Max active vacancies to process (0=all)")
    parser.add_argument("--max-terms-per-vacancy", type=int, default=30, help="Max terms requested per vacancy")
    parser.add_argument("--llm-max-tokens", type=int, default=1100, help="Max LLM output tokens per vacancy")
    parser.add_argument("--max-input-chars", type=int, default=3000, help="Cap per-vacancy text before LLM call")
    parser.add_argument(
        "--preset",
        default="cpu_safe",
        choices=("cpu_safe", "quality_pass"),
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
        default=10,
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
        help="Skip vacancies already marked as ok in checkpoint file.",
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
    parser.add_argument("--dry-run", action="store_true", help="Do not write output files")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    if args.timeout_seconds < 0:
        raise ValueError("--timeout-seconds must be >= 0")
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
    # aggregate schema is loaded for early contract check only.
    _ = load_schema(args.aggregate_schema)

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
        load_merged_taxonomy(
            release_path=args.taxonomy_release_path,
            auto_path=args.taxonomy_auto_path,
        )
        if args.apply_runtime_taxonomy
        else None
    )

    extracted_rows: list[ExtractedTerm] = []
    processed = 0
    failed = 0
    skipped = 0
    checkpoint_every = max(0, args.checkpoint_every)
    checkpoint_seen = load_checkpoint(args.checkpoint_path) if args.resume_from_checkpoint else {}

    if not effective_dry_run and args.overwrite_output:
        args.out_terms_jsonl.parent.mkdir(parents=True, exist_ok=True)
        args.llm_calls_log.parent.mkdir(parents=True, exist_ok=True)
        args.out_terms_jsonl.write_text("", encoding="utf-8")
        args.llm_calls_log.write_text("", encoding="utf-8")

    for meta, details in vacancies:
        raw = MergedVacancyRaw.from_sources(meta, details)
        vacancy = MergedVacancyNormalized.from_raw(raw)
        if not vacancy.referenznummer:
            continue
        if checkpoint_seen.get(vacancy.referenznummer) == "ok":
            skipped += 1
            append_process_log(
                args.process_log,
                {
                    "event": "vacancy_skipped_checkpoint",
                    "run_id": run_id,
                    "referenznummer": vacancy.referenznummer,
                },
                level=args.process_log_level,
            )
            continue
        response = None
        attempt = 0
        payload: dict[str, Any] | None = None
        try:
            while True:
                attempt += 1
                attempt_terms = max(3, args.max_terms_per_vacancy // 2) if attempt > 1 else args.max_terms_per_vacancy
                attempt_input_chars = max(1200, args.max_input_chars - 800) if attempt > 1 else args.max_input_chars
                payload, response = request_extraction(
                    provider=provider,
                    model=args.model,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    timeout_seconds=args.timeout_seconds,
                    llm_max_tokens=args.llm_max_tokens,
                    extraction_schema=extraction_schema,
                    domain_list=domain_list,
                    max_terms_per_vacancy=attempt_terms,
                    max_input_chars=attempt_input_chars,
                    vacancy=vacancy,
                )
                if response.finish_reason == "length" and attempt <= args.retry_on_length + 1:
                    if attempt <= args.retry_on_length:
                        append_process_log(
                            args.process_log,
                            {
                                "event": "retry_on_length",
                                "run_id": run_id,
                                "referenznummer": vacancy.referenznummer,
                                "attempt": attempt,
                                "max_terms_per_vacancy": attempt_terms,
                                "max_input_chars": attempt_input_chars,
                            },
                            level=args.process_log_level,
                        )
                        continue
                break

            vacancy_domain_top, vacancy_domain_conf = extract_vacancy_domain_top(payload.get("vacancy_domains"))
            raw_terms = payload.get("terms", [])
            if not isinstance(raw_terms, list):
                raw_terms = []
            vacancy_rows: list[ExtractedTerm] = []

            for term_item in raw_terms[: args.max_terms_per_vacancy]:
                if not isinstance(term_item, dict):
                    continue
                sanitized = sanitize_term_record(
                    raw=term_item,
                    referenznummer=vacancy.referenznummer,
                    vacancy_domain_top=vacancy_domain_top,
                    vacancy_domain_confidence=vacancy_domain_conf,
                )
                if sanitized is not None:
                    before = {
                        "term": sanitized.term,
                        "term_key": sanitized.term_key,
                        "canonical": sanitized.canonical,
                        "category": sanitized.category,
                        "skill_class": sanitized.skill_class,
                    }
                    if runtime_taxonomy is not None:
                        sanitized = apply_runtime_taxonomy_to_row(sanitized, runtime_taxonomy)
                    after = {
                        "term": sanitized.term,
                        "term_key": sanitized.term_key,
                        "canonical": sanitized.canonical,
                        "category": sanitized.category,
                        "skill_class": sanitized.skill_class,
                    }
                    append_process_log(
                        args.process_log,
                        {
                            "event": "term_normalized",
                            "run_id": run_id,
                            "referenznummer": vacancy.referenznummer,
                            "before": before,
                            "after": after,
                            "facet": sanitized.facet,
                            "domain_hint": sanitized.domain_hint,
                        },
                        level=args.process_log_level,
                    )
                    extracted_rows.append(sanitized)
                    vacancy_rows.append(sanitized)
            term_rows_payload = [
                {
                    "run_id": run_id,
                    "referenznummer": row.referenznummer,
                    "term": row.term,
                    "term_key": row.term_key,
                    "canonical": row.canonical,
                    "category": row.category,
                    "facet": row.facet,
                    "skill_class": row.skill_class,
                    "domain_hint": row.domain_hint,
                    "domain_confidence": row.domain_confidence,
                    "context_phrase": row.context_phrase,
                    "vacancy_domain_top": row.vacancy_domain_top,
                    "vacancy_domain_confidence": row.vacancy_domain_confidence,
                    "provider": provider,
                    "model": args.model,
                    "created_at": now_utc_iso(),
                    "attempt": attempt,
                }
                for row in vacancy_rows
            ]

            processed += 1
            llm_meta_row = {
                "run_id": run_id,
                "status": "ok",
                "referenznummer": vacancy.referenznummer,
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
                "finish_reason": response.finish_reason,
                "elapsed_ms": response.elapsed_ms,
                "started_at": response.started_at,
                "usage": response.usage,
                "timings": response.timings,
            }
            if not effective_dry_run:
                append_jsonl(args.out_terms_jsonl, term_rows_payload)
                append_jsonl(args.llm_calls_log, [llm_meta_row])
                if checkpoint_every > 0 and processed % checkpoint_every == 0:
                    checkpoint = build_aggregate(
                        extracted_rows=extracted_rows,
                        provider=provider,
                        model=args.model,
                        scanned_vacancies=scanned,
                        processed_vacancies=processed,
                        failed_vacancies=failed,
                    )
                    checkpoint["run_id"] = run_id
                    checkpoint["checkpoint"] = True
                    write_json(args.out_aggregate_json, checkpoint)
                append_checkpoint(args.checkpoint_path, run_id=run_id, referenznummer=vacancy.referenznummer, status="ok")
        except Exception:
            failed += 1
            exc_type, exc_value, _ = sys.exc_info()
            append_process_log(
                args.process_log,
                {
                    "event": "vacancy_failed",
                    "run_id": run_id,
                    "referenznummer": vacancy.referenznummer,
                    "error_type": exc_type.__name__ if exc_type else "Exception",
                    "error_message": str(exc_value)[:2000] if exc_value else "",
                },
                level=args.process_log_level,
            )
            LOGGER.exception("vacancy_failed run_id=%s referenznummer=%s", run_id, vacancy.referenznummer)
            if not effective_dry_run:
                error_row: dict[str, Any] = {
                    "run_id": run_id,
                    "status": "error",
                    "referenznummer": vacancy.referenznummer,
                    "provider": provider,
                    "model": args.model,
                    "error_type": exc_type.__name__ if exc_type else "Exception",
                    "error_message": str(exc_value)[:2000] if exc_value else "",
                    "created_at": now_utc_iso(),
                }
                if response is not None:
                    error_row["response_finish_reason"] = response.finish_reason
                    error_row["response_id"] = response.response_id
                    error_row["response_content_preview"] = content_preview(response.content)
                append_jsonl(
                    args.llm_calls_log,
                    [error_row],
                )
                append_checkpoint(args.checkpoint_path, run_id=run_id, referenznummer=vacancy.referenznummer, status="error")
            continue

    aggregate = build_aggregate(
        extracted_rows=extracted_rows,
        provider=provider,
        model=args.model,
        scanned_vacancies=scanned,
        processed_vacancies=processed,
        failed_vacancies=failed,
    )
    aggregate["run_id"] = run_id
    aggregate["checkpoint"] = False

    if not effective_dry_run:
        write_json(args.out_aggregate_json, aggregate)
    elif not args.allow_learning:
        print("learning_disabled_skip_write=true")

    append_process_log(
        args.process_log,
        {
            "event": "run_finished",
            "run_id": run_id,
            "scanned": scanned,
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "term_rows": len(extracted_rows),
            "provider": provider,
            "model": args.model,
            "effective_dry_run": effective_dry_run,
        },
        level=args.process_log_level,
    )
    LOGGER.info(
        "run_finished run_id=%s scanned=%s processed=%s failed=%s skipped=%s term_rows=%s",
        run_id,
        scanned,
        processed,
        failed,
        skipped,
        len(extracted_rows),
    )

    print(
        f"DONE: scanned={scanned}, processed={processed}, failed={failed}, skipped={skipped}, "
        f"term_rows={len(extracted_rows)}, provider={provider}, model={args.model}, "
        f"dry_run={effective_dry_run}, allow_learning={args.allow_learning}"
    )
    print(f"terms_jsonl={args.out_terms_jsonl}")
    print(f"aggregate_json={args.out_aggregate_json}")
    print(f"llm_calls_log={args.llm_calls_log}")
    print(f"process_log={args.process_log}")


if __name__ == "__main__":
    main()
