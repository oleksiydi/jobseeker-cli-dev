#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Review and apply taxonomy pending suggestions.

Examples:
  # List pending items with status counters
  .venv/bin/python tools/review_taxonomy_pending.py --action list

  # Approve one term
  .venv/bin/python tools/review_taxonomy_pending.py \
    --action approve --term-key devops --by reindeer --note "domain confirmed"

  # Reject one term
  .venv/bin/python tools/review_taxonomy_pending.py \
    --action reject --term-key rollout --by reindeer --note "too generic"

  # Apply all approved terms into taxonomy
  .venv/bin/python tools/review_taxonomy_pending.py --action apply
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import update_taxonomy_from_unknown_terms as taxo_updater


REVIEW_STATUS_TO_CODE = {
    "auto_suggested": 0,
    "approved": 1,
    "rejected": 2,
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_review_fields(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("review_status") or "auto_suggested")
    if status not in REVIEW_STATUS_TO_CODE:
        status = "auto_suggested"
    out = dict(record)
    out["review_status"] = status
    out["review_code"] = int(record.get("review_code") or REVIEW_STATUS_TO_CODE[status])
    out["review_note"] = str(record.get("review_note") or "")
    out["reviewed_by"] = str(record.get("reviewed_by") or "")
    out["reviewed_at"] = str(record.get("reviewed_at") or "")
    return out


def _load_pending(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if not isinstance(parsed, dict):
                continue
            rows.append(_ensure_review_fields(parsed))
    return rows


def _save_pending(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _match(row: dict[str, Any], *, term_key: str, category: str) -> bool:
    if term_key and str(row.get("term_key", "")) != term_key:
        return False
    if category and str(row.get("category", "")) != category:
        return False
    return True


def _set_status(
    rows: list[dict[str, Any]],
    *,
    term_key: str,
    category: str,
    status: str,
    by: str,
    note: str,
) -> int:
    updated = 0
    for row in rows:
        if not _match(row, term_key=term_key, category=category):
            continue
        row["review_status"] = status
        row["review_code"] = REVIEW_STATUS_TO_CODE[status]
        row["reviewed_by"] = by
        row["review_note"] = note
        row["reviewed_at"] = _now_utc()
        updated += 1
    return updated


def _print_list(rows: list[dict[str, Any]], *, only_status: str, limit: int) -> None:
    counters = {"auto_suggested": 0, "approved": 0, "rejected": 0}
    for row in rows:
        status = str(row.get("review_status") or "auto_suggested")
        counters[status] = counters.get(status, 0) + 1

    print(
        f"total={len(rows)}, auto_suggested={counters.get('auto_suggested', 0)}, "
        f"approved={counters.get('approved', 0)}, rejected={counters.get('rejected', 0)}"
    )

    shown = 0
    for row in rows:
        status = str(row.get("review_status") or "auto_suggested")
        if only_status and status != only_status:
            continue
        print(
            json.dumps(
                {
                    "term_key": row.get("term_key", ""),
                    "canonical": row.get("canonical", ""),
                    "category": row.get("category", ""),
                    "confidence": row.get("confidence", ""),
                    "review_status": status,
                },
                ensure_ascii=False,
            )
        )
        shown += 1
        if limit > 0 and shown >= limit:
            break


def _apply_approved(
    rows: list[dict[str, Any]],
    *,
    taxonomy_path: Path,
) -> int:
    taxonomy_raw = taxo_updater._load_json(taxonomy_path, {"version": 1, "aliases": {}, "categories": {}})
    taxonomy = taxo_updater._ensure_taxonomy_shape(taxonomy_raw)
    applied = 0
    for row in rows:
        if str(row.get("review_status")) != "approved":
            continue
        suggestion = {
            "term_key": row.get("term_key", ""),
            "canonical": row.get("canonical", ""),
            "category": row.get("category", "skills"),
        }
        if not suggestion["term_key"] or not suggestion["canonical"]:
            continue
        taxo_updater.apply_suggestion(taxonomy, suggestion)
        applied += 1
    taxo_updater._dump_json(taxonomy_path, taxo_updater._ensure_taxonomy_shape(taxonomy))
    return applied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review and apply taxonomy pending suggestions.")
    parser.add_argument(
        "--pending",
        type=Path,
        default=Path("logs/normalization/taxonomy_pending.jsonl"),
        help="Pending JSONL file.",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("conf/normalization/taxonomy.auto.json"),
        help="Taxonomy JSON file.",
    )
    parser.add_argument(
        "--action",
        choices=["list", "approve", "reject", "reset", "apply"],
        required=True,
        help="Action to perform.",
    )
    parser.add_argument("--term-key", default="", help="Exact term_key to filter.")
    parser.add_argument("--category", default="", choices=["", "skills", "tools", "stack"], help="Filter category.")
    parser.add_argument("--by", default="manual", help="Reviewer id.")
    parser.add_argument("--note", default="", help="Review note.")
    parser.add_argument("--status", default="", choices=["", "auto_suggested", "approved", "rejected"], help="For list filter.")
    parser.add_argument("--limit", type=int, default=50, help="List output limit (0 = all).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = _load_pending(args.pending)

    if args.action == "list":
        _print_list(rows, only_status=args.status, limit=max(0, args.limit))
        return

    if args.action == "apply":
        applied = _apply_approved(rows, taxonomy_path=args.taxonomy)
        print(f"applied_to_taxonomy={applied}, taxonomy={args.taxonomy}")
        return

    if not args.term_key:
        raise ValueError("--term-key is required for approve/reject/reset")

    if args.action == "approve":
        updated = _set_status(
            rows,
            term_key=args.term_key,
            category=args.category,
            status="approved",
            by=args.by,
            note=args.note,
        )
    elif args.action == "reject":
        updated = _set_status(
            rows,
            term_key=args.term_key,
            category=args.category,
            status="rejected",
            by=args.by,
            note=args.note,
        )
    else:
        updated = _set_status(
            rows,
            term_key=args.term_key,
            category=args.category,
            status="auto_suggested",
            by=args.by,
            note=args.note,
        )

    _save_pending(args.pending, rows)
    print(f"updated={updated}, action={args.action}, pending={args.pending}")


if __name__ == "__main__":
    main()

