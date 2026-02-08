#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Import profile document(s) into MongoDB (profiles collection).
Default source: conf/profiles/default.profile.json.
Supports JSON object or JSON array and performs idempotent upsert by key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import ASCENDING

# Allows running as script: python tools/import_profiles.py
if __package__ is None or __package__ == "":
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import project_root, load_app_config
from app.db.mongo import get_database, resolve_collection_name

SERVICE_FIELDS = {"_id", "created_at", "updated_at", "profile_hash"}


def resolve_default_profile_file() -> Path:
    return project_root() / "conf" / "profiles" / "default.profile.json"


def _debug_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def load_docs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {_debug_path(path)}")
    if path.stat().st_size == 0:
        raise ValueError(f"Файл пустой: {_debug_path(path)}")

    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        docs = [data]
    elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
        docs = data
    else:
        raise ValueError("Ожидается JSON-объект или массив JSON-объектов.")

    if not docs:
        raise ValueError("Список profile-документов пуст.")
    return docs


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _profile_business_payload(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in SERVICE_FIELDS}


def normalize_profile_doc(
    doc: dict[str, Any],
    *,
    default_key: str,
    upsert_key: str,
    strict_validate: bool,
    created_by: str | None,
    now: datetime,
) -> dict[str, Any]:
    normalized = dict(doc)
    normalized.setdefault("key", default_key)

    upsert_value = normalized.get(upsert_key)
    if upsert_value is None:
        raise ValueError(f"Отсутствует поле upsert-key '{upsert_key}' в profile-документе.")

    if isinstance(upsert_value, str):
        normalized[upsert_key] = upsert_value.strip()
        upsert_value = normalized[upsert_key]
    if upsert_value in ("", None):
        raise ValueError(f"Пустое значение upsert-key '{upsert_key}'.")

    business_payload = _profile_business_payload(normalized)
    business_payload_wo_upsert = {k: v for k, v in business_payload.items() if k != upsert_key}
    if strict_validate and not business_payload_wo_upsert:
        raise ValueError("Profile-документ не содержит бизнес-полей.")

    normalized["profile_hash"] = _canonical_hash(business_payload)
    normalized["updated_at"] = now
    if created_by:
        normalized["created_by"] = created_by
    return normalized


def _comparable_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in {"_id", "created_at", "updated_at"}}


def main() -> None:
    default_file = resolve_default_profile_file()
    parser = argparse.ArgumentParser(description="Импорт профилей в MongoDB.")
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=default_file,
        help=f"Путь к JSON-файлу (по умолчанию: {_debug_path(default_file)})",
    )
    parser.add_argument(
        "--collection",
        "-c",
        default=None,
        help="Имя коллекции (по умолчанию берется из conf/app.json: collections.profiles).",
    )
    parser.add_argument(
        "--upsert-key",
        default="key",
        help="Поле для upsert (по умолчанию: key).",
    )
    parser.add_argument(
        "--key",
        default="default",
        help="Значение key по умолчанию, если key отсутствует в документе.",
    )
    parser.add_argument(
        "--created-by",
        default="tools/import_profiles.py",
        help="Значение created_by для записей.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать изменения без записи в MongoDB.",
    )
    parser.add_argument(
        "--strict-validate",
        action="store_true",
        help="Включить строгую валидацию profile-документов.",
    )
    args = parser.parse_args()

    docs = load_docs(args.file)
    cfg = load_app_config()
    db = get_database(cfg)
    collection_name = args.collection or resolve_collection_name(cfg, key="profiles", default="profiles")
    col = db[collection_name]
    col.create_index([(args.upsert_key, ASCENDING)], unique=True)

    inserted = 0
    updated = 0
    unchanged = 0
    now = datetime.now(timezone.utc)

    for raw in docs:
        normalized = normalize_profile_doc(
            raw,
            default_key=args.key,
            upsert_key=args.upsert_key,
            strict_validate=args.strict_validate,
            created_by=args.created_by,
            now=now,
        )
        upsert_value = normalized[args.upsert_key]
        created_at = raw.get("created_at", now)
        set_doc = dict(normalized)
        set_doc.pop("created_at", None)

        existing = col.find_one({args.upsert_key: upsert_value})
        if existing and _comparable_doc(existing) == _comparable_doc(set_doc):
            unchanged += 1
            continue

        if args.dry_run:
            if existing:
                updated += 1
            else:
                inserted += 1
            continue

        result = col.update_one(
            {args.upsert_key: upsert_value},
            {"$setOnInsert": {"created_at": created_at}, "$set": set_doc},
            upsert=True,
        )
        if result.matched_count == 0 and result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            updated += 1
        else:
            unchanged += 1

    print(
        f"Готово. collection={collection_name}, dry_run={args.dry_run}, "
        f"inserted={inserted}, updated={updated}, unchanged={unchanged}"
    )


if __name__ == "__main__":
    main()
