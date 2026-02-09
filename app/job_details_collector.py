#!/usr/bin/env python3  # Kann unter Unix direkt als Skript ausgeführt werden
# -*- coding: utf-8 -*-  # Dateikodierung (UTF-8)

"""
Lädt Stellendetails aus Jobcenter mit API-Aufrufbedingung:
- Wenn in jobdetails bereits ein Datensatz existiert, vergleichen wir:
    stellenangebote.modifikationsTimestamp (aus der Listen-Collection)
    VS jobdetails.aenderungsdatum (in den Details)
  Die API wird NUR aufgerufen, wenn modifikationsTimestamp strikt größer ist als aenderungsdatum.
- Wenn kein Datensatz in jobdetails existiert, wird die API aufgerufen und ein neuer Datensatz erstellt.
- Bei Inhaltsänderungen speichern wir den vorherigen Zustand in einer separaten History-Collection.
- Bei unverändertem Inhalt speichern wir ein leichtgewichtiges Zeit-Update-Event.

Annahmen:
- Datumswerte kommen als Strings wie "2025-08-05T06:14:28.330" oder "2025-08-05T06:14:28.33".
- Der Bruchteil der Sekunden wird auf 6 Stellen normalisiert, um datetime korrekt zu parsen.
"""

import base64
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone  # datetime/timezone für Zeitverarbeitung (UTC)
from typing import Any

import requests
from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

# Erlaubt Direktstart: python app/job_details_collector.py
if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.core.time_utils import ensure_utc_datetime, parse_remote_dt, utcnow_naive
from app.db.mongo import get_database, resolve_collection_name

# ---------- Hilfsfunktionen ----------

SERVICE_FIELDS = {
    "_id",
    "created_at",
    "updated_at",
    "details_hash",
    "old",
    "last_seen_remote_updated_at",
    "last_seen_remote_updated_at_dt",
    "aenderungsdatum_dt",
    "last_checked_at",
}
CONTENT_HASH_EXCLUDE_FIELDS = {"aenderungsdatum"}


def _history_collection_name(cfg: dict[str, Any]) -> str:
    env_value = os.getenv("COLLECTION_JOBDETAILS_HISTORY")
    if env_value:
        return env_value
    return cfg.get("history_collections", {}).get("details", "jobdetails_history")


def ensure_indexes(
    db: Database, details_collection_name: str, history_collection_name: str
) -> None:
    """Erstellt zuverlässig Indizes in Details- und History-Collections."""
    det = db[details_collection_name]
    det.create_index([("referenznummer", ASCENDING)], unique=True)
    det.create_index([("aenderungsdatum", ASCENDING)])
    det.create_index([("aenderungsdatum_dt", ASCENDING)])
    det.create_index([("last_seen_remote_updated_at_dt", ASCENDING)])
    det.create_index([("details_hash", ASCENDING)])

    history = db[history_collection_name]
    history.create_index([("referenznummer", ASCENDING), ("occurred_at", ASCENDING)])
    history.create_index([("occurred_at", ASCENDING)])
    history.create_index([("event_type", ASCENDING)])

def to_b64(s: str) -> str:  # Base64-Kodierung für URL (API-Anforderung)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8").rstrip("=")  # Kodieren und abschließendes '=' entfernen


