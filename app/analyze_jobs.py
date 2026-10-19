#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analyse + Begleitschreiben in MongoDB.
- Profil: aus der Collection profiles via analyzer.profile_key
- Stelle: meta (stellenangebote) + details (jobdetails) via $lookup
- An OpenAI wird NUR der aktuelle Zustand gesendet (ohne _id/old)
- Modellantwort strikt als JSON; Speicherung in collections.analysis
"""

import json
import os
import re
import hashlib
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from bson import ObjectId
from openai import OpenAI

# Erlaubt Direktstart: python app/analyze_jobs.py
if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.db.mongo import get_database, resolve_collection_name

# ---------- Konfiguration / Verbindung ----------

def ensure_indexes(db: Database, cfg: dict):
    col = db[resolve_collection_name(cfg, key="analysis", default="analysen")]
    # Pro Referenznummer + Profil darf derselbe Input nur einmal gespeichert werden.
    col.create_index(
        [("referenznummer", ASCENDING), ("candidate_profile_key", ASCENDING), ("input_hash", ASCENDING)],
        unique=True,
    )
    # Für Queries auf die aktuelle/alte Versionen pro Stelle und Kandidatenprofil.
    col.create_index([("referenznummer", ASCENDING), ("candidate_profile_key", ASCENDING), ("is_latest", ASCENDING)])
    col.create_index([("prompt_key", ASCENDING), ("prompt_version", ASCENDING)])
    col_prompt = db[resolve_collection_name(cfg, key="analysis_prompts", default="analysis_prompts")]
    col_prompt.create_index([("key", ASCENDING), ("version", ASCENDING)], unique=True)
    col_prompt.create_index([("key", ASCENDING), ("is_active", ASCENDING), ("version", DESCENDING)])


INPUT_HASH_IGNORE_FIELDS = {
    "_id",
    "old",
    "created_at",
    "updated_at",
    "last_checked_at",
    "meta_hash",
    "details_hash",
    "input_hash",
    "version",
    "is_latest",
    "run_id",
    "seen_at",
    "missing_since",
}

# ---------- Hilfsfunktionen ----------

def extract_numbers(text: str, limit=3):
    """Extrahiert bis zu 3 Zahlen aus Text (inkl. Komma/Punkt)."""
    if not text:
        return []
    nums = re.findall(r'(?<!\w)(?:\d{1,3}(?:[.,]\d+)?)', text)
    out = []
    for s in nums:
        s = s.replace(',', '.')
        try:
            v = float(s)
        except Exception:
            continue
        out.append(v)
        if len(out) >= limit:
            break
    return out

def json_default(o):
    """Serialisierung nicht standardmäßiger Typen für json.dumps."""
    if isinstance(o, ObjectId):
        return str(o)
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.isoformat()
    return str(o)

def make_json_safe(x):
    """Entfernt rekursiv Service-Felder und macht Daten JSON-sicher."""
    if isinstance(x, dict):
        return {k: make_json_safe(v)
                for k, v in x.items()
                if k not in {"_id", "old"}}
    if isinstance(x, list):
        return [make_json_safe(i) for i in x]
    if isinstance(x, tuple):
        return [make_json_safe(i) for i in x]
    if isinstance(x, ObjectId):
        return str(x)
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.isoformat()
    return x


def _is_input_hash_service_field(key: str) -> bool:
    return (
        key in INPUT_HASH_IGNORE_FIELDS
        or key.startswith("list_seen")
        or key.startswith("last_seen_remote_updated_at")
    )


def _strip_input_hash_service_fields(value):
    if isinstance(value, dict):
        return {
            k: _strip_input_hash_service_fields(v)
            for k, v in value.items()
            if not _is_input_hash_service_field(k)
        }
    if isinstance(value, list):
        return [_strip_input_hash_service_fields(v) for v in value]
    if isinstance(value, tuple):
        return [_strip_input_hash_service_fields(v) for v in value]
    return value


def _compute_input_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_prompt_key(cfg: dict) -> str:
    env_value = os.getenv("ANALYZER_PROMPT_KEY")
    if env_value:
        return env_value.strip()
    key = str(cfg.get("analyzer", {}).get("prompt_key", "default")).strip()
    return key or "default"


def _resolve_profile_key(cfg: dict) -> str:
    env_value = os.getenv("ANALYZER_PROFILE_KEY")
    if env_value:
        return env_value.strip()
    key = str(cfg.get("analyzer", {}).get("profile_key", "default")).strip()
    return key or "default"


def _resolve_prompt_version(cfg: dict) -> int | None:
    env_value = os.getenv("ANALYZER_PROMPT_VERSION")
    raw = env_value if env_value is not None else cfg.get("analyzer", {}).get("prompt_version")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except Exception as exc:
        raise RuntimeError(f"Ungültige prompt_version: {raw}") from exc
    if value < 1:
        raise RuntimeError(f"prompt_version muss >= 1 sein, erhalten: {value}")
    return value


def _load_analysis_prompt(col_prompt, prompt_key: str, prompt_version: int | None) -> dict:
    if prompt_version is None:
        prompt = col_prompt.find_one(
            {"key": prompt_key, "is_active": True},
            sort=[("version", DESCENDING)],
        )
        if prompt:
            return prompt
        raise RuntimeError(
            f"Prompt nicht gefunden: key='{prompt_key}' (aktive Version erwartet). "
            "Importieren Sie den default Prompt: python3 tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json"
        )

    prompt = col_prompt.find_one({"key": prompt_key, "version": prompt_version})
    if prompt:
        return prompt
    raise RuntimeError(f"Prompt nicht gefunden: key='{prompt_key}', version={prompt_version}")


def _extract_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    response = getattr(exc, "response", None)
    response_status_code = getattr(response, "status_code", None)
    if isinstance(response_status_code, int):
        return response_status_code
    return None


def _is_retryable_openai_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code == 429:
        return True
    if isinstance(status_code, int) and 500 <= status_code < 600:
        return True
    return False


def _create_chat_completion_with_retries(
    client: OpenAI,
    req: dict,
    log,
    ref: str,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
):
    # Nur temporäre API-Fehler (429/5xx) werden erneut versucht.
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(**req)
        except Exception as exc:
            if not _is_retryable_openai_error(exc):
                raise
            if attempt >= max_attempts:
                raise

            delay = base_delay_seconds * (2 ** (attempt - 1))
            status_code = _extract_status_code(exc)
            log.warning(
                f"{ref}: OpenAI temporärer Fehler (status={status_code}), "
                f"Retry {attempt}/{max_attempts} in {delay:.1f}s"
            )
            time.sleep(delay)


def _validate_analysis_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("LLM-Antwort muss ein JSON-Objekt sein.")

    required_fields = {
        "score",
        "decision",
        "reasons",
        "risks",
        "missing_evidence",
        "notes",
    }
    missing = [field for field in required_fields if field not in data]
    if missing:
        raise ValueError(f"Pflichtfelder fehlen: {', '.join(sorted(missing))}")

    try:
        float(data.get("score"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Feld 'score' muss numerisch sein.") from exc

    decision = data.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        raise ValueError("Feld 'decision' muss ein nicht-leerer String sein.")

    notes = data.get("notes")
    if not isinstance(notes, str):
        raise ValueError("Feld 'notes' muss ein String sein.")

    # Für Listenfelder akzeptieren wir nur String-Elemente, damit Snapshot und UI stabil bleiben.
    for field_name in ("reasons", "risks", "missing_evidence"):
        field_value = data.get(field_name)
        if not isinstance(field_value, list):
            raise ValueError(f"Feld '{field_name}' muss eine Liste sein.")
        if not all(isinstance(item, str) for item in field_value):
            raise ValueError(f"Feld '{field_name}' darf nur String-Elemente enthalten.")

    return data
 
# ---------- Hauptlogik ----------

def main():
    log = get_logger("analyze_jobs")

    cfg = load_app_config()
    db = get_database(cfg)
    ensure_indexes(db, cfg)

    col_list = db[resolve_collection_name(cfg, key="list", default="stellenangebote")]  # stellenangebote
    col_an = db[resolve_collection_name(cfg, key="analysis", default="analysen")]  # analysen
    col_prof = db[resolve_collection_name(cfg, key="profiles", default="profiles")]  # profiles
    col_prompt = db[
        resolve_collection_name(cfg, key="analysis_prompts", default="analysis_prompts")
    ]  # analysis_prompts

    # Kandidatenprofil
    key = _resolve_profile_key(cfg)
    profile = col_prof.find_one({"key": key}, {"_id": 0})
    if not profile:
        profiles_collection_name = resolve_collection_name(cfg, key="profiles", default="profiles")
        raise RuntimeError(f"Profil mit key='{key}' wurde in '{profiles_collection_name}' nicht gefunden")

    # OpenAI-Client
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = cfg["analyzer"]["model"]
    prompt_key = _resolve_prompt_key(cfg)
    prompt_version = _resolve_prompt_version(cfg)
    prompt_doc = _load_analysis_prompt(col_prompt, prompt_key=prompt_key, prompt_version=prompt_version)
    system_prompt = str(prompt_doc.get("system_prompt", "")).strip()
    analysis_task = str(prompt_doc.get("analysis_task", "")).strip()
    if not system_prompt or not analysis_task:
        raise RuntimeError(
            f"Prompt key='{prompt_doc.get('key')}', version={prompt_doc.get('version')} "
            "enthält kein system_prompt und/oder analysis_task."
        )

    # Aggregation: verbindet meta+details, entfernt _id/old (nur aktueller Zustand)
    cursor = col_list.aggregate([
        { "$match": { "is_active": True } },
        {
            "$lookup": {
                "from": resolve_collection_name(cfg, key="details", default="jobdetails"),
                "let": {"ref": "$referenznummer"},
                "pipeline": [
                    { "$match": { "$expr": { "$eq": ["$referenznummer", "$$ref"] } } },
                    { "$project": { "_id": 0, "old": 0 } }
                ],
                
                "as": "details"
            }
        },
        { "$addFields": { "details": { "$first": "$details" } } },
        { "$project": { "_id": 0, "old": 0 } }
    ])

    processed = 0
    for doc in cursor:
        ref = doc.get("referenznummer") or doc.get("refnr")
        if not ref:
            continue

        title = (
            doc.get("stellenangebotsTitel")
            or (doc.get("details") or {}).get("stellenangebotsTitel")
            or doc.get("titel")
            or ""
        ).strip()
        firma = (doc.get("arbeitgeber") or doc.get("firma") or "").strip()

        details = doc.get("details") or {}
        job_meta = {k: v for k, v in doc.items() if k != "details"}

        merged = {
            "meta": job_meta,
            "details": details
        }

        # JSON-sichere Kopien für die Modellanfrage
        safe_profile = make_json_safe(profile)
        safe_merged  = make_json_safe(merged)

        input_hash_payload = {
            "meta": _strip_input_hash_service_fields(safe_merged.get("meta", {})),
            "details": _strip_input_hash_service_fields(safe_merged.get("details", {})),
            "profile": _strip_input_hash_service_fields(safe_profile),
            "prompt": {
                "key": prompt_doc.get("key"),
                "version": prompt_doc.get("version"),
                "hash": prompt_doc.get("prompt_hash"),
                "system": system_prompt,
                "task": analysis_task,
            },
            "model": model,
        }
        input_hash = _compute_input_hash(input_hash_payload)
        same_hash_doc = col_an.find_one(
            {"referenznummer": ref, "candidate_profile_key": key, "input_hash": input_hash},
            {"_id": 1, "version": 1},
        )
        if same_hash_doc:
            log.info(f"{ref}: Eingang unverändert — übersprungen ({title} bei {firma})")
            continue

        latest_doc = col_an.find_one(
            {"referenznummer": ref, "candidate_profile_key": key},
            {"_id": 1, "version": 1},
            sort=[("version", DESCENDING)],
        )
        if latest_doc:
            log.info(f"{ref}: Eingang geändert — erneute Analyse ({title} bei {firma})")
        else:
            log.info(f"{ref}: neue Stelle — {title} bei {firma}")

        latest_version = latest_doc.get("version") if latest_doc else None
        next_version = int(latest_version) + 1 if latest_version else 1


        user_payload = {
            "candidate_profile": safe_profile,
            "job_payload": safe_merged,
            "task": analysis_task
        }
        user_content = json.dumps(user_payload, ensure_ascii=False, default=json_default)
    
        try:
            req = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                #"max_completion_tokens": 1100,
                #"temperature": 0.2,  # gewünscht, aber nicht von allen Modellen unterstützt
                "reasoning_effort":"low",
                "verbosity":"low"
            }

            resp = _create_chat_completion_with_retries(client=client, req=req, log=log, ref=ref)

            content = resp.choices[0].message.content
            data = json.loads(content)
            data = _validate_analysis_response(data)

        except Exception as e:
            log.error(f"{ref}: Fehler beim OpenAI-Aufruf/Parsing: {e}")
            continue


        # Score normalisieren
        score = data.get("score")
        try:
            score = float(score) if score is not None else None
        except Exception:
            score = None

        # Erste gefundene Zahlen (bis zu 3) aus allen Textfeldern
        concat_text = " ".join(filter(None, [
            data.get("notes", ""),
            data.get("cover_letter", ""),
            " ".join(data.get("reasons", []) or []),
            " ".join(data.get("improvements", []) or []),
        ]))
        probabilities = []
        if score is not None:
            probabilities.append(score)
        for n in extract_numbers(concat_text, limit=3):
            if n not in probabilities:
                probabilities.append(n)
        probabilities = probabilities[:3]

        now_utc = datetime.now(timezone.utc)
        out_doc = {
            "referenznummer": ref,
            "version": next_version,
            "is_latest": True,
            "updated_at": now_utc,
            "created_at": now_utc,
            "candidate_profile_key": key,
            "model": model,
            "input_hash": input_hash,
            "prompt_key": prompt_doc.get("key"),
            "prompt_version": prompt_doc.get("version"),
            "prompt_hash": prompt_doc.get("prompt_hash"),

            "job_payload": make_json_safe(merged),
            "request_snapshot": {
                "model": req.get("model"),
                "response_format": req.get("response_format"),
                "reasoning_effort": req.get("reasoning_effort"),
                "verbosity": req.get("verbosity"),
                "messages": req.get("messages"),
                "user_payload": user_payload,
            },
            "prompt_snapshot": {
                "key": prompt_doc.get("key"),
                "version": prompt_doc.get("version"),
                "name": prompt_doc.get("name"),
                "description": prompt_doc.get("description"),
                "prompt_hash": prompt_doc.get("prompt_hash"),
                "system": system_prompt,
                "task": analysis_task,
            },

            #"candidate_profile": make_json_safe(profile),  # kann entfernt werden, falls das Dokument zu groß wird
           
            "score": score,
            "score_": int(score) if score is not None else 0,
            #"wahrscheinlichkeit": score,
            #"probabilities": probabilities,
            "decision": data.get("decision", "hold"),
            "reasons": (data.get("reasons") or [])[:5],
            "risks": (data.get("risks") or [])[:4],
            "missing_evidence": (data.get("missing_evidence") or [])[:4],
            "notes": data.get("notes", ""),
            #"improvements": (data.get("improvements") or [])[:3],
            #"cover_letter": data.get("cover_letter", "")
            "openai_raw": data,     # Modellantwort als JSON
        }

        if latest_doc:
            col_an.update_many(
                {"referenznummer": ref, "candidate_profile_key": key},
                {
                    "$set": {
                        "is_latest": False,
                        "updated_at": out_doc["updated_at"],
                    }
                },
            )
        col_an.insert_one(out_doc)
        processed += 1
        log.info(f"{ref}: Analyse gespeichert")

    log.info(f"Fertig. Analysiert: {processed}")

if __name__ == "__main__":
    main()
