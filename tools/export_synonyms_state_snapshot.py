#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _state_record(document: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    return {
        "snapshot_run_id": run_id,
        "exported_at": _now_utc_iso(),
        "state_id": str(document.get("_id") or ""),
        "alias": str(document.get("alias") or ""),
        "alias_key": str(document.get("alias_key") or ""),
        "canonical": str(document.get("canonical") or ""),
        "domain": str(document.get("domain") or ""),
        "category": str(document.get("category") or ""),
        "facet": str(document.get("facet") or ""),
        "skill_class": str(document.get("skill_class") or ""),
        "processing_status": str(document.get("processing_status") or ""),
        "decision_source": str(document.get("decision_source") or ""),
        "confidence": document.get("confidence"),
        "source": str(document.get("source") or ""),
        "source_refs_count": document.get("source_refs_count"),
        "sample_phrases": document.get("sample_phrases", []),
        "run_id": str(document.get("run_id") or ""),
        "lease_owner": document.get("lease_owner"),
        "lease_expires_at": document.get("lease_expires_at"),
        "retry_count": document.get("retry_count"),
        "last_error": document.get("last_error"),
        "adjudication_policy": document.get("adjudication_policy", {}),
        "adjudication": document.get("adjudication", {}),
        "created_at": str(document.get("created_at") or ""),
        "updated_at": str(document.get("updated_at") or ""),
    }


def _audit_record(document: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    return {
        "snapshot_run_id": run_id,
        "exported_at": _now_utc_iso(),
        "state_id": str(document.get("state_id") or ""),
        "event_type": str(document.get("event_type") or ""),
        "run_id": str(document.get("run_id") or ""),
        "payload": document.get("payload", {}),
        "created_at": str(document.get("created_at") or ""),
    }


def _validate_state_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("state_id", "alias_key", "canonical", "domain", "category", "facet", "processing_status"):
        if not str(record.get(key) or "").strip():
            errors.append(f"missing_or_empty_{key}")
    return errors


def _validate_audit_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("state_id", "event_type", "run_id"):
        if not str(record.get(key) or "").strip():
            errors.append(f"missing_or_empty_{key}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export full JSONL snapshot from synonyms Mongo working state.")
    parser.add_argument("--run-id", default="", help="Optional snapshot run id.")
    parser.add_argument(
        "--out-state-jsonl",
        type=Path,
        default=Path("logs/normalization/synonyms_state_snapshot.full.jsonl"),
        help="Output path for full state snapshot JSONL.",
    )
    parser.add_argument(
        "--out-audit-jsonl",
        type=Path,
        default=Path("logs/normalization/synonyms_audit_snapshot.full.jsonl"),
        help="Output path for full audit snapshot JSONL.",
    )
    parser.add_argument(
        "--status",
        default="",
        help="Optional state filter by processing_status (empty = all statuses).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for exported state rows (0 = no limit).")
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Do not export audit snapshot.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate required record fields before writing snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id.strip() or f"syn-snapshot-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

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

    selector: dict[str, Any] = {}
    if args.status.strip():
        selector["processing_status"] = args.status.strip()
    cursor = state_collection.find(selector)
    if args.limit > 0:
        cursor = cursor.limit(args.limit)
    state_rows = [_state_record(doc, run_id=run_id) for doc in cursor]
    if args.validate:
        errors = []
        for idx, row in enumerate(state_rows):
            row_errors = _validate_state_record(row)
            if row_errors:
                errors.append(f"state_row[{idx}] errors={','.join(row_errors)}")
        if errors:
            raise ValueError("; ".join(errors))
    _write_jsonl(args.out_state_jsonl, state_rows)

    audit_rows: list[dict[str, Any]] = []
    if not args.skip_audit:
        audit_cursor = audit_collection.find({})
        if args.limit > 0:
            audit_cursor = audit_cursor.limit(args.limit)
        audit_rows = [_audit_record(doc, run_id=run_id) for doc in audit_cursor]
        if args.validate:
            errors = []
            for idx, row in enumerate(audit_rows):
                row_errors = _validate_audit_record(row)
                if row_errors:
                    errors.append(f"audit_row[{idx}] errors={','.join(row_errors)}")
            if errors:
                raise ValueError("; ".join(errors))
        _write_jsonl(args.out_audit_jsonl, audit_rows)

    print(
        f"snapshot_run_id={run_id} state_rows={len(state_rows)} audit_rows={len(audit_rows)} "
        f"state_out={args.out_state_jsonl} audit_out={args.out_audit_jsonl} status_filter={selector.get('processing_status','')}"
    )


if __name__ == "__main__":
    main()
