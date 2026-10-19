#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Import prompt/task documents into MongoDB (analysis_prompts collection).
Default source: conf/prompts/default.prompt_task.json.
Supports JSON object or JSON array and performs idempotent upsert by (key, version).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import ASCENDING

# Allows running as script: python tools/import_analysis_prompts.py
if __package__ is None or __package__ == "":
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import project_root, load_app_config
from app.db.mongo import get_database, resolve_collection_name


def resolve_default_prompt_file() -> Path:
    return project_root() / "conf" / "prompts" / "default.prompt_task.json"


def _debug_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def load_docs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {_debug_path(path)}")

    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        docs = [data]
    elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
        docs = data
    else:
        raise ValueError("Erwartet wird ein JSON-Objekt oder ein Array von JSON-Objekten.")

    if not docs:
        raise ValueError("Liste der Prompt-Dokumente ist leer.")
    return docs


def _prompt_hash(system_prompt: str, analysis_task: str) -> str:
    canonical = json.dumps(
        {"system_prompt": system_prompt, "analysis_task": analysis_task},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_prompt_doc(doc: dict[str, Any], now: datetime, created_by: str | None) -> dict[str, Any]:
    key = str(doc.get("key", "")).strip()
    if not key:
        raise ValueError("Das Feld 'key' ist Pflicht und darf nicht leer sein.")

    if doc.get("version") is None:
        raise ValueError(f"Für key='{key}' ist das Feld 'version' erforderlich.")
    try:
        version = int(doc["version"])
    except Exception as exc:
        raise ValueError(f"Ungültige version für key='{key}': {doc.get('version')}") from exc
    if version < 1:
        raise ValueError(f"version muss >= 1 sein für key='{key}'.")

    system_prompt = str(doc.get("system_prompt", "")).strip()
    analysis_task = str(doc.get("analysis_task", "")).strip()
    if not system_prompt:
        raise ValueError(f"Für key='{key}', version={version} ist system_prompt leer.")
    if not analysis_task:
        raise ValueError(f"Für key='{key}', version={version} ist analysis_task leer.")

    normalized = dict(doc)
    normalized["key"] = key
    normalized["version"] = version
    normalized["system_prompt"] = system_prompt
    normalized["analysis_task"] = analysis_task
    normalized["is_active"] = bool(doc.get("is_active", True))
    normalized["prompt_hash"] = _prompt_hash(system_prompt, analysis_task)
    normalized["updated_at"] = now
    if created_by:
        normalized["created_by"] = created_by
    return normalized


def _comparable_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in {"_id", "created_at", "updated_at"}}


def main() -> None:
    default_file = resolve_default_prompt_file()
    parser = argparse.ArgumentParser(description="Import von Prompt/Task-Dokumenten in MongoDB.")
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=default_file,
        help=f"Pfad zur JSON-Datei (Standard: {_debug_path(default_file)})",
    )
    parser.add_argument(
        "--collection",
        "-c",
        default=None,
        help="Collection-Name (Standard aus conf/app.json: collections.analysis_prompts).",
    )
    parser.add_argument(
        "--created-by",
        default="tools/import_analysis_prompts.py",
        help="Wert für created_by (Standard: tools/import_analysis_prompts.py).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Zeigt Änderungen ohne Schreiben in MongoDB.",
    )
    parser.add_argument(
        "--strict-validate",
        action="store_true",
        help="Aktiviert strikte Validierung des Prompt/Task-Formats.",
    )
    args = parser.parse_args()

    docs = load_docs(args.file)
    cfg = load_app_config()
    db = get_database(cfg)
    collection_name = args.collection or resolve_collection_name(
        cfg, key="analysis_prompts", default="analysis_prompts"
    )
    col = db[collection_name]

    col.create_index([("key", ASCENDING), ("version", ASCENDING)], unique=True)
    col.create_index([("key", ASCENDING), ("is_active", ASCENDING), ("version", ASCENDING)])

    inserted = 0
    updated = 0
    unchanged = 0
    now = datetime.now(timezone.utc)

    for raw in docs:
        normalized = normalize_prompt_doc(raw, now=now, created_by=args.created_by)
        if args.strict_validate:
            extra_keys = set(normalized.keys()) - {
                "key",
                "version",
                "name",
                "description",
                "system_prompt",
                "analysis_task",
                "prompt_hash",
                "is_active",
                "created_at",
                "updated_at",
                "created_by",
            }
            if extra_keys:
                raise ValueError(
                    f"Unerwartete Felder im Prompt key='{normalized['key']}', version={normalized['version']}: "
                    f"{sorted(extra_keys)}"
                )
        key = normalized["key"]
        version = normalized["version"]
        created_at = raw.get("created_at", now)

        set_doc = dict(normalized)
        set_doc.pop("created_at", None)

        existing = col.find_one({"key": key, "version": version})
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
            {"key": key, "version": version},
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
        f"Fertig. collection={collection_name}, dry_run={args.dry_run}, "
        f"inserted={inserted}, updated={updated}, unchanged={unchanged}"
    )


if __name__ == "__main__":
    main()
