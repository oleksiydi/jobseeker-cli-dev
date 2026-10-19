#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name


LEGACY_UNIQUE_KEYS: list[tuple[str, int]] = [("referenznummer", 1), ("input_hash", 1)]
TARGET_UNIQUE_KEYS: list[tuple[str, int]] = [
    ("referenznummer", 1),
    ("candidate_profile_key", 1),
    ("input_hash", 1),
]
TARGET_INDEX_NAME = "ref_profile_input_hash_unique"


def _normalize_index_keys(index_doc: dict[str, Any]) -> list[tuple[str, int]]:
    key_data = index_doc.get("key", {})
    if hasattr(key_data, "items"):
        return [(str(field), int(direction)) for field, direction in key_data.items()]
    if isinstance(key_data, list):
        return [(str(field), int(direction)) for field, direction in key_data]
    return []


def _find_unique_index_name(indexes: list[dict[str, Any]], keys: list[tuple[str, int]]) -> str | None:
    for index_doc in indexes:
        if not bool(index_doc.get("unique", False)):
            continue
        if _normalize_index_keys(index_doc) == keys:
            return str(index_doc.get("name"))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-off migration for analysen unique index: "
            "(referenznummer,input_hash) -> (referenznummer,candidate_profile_key,input_hash)."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur geplante Änderungen anzeigen.")
    args = parser.parse_args()

    cfg = load_app_config()
    db = get_database(cfg)
    analysis_collection_name = resolve_collection_name(cfg, key="analysis", default="analysen")
    col = db[analysis_collection_name]

    indexes = list(col.list_indexes())
    legacy_name = _find_unique_index_name(indexes, LEGACY_UNIQUE_KEYS)
    target_name = _find_unique_index_name(indexes, TARGET_UNIQUE_KEYS)

    print(f"collection={analysis_collection_name}")
    print(f"dry_run={args.dry_run}")
    print(f"legacy_unique_index={legacy_name}")
    print(f"target_unique_index={target_name}")

    if legacy_name and not args.dry_run:
        col.drop_index(legacy_name)
        print(f"dropped_legacy_index={legacy_name}")

    if not target_name and not args.dry_run:
        created_name = col.create_index(TARGET_UNIQUE_KEYS, unique=True, name=TARGET_INDEX_NAME)
        print(f"created_target_index={created_name}")

    if args.dry_run:
        if legacy_name:
            print(f"plan_drop_legacy_index={legacy_name}")
        if not target_name:
            print(f"plan_create_target_index={TARGET_INDEX_NAME}")


if __name__ == "__main__":
    main()

