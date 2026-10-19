"""Normalize candidate profile sources for matching and search."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


_LANGUAGE_NAMES = {
    "deutsch": "de",
    "german": "de",
    "englisch": "en",
    "english": "en",
    "russisch": "ru",
    "russian": "ru",
    "ukrainisch": "uk",
    "ukrainian": "uk",
}
_SERVICE_FIELDS = {"_id", "created_at", "updated_at", "profile_hash", "created_by"}


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_clean_text(value)] if _clean_text(value) else []
    if not isinstance(value, (list, tuple)):
        return []
    return [text for item in value if (text := _clean_text(item))]


def _add_unique(target: list[str], values: Any) -> None:
    for value in _text_list(values):
        if value not in target:
            target.append(value)


def _add_skill(
    skills: list[dict[str, Any]],
    label: Any,
    source_path: str,
    status: str,
    evidence_quote: str | None = None,
    last_used: str | None = None,
) -> None:
    clean_label = _clean_text(label)
    if not clean_label:
        return
    skill = {
        "label": clean_label,
        "source_path": source_path,
        "evidence_quote": evidence_quote or clean_label,
        "status": status,
        "last_used": last_used,
    }
    if skill not in skills:
        skills.append(skill)


def _flatten_skill_values(value: Any, path: str, skills: list[dict[str, Any]], status: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _flatten_skill_values(item, f"{path}.{key}" if path else str(key), skills, status)
        return
    for item in _text_list(value):
        _add_skill(skills, item, path, status, evidence_quote=item)


def _language_code(name: Any) -> str:
    normalized = _clean_text(name).lower()
    return _LANGUAGE_NAMES.get(normalized, normalized)


def _add_languages(target: dict[str, dict[str, Any]], raw: Any, path: str) -> None:
    if isinstance(raw, Mapping):
        entries = raw.items()
    elif isinstance(raw, (list, tuple)):
        entries = ((item, item) for item in raw)
    else:
        return
    for name, description in entries:
        code = _language_code(name)
        text = _clean_text(description)
        if not code or not text:
            continue
        cefr_match = re.search(r"(?<![A-Za-z0-9])([ABC][12])(?![A-Za-z0-9])", text, re.IGNORECASE)
        target.setdefault(
            code,
            {
                "description": text,
                "cefr": cefr_match.group(1).upper() if cefr_match else None,
                "source_path": f"{path}.{name}",
            },
        )


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _projects(items: Any, path: str, *, legacy: bool = False) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        prefix = f"{path}[{index}]"
        role = _clean_text(item.get("role_title") or item.get("Position") or item.get("role"))
        period = _clean_text(item.get("timeframe") or item.get("Jahr") or item.get("period"))
        stack = item.get("tech_stack") or item.get("skills")
        if legacy:
            stack = item.get("skills", [])
        result.append(
            {
                "role": role,
                "period": period,
                "skills": _text_list(stack),
                "source_path": prefix,
            }
        )
    return result


def _extended_projects(items: Any, path: str) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """Return project records and skills with the exact project period as provenance."""
    projects: list[dict[str, Any]] = []
    project_skills: list[tuple[str, str, str]] = []
    if not isinstance(items, list):
        return projects, project_skills

    def visit(item: Mapping[str, Any], item_path: str, inherited_role: str, inherited_period: str) -> None:
        role = _clean_text(item.get("role_title")) or inherited_role
        period = _clean_text(item.get("timeframe")) or inherited_period
        stack = _text_list(item.get("tech_stack"))
        projects.append({"role": role, "period": period, "skills": stack, "source_path": item_path})
        project_skills.extend((skill, f"{item_path}.tech_stack", period) for skill in stack)
        subprojects = item.get("subprojects")
        if isinstance(subprojects, list):
            for index, child in enumerate(subprojects):
                if isinstance(child, Mapping):
                    visit(child, f"{item_path}.subprojects[{index}]", role, period)

    for index, item in enumerate(items):
        if isinstance(item, Mapping):
            visit(item, f"{path}[{index}]", "", "")
    return projects, project_skills


def _explicit_heavy_work_prohibition(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    text = raw.lower()
    return bool(
        re.search(r"\b(keine?|nicht|vermeide[nr]?|ohne)\b.{0,80}\b(schwere[nr]?\s+)?körperliche[nr]?\s+arbeit\b", text)
        or re.search(r"\b(not|no|avoid)\b.{0,80}\b(heavy\s+physical\s+work|heavy\s+manual\s+labor)\b", text)
    )


def normalize_profile(raw: dict, source: str = "profile") -> dict:
    """Map supported profile formats into a provenance-aware candidate view."""
    if not isinstance(raw, dict):
        raise TypeError("raw profile must be a dict")

    normalized_source = source or "profile"
    key = _clean_text(raw.get("key")) or ("default" if normalized_source == "profile" else normalized_source)
    roles: list[str] = []
    target_roles: list[str] = []
    skills: list[dict[str, Any]] = []
    languages: dict[str, dict[str, Any]] = {}
    constraints = dict(raw.get("constraints")) if isinstance(raw.get("constraints"), Mapping) else {}
    preferences = dict(raw.get("preferences")) if isinstance(raw.get("preferences"), Mapping) else {}
    licenses = list(raw.get("licenses", [])) if isinstance(raw.get("licenses"), list) else []
    education: list[Any] = []
    projects: list[dict[str, Any]] = []

    # Compact/demo and already-normalized profile format.
    _add_unique(roles, raw.get("roles"))
    _add_unique(target_roles, raw.get("target_roles"))
    _flatten_skill_values(raw.get("skills"), "skills", skills, "declared")
    _add_languages(languages, raw.get("languages"), "languages")
    if isinstance(raw.get("education"), list):
        education.extend(raw["education"])
    projects.extend(_projects(raw.get("projects"), "projects"))

    # German legacy CV format.
    legacy_experience = raw.get("Berufserfahrung")
    if isinstance(legacy_experience, list):
        for index, item in enumerate(legacy_experience):
            if isinstance(item, Mapping):
                _add_unique(roles, item.get("Position"))
        projects.extend(_projects(legacy_experience, "Berufserfahrung", legacy=True))
    legacy_skills = raw.get("TechnischeKenntnisse")
    if legacy_skills is not None:
        _flatten_skill_values(legacy_skills, "TechnischeKenntnisse", skills, "declared")
    _add_languages(languages, raw.get("Sprachkenntnisse"), "Sprachkenntnisse")
    legacy_education = raw.get("Ausbildung")
    if isinstance(legacy_education, list):
        education.extend(dict(item) for item in legacy_education if isinstance(item, Mapping))
    additional = raw.get("zusätzliche_informationen")
    if isinstance(additional, Mapping):
        if _explicit_heavy_work_prohibition(additional.get("körperliche_verfassung")):
            constraints["heavy_physical_work"] = False

    # Extended profile. Deliberately only consume matching-relevant fields.
    candidate = raw.get("candidate")
    if isinstance(candidate, Mapping):
        positioning = candidate.get("positioning_core")
        if isinstance(positioning, Mapping):
            _add_unique(target_roles, positioning.get("best_fit_roles"))
            roles_to_avoid = _text_list(positioning.get("roles_to_avoid"))
            if roles_to_avoid:
                preferences.setdefault("roles_to_avoid", roles_to_avoid)
        work_style = candidate.get("work_style")
        if isinstance(work_style, Mapping):
            preferred_environment = _text_list(work_style.get("preferred_environment"))
            if preferred_environment:
                preferences.setdefault("preferred_environment", preferred_environment)
    extended_skills = raw.get("skills")
    if isinstance(extended_skills, Mapping):
        _flatten_skill_values(extended_skills, "skills", skills, "declared")
    experience = raw.get("experience_projects")
    if isinstance(experience, list):
        extended_projects, evidence_skills = _extended_projects(experience, "experience_projects")
        projects.extend(extended_projects)
        for project in extended_projects:
            _add_unique(roles, project.get("role"))
        for skill, skill_path, period in evidence_skills:
            _add_skill(skills, skill, skill_path, "project_evidence", skill, period or None)
    if isinstance(candidate, Mapping):
        _add_languages(languages, candidate.get("languages"), "candidate.languages")

    # Keep useful explicit preference fields for compact formats.
    for field in ("on_call", "shift_work", "locations", "radius_km"):
        if field in raw and field not in constraints:
            constraints[field] = raw[field]
    payload = {
        "key": key,
        "roles": roles,
        "target_roles": target_roles,
        "skills": skills,
        "languages": languages,
        "licenses": licenses,
        "constraints": constraints,
        "preferences": preferences,
        "education": education,
        "projects": projects,
    }
    return {**payload, "profile_hash": _canonical_hash(payload)}


def profile_search_text(normalized: dict) -> str:
    """Build a short deterministic search string without private profile fields."""
    parts: list[str] = []
    roles = _text_list(normalized.get("roles"))
    target_roles = _text_list(normalized.get("target_roles"))
    skill_labels = [
        _clean_text(item.get("label"))
        for item in normalized.get("skills", [])
        if isinstance(item, Mapping) and _clean_text(item.get("label"))
    ]
    project_skills: list[str] = []
    for project in normalized.get("projects", []):
        if isinstance(project, Mapping):
            _add_unique(project_skills, project.get("skills"))
    if roles:
        parts.append(f"roles: {' '.join(roles)}")
    if target_roles:
        parts.append(f"target_roles: {' '.join(target_roles)}")
    if skill_labels:
        parts.append(f"skills: {' '.join(dict.fromkeys(skill_labels))}")
    if project_skills:
        parts.append(f"project skills: {' '.join(project_skills)}")
    languages = normalized.get("languages")
    if isinstance(languages, Mapping) and languages:
        parts.append(f"languages: {' '.join(str(code) for code in languages)}")
    return " ".join(parts)[:3000].strip()
