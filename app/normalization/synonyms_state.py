from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.results import UpdateResult

from app.normalization.constants import CATEGORY_ALLOWED, FACET_ALLOWED, SKILL_CLASS_ALLOWED

from app.normalization.runtime import normalize_term_key

PROCESSING_STATUS_ALLOWED = {
    "new",
    "accepted",
    "rejected",
    "needs_adjudication",
    "in_adjudication",
    "adjudicated_accepted",
    "adjudicated_rejected",
    "needs_escalated_adjudication",
    "escalated_in_progress",
    "escalated_accepted",
    "escalated_rejected",
    "published",
    "unresolved",
}
ADJUDICATION_MODE_ALLOWED = {"machine", "human", "hybrid", "disabled"}
CANDIDATE_HASH_FIELDS = (
    "alias",
    "canonical",
    "domain",
    "category",
    "facet",
    "skill_class",
    "source",
    "source_refs_count",
    "sample_phrases",
    "confidence",
)
PROPOSED_ADJUDICATION_FIELDS = ("status", "decision", "confidence", "provider", "model")
REVIEW_STATUSES = {"needs_adjudication", "needs_escalated_adjudication", "unresolved"}


@dataclass(frozen=True)
class SynonymStateCandidate:
    alias: str
    canonical: str
    domain: str
    category: str
    facet: str
    skill_class: str
    confidence: float
    source: str
    run_id: str
    source_refs_count: int = 1
    sample_phrases: tuple[str, ...] = ()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _normalize_surface(value: Any) -> str:
    return str(value or "").strip()


