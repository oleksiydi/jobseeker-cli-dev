#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.normalization.synonyms_state import (
    SynonymStateCandidate,
    build_synonym_state_document,
    build_synonyms_audit_event,
    ensure_synonyms_state_indexes,
    upsert_synonym_state,
)
from tools.dictionary_settings import dictionary_settings
from tools.taxonomy_io import write_jsonl
from tools.llm_artifact_cache import ArtifactCache, input_hash
from tools.synonyms_cluster import build_proposals
from tools.taxonomy_transform import normalize_term_key, safe_float
from app.normalization.loader import load_runtime_taxonomy

LOGGER = logging.getLogger('tools.build_synonyms_from_taxonomy')


@dataclass(frozen=True)
class CandidateGroup:
    alias: str
    canonical: str
    domain: str
    category: str
    facet: str
    skill_class: str
    confidence: float
    source_refs_count: int
    sample_phrases: tuple[str, ...]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_dry_run(*, dry_run: bool, allow_learning: bool) -> bool:
    return dry_run or (not allow_learning)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _aggregate_index(aggregate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    terms = aggregate.get("terms", [])
    if not isinstance(terms, list):
        return result
    for item in terms:
        if not isinstance(item, dict):
            continue
        term_key = normalize_term_key(str(item.get("term_key") or ""))
        if not term_key:
            continue
        result[term_key] = item
    return result


def _top_domain(aggregate_item: dict[str, Any]) -> str:
    votes = aggregate_item.get("domain_votes")
    if not isinstance(votes, dict) or not votes:
        return "sonstiges"

    ranked = sorted(
        votes.items(),
        key=lambda kv: (
            int(kv[1].get("count", 0)) if isinstance(kv[1], dict) else 0,
            float(kv[1].get("avg_confidence", 0.0)) if isinstance(kv[1], dict) else 0.0,
            str(kv[0]),
        ),
        reverse=True,
    )
    return str(ranked[0][0] or "sonstiges")


def _compute_confidence(*, support_count: int, avg_domain_confidence: float) -> float:
    support_boost = min(0.35, support_count * 0.05)
    return max(0.0, min(1.0, 0.55 + support_boost + avg_domain_confidence * 0.1))


def _build_candidate_groups(
    *,
    raw_terms: list[dict[str, Any]],
    aggregate_by_term_key: dict[str, dict[str, Any]],
) -> list[CandidateGroup]:
    bucket: dict[tuple[str, str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "skill_class_counter": Counter(),
            "domain_conf_sum": 0.0,
            "support_count": 0,
            "source_refs": set(),
            "sample_phrases": set(),
        }
    )

    for row in raw_terms:
        term = str(row.get("term") or "").strip()
        if not term:
            continue
        term_key = normalize_term_key(str(row.get("term_key") or term))
        if not term_key:
            continue
        aggregate_item = aggregate_by_term_key.get(term_key, {})

        canonical = str(aggregate_item.get("canonical") or row.get("canonical") or term).strip()
        category = str(aggregate_item.get("primary_category") or row.get("category") or "skills").strip()
        facet = str(aggregate_item.get("primary_facet") or row.get("facet") or "other").strip()
        domain = str(row.get("domain_hint") or _top_domain(aggregate_item) or "sonstiges").strip()
        skill_class = str(aggregate_item.get("primary_skill_class") or row.get("skill_class") or "professional").strip()
        context_phrase = str(row.get("context_phrase") or "").strip()
        domain_conf = safe_float(row.get("domain_confidence"), default=0.0)

        key = (normalize_term_key(term), canonical, domain, category, facet)
        item = bucket[key]
        item["skill_class_counter"][skill_class] += 1
        item["domain_conf_sum"] += domain_conf
        item["support_count"] += 1
        if row.get("referenznummer"):
            item["source_refs"].add(row["referenznummer"])
        if context_phrase:
            item["sample_phrases"].add(context_phrase)

    groups: list[CandidateGroup] = []
    for key in sorted(bucket.keys()):
        alias_key, canonical, domain, category, facet = key
        item = bucket[key]
        if alias_key == normalize_term_key(canonical):
            continue
        support_count = len(item["source_refs"])
        avg_domain_conf = item["domain_conf_sum"] / item["support_count"] if item["support_count"] else 0.0
        skill_class = item["skill_class_counter"].most_common(1)[0][0] if item["skill_class_counter"] else "professional"
        sample_phrases = tuple(sorted(item["sample_phrases"])[:5])
        groups.append(
            CandidateGroup(
                alias=alias_key,
                canonical=canonical,
                domain=domain,
                category=category,
                facet=facet,
                skill_class=skill_class,
                confidence=_compute_confidence(support_count=support_count, avg_domain_confidence=avg_domain_conf),
                source_refs_count=support_count,
                sample_phrases=sample_phrases,
            )
        )
    return groups


def parse_args() -> argparse.Namespace:
    settings = dictionary_settings()
    parser = argparse.ArgumentParser(description="Build synonyms with a small model; escalate ambiguous mappings.")
    parser.add_argument(
        "--aggregate-json",
        type=Path,
        default=Path("logs/normalization/taxonomy_domain_aggregate.json"),
        help="Aggregate JSON input from taxonomy extraction stage.",
    )
    parser.add_argument(
        "--terms-jsonl",
        type=Path,
        default=Path("logs/normalization/vacancy_terms.jsonl"),
        help="Raw terms JSONL input from taxonomy extraction stage.",
    )
    parser.add_argument(
        "--snapshot-jsonl",
        type=Path,
        default=Path("logs/normalization/synonyms_state_snapshot.jsonl"),
        help="Optional JSONL snapshot of inserted candidate documents.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Limit number of candidates to process (0=all).",
    )
    parser.add_argument(
        "--allow-learning",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("ALLOW_VACANCY_LEARNING", False),
        help="Allow writing to Mongo state collections (off by default unless enabled).",
    )
    parser.add_argument("--run-id", default="", help="Optional run id override.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to Mongo collections.")
    parser.add_argument("--mode", choices=("llm", "deterministic"), default="llm")
    parser.add_argument('--reference-first', action=argparse.BooleanOptionalAction,
                        default=settings['reference_first'], help='Reuse verified names before clustering new terms')
    parser.add_argument("--provider", default=settings["extraction_provider"])
    parser.add_argument("--model", default=settings["extraction_model"])
    parser.add_argument("--review-provider", default=settings["review_provider"])
    parser.add_argument("--review-model", default=settings["review_model"])
    parser.add_argument("--escalation-provider", default=settings["escalation_provider"])
    parser.add_argument("--escalation-model", default=settings["escalation_model"])
    parser.add_argument("--block-size", type=int, default=160)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--cache-path", type=Path, default=Path("logs/normalization/synonyms_cache.sqlite3"))
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    effective_dry_run = _effective_dry_run(dry_run=args.dry_run, allow_learning=args.allow_learning)
    run_id = args.run_id.strip() or f"syn-taxo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    raw_terms = _iter_jsonl(args.terms_jsonl)
    groups = []
    proposal_by_key = {}
    reference_index, cfg, db = None, None, None
    reference_metrics = {}
    if getattr(args, "mode", "deterministic") == "llm":
        if not 2 <= args.block_size <= 400:
            raise ValueError("--block-size must be between 2 and 400")
        if effective_dry_run:
            from tools.synonyms_cluster import build_blocks
            print(f"dry_run=true planned_blocks={len(build_blocks(raw_terms, args.block_size))} api_calls=0")
            return
        if getattr(args, 'reference_first', False):
            from tools.reference_prefilter import load_index
            cfg = load_app_config()
            db = get_database(cfg)
            reference_index = load_index(db, load_runtime_taxonomy())
        cache = ArtifactCache(args.cache_path)
        try:
            proposals = build_proposals(raw_terms, provider=args.provider, model=args.model,
                cache=cache, block_size=args.block_size, timeout_seconds=args.timeout_seconds,
                review_provider=args.review_provider, review_model=args.review_model,
                escalation_provider=args.escalation_provider, escalation_model=args.escalation_model,
                **({'reference_index': reference_index, 'metrics': reference_metrics} if reference_index is not None else {}))
        finally:
            cache.close()
        if reference_index is not None:
            LOGGER.info('Reference synonym prefilter: %s', reference_metrics)
        groups = [CandidateGroup(**{field: item[field] for field in CandidateGroup.__dataclass_fields__})
                  for item in proposals]
        proposal_by_key = {(item["alias"], item["canonical"], item["domain"], item["category"], item["facet"]): item
                           for item in proposals}
    else:
        aggregate = _load_json(args.aggregate_json)
        groups = _build_candidate_groups(raw_terms=raw_terms, aggregate_by_term_key=_aggregate_index(aggregate))
    if args.max_candidates > 0:
        groups = groups[: args.max_candidates]

    cfg = cfg if cfg is not None else load_app_config()
    state_collection_name = resolve_collection_name(
        cfg,
        key="normalization_synonyms_state",
        default="normalization_synonyms_state",
    )
    audit_collection_name = resolve_collection_name(
        cfg,
        key="normalization_synonyms_audit",
        default="normalization_synonyms_audit",
    )
    state_collection = None
    audit_collection = None
    if not effective_dry_run:
        db = db if db is not None else get_database(cfg)
        state_collection = db[state_collection_name]
        audit_collection = db[audit_collection_name]
        ensure_synonyms_state_indexes(state_collection, audit_collection)

    inserted = 0
    snapshot_rows: list[dict[str, Any]] = []

    for group in groups:
        proposal = proposal_by_key.get((group.alias, group.canonical, group.domain, group.category, group.facet))
        origin = proposal.get('origin', 'llm_cluster') if proposal is not None else 'deterministic_builder'
        if origin == 'reference_prefilter' and state_collection is not None:
            previous = state_collection.find_one({'alias_key': normalize_term_key(group.alias),
                'domain': group.domain, 'category': group.category, 'facet': group.facet})
            if (previous and previous.get('decision_source') != 'reference_prefilter'
                    and previous.get('processing_status') not in {None, 'new'}):
                # A dictionary match must not override a scoped review/human
                # decision, including a previously rejected or conflicting pair.
                LOGGER.info('Reference synonym: existing scoped decision preserved')
                continue
        candidate = SynonymStateCandidate(
            alias=group.alias,
            canonical=group.canonical,
            domain=group.domain,
            category=group.category,
            facet=group.facet,
            skill_class=group.skill_class,
            confidence=group.confidence,
            source=origin,
            run_id=run_id,
            source_refs_count=group.source_refs_count,
            sample_phrases=group.sample_phrases,
        )
        document = build_synonym_state_document(candidate)
        if proposal is not None:
            document["processing_status"] = "needs_adjudication" if proposal["requires_review"] else "accepted"
            document["decision_source"] = origin
            if proposal.get('reference_id'):
                document['dictionary_reference'] = {'id': proposal['reference_id'],
                    'version': reference_index.version if reference_index is not None else None}
            if proposal["reviewed_by_strong"] and proposal["requires_review"]:
                document["processing_status"] = "unresolved"
            decision_stage = ("pro" if proposal.get("review_stage") == "pro"
                              else "escalated" if proposal["reviewed_by_strong"] else "primary")
            if proposal.get("flash_failure"):
                document["adjudication"]["escalated"] = proposal["flash_failure"]
            document["adjudication"][decision_stage] = {
                "status": "done", "decision": "unresolved" if proposal["requires_review"] else "accept",
                "confidence": group.confidence, "provider": proposal["provider"], "model": proposal["model"],
                "reason": proposal["reason"],
            }
        snapshot_row = {
            "run_id": run_id,
            "alias": document["alias"],
            "alias_key": document["alias_key"],
            "canonical": document["canonical"],
            "domain": document["domain"],
            "category": document["category"],
            "facet": document["facet"],
            "skill_class": document["skill_class"],
            "confidence": document["confidence"],
            "source_refs_count": document["source_refs_count"],
            "processing_status": document["processing_status"],
            **({'decision_source': document.get('decision_source'),
                'dictionary_reference': document['dictionary_reference']}
               if document.get('dictionary_reference') else {}),
            "created_at": _now_utc_iso(),
        }
        snapshot_rows.append(snapshot_row)

        if effective_dry_run:
            continue

        if state_collection is None or audit_collection is None:
            raise RuntimeError("state/audit collections are not initialized")

        result = upsert_synonym_state(state_collection, document)
        if result.matched_count == 0 and result.upserted_id is None:
            raise RuntimeError("Candidate changed concurrently; rerun the pipeline")
        stored = state_collection.find_one({field: document[field] for field in
            ("alias_key", "canonical", "domain", "category", "facet")})
        if stored is None:
            raise RuntimeError("Candidate state disappeared after upsert")
        state_id = stored["_id"]
        snapshot_row.update({field: stored[field] for field in snapshot_row if field in stored})
        inserted += int(result.upserted_id is not None or result.modified_count > 0)
        audit_event = build_synonyms_audit_event(
            state_id=state_id,
            event_type="candidate_built",
            run_id=run_id,
            payload={
                "canonical": document["canonical"],
                "domain": document["domain"],
                "category": document["category"],
                "facet": document["facet"],
            },
        )
        event_key = input_hash({"state_id": str(state_id), "event_type": "candidate_built",
            "candidate": {field: document[field] for field in ("alias_key", "canonical", "domain", "category", "facet", "source_refs_count", "sample_phrases", "confidence")},
            "primary": document["adjudication"], "source": document["source"]})
        # Повтор после сбоя допишет отсутствующий audit, но не создаст дубль.
        audit_collection.update_one({"_id": event_key}, {"$setOnInsert": audit_event}, upsert=True)

    write_jsonl(args.snapshot_jsonl, snapshot_rows)

    logging.getLogger("synonyms_builder").info("candidates=%s changed=%s", len(groups), inserted)
    print(
        f"run_id={run_id} candidates={len(groups)} changed={inserted} "
        f"effective_dry_run={effective_dry_run} state_collection={state_collection_name} "
        f"audit_collection={audit_collection_name} snapshot_jsonl={args.snapshot_jsonl}"
    )


if __name__ == "__main__":
    main()
