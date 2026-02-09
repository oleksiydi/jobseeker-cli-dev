#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Prüft, ob referenznummern im letzten (oder angegebenen) list-run vorhanden waren.

Unterstützt 2 Eingabemodi:
1) Explizite Liste über --ref (mehrfach verwendbar).
2) Automatisches Extrahieren aus job_details_collector-Log mit HTTP 404 via --from-log.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import load_app_config
from app.db.mongo import get_database


LOG_404_PATTERN = re.compile(r"job_details_collector - ([^:]+): HTTP 404")


def _history_collection_name(cfg: dict[str, Any]) -> str:
    return cfg.get("history_collections", {}).get("list", "stellenangebote_history")


def _read_refs_from_log(path: Path) -> set[str]:
    refs: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = LOG_404_PATTERN.search(line)
            if match:
                refs.add(match.group(1).strip())
    return refs


def _resolve_run_id(history_col, run_id: str | None) -> str:
    if run_id:
        return run_id

    doc = history_col.find_one(
        {"event_type": "list_seen", "run_id": {"$exists": True}},
        sort=[("seen_at", -1)],
        projection={"_id": 0, "run_id": 1},
    )
    if not doc or not doc.get("run_id"):
        raise RuntimeError("Kein run_id mit event_type=list_seen gefunden.")
    return str(doc["run_id"])


def _collect_refs(args: argparse.Namespace) -> set[str]:
    refs = {value.strip() for value in args.ref if value and value.strip()}
    if args.from_log:
        refs_from_log = _read_refs_from_log(args.from_log)
        refs.update(refs_from_log)
    return refs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prüft referenznummern in list_seen-Events des letzten/angegebenen run_id."
    )
    parser.add_argument(
        "--run-id",
        help="Expliziter run_id des list collectors. Wenn nicht gesetzt, wird der letzte run_id verwendet.",
    )
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Referenznummer zur Prüfung. Argument kann mehrfach verwendet werden.",
    )
    parser.add_argument(
        "--from-log",
        type=Path,
        help="Pfad zum job_details_collector-Log; referenznummern mit HTTP 404 werden extrahiert.",
    )
    parser.add_argument(
        "--print-missing",
        action="store_true",
        help="Gibt referenznummern aus, die im gewählten run_id fehlen.",
    )
    parser.add_argument(
        "--print-present",
        action="store_true",
        help="Gibt referenznummern aus, die im gewählten run_id vorhanden sind.",
    )
    args = parser.parse_args()

    refs = _collect_refs(args)
    if not refs:
        raise RuntimeError("Keine referenznummern angegeben. Verwenden Sie --ref oder --from-log.")

    cfg = load_app_config()
    db = get_database(cfg)
    history_collection_name = _history_collection_name(cfg)
    history = db[history_collection_name]

    run_id = _resolve_run_id(history, args.run_id)

    seen_docs = history.find(
        {"event_type": "list_seen", "run_id": run_id, "referenznummer": {"$in": sorted(refs)}},
        projection={"_id": 0, "referenznummer": 1},
    )
    seen_refs = {doc["referenznummer"] for doc in seen_docs if doc.get("referenznummer")}
    missing_refs = sorted(refs - seen_refs)
    present_refs = sorted(seen_refs)

    print(f"run_id={run_id}")
    print(f"input_refs={len(refs)}")
    print(f"present_in_run={len(present_refs)}")
    print(f"missing_in_run={len(missing_refs)}")

    if args.print_missing and missing_refs:
        print("missing_refs:")
        for ref in missing_refs:
            print(ref)

    if args.print_present and present_refs:
        print("present_refs:")
        for ref in present_refs:
            print(ref)


if __name__ == "__main__":
    main()
