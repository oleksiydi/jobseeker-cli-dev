#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.normalization.synonyms_state import ensure_synonyms_state_indexes


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Snapshot file not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid JSONL object at {path}:{line_no}: expected object")
            rows.append(payload)
    return rows


def _validate_state_snapshot_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "state_id",
        "alias",
        "alias_key",
        "canonical",
        "domain",
        "category",
        "facet",
        "skill_class",
        "processing_status",
    ):
        if not str(record.get(key) or "").strip():
            errors.append(f"missing_or_empty_{key}")
    return errors


def _validate_audit_snapshot_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("state_id", "event_type", "run_id", "created_at"):
        if not str(record.get(key) or "").strip():
            errors.append(f"missing_or_empty_{key}")
    payload = record.get("payload", {})
    if not isinstance(payload, dict):
        errors.append("payload_must_be_object")
    return errors


def _state_document_from_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "_id": str(record.get("state_id") or ""),
        "alias": str(record.get("alias") or ""),
        "alias_key": str(record.get("alias_key") or ""),
        "canonical": str(record.get("canonical") or ""),
        "domain": str(record.get("domain") or ""),
        "category": str(record.get("category") or ""),
        "facet": str(record.get("facet") or ""),
        "skill_class": str(record.get("skill_class") or ""),
        "processing_status": str(record.get("processing_status") or ""),
        "decision_source": str(record.get("decision_source") or ""),
        "confidence": record.get("confidence"),
        "source": str(record.get("source") or ""),
        "source_refs_count": record.get("source_refs_count"),
        "sample_phrases": record.get("sample_phrases", []),
        "run_id": str(record.get("run_id") or ""),
        "lease_owner": record.get("lease_owner"),
        "lease_expires_at": record.get("lease_expires_at"),
        "retry_count": record.get("retry_count"),
        "last_error": record.get("last_error"),
        "adjudication_policy": record.get("adjudication_policy", {}),
        "adjudication": record.get("adjudication", {}),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }


def _audit_document_from_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "state_id": str(record.get("state_id") or ""),
        "event_type": str(record.get("event_type") or ""),
        "run_id": str(record.get("run_id") or ""),
        "payload": record.get("payload", {}),
        "created_at": str(record.get("created_at") or ""),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import JSONL snapshot into Mongo synonyms working state.")
    parser.add_argument(
        "--state-jsonl",
        type=Path,
        default=Path("logs/normalization/synonyms_state_snapshot.full.jsonl"),
        help="State snapshot JSONL exported from synonyms working state.",
    )
    parser.add_argument(
        "--audit-jsonl",
        type=Path,
        default=Path("logs/normalization/synonyms_audit_snapshot.full.jsonl"),
        help="Audit snapshot JSONL exported from synonyms working state.",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Import only state snapshot, skip audit snapshot.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing Mongo state/audit collections with snapshot content.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate snapshot records before import.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize import without writing to Mongo.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.replace_existing and not args.dry_run:
        raise ValueError("Import is replace-only. Pass --replace-existing to confirm destructive replay.")

    state_rows = _read_jsonl(args.state_jsonl)
    audit_rows = [] if args.skip_audit else _read_jsonl(args.audit_jsonl)

    if args.validate:
        errors: list[str] = []
        for idx, row in enumerate(state_rows):
            row_errors = _validate_state_snapshot_record(row)
            if row_errors:
                errors.append(f"state_row[{idx}] errors={','.join(row_errors)}")
        for idx, row in enumerate(audit_rows):
            row_errors = _validate_audit_snapshot_record(row)
            if row_errors:
                errors.append(f"audit_row[{idx}] errors={','.join(row_errors)}")
        if errors:
            raise ValueError("; ".join(errors))

    state_docs = [_state_document_from_snapshot(row) for row in state_rows]
    audit_docs = [_audit_document_from_snapshot(row) for row in audit_rows]

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

    if args.dry_run:
        print(
            f"dry_run=true state_rows={len(state_docs)} audit_rows={len(audit_docs)} "
            f"state_collection={state_collection_name} audit_collection={audit_collection_name}"
        )
        return

    state_collection.delete_many({})
    if state_docs:
        state_collection.insert_many(state_docs, ordered=True)

    if not args.skip_audit:
        audit_collection.delete_many({})
        if audit_docs:
            audit_collection.insert_many(audit_docs, ordered=True)

    print(
        f"synonyms_snapshot_imported state_rows={len(state_docs)} audit_rows={len(audit_docs)} "
        f"state_collection={state_collection_name} audit_collection={audit_collection_name} "
        f"replace_existing={args.replace_existing}"
    )


if __name__ == "__main__":
    main()
