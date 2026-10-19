#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.normalization.synonyms_state import (
    build_synonyms_audit_event,
    ensure_synonyms_state_indexes,
    write_synonyms_audit_event,
)

ACCEPTED_STATUS = "accepted"
REJECTED_STATUS = "rejected"
NEEDS_ADJUDICATION_STATUS = "needs_adjudication"
AUTO_GATE_DECISION_SOURCE = "auto_gate"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def classify_candidate(
    document: dict[str, Any],
    *,
    min_accept_confidence: float,
    min_accept_support: int,
    max_reject_confidence: float,
    min_reject_support: int,
    min_alias_len: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    alias = str(document.get("alias") or "").strip()
    confidence = _safe_float(document.get("confidence"), default=0.0)
    support = _safe_int(document.get("source_refs_count"), default=0)
    category = str(document.get("category") or "").strip()
    facet = str(document.get("facet") or "").strip()

    if len(alias) < min_alias_len:
        reasons.append("alias_too_short")
    if support < min_reject_support and confidence <= max_reject_confidence:
        reasons.append("low_support_low_confidence")
    if category == "skills" and facet == "technology_stack":
        reasons.append("category_facet_mismatch")
    if category == "stack" and facet in {"benefit", "work_condition"}:
        reasons.append("category_facet_mismatch")

    if reasons:
        return REJECTED_STATUS, reasons

    if confidence >= min_accept_confidence and support >= min_accept_support:
        return ACCEPTED_STATUS, ["high_confidence_supported"]

    return NEEDS_ADJUDICATION_STATUS, ["insufficient_confidence_or_support"]


def classify_and_update(
    *,
    state_collection: Any,
    audit_collection: Any,
    run_id: str,
    min_accept_confidence: float,
    min_accept_support: int,
    max_reject_confidence: float,
    min_reject_support: int,
    min_alias_len: int,
    limit: int,
    dry_run: bool,
) -> dict[str, int]:
    selector = {"processing_status": "new"}
    cursor = state_collection.find(selector)
    if limit > 0:
        cursor = cursor.limit(limit)

    stats = Counter(total=0, accepted=0, rejected=0, needs_adjudication=0)
    for document in cursor:
        stats["total"] += 1
        status, reasons = classify_candidate(
            document,
            min_accept_confidence=min_accept_confidence,
            min_accept_support=min_accept_support,
            max_reject_confidence=max_reject_confidence,
            min_reject_support=min_reject_support,
            min_alias_len=min_alias_len,
        )
        stats[status] += 1

        if dry_run:
            continue

        state_id = document.get("_id")
        update_payload = {
            "processing_status": status,
            "decision_source": AUTO_GATE_DECISION_SOURCE,
            "updated_at": _now_utc_iso(),
            "auto_gate": {
                "decision": status,
                "reasons": reasons,
                "confidence": _safe_float(document.get("confidence"), default=0.0),
            },
        }
        state_collection.update_one({"_id": state_id}, {"$set": update_payload})
        audit_event = build_synonyms_audit_event(
            state_id=state_id,
            event_type="auto_gate_classified",
            run_id=run_id,
            payload={
                "decision": status,
                "reasons": reasons,
                "confidence": _safe_float(document.get("confidence"), default=0.0),
            },
        )
        write_synonyms_audit_event(audit_collection, audit_event)

    return {
        "total": int(stats["total"]),
        "accepted": int(stats[ACCEPTED_STATUS]),
        "rejected": int(stats[REJECTED_STATUS]),
        "needs_adjudication": int(stats[NEEDS_ADJUDICATION_STATUS]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-gate synonyms state candidates.")
    parser.add_argument("--run-id", default="", help="Optional run id override.")
    parser.add_argument("--limit", type=int, default=0, help="Process up to N candidates (0=all).")
    parser.add_argument("--min-accept-confidence", type=float, default=0.85)
    parser.add_argument("--min-accept-support", type=int, default=2)
    parser.add_argument("--max-reject-confidence", type=float, default=0.45)
    parser.add_argument("--min-reject-support", type=int, default=1)
    parser.add_argument("--min-alias-len", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Do not write statuses.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id.strip() or f"syn-gate-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    cfg = load_app_config()
    db = get_database(cfg)
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
    state_collection = db[state_collection_name]
    audit_collection = db[audit_collection_name]
    ensure_synonyms_state_indexes(state_collection, audit_collection)

    stats = classify_and_update(
        state_collection=state_collection,
        audit_collection=audit_collection,
        run_id=run_id,
        min_accept_confidence=args.min_accept_confidence,
        min_accept_support=args.min_accept_support,
        max_reject_confidence=args.max_reject_confidence,
        min_reject_support=args.min_reject_support,
        min_alias_len=args.min_alias_len,
        limit=max(0, args.limit),
        dry_run=args.dry_run,
    )
    print(
        f"run_id={run_id} total={stats['total']} accepted={stats['accepted']} "
        f"rejected={stats['rejected']} needs_adjudication={stats['needs_adjudication']} "
        f"dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
