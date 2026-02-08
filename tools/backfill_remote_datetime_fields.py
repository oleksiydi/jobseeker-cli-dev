#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
from datetime import datetime
import sys
from pathlib import Path
from typing import Any

from pymongo import UpdateOne

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import load_app_config
from app.core.time_utils import ensure_utc_datetime, parse_remote_dt
from app.db.mongo import get_database


FIELD_MAP: dict[str, list[tuple[str, str]]] = {
    "stellenangebote": [
        ("modifikationsTimestamp", "modifikationsTimestamp_dt"),
    ],
    "jobdetails": [
        ("aenderungsdatum", "aenderungsdatum_dt"),
        ("last_seen_remote_updated_at", "last_seen_remote_updated_at_dt"),
    ],
}


def _to_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return ensure_utc_datetime(value)

    if isinstance(value, str):
        return parse_remote_dt(value, default_tz="Europe/Berlin")

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill MongoDB Date-полей для remote timestamp с правилом Europe/Berlin."
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Размер пакета bulk_write.")
    parser.add_argument("--force", action="store_true", help="Перезаписывать *_dt даже если поле уже заполнено.")
    parser.add_argument("--dry-run", action="store_true", help="Только показать статистику без записи в MongoDB.")
    args = parser.parse_args()

    cfg = load_app_config()
    db = get_database(cfg)

    print(f"dry_run={args.dry_run}")
    print(f"batch_size={args.batch_size}")
    print(f"force={args.force}")

    for collection_name, fields in FIELD_MAP.items():
        collection = db[collection_name]
        print(f"\ncollection={collection_name}")

        for source_field, target_field in fields:
            base_filter: dict[str, Any] = {source_field: {"$exists": True}}
            if not args.force:
                base_filter["$or"] = [
                    {target_field: {"$exists": False}},
                    {target_field: None},
                ]

            projection = {"_id": 1, source_field: 1}
            cursor = collection.find(base_filter, projection=projection)

            scanned = 0
            updated = 0
            failed = 0
            ops: list[UpdateOne] = []

            for doc in cursor:
                scanned += 1
                parsed = _to_utc_datetime(doc.get(source_field))
                if parsed is None:
                    failed += 1
                    continue

                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {target_field: parsed}}))

                if len(ops) >= args.batch_size:
                    if not args.dry_run:
                        result = collection.bulk_write(ops, ordered=False)
                        updated += result.modified_count
                    else:
                        updated += len(ops)
                    ops = []

            if ops:
                if not args.dry_run:
                    result = collection.bulk_write(ops, ordered=False)
                    updated += result.modified_count
                else:
                    updated += len(ops)

            print(
                f"  field={source_field} -> {target_field}: scanned={scanned}, "
                f"updated={updated}, failed_parse={failed}"
            )


if __name__ == "__main__":
    main()
