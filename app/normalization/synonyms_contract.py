from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.taxonomy_transform import CATEGORY_ALLOWED, FACET_ALLOWED, SKILL_CLASS_ALLOWED

PENDING_REQUIRED = {
    "run_id",
    "alias",
    "canonical",
    "domain",
    "category",
    "facet",
    "skill_class",
    "confidence",
    "source",
    "review_status",
    "created_at",
}
PENDING_SOURCE_ALLOWED = {"llm", "rule", "manual"}
PENDING_REVIEW_STATUS_ALLOWED = {"auto_suggested", "approved", "rejected"}
DICTIONARY_REQUIRED = {"version", "entries"}
ENTRY_REQUIRED = {"alias", "canonical", "domain", "category", "facet", "skill_class", "domain_scope", "facet_scope"}
DOMAIN_SCOPE_ALLOWED = {"specific", "cross_domain_candidate", "global_safe"}
FACET_SCOPE_ALLOWED = {"specific", "unstable"}


def _normalize_domain(value: Any) -> str:
    return str(value or "").strip()


def load_allowed_domains(domains_path: Path) -> set[str]:
    if not domains_path.exists():
        return {"sonstiges"}
    payload = json.loads(domains_path.read_text(encoding="utf-8"))
    domains = payload.get("domains", [])
    if not isinstance(domains, list):
        return {"sonstiges"}
    result = {_normalize_domain(item) for item in domains if _normalize_domain(item)}
    result.add("sonstiges")
    return result


def validate_pending_record(record: dict[str, Any], *, allowed_domains: set[str]) -> list[str]:
    errors: list[str] = []
    missing = sorted(PENDING_REQUIRED - set(record.keys()))
    if missing:
        errors.append(f"missing_required={','.join(missing)}")
        return errors

    if _normalize_domain(record.get("domain")) not in allowed_domains:
        errors.append(f"invalid_domain={record.get('domain')}")
    if record.get("category") not in CATEGORY_ALLOWED:
        errors.append(f"invalid_category={record.get('category')}")
    if record.get("facet") not in FACET_ALLOWED:
        errors.append(f"invalid_facet={record.get('facet')}")
    if record.get("skill_class") not in SKILL_CLASS_ALLOWED:
        errors.append(f"invalid_skill_class={record.get('skill_class')}")
    if record.get("source") not in PENDING_SOURCE_ALLOWED:
        errors.append(f"invalid_source={record.get('source')}")
    if record.get("review_status") not in PENDING_REVIEW_STATUS_ALLOWED:
        errors.append(f"invalid_review_status={record.get('review_status')}")

    confidence = record.get("confidence")
    try:
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            errors.append(f"invalid_confidence={confidence}")
    except (TypeError, ValueError):
        errors.append(f"invalid_confidence={confidence}")

    return errors


def validate_dictionary_payload(payload: dict[str, Any], *, allowed_domains: set[str]) -> list[str]:
    errors: list[str] = []
    missing = sorted(DICTIONARY_REQUIRED - set(payload.keys()))
    if missing:
        return [f"missing_required={','.join(missing)}"]

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ["entries_must_be_list"]

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry[{idx}]_must_be_object")
            continue
        entry_missing = sorted(ENTRY_REQUIRED - set(entry.keys()))
        if entry_missing:
            errors.append(f"entry[{idx}]_missing_required={','.join(entry_missing)}")
            continue
        domain = _normalize_domain(entry.get("domain"))
        if domain not in allowed_domains:
            errors.append(f"entry[{idx}]_invalid_domain={entry.get('domain')}")
        if entry.get("category") not in CATEGORY_ALLOWED:
            errors.append(f"entry[{idx}]_invalid_category={entry.get('category')}")
        if entry.get("facet") not in FACET_ALLOWED:
            errors.append(f"entry[{idx}]_invalid_facet={entry.get('facet')}")
        if entry.get("skill_class") not in SKILL_CLASS_ALLOWED:
            errors.append(f"entry[{idx}]_invalid_skill_class={entry.get('skill_class')}")
        if entry.get("source_refs_count") is not None:
            try:
                source_refs_count = int(entry.get("source_refs_count"))
                if source_refs_count < 0:
                    errors.append(f"entry[{idx}]_invalid_source_refs_count={entry.get('source_refs_count')}")
            except (TypeError, ValueError):
                errors.append(f"entry[{idx}]_invalid_source_refs_count={entry.get('source_refs_count')}")
        domain_scope = entry.get("domain_scope")
        if domain_scope is not None and domain_scope not in DOMAIN_SCOPE_ALLOWED:
            errors.append(f"entry[{idx}]_invalid_domain_scope={domain_scope}")
        facet_scope = entry.get("facet_scope")
        if facet_scope is not None and facet_scope not in FACET_SCOPE_ALLOWED:
            errors.append(f"entry[{idx}]_invalid_facet_scope={facet_scope}")

    return errors
