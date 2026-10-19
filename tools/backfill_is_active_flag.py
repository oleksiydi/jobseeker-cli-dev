#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pymongo import UpdateOne

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name


def _legacy_active_filter() -> dict[str, Any]:
    # Документы без `is_active` ранее считались активными по совместимому контракту.
    return {"is_active": {"$exists": False}}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-off migration: setzt is_active=true für legacy Stellenangebote-Dokumente "
            "ohne explizites is_active Feld."
        )
    )
    parser.add_argument("--batch-size", type=int, default=1000, help="Größe des bulk_write-Batches.")
    parser.add_argument("--dry-run", action="store_true", help="Nur Statistik ausgeben, ohne in MongoDB zu schreiben.")
    args = parser.parse_args()

    cfg = load_app_config()
    db = get_database(cfg)
    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    col = db[list_collection_name]

    query = _legacy_active_filter()
    projection = {"_id": 1}
    cursor = col.find(query, projection)

    scanned = 0
    updated = 0
    ops: list[UpdateOne] = []

    for doc in cursor:
        scanned += 1
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"is_active": True}}, upsert=False))
        if len(ops) >= args.batch_size:
            if args.dry_run:
                updated += len(ops)
            else:
                result = col.bulk_write(ops, ordered=False)
                updated += result.modified_count
            ops = []

    if ops:
        if args.dry_run:
            updated += len(ops)
        else:
            result = col.bulk_write(ops, ordered=False)
            updated += result.modified_count

    print(f"collection={list_collection_name}")
    print(f"dry_run={args.dry_run}")
    print(f"legacy_without_flag={scanned}")
    print(f"updated={updated}")


if __name__ == "__main__":
    main()