def _details_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Behält nur Business-Felder für den Änderungsvergleich."""
    return {k: v for k, v in item.items() if k not in SERVICE_FIELDS}


def _content_payload_for_hash(payload: dict[str, Any]) -> dict[str, Any]:
    """Felder für Inhalts-Hash (ohne Remote-Update-Zeitstempel)."""
    return {k: v for k, v in payload.items() if k not in CONTENT_HASH_EXCLUDE_FIELDS}


def _compute_details_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_diff_update(
    existing_doc: dict[str, Any], new_payload: dict[str, Any], new_hash: str, now: datetime
) -> dict[str, Any]:
    existing_payload = _details_payload(existing_doc)
    set_fields: dict[str, Any] = {"updated_at": now, "details_hash": new_hash}
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
    existing_doc: dict[str, Any], new_hash: str, remote_updated_at: Any, now: datetime
) -> dict[str, Any]:
    snapshot = dict(existing_doc)
    snapshot.pop("_id", None)
    return {
        "referenznummer": existing_doc.get("referenznummer"),
        "occurred_at": now,
        "event_type": "content_change",
        "source": "jobcenter_details_api",
        "previous_remote_updated_at": existing_doc.get("last_seen_remote_updated_at"),
        "remote_updated_at": remote_updated_at,
        "previous_details_hash": existing_doc.get("details_hash"),
        "new_details_hash": new_hash,
        "snapshot": snapshot,
    }


def _build_no_content_change_event(
    referenznummer: str,
    details_hash: str,
    previous_remote_updated_at: Any,
    remote_updated_at: Any,
    now: datetime,
) -> dict[str, Any]:
    return {
        "referenznummer": referenznummer,
        "occurred_at": now,
        "event_type": "no_content_change",
        "source": "jobcenter_details_api",
        "details_hash": details_hash,
        "previous_remote_updated_at": previous_remote_updated_at,
        "remote_updated_at": remote_updated_at,
    }


def _build_remote_not_found_event(
    referenznummer: str,
    previous_remote_updated_at: Any,
    list_modifikations_timestamp: Any,
    now: datetime,
) -> dict[str, Any]:
    return {
        "referenznummer": referenznummer,
        "occurred_at": now,
        "event_type": "remote_not_found",
        "source": "jobcenter_details_api",
        "http_status": 404,
        "previous_remote_updated_at": previous_remote_updated_at,
        "list_modifikations_timestamp": list_modifikations_timestamp,
    }


# ---------- Hauptlogik ----------

def main():  # Hauptfunktion, die den Ablauf steuert
    log = get_logger("job_details_collector")  # Benannter Logger

    cfg = load_app_config()  # Anwendungskonfiguration laden
    db = get_database(cfg)  # Mit MongoDB verbinden
    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    details_collection_name = resolve_collection_name(cfg, key="details", default="jobdetails")
    history_collection_name = _history_collection_name(cfg)
    ensure_indexes(db, details_collection_name, history_collection_name)

    list_col: Collection = db[list_collection_name]
    det_col: Collection = db[details_collection_name]
    det_history_col: Collection = db[history_collection_name]

    api = cfg["jobcenter"]["details_api"]          # API-Parameter für Details
    headers = dict(api.get("headers", {}))         # HTTP-Header (kopieren, um Original nicht zu ändern)
    url_tpl = api["url_template"]                   # URL-Vorlage mit Base64(ref)

    # Nur aktive Stellen (Kompatibilität: fehlendes is_active gilt als aktiv).
    cursor = list_col.find(
        {"is_active": {"$ne": False}},
        {"referenznummer": 1, "refnr": 1, "modifikationsTimestamp": 1, "modifikationsTimestamp_dt": 1},
    )  # Iterator der Kandidaten zur Detail-Aktualisierung
    processed = 0  # Zähler für verarbeitete (erstellt/aktualisiert) Detail-Datensätze

    for doc in cursor:  # Durch alle Listen-Dokumente iterieren
        ref = doc.get("referenznummer") or doc.get("refnr")  # Referenznummer der Stelle
        if not ref:  # Ohne Referenznummer überspringen
            continue

        sa_raw = doc.get("modifikationsTimestamp")  # Änderungszeit in der Liste (String)
        sa_dt = ensure_utc_datetime(doc.get("modifikationsTimestamp_dt"))
        if sa_dt is None:
            sa_dt = parse_remote_dt(sa_raw)  # Fallback für ältere Dokumente ohne *_dt
        if sa_dt is None:  # Ohne parsebares Datum kein korrekter Vergleich möglich
            log.info(f"{ref}: kein gültiger modifikationsTimestamp in stellenangebote — Request übersprungen")
            continue

        current = det_col.find_one({"referenznummer": ref})  # Bestehenden Detail-Datensatz suchen

        if current:  # Falls Detail-Datensatz bereits existiert
            jd_raw = current.get("last_seen_remote_updated_at") or current.get("aenderungsdatum")
            jd_dt = ensure_utc_datetime(
                current.get("last_seen_remote_updated_at_dt") or current.get("aenderungsdatum_dt")
            )
            if jd_dt is None:
                jd_dt = parse_remote_dt(jd_raw)  # Fallback für ältere Dokumente ohne *_dt

            # --- Schlüsselbedingung: API-Aufruf NUR wenn Listen-Datum strikt neuer ist ---
            if jd_dt is not None and sa_dt <= jd_dt:
                log.info(f"{ref}: Liste ist nicht neuer (SA<=JD), API wird übersprungen")
                continue

            # Wenn in Details kein Datum vorhanden ist (jd_dt is None), wird aktualisiert.
            # Falls sa_dt > jd_dt, wird ebenfalls aktualisiert.

        else:
            # Wenn kein Detail-Datensatz existiert, API aufrufen und neu anlegen
            pass

        # --- Ab hier: API-Aufruf erforderlich (kein Datensatz oder Liste neuer) ---
        
        ref_b64 = to_b64(ref)  # Referenznummer in Base64 (API-Anforderung)
        url = url_tpl.format(ref_b64=ref_b64)  # In URL-Vorlage einsetzen

        payload = None  # API-Antwort (Stellendetails)
        remote_not_found = False
        time.sleep(random.uniform(0.5, 2)) # Kurze Pause zur Reduktion von Throttling-Risiko
        for attempt in range(2):  # Bis zu 2 Versuche bei temporären Fehlern
            try:
                r = requests.get(url, headers=headers, timeout=10)  # HTTP GET senden
                if r.status_code == 200:  # Erfolg
                    payload = r.json()  # JSON parsen -> dict
                    break
                if r.status_code == 404:
                    remote_not_found = True
                    log.info(f"{ref}: HTTP 404 (Details an der Quelle nicht verfügbar), ohne Retry übersprungen")
                    break
                else:
                    log.warning(f"{ref}: HTTP {r.status_code}")
            except Exception as e:  # Netzwerk-/Timeout-Fehler
                log.warning(f"{ref}: Request-Fehler: {e}")
            time.sleep(2 ** attempt ** random.uniform(0.9, 4))  # Exponentielle Pause zwischen Versuchen

        if remote_not_found:
            now = utcnow_naive()
            det_history_col.insert_one(
                _build_remote_not_found_event(
                    referenznummer=ref,
                    previous_remote_updated_at=(
                        current.get("last_seen_remote_updated_at") if current else None
                    ),
                    list_modifikations_timestamp=sa_raw,
                    now=now,
                )
            )
            continue

        if not payload:  # Nach allen Versuchen keine Daten
            log.info(f"{ref}: keine Daten von der API (übersprungen)")
            continue

        # Payload normalisieren (referenznummer setzen, aenderungsdatum notfalls auf None)
        payload["referenznummer"] = ref
        payload.setdefault("aenderungsdatum", None)
        remote_updated_raw = payload.get("aenderungsdatum")
        remote_updated_dt = parse_remote_dt(remote_updated_raw)
        new_dt = remote_updated_dt or datetime.now(timezone.utc)  # Für date-gate Vergleiche

        if current:  # Bei bestehendem Dokument entscheiden: aktualisieren oder überspringen
            cur_dt = ensure_utc_datetime(
                current.get("last_seen_remote_updated_at_dt") or current.get("aenderungsdatum_dt")
            )
            if cur_dt is None:
                cur_dt = parse_remote_dt(
                    current.get("last_seen_remote_updated_at") or current.get("aenderungsdatum")
                )

            if cur_dt and new_dt <= cur_dt:  # Zusätzlicher Schutz gegen nicht-neuere Daten
                log.info(f"{ref}: API-Details nicht neuer (new<=old), übersprungen")
                continue

            business_payload = _details_payload(payload)
            business_payload["referenznummer"] = ref
            content_payload = _content_payload_for_hash(business_payload)
            new_hash = _compute_details_hash(content_payload)
            if current.get("details_hash") == new_hash:
                now = utcnow_naive()
                previous_remote_updated_at = current.get("last_seen_remote_updated_at")
                det_history_col.insert_one(
                    _build_no_content_change_event(
                        referenznummer=ref,
                        details_hash=new_hash,
                        previous_remote_updated_at=previous_remote_updated_at,
                        remote_updated_at=remote_updated_raw,
                        now=now,
                    )
                )
                det_col.update_one(
                    {"_id": current["_id"]},
                    {
                        "$set": {
                            "aenderungsdatum": remote_updated_raw,
                            "aenderungsdatum_dt": remote_updated_dt,
                            "last_seen_remote_updated_at": remote_updated_raw,
                            "last_seen_remote_updated_at_dt": remote_updated_dt,
                            "last_checked_at": now,
                        }
                    },
                )
                log.info(f"{ref}: Inhalt unverändert, Zeitmarken und History-Event aktualisiert")
                continue

            now = utcnow_naive()
            business_payload["last_seen_remote_updated_at"] = remote_updated_raw
            business_payload["last_seen_remote_updated_at_dt"] = remote_updated_dt
            business_payload["aenderungsdatum_dt"] = remote_updated_dt
            business_payload["last_checked_at"] = now
            det_history_col.insert_one(
                _build_history_snapshot(current, new_hash, remote_updated_raw, now)
            )
            det_col.update_one(
                {"_id": current["_id"], "details_hash": {"$ne": new_hash}},
                _build_diff_update(current, business_payload, new_hash, now),
            )
            processed += 1  # Zähler erhöhen
            log.info(f"{ref}: aktualisiert (vorheriger Zustand in {history_collection_name} archiviert)")
        else:
            # Kein bestehendes Dokument: neu einfügen
            now = utcnow_naive()  # Aktuelle UTC-Zeit für Service-Felder
            business_payload = _details_payload(payload)
            business_payload["referenznummer"] = ref
            business_payload["details_hash"] = _compute_details_hash(_content_payload_for_hash(business_payload))
            business_payload["last_seen_remote_updated_at"] = remote_updated_raw
            business_payload["last_seen_remote_updated_at_dt"] = remote_updated_dt
            business_payload["aenderungsdatum_dt"] = remote_updated_dt
            business_payload["last_checked_at"] = now
            business_payload["created_at"] = now
            business_payload["updated_at"] = now
            det_col.insert_one(business_payload)
            processed += 1
            log.info(f"{ref}: eingefügt")

    log.info(f"Fertig. Aktualisiert/eingefügt: {processed}")

if __name__ == "__main__":  # Bei Direktstart als Hauptprogramm
    main()
