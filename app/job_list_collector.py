#!/usr/bin/env python3  # Das Skript kann unter Unix direkt ausgeführt werden
# -*- coding: utf-8 -*-  # Dateikodierung (UTF-8)

"""
Skript: lädt die Stellenliste aus Jobcenter und speichert sie in MongoDB (ohne Dateien).

Konfigurationspriorität:
1) APP_CONFIG_PATH (env)
2) conf/app.json
"""

import hashlib
import json
import os
import time  # Pausen zwischen Requests, um die API zu entlasten
from datetime import datetime  # Zeitstempel für created_at/updated_at
import random
from uuid import uuid4
from typing import Any

import requests  # HTTP-Requests an die Jobcenter-API
from pymongo import ASCENDING, UpdateOne, errors  # MongoDB-Indizes und Fehler
from pymongo.errors import OperationFailure
from pymongo.collection import Collection
from pymongo.database import Database

# Erlaubt Direktstart: python app/job_list_collector.py
if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.core.time_utils import parse_remote_dt, utcnow_naive
from app.db.mongo import get_database, resolve_collection_name


SERVICE_FIELDS = {"_id", "created_at", "updated_at", "meta_hash", "is_active", "missing_since"}


def _history_collection_name(cfg: dict[str, Any]) -> str:
    env_value = os.getenv("COLLECTION_STELLENANGEBOTE_HISTORY")
    if env_value:
        return env_value
    return cfg.get("history_collections", {}).get("list", "stellenangebote_history")


def _state_collection_name(cfg: dict[str, Any]) -> str:
    return cfg.get("state_collections", {}).get("list", "pipeline_state")


def _list_seen_ttl_days(cfg: dict[str, Any]) -> int:
    env_value = os.getenv("STELLENANGEBOTE_LIST_SEEN_TTL_DAYS")
    if env_value is not None:
        try:
            return max(int(env_value), 1)
        except ValueError:
            pass
    cfg_value = cfg.get("history_ttl_days", {}).get("list_seen")
    if cfg_value is not None:
        try:
            return max(int(cfg_value), 1)
        except (ValueError, TypeError):
            pass
    # Lange Aufbewahrung zur Analyse langfristiger Verfügbarkeits-Trends.
    return 540


