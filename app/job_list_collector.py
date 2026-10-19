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
from app.core.i18n import language_scope, resolve_language, tr, message_record
from app.core.logging import get_logger
from app.core.time_utils import parse_remote_dt, utcnow_naive
from app.db.mongo import get_database, resolve_collection_name
from app.jobcenter.list_query_expansion import expand_list_query
from app.jobcenter.adaptive_search import AdaptiveSearch
from app.jobcenter.search_url import (
    advertised_total, configured_search_url, parse_search_url, result_window_limit,
)
from app.jobcenter.throttle import RequestJitter, request_jitter_from_config, sleep_with_jitter


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


def _compute_scope_hash(
    api_url: str,
    params: dict[str, Any],
    scope_payload: dict[str, Any] | None = None,
) -> str:
    payload = {"api_url": api_url, "params": params}
    if scope_payload is not None:
        payload["scope_payload"] = scope_payload
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


def _extract_list_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Возвращает вакансии из формата v6 или из legacy-ответа v4."""
    # В v6 BA переименовала список вакансий: `stellenangebote` -> `ergebnisliste`.
    items = data.get("ergebnisliste")
    if items is None:
        items = data.get("stellenangebote")
    return items if isinstance(items, list) else []


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


def _normalize_list_updated_at(item: dict[str, Any]) -> None:
    """Приводит дату изменения v6 к полю, которое использует details-пайплайн."""
    # В v6 дата называется `aenderungsdatum`, в прежнем контракте —
    # `modifikationsTimestamp`. Alias сохраняет date-gate details-этапа.
    if not item.get("modifikationsTimestamp") and item.get("aenderungsdatum"):
        item["modifikationsTimestamp"] = item["aenderungsdatum"]
    item["modifikationsTimestamp_dt"] = parse_remote_dt(item.get("modifikationsTimestamp"))


def _add_legacy_v4_aliases(item: dict[str, Any]) -> None:
    """Добавляет прежние имена полей, чтобы v6 не ломала поиск и matching."""
    aliases = {
        "titel": item.get("stellenangebotsTitel"),
        "arbeitgeber": item.get("firma"),
        "beruf": item.get("hauptberuf"),
    }
    for legacy_field, value in aliases.items():
        if value is not None and not item.get(legacy_field):
            item[legacy_field] = value

    locations = item.get("stellenlokationen")
    if not item.get("arbeitsort") and isinstance(locations, list) and locations:
        first_location = locations[0]
        if isinstance(first_location, dict) and isinstance(first_location.get("adresse"), dict):
            item["arbeitsort"] = dict(first_location["adresse"])


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
    existing_doc: dict[str, Any],
    new_payload: dict[str, Any],
    new_hash: str,
    now: datetime,
    *,
    preserve_fields_missing_from_response: bool = False,
) -> dict[str, Any]:
    existing_payload = _meta_payload(existing_doc)
    set_fields: dict[str, Any] = {"updated_at": now, "meta_hash": new_hash}
    unset_fields: dict[str, str] = {}

    for key, new_value in new_payload.items():
        if existing_payload.get(key) != new_value:
            set_fields[key] = new_value

    if not preserve_fields_missing_from_response:
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
    shard_id: str | None = None,
    plz: str | None = None,
    logical_query_mode: str | None = None,
) -> dict[str, Any]:
    event = {
        "event_type": "list_seen",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "page": page,
        "seen_at": seen_at,
        "remote_updated_at": item.get("aktualisierungsdatum") or item.get("aenderungsdatum"),
        "meta_hash": meta_hash,
    }
    if shard_id is not None:
        event["shard_id"] = shard_id
    if plz is not None:
        event["plz"] = plz
    if logical_query_mode is not None:
        event["logical_query_mode"] = logical_query_mode
    return event


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

def _collect(cfg: dict) -> None:
    """Hauptfunktion: lädt Konfiguration, iteriert API-Seiten und führt idempotente Updates aus."""
    log = get_logger("job_list_collector")  # Benannter Logger

    list_api = cfg["jobcenter"]["list_api"]  # Konfigurationsbereich für Stellenliste

    api_url = list_api["api_url"]  # API-Endpunkt für Stellenliste
    api_key = os.getenv("JOBCENTER_LIST_API_KEY", str(list_api.get("api_key", ""))).strip()
    base_params = dict(list_api.get("params", {}))  # Basis-Suchparameter
    window_limit = result_window_limit(list_api)
    search_url = configured_search_url(list_api)
    search = parse_search_url(search_url, base_params) if search_url else None
    adaptive = AdaptiveSearch(search, list_api, window_limit) if search else None
    if search:
        base_params = search.params
    base_headers = dict(list_api.get("headers", {}))  # Basis-HTTP-Header
    max_requests = int(list_api.get("max_requests", 1000))  # Max. Seiten/Requests als Schutz
    list_retry_attempts = max(1, int(list_api.get("retry_attempts", 3)))
    list_retry_backoff_seconds = max(0.0, float(list_api.get("retry_backoff_seconds", 1.0)))
    request_jitter = request_jitter_from_config(
        list_api,
        env_var="JOBCENTER_LIST_REQUEST_JITTER_SECONDS",
        default=RequestJitter(0.5, 5.0),
    )
    expansion = expand_list_query(list_api) if not search else None
    _ = list_api.get("output_filenames", {})  # Kompatibilität: in config vorhanden, hier nicht verwendet

    if (list_api.get('search_area') or {}).get('dry_run'):
        preview = adaptive.preview() if adaptive else expansion.to_dict()
        log.info(
            "Dry-run list query expansion: %s",
            json.dumps(preview, ensure_ascii=False, sort_keys=True),
        )
        return

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
    query_hash = _compute_scope_hash(
        api_url=api_url,
        params=base_params,
        scope_payload=({'search_id': search.search_id, 'result_window_limit': window_limit}
                       if search else expansion.scope_payload if expansion.mode == "plz" else None),
    )
    previous_scope = col_state.find_one({"key": "job_list_collector_query_scope"})
    previous_query_hash = previous_scope.get("query_hash") if previous_scope else None
    scope_changed = bool(previous_query_hash and previous_query_hash != query_hash)
    log.info(
        "Start list_collector: run_id=%s, mode=%s, logical_query_mode=%s, shards=%s, list_seen_ttl_days=%s, scope_changed=%s, jitter=%.2f-%.2fs",
        run_id,
        'url' if search else expansion.mode,
        'url' if search else expansion.logical_query_mode,
        1 if search else len(expansion.physical_queries),
        list_seen_ttl_days,
        scope_changed,
        request_jitter.min_seconds,
        request_jitter.max_seconds,
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
    completed_full_scan = True
    execution_complete = True
    query_outcomes = []
    warnings = []
    made = 0  # Bereits gesendete Requests (Limit via max_requests)
    pending = adaptive.pending if adaptive else iter(expansion.physical_queries)
    processed_queries = 0
    while True:
        if adaptive:
            if not pending:
                break
            physical_query = pending.popleft()
        else:
            physical_query = next(pending, None)
            if physical_query is None:
                break
        processed_queries += 1
        shard_id = physical_query.shard_id
        shard_page_limit = (int((list_api.get('search_area') or {}).get('max_pages_per_physical_query', 50))
                            if adaptive else expansion.max_pages_per_physical_query or max_requests)
        page = 1
        shard_requests = 0
        shard_completed = False
        redirected = False
        received = 0
        total = None
        outcome = 'incomplete'
        write_failed = False

        while made < max_requests and shard_requests < shard_page_limit:
            params = {**physical_query.params, "page": page}
            made += 1
            shard_requests += 1

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
            except Exception as e:
                log.error("Shard %s Seite %s: Request/Parsing-Fehler (%s)", shard_id, page, type(e).__name__)
                completed_full_scan = False
                execution_complete = False
                outcome = 'request_error'
                break

            if not isinstance(data, dict) or not any(
                    isinstance(data.get(key), list) for key in ('ergebnisliste', 'stellenangebote')):
                log.warning('Shard %s Seite %s: invalid list response', shard_id, page)
                execution_complete = completed_full_scan = False
                outcome = 'invalid_response'
                break
            if page == 1:
                total = advertised_total(data)
                if adaptive and adaptive.redirect(physical_query, data):
                    redirected = True
                    sleep_with_jitter(request_jitter)
                    break
            items = _extract_list_items(data)
            received += len(items)
            is_v6_response = isinstance(data.get("ergebnisliste"), list)
            if not items:
                log.info("Shard %s Seite %s: keine Stellen — shard abgeschlossen.", shard_id, page)
                shard_completed = True
                outcome = 'window_limit' if total is not None and total >= window_limit else 'complete'
                if total is not None and received < total and total < window_limit:
                    outcome = 'incomplete'
                    shard_completed = False
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
                log.info("Shard %s Seite %s: keine validen Einträge mit referenznummer.", shard_id, page)
                page += 1
                completed_full_scan = False
                execution_complete = False
                if received >= window_limit:
                    outcome = 'window_limit'
                    break
                sleep_with_jitter(request_jitter)
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
                _normalize_list_updated_at(raw)
                if is_v6_response:
                    _add_legacy_v4_aliases(raw)
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
                        shard_id=shard_id,
                        plz=physical_query.plz,
                        logical_query_mode='url' if search else expansion.logical_query_mode,
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
                diff_update = _build_diff_update(
                    current,
                    payload,
                    new_hash,
                    now,
                    preserve_fields_missing_from_response=is_v6_response,
                )
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
                log.warning("Shard %s Seite %s: Race Condition am Unique-Key erkannt, fortgesetzt.", shard_id, page)
                execution_complete = completed_full_scan = False
                write_failed = True
            except Exception as e:
                log.warning("Shard %s Seite %s: Fehler beim Schreiben nach Mongo (%s)", shard_id, page, type(e).__name__)
                execution_complete = completed_full_scan = False
                write_failed = True

            total_inserted += inserted_this_page
            total_updated += updated_this_page
            total_skipped += skipped_this_page
            total_archived += archived_this_page
            total_seen += seen_this_page
            total_restored += restored_this_page
            log.info(
                "Shard %s Seite %s: seen=%s, inserted=%s, updated=%s, skipped=%s, archived=%s, restored=%s",
                shard_id,
                page,
                seen_this_page,
                inserted_this_page,
                updated_this_page,
                skipped_this_page,
                archived_this_page,
                restored_this_page,
            )

            page += 1
            sleep_with_jitter(request_jitter)
            if received >= window_limit:
                outcome = 'window_limit'
                shard_completed = True
                break
            if total is not None and received >= total:
                outcome = 'complete'
                shard_completed = True
                break

        if redirected:
            continue
        if outcome == 'window_limit':
            completed_full_scan = False
            message = tr('Достигнут предел API {p0}; полнота выдачи не обеспечена.', p0=window_limit)
            log.warning('run_id=%s shard=%s: %s', run_id, shard_id, message)
            warnings.append({'code': 'result_window_reached', **message_record(message), 'shard_id': shard_id})
        if not shard_completed:
            completed_full_scan = False
            execution_complete = False
            if made >= max_requests:
                outcome = 'global_limit'
                log.warning(
                    "run_id=%s: global max_requests=%s erreicht, shard=%s bleibt unvollständig",
                    run_id,
                    max_requests,
                    shard_id,
                )
            elif shard_requests >= shard_page_limit:
                outcome = 'page_limit'
                log.warning(
                    "run_id=%s: shard=%s hit max_pages_per_physical_query=%s",
                    run_id,
                    shard_id,
                    shard_page_limit,
                )
        if write_failed:
            outcome = 'write_error'
        record = {'shard_id': shard_id, 'params': physical_query.params,
                  'total': total, 'received': received, 'status': outcome}
        query_outcomes.append(record)
        if adaptive:
            adaptive.record(physical_query, total=total, received=received, status=outcome,
                            attempted=shard_requests > 0)
        if made >= max_requests and not shard_completed:
            break

    if adaptive:
        for warning in warnings:
            if warning not in adaptive.warnings:
                adaptive.warnings.append(warning)
        provenance = adaptive.provenance(execution_complete)
        completed_full_scan = completed_full_scan and provenance['coverage_complete']
    else:
        provenance = {'result_window_limit': window_limit, 'warnings': warnings,
                      'coverage_complete': completed_full_scan,
                      'execution_complete': execution_complete, 'queries': query_outcomes}

    if completed_full_scan and not scope_changed and not search:
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
    elif completed_full_scan and scope_changed and not search:
        log.warning(
            "run_id=%s: list query scope changed since previous run, missing-mark is skipped to avoid false inactive",
            run_id,
        )
    elif search:
        log.warning('run_id=%s: URL search does not mark records outside its result inactive', run_id)
    else:
        log.warning(
            "run_id=%s ohne vollständigen Seitenlauf beendet, Deaktivierung fehlender Stellen wird übersprungen",
            run_id,
        )

    if search:
        # One immutable-by-run snapshot, separate from the current-state pointer.
        runs = db['job_search_runs']
        runs.create_index([('search_id', ASCENDING), ('created_at', ASCENDING)])
        runs.update_one({'_id': run_id}, {'$setOnInsert': {
            'run_id': run_id, 'query_hash': query_hash, 'search_id': search.search_id,
            'created_at': utcnow_naive(), **provenance}}, upsert=True)

    col_state.update_one(
        {"key": "job_list_collector_query_scope"},
        {
            "$set": {
                "key": "job_list_collector_query_scope",
                "query_hash": query_hash,
                "api_url": api_url,
                "params": base_params,
                "scope_payload": {'mode': 'url', 'search_id': search.search_id} if search else expansion.scope_payload,
                "logical_query_mode": 'url' if search else expansion.logical_query_mode,
                "physical_queries_count": processed_queries,
                "last_run_id": run_id,
                "last_completed_full_scan": completed_full_scan,
                "last_run_usable": execution_complete,
                "search_provenance": provenance,
                "scope_changed": scope_changed,
                "updated_at": utcnow_naive(),
            }
        },
        upsert=True,
    )

    if search:
        for warning in provenance['warnings']:
            log.warning('search_id=%s [%s]: %s', search.search_id, warning['code'], warning['message'])

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


def main() -> None:
    cfg = load_app_config()
    with language_scope(resolve_language(cfg)):
        return _collect(cfg)


if __name__ == "__main__":  # Bei Direktstart als Skript
    main()  # Hauptfunktion ausführen