def _candidate_hash(document: dict[str, Any]) -> str:
    """Hash only the candidate evidence and builder proposal, not mutable run state."""
    adjudication = document.get("adjudication") or {}
    stages = ("primary", "escalated")
    if (adjudication.get("pro") or {}).get("status") == "done":
        stages += ("pro",)
    hash_payload = {field: document.get(field) for field in CANDIDATE_HASH_FIELDS}
    hash_payload["proposed_status"] = document.get("processing_status")
    if document.get('dictionary_reference') is not None:
        hash_payload['dictionary_reference'] = document['dictionary_reference']
    hash_payload["requires_review"] = document.get(
        "requires_review", document.get("processing_status") in REVIEW_STATUSES
    )
    hash_payload["proposed_adjudication"] = {
        stage: {
            field: (adjudication.get(stage) or {}).get(field)
            for field in PROPOSED_ADJUDICATION_FIELDS
        }
        for stage in stages
    }
    encoded = json.dumps(
        hash_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_candidate(candidate: SynonymStateCandidate) -> None:
    if not _normalize_surface(candidate.alias):
        raise ValueError("candidate.alias must be non-empty")
    if not _normalize_surface(candidate.canonical):
        raise ValueError("candidate.canonical must be non-empty")
    if not _normalize_surface(candidate.domain):
        raise ValueError("candidate.domain must be non-empty")
    if candidate.category not in CATEGORY_ALLOWED:
        raise ValueError(f"candidate.category must be one of {sorted(CATEGORY_ALLOWED)}")
    if candidate.facet not in FACET_ALLOWED:
        raise ValueError(f"candidate.facet must be one of {sorted(FACET_ALLOWED)}")
    if candidate.skill_class not in SKILL_CLASS_ALLOWED:
        raise ValueError(f"candidate.skill_class must be one of {sorted(SKILL_CLASS_ALLOWED)}")


def ensure_synonyms_state_indexes(state_collection: Collection, audit_collection: Collection) -> None:
    state_collection.create_index(
        [
            ("alias_key", ASCENDING),
            ("canonical", ASCENDING),
            ("domain", ASCENDING),
            ("category", ASCENDING),
            ("facet", ASCENDING),
        ],
        unique=True,
        name="uniq_alias_canonical_domain_category_facet",
    )
    state_collection.create_index([("processing_status", ASCENDING), ("updated_at", ASCENDING)], name="status_updated_at")
    state_collection.create_index(
        [("processing_status", ASCENDING), ("lease_expires_at", ASCENDING)],
        name="status_lease_expires_at",
    )
    state_collection.create_index([("run_id", ASCENDING)], name="run_id")
    state_collection.create_index([("canonical", ASCENDING), ("domain", ASCENDING)], name="canonical_domain")

    audit_collection.create_index([("state_id", ASCENDING), ("created_at", ASCENDING)], name="state_id_created_at")
    audit_collection.create_index([("event_type", ASCENDING), ("created_at", ASCENDING)], name="event_type_created_at")
    audit_collection.create_index([("run_id", ASCENDING)], name="run_id")


def build_synonym_state_document(
    candidate: SynonymStateCandidate,
    *,
    adjudication_mode: str = "machine",
    human_review_enabled: bool = False,
    stronger_model_escalation_enabled: bool = True,
    primary_confidence_threshold: float = 0.75,
    escalation_confidence_threshold: float = 0.8,
) -> dict[str, Any]:
    _validate_candidate(candidate)
    if adjudication_mode not in ADJUDICATION_MODE_ALLOWED:
        raise ValueError(f"adjudication_mode must be one of {sorted(ADJUDICATION_MODE_ALLOWED)}")

    now = _now_utc_iso()
    alias = _normalize_surface(candidate.alias)
    canonical = _normalize_surface(candidate.canonical)
    domain = _normalize_surface(candidate.domain)

    return {
        "alias": alias,
        "alias_key": normalize_term_key(alias),
        "canonical": canonical,
        "domain": domain,
        "category": candidate.category,
        "facet": candidate.facet,
        "skill_class": candidate.skill_class,
        "confidence": _safe_float(candidate.confidence),
        "source": _normalize_surface(candidate.source) or "rule",
        "source_refs_count": _safe_int(candidate.source_refs_count, default=0),
        "sample_phrases": [item for item in candidate.sample_phrases if _normalize_surface(item)],
        "processing_status": "new",
        "decision_source": "candidate_builder",
        "run_id": candidate.run_id,
        "lease_owner": None,
        "lease_expires_at": None,
        "retry_count": 0,
        "pro_retry_count": 0,
        "last_error": None,
        "adjudication_policy": {
            "mode": adjudication_mode,
            "human_review_enabled": bool(human_review_enabled),
            "stronger_model_escalation_enabled": bool(stronger_model_escalation_enabled),
            "primary_confidence_threshold": _safe_float(primary_confidence_threshold, default=0.75),
            "escalation_confidence_threshold": _safe_float(escalation_confidence_threshold, default=0.8),
        },
        "adjudication": {
            "primary": {"status": "pending", "decision": None, "confidence": None, "provider": None, "model": None},
            "escalated": {
                "status": "pending",
                "decision": None,
                "confidence": None,
                "provider": None,
                "model": None,
            },
            "pro": {
                "status": "pending", "decision": None, "confidence": None,
                "provider": None, "model": None,
            },
        },
        "created_at": now,
        "updated_at": now,
    }


def upsert_synonym_state(state_collection: Collection, document: dict[str, Any]) -> Any:
    required = {"alias_key", "canonical", "domain", "category", "facet"}
    missing = sorted(required - set(document.keys()))
    if missing:
        raise ValueError(f"document missing required key fields: {','.join(missing)}")

    key_filter = {
        "alias_key": document["alias_key"],
        "canonical": document["canonical"],
        "domain": document["domain"],
        "category": document["category"],
        "facet": document["facet"],
    }
    payload = dict(document)
    candidate_hash = _candidate_hash(payload)
    payload["candidate_hash"] = candidate_hash

    existing = state_collection.find_one(key_filter)
    if existing is None:
        return state_collection.update_one(
            key_filter,
            {"$setOnInsert": payload},
            upsert=True,
        )

    if "candidate_hash" not in existing:
        stored_hash = _candidate_hash(existing)
        expected_hash = {"candidate_hash": {"$exists": False}}
    else:
        stored_hash = existing["candidate_hash"]
        expected_hash = {"candidate_hash": stored_hash}

    if stored_hash == candidate_hash:
        return UpdateResult({"n": 1, "nModified": 0}, acknowledged=True)

    payload["created_at"] = existing.get("created_at") or payload.get("created_at") or _now_utc_iso()
    payload["updated_at"] = _now_utc_iso()
    return state_collection.update_one(
        {**key_filter, **expected_hash},
        {"$set": payload},
        upsert=False,
    )


def build_synonyms_audit_event(
    *,
    state_id: Any,
    event_type: str,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _normalize_surface(event_type):
        raise ValueError("event_type must be non-empty")
    return {
        "state_id": state_id,
        "event_type": event_type,
        "run_id": run_id,
        "payload": payload or {},
        "created_at": _now_utc_iso(),
    }


def write_synonyms_audit_event(audit_collection: Collection, event: dict[str, Any]) -> Any:
    required = {"state_id", "event_type", "run_id", "created_at"}
    missing = sorted(required - set(event.keys()))
    if missing:
        raise ValueError(f"audit event missing required fields: {','.join(missing)}")
    return audit_collection.insert_one(event)