def _compute_scope_hash(api_url: str, params: dict[str, Any]) -> str:
    payload = {
        "api_url": api_url,
        "params": params,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _is_retryable_list_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    status_code = _extract_status_code(exc)
    if status_code == 429:
        return True
    if isinstance(status_code, int) and 500 <= status_code < 600:
        return True
    return False


def _fetch_list_page(
    *,
    session: requests.Session,
    api_url: str,
    headers: dict[str, Any],
    params: dict[str, Any],
    page: int,
    log: Any,
    timeout_seconds: int = 20,
    max_attempts: int = 3,
    base_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(api_url, headers=headers, params=params, timeout=timeout_seconds)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if not _is_retryable_list_error(exc) or attempt >= attempts:
                raise
            delay = base_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 0.3)
            log.warning(
                "Seite %s: temporärer API-Fehler (%s), retry %s/%s in %.2fs",
                page,
                exc,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)


def _ensure_indexes(
    db: Database,
    list_collection_name: str,
    history_collection_name: str,
    state_collection_name: str,
    list_seen_ttl_days: int,
) -> None:
    """Erstellt Indizes für aktuelle und historische Listendokumente."""
    col = db[list_collection_name]
    col.create_index([("referenznummer", ASCENDING)], unique=True)
    col.create_index([("meta_hash", ASCENDING)])
    col.create_index([("is_active", ASCENDING)])
    col.create_index([("updated_at", ASCENDING)])
    col.create_index([("modifikationsTimestamp_dt", ASCENDING)])

    history = db[history_collection_name]
    history.create_index([("referenznummer", ASCENDING), ("changed_at", ASCENDING)])
    history.create_index([("changed_at", ASCENDING)])
    history.create_index([("event_type", ASCENDING), ("run_id", ASCENDING), ("referenznummer", ASCENDING)])
    history.create_index([("run_id", ASCENDING)])
    history.create_index([("event_type", ASCENDING), ("seen_at", ASCENDING)])
    ttl_seconds = list_seen_ttl_days * 24 * 60 * 60
    try:
        history.create_index(
            [("seen_at", ASCENDING)],
            name="list_seen_ttl_idx",
            expireAfterSeconds=ttl_seconds,
            partialFilterExpression={
                "event_type": "list_seen",
                "seen_at": {"$type": "date"},
            },
        )
    except OperationFailure:
        # Der Index kann in älteren Umgebungen bereits mit anderen Parametern existieren.
        pass

    state = db[state_collection_name]
    state.create_index([("key", ASCENDING)], unique=True)

def _normalize_referenznummer(item: dict) -> str | None:
    """Normalisiert die Referenznummer im Feld 'referenznummer' und gibt sie zurück (oder None)."""
    ref = item.get("referenznummer") or item.get("refnr")  # Manche Quellen verwenden refnr
    if ref is not None:  # Referenznummer vorhanden
        ref = str(ref).strip()  # In String umwandeln und trimmen
        item["referenznummer"] = ref  # Einheitliches Feld für die weitere Verarbeitung
    return ref  # Normalisierter Wert (oder None)


def _meta_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Gibt nur Business-Felder für den Änderungsvergleich zurück."""
    return {k: v for k, v in item.items() if k not in SERVICE_FIELDS}


def _compute_meta_hash(payload: dict[str, Any]) -> str:
    """Stabiler Hash zur Erkennung von Änderungen im Meta-Input."""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_diff_update(
    existing_doc: dict[str, Any], new_payload: dict[str, Any], new_hash: str, now: datetime
) -> dict[str, Any]:
    existing_payload = _meta_payload(existing_doc)
    set_fields: dict[str, Any] = {"updated_at": now, "meta_hash": new_hash}
    unset_fields: dict[str, str] = {}

    for key, new_value in new_payload.items():
        if existing_payload.get(key) != new_value:
            set_fields[key] = new_value

    for key in existing_payload.keys():
        if key not in new_payload:
            unset_fields[key] = ""

    update_doc: dict[str, Any] = {"$set": set_fields}
    if unset_fields:
        update_doc["$unset"] = unset_fields
    return update_doc


def _build_history_snapshot(
    existing_doc: dict[str, Any], new_hash: str, now: datetime, run_id: str
) -> dict[str, Any]:
    snapshot = dict(existing_doc)
    snapshot.pop("_id", None)
    return {
        "event_type": "content_change",
        "run_id": run_id,
        "source": "job_list_collector",
        "referenznummer": existing_doc.get("referenznummer"),
        "changed_at": now,
        "previous_meta_hash": existing_doc.get("meta_hash"),
        "new_meta_hash": new_hash,
        "snapshot": snapshot,
    }


def _build_list_seen_event(
    ref: str,
    run_id: str,
    page: int,
    seen_at: datetime,
    item: dict[str, Any],
    meta_hash: str,
) -> dict[str, Any]:
    return {
        "event_type": "list_seen",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "page": page,
        "seen_at": seen_at,
        "remote_updated_at": item.get("aktualisierungsdatum") or item.get("aenderungsdatum"),
        "meta_hash": meta_hash,
    }


def _build_list_missing_event(ref: str, run_id: str, now: datetime, missing_since: datetime) -> dict[str, Any]:
    return {
        "event_type": "list_missing",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "occurred_at": now,
        "missing_since": missing_since,
    }


def _build_list_restored_event(ref: str, run_id: str, now: datetime) -> dict[str, Any]:
    return {
        "event_type": "list_restored",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "occurred_at": now,
    }

def main() -> None:
    """Hauptfunktion: lädt Konfiguration, iteriert API-Seiten und führt idempotente Updates aus."""
    log = get_logger("job_list_collector")  # Benannter Logger

    cfg = load_app_config()  # Einheitliche Konfiguration laden
    list_api = cfg["jobcenter"]["list_api"]  # Konfigurationsbereich für Stellenliste

    api_url = list_api["api_url"]  # API-Endpunkt für Stellenliste
    api_key = list_api.get("api_key", "")  # API-Key (oft 'jobboerse-jobsuche'), optional
    base_params = dict(list_api.get("params", {}))  # Basis-Suchparameter
    base_headers = dict(list_api.get("headers", {}))  # Basis-HTTP-Header
    max_requests = int(list_api.get("max_requests", 1000))  # Max. Seiten/Requests als Schutz
    list_retry_attempts = max(1, int(list_api.get("retry_attempts", 3)))
    list_retry_backoff_seconds = max(0.0, float(list_api.get("retry_backoff_seconds", 1.0)))
    _ = list_api.get("output_filenames", {})  # Kompatibilität: in config vorhanden, hier nicht verwendet

    db = get_database(cfg)  # DB-Verbindung
    list_collection_name = resolve_collection_name(
        cfg,
        key="list",
        default="stellenangebote",
    )
    history_collection_name = _history_collection_name(cfg)
    state_collection_name = _state_collection_name(cfg)
    list_seen_ttl_days = _list_seen_ttl_days(cfg)
    _ensure_indexes(
        db,
        list_collection_name,
        history_collection_name,
        state_collection_name,
        list_seen_ttl_days,
    )
    col: Collection = db[list_collection_name]
    col_history: Collection = db[history_collection_name]
    col_state: Collection = db[state_collection_name]
    run_id = f"list-{utcnow_naive().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    query_hash = _compute_scope_hash(api_url=api_url, params=base_params)
    previous_scope = col_state.find_one({"key": "job_list_collector_query_scope"})
    previous_query_hash = previous_scope.get("query_hash") if previous_scope else None
    scope_changed = bool(previous_query_hash and previous_query_hash != query_hash)
    log.info(
        "Start list_collector: run_id=%s, list_seen_ttl_days=%s, scope_changed=%s",
        run_id,
        list_seen_ttl_days,
        scope_changed,
    )

    session = requests.Session()  # HTTP-Session (Connection-Reuse)
    headers = dict(base_headers)  # Header kopieren, um Originalwerte nicht zu ändern
    if api_key:  # API-Key vorhanden
        headers["X-API-Key"] = api_key  # Erforderlicher Header für den Dienst

    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_archived = 0
    total_seen = 0
    total_restored = 0
    total_marked_missing = 0
    seen_refs_in_run: set[str] = set()
    completed_full_scan = False
    page = 1  # Aktuelle Seite (API startet bei 1)
    made = 0  # Bereits gesendete Requests (Limit via max_requests)

    while made < max_requests:  # Schleife mit Request-Limit
        params = {**base_params, "page": page}  # Basisparameter + Seitennummer

        try:
            data = _fetch_list_page(
                session=session,
                api_url=api_url,
                headers=headers,
                params=params,
                page=page,
                log=log,
                timeout_seconds=20,
                max_attempts=list_retry_attempts,
                base_backoff_seconds=list_retry_backoff_seconds,
            )
        except Exception as e:  # Request-/Parsing-Fehler
            log.error(f"Seite {page}: Request/Parsing-Fehler: {e}")
            break

        items = data.get("stellenangebote") or []  # Liste aus Feld 'stellenangebote'
        if not items:  # Leere Liste => Ende
            log.info(f"Seite {page}: keine Stellen — beende Lauf.")
            completed_full_scan = True
            break

        refs: list[str] = []
        prepared_items: list[dict[str, Any]] = []
        for raw in items:
            ref = _normalize_referenznummer(raw)
            if not ref:
                continue
            refs.append(ref)
            prepared_items.append(raw)

        if not prepared_items:
            log.info(f"Seite {page}: keine validen Einträge mit referenznummer.")
            page += 1
            made += 1
            time.sleep(random.uniform(0.5, 5))
            continue

        existing_docs = col.find({"referenznummer": {"$in": refs}})
        existing_by_ref = {doc["referenznummer"]: doc for doc in existing_docs if doc.get("referenznummer")}

        main_ops: list[UpdateOne] = []
        history_docs: list[dict[str, Any]] = []
        list_seen_docs: list[dict[str, Any]] = []
        inserted_this_page = 0
        updated_this_page = 0
        skipped_this_page = 0
        archived_this_page = 0
        seen_this_page = 0
        restored_this_page = 0

        for raw in prepared_items:
            ref = raw["referenznummer"]
            seen_refs_in_run.add(ref)
            now = utcnow_naive()
            raw["modifikationsTimestamp_dt"] = parse_remote_dt(raw.get("modifikationsTimestamp"))
            payload = _meta_payload(raw)
            payload["referenznummer"] = ref
            new_hash = _compute_meta_hash(payload)
            list_seen_docs.append(
                _build_list_seen_event(
                    ref=ref,
                    run_id=run_id,
                    page=page,
                    seen_at=now,
                    item=raw,
                    meta_hash=new_hash,
                )
            )
            seen_this_page += 1

            current = existing_by_ref.get(ref)
            if not current:
                main_ops.append(
                    UpdateOne(
                        {"referenznummer": ref},
                        {
                            "$setOnInsert": {"created_at": now},
                            "$set": payload
                            | {"updated_at": now, "meta_hash": new_hash, "is_active": True},
                            "$unset": {"missing_since": ""},
                        },
                        upsert=True,
                    )
                )
                inserted_this_page += 1
                continue

            if current.get("meta_hash") == new_hash:
                if current.get("is_active") is False:
                    history_docs.append(_build_list_restored_event(ref=ref, run_id=run_id, now=now))
                    main_ops.append(
                        UpdateOne(
                            {"_id": current["_id"], "is_active": False},
                            {
                                "$set": {"is_active": True, "updated_at": now},
                                "$unset": {"missing_since": ""},
                            },
                        )
                    )
                    restored_this_page += 1
                skipped_this_page += 1
                continue

            history_docs.append(
                _build_history_snapshot(current, new_hash=new_hash, now=now, run_id=run_id)
            )
            if current.get("is_active") is False:
                history_docs.append(_build_list_restored_event(ref=ref, run_id=run_id, now=now))
                restored_this_page += 1
            archived_this_page += 1
            diff_update = _build_diff_update(current, payload, new_hash, now)
            diff_update["$set"]["is_active"] = True
            if current.get("missing_since") is not None:
                diff_update["$unset"] = diff_update.get("$unset", {})
                diff_update["$unset"]["missing_since"] = ""
            main_ops.append(
                UpdateOne(
                    {"_id": current["_id"], "meta_hash": {"$ne": new_hash}},
                    diff_update,
                )
            )
            updated_this_page += 1

        try:
            if main_ops:
                col.bulk_write(main_ops, ordered=False)
            all_history_docs = history_docs + list_seen_docs
            if all_history_docs:
                col_history.insert_many(all_history_docs, ordered=False)
        except errors.DuplicateKeyError:
            log.warning(f"Seite {page}: Race Condition am Unique-Key erkannt, fortgesetzt.")
        except Exception as e:
            log.warning(f"Seite {page}: Fehler beim Schreiben nach Mongo: {e}")

        total_inserted += inserted_this_page
        total_updated += updated_this_page
        total_skipped += skipped_this_page
        total_archived += archived_this_page
        total_seen += seen_this_page
        total_restored += restored_this_page
        log.info(
            "Seite %s: seen=%s, inserted=%s, updated=%s, skipped=%s, archived=%s, restored=%s",
            page,
            seen_this_page,
            inserted_this_page,
            updated_this_page,
            skipped_this_page,
            archived_this_page,
            restored_this_page,
        )

        page += 1  # Nächste Seite
        made += 1  # Request-Zähler erhöhen
        time.sleep(random.uniform(0.5, 5)) # Kurze Pause gegen mögliches Throttling

    if completed_full_scan and not scope_changed:
        missing_filter: dict[str, Any] = {"is_active": {"$ne": False}}
        if seen_refs_in_run:
            missing_filter["referenznummer"] = {"$nin": sorted(seen_refs_in_run)}

        missing_docs = list(
            col.find(missing_filter, {"_id": 1, "referenznummer": 1, "missing_since": 1})
        )
        if missing_docs:
            now = utcnow_naive()
            missing_ops: list[UpdateOne] = []
            missing_history_docs: list[dict[str, Any]] = []
            for doc in missing_docs:
                ref = doc.get("referenznummer")
                if not ref:
                    continue
                missing_since = doc.get("missing_since") or now
                missing_ops.append(
                    UpdateOne(
                        {"_id": doc["_id"], "is_active": {"$ne": False}},
                        {
                            "$set": {
                                "is_active": False,
                                "updated_at": now,
                                "missing_since": missing_since,
                            }
                        },
                    )
                )
                missing_history_docs.append(
                    _build_list_missing_event(
                        ref=ref,
                        run_id=run_id,
                        now=now,
                        missing_since=missing_since,
                    )
                )
            if missing_ops:
                try:
                    col.bulk_write(missing_ops, ordered=False)
                    total_marked_missing = len(missing_ops)
                    if missing_history_docs:
                        col_history.insert_many(missing_history_docs, ordered=False)
                except Exception as e:
                    log.warning("run_id=%s: Fehler bei Deaktivierung fehlender Stellen: %s", run_id, e)
    elif completed_full_scan and scope_changed:
        log.warning(
            "run_id=%s: list query scope changed since previous run, missing-mark is skipped to avoid false inactive",
            run_id,
        )
    else:
        log.warning(
            "run_id=%s ohne vollständigen Seitenlauf beendet, Deaktivierung fehlender Stellen wird übersprungen",
            run_id,
        )

    col_state.update_one(
        {"key": "job_list_collector_query_scope"},
        {
            "$set": {
                "key": "job_list_collector_query_scope",
                "query_hash": query_hash,
                "api_url": api_url,
                "params": base_params,
                "last_run_id": run_id,
                "last_completed_full_scan": completed_full_scan,
                "scope_changed": scope_changed,
                "updated_at": utcnow_naive(),
            }
        },
        upsert=True,
    )

    log.info(
        "Fertig. run_id=%s, seen=%s, inserted=%s, updated=%s, skipped=%s, archived=%s, restored=%s, marked_missing=%s",
        run_id,
        total_seen,
        total_inserted,
        total_updated,
        total_skipped,
        total_archived,
        total_restored,
        total_marked_missing,
    )


if __name__ == "__main__":  # Bei Direktstart als Skript
    main()  # Hauptfunktion ausführen
