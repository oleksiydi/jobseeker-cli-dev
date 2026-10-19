#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.normalization.runtime import build_vacancy_normalization_context, resolve_canonical_term

from app.normalization.constants import CATEGORY_ALLOWED, FACET_ALLOWED, SKILL_CLASS_ALLOWED

MAX_CONTEXT_PHRASE_CHARS = 200
SURFACE_EVIDENCE_STOPWORDS = {
    "and",
    "der",
    "die",
    "das",
    "den",
    "des",
    "ein",
    "eine",
    "for",
    "mit",
    "oder",
    "und",
    "von",
    "zur",
}
FACET_KEY_TO_CANONICAL = {
    "skill requirement": "skill_requirement",
    "tool or platform": "tool_or_platform",
    "technology stack": "technology_stack",
    "task responsibility": "task_responsibility",
    "benefit": "benefit",
    "work condition": "work_condition",
    "language requirement": "language_requirement",
    "certification": "certification",
    "soft skill": "interpersonal_competency",
    "soft_skill": "interpersonal_competency",
    "interpersonal competency": "interpersonal_competency",
    "interpersonal_competency": "interpersonal_competency",
    "other": "other",
}
DEFAULT_DOMAIN_LIST = [
    "it_software_daten",
    "ingenieurwesen_technik",
    "industrie_produktion_fertigung",
    "handwerk_bau_ausbau",
    "logistik_transport_lager",
    "gesundheitswesen_pflege_medizin",
    "bildung_soziales_erziehung",
    "vertrieb_marketing_kommunikation",
    "finanzen_versicherung_recht",
    "verwaltung_oeffentlicher_dienst",
    "kundenservice_callcenter_buero",
    "einzelhandel_grosshandel",
    "gastgewerbe_tourismus_event",
    "reinigung_sicherheit_facility",
    "sonstiges",
]
DOMAIN_ALIASES = {
    "other": "sonstiges",
    "sonstige": "sonstiges",
    "it": "it_software_daten",
    "software": "it_software_daten",
    "data": "it_software_daten",
    "healthcare": "gesundheitswesen_pflege_medizin",
    "health care": "gesundheitswesen_pflege_medizin",
    "medizin": "gesundheitswesen_pflege_medizin",
    "pflege": "gesundheitswesen_pflege_medizin",
    "education": "bildung_soziales_erziehung",
    "soziales": "bildung_soziales_erziehung",
    "hospitality": "gastgewerbe_tourismus_event",
    "tourismus": "gastgewerbe_tourismus_event",
    "logistics": "logistik_transport_lager",
    "transport": "logistik_transport_lager",
    "manufacturing": "industrie_produktion_fertigung",
    "produktion": "industrie_produktion_fertigung",
    "engineering": "ingenieurwesen_technik",
    "technik": "ingenieurwesen_technik",
    "finance": "finanzen_versicherung_recht",
    "financial": "finanzen_versicherung_recht",
    "public sector": "verwaltung_oeffentlicher_dienst",
    "public-sector": "verwaltung_oeffentlicher_dienst",
    "oeffentlicher dienst": "verwaltung_oeffentlicher_dienst",
    "sales marketing": "vertrieb_marketing_kommunikation",
    "sales-marketing": "vertrieb_marketing_kommunikation",
    "vertrieb": "vertrieb_marketing_kommunikation",
}


@dataclass(frozen=True)
class ExtractedTerm:
    referenznummer: str
    term: str
    term_key: str
    canonical: str
    category: str
    facet: str
    domain_hint: str
    domain_confidence: float
    context_phrase: str
    skill_class: str
    vacancy_domain_top: str
    vacancy_domain_confidence: float


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_term_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


def normalize_surface_text(value: str) -> str:
    text = value.lower().replace("ß", "ss")
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return normalize_ws(text)


def surface_evidence_tokens(value: str) -> list[str]:
    return [
        token
        for token in normalize_surface_text(value).split()
        if len(token) >= 4 and token not in SURFACE_EVIDENCE_STOPWORDS
    ]


def has_surface_evidence(*, term: str, context_phrase: str) -> bool:
    term_surface = normalize_surface_text(term)
    context_surface = normalize_surface_text(context_phrase)
    if not term_surface or not context_surface:
        return False

    compact_term = term_surface.replace(" ", "")
    compact_context = context_surface.replace(" ", "")
    if compact_term and compact_term in compact_context:
        return True

    context_tokens = surface_evidence_tokens(context_phrase)
    if not context_tokens:
        return False

    for term_token in surface_evidence_tokens(term):
        for context_token in context_tokens:
            if term_token == context_token:
                return True
            # German compounds and inflected forms often keep the meaningful stem.
            if len(term_token) >= 5 and context_token.startswith(term_token[:5]):
                return True
            if len(context_token) >= 5 and term_token.startswith(context_token[:5]):
                return True
    return False


def normalize_domain(value: str) -> str:
    domain = normalize_term_key(value)
    domain = DOMAIN_ALIASES.get(domain, domain)
    domain = domain.replace(" ", "_")
    return domain if domain else "sonstiges"


def load_domains(path: Path) -> list[str]:
    if not path.exists():
        return DEFAULT_DOMAIN_LIST[:]
    payload = json.loads(path.read_text(encoding="utf-8"))
    domains = payload.get("domains", [])
    if not isinstance(domains, list):
        return DEFAULT_DOMAIN_LIST[:]
    normalized = [normalize_domain(str(item)) for item in domains if normalize_domain(str(item))]
    if not normalized:
        return DEFAULT_DOMAIN_LIST[:]
    dedup: list[str] = []
    seen: set[str] = set()
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def skill_class_for(*, category: str, facet: str) -> str:
    if category != "skills":
        skill_class = "not_applicable"
    elif facet == "interpersonal_competency":
        skill_class = "interpersonal"
    elif facet in {"benefit", "work_condition"}:
        skill_class = "non_skill_conditions"
    elif facet in {"skill_requirement", "task_responsibility", "language_requirement", "certification", "other"}:
        skill_class = "professional"
    else:
        skill_class = "professional"
    if skill_class not in SKILL_CLASS_ALLOWED:
        return "not_applicable"
    return skill_class


def sanitize_term_record(
    *,
    raw: dict[str, Any],
    referenznummer: str,
    vacancy_domain_top: str,
    vacancy_domain_confidence: float,
) -> ExtractedTerm | None:
    term = normalize_ws(str(raw.get("term") or ""))
    category = normalize_term_key(str(raw.get("category") or ""))
    facet_key = normalize_term_key(str(raw.get("facet") or ""))
    domain_hint = normalize_domain(str(raw.get("domain_hint") or "other"))
    context_phrase = normalize_ws(str(raw.get("context_phrase") or ""))
    domain_confidence = safe_float(raw.get("domain_confidence"), default=0.0)

    if not term or category not in CATEGORY_ALLOWED:
        return None
    if not facet_key:
        facet = {"skills": "skill_requirement", "tools": "tool_or_platform", "stack": "technology_stack"}.get(
            category, "other"
        )
    else:
        facet = FACET_KEY_TO_CANONICAL.get(facet_key, "other")
    if facet not in FACET_ALLOWED:
        facet = "other"
    if not context_phrase:
        return None
    if len(context_phrase) > MAX_CONTEXT_PHRASE_CHARS:
        context_phrase = context_phrase[:MAX_CONTEXT_PHRASE_CHARS].rstrip()
    if not has_surface_evidence(term=term, context_phrase=context_phrase):
        return None

    if facet == "technology_stack":
        category = "stack"
    elif facet == "tool_or_platform":
        category = "tools"
    elif facet in {
        "skill_requirement",
        "task_responsibility",
        "benefit",
        "work_condition",
        "language_requirement",
        "certification",
        "interpersonal_competency",
        "other",
    }:
        category = "skills"

    skill_class = skill_class_for(category=category, facet=facet)
    return ExtractedTerm(
        referenznummer=referenznummer,
        term=term,
        term_key=normalize_term_key(term),
        canonical=term,
        category=category,
        facet=facet,
        domain_hint=domain_hint,
        domain_confidence=domain_confidence,
        context_phrase=context_phrase,
        skill_class=skill_class,
        vacancy_domain_top=vacancy_domain_top,
        vacancy_domain_confidence=vacancy_domain_confidence,
    )


def apply_runtime_taxonomy_to_row(row: ExtractedTerm, taxonomy: dict[str, Any]) -> ExtractedTerm:
    context = build_vacancy_normalization_context(
        vacancy_domain_top=row.vacancy_domain_top,
        vacancy_domain_confidence=row.vacancy_domain_confidence,
        facet_hint=row.facet,
    )
    resolution = resolve_canonical_term(
        term=row.canonical,
        source_category=row.category,
        taxonomy=taxonomy,
        context=context,
    )
    canonical = resolution.canonical
    category = resolution.category
    skill_class = skill_class_for(category=category, facet=row.facet)
    return ExtractedTerm(
        referenznummer=row.referenznummer,
        term=row.term,
        term_key=normalize_term_key(canonical),
        canonical=canonical,
        category=category,
        facet=row.facet,
        domain_hint=row.domain_hint,
        domain_confidence=row.domain_confidence,
        context_phrase=row.context_phrase,
        skill_class=skill_class,
        vacancy_domain_top=row.vacancy_domain_top,
        vacancy_domain_confidence=row.vacancy_domain_confidence,
    )


def extract_vacancy_domain_top(vacancy_domains: Any) -> tuple[str, float]:
    if not isinstance(vacancy_domains, list) or not vacancy_domains:
        return "sonstiges", 0.0
    best_domain = "sonstiges"
    best_conf = 0.0
    for item in vacancy_domains:
        if not isinstance(item, dict):
            continue
        domain = normalize_domain(str(item.get("domain") or "other"))
        conf = safe_float(item.get("confidence"), default=0.0)
        if conf > best_conf:
            best_domain = domain
            best_conf = conf
    return best_domain, best_conf


def build_aggregate(
    *,
    extracted_rows: list[ExtractedTerm],
    provider: str,
    model: str,
    scanned_vacancies: int,
    processed_vacancies: int,
    failed_vacancies: int,
) -> dict[str, Any]:
    bucket: dict[str, dict[str, Any]] = {}
    for row in extracted_rows:
        item = bucket.setdefault(
            row.term_key,
            {
                "canonical_counter": Counter(),
                "category_counter": Counter(),
                "facet_counter": Counter(),
                "domain_counter": Counter(),
                "domain_conf_sum": defaultdict(float),
                "skill_class_counter": Counter(),
                "source_refs": set(),
                "phrases": set(),
            },
        )
        item["canonical_counter"][row.canonical] += 1
        item["category_counter"][row.category] += 1
        item["facet_counter"][row.facet] += 1
        item["domain_counter"][row.domain_hint] += 1
        item["domain_conf_sum"][row.domain_hint] += row.domain_confidence
        item["skill_class_counter"][row.skill_class] += 1
        item["source_refs"].add(row.referenznummer)
        item["phrases"].add(row.context_phrase)

    terms_payload: list[dict[str, Any]] = []
    for term_key, item in sorted(bucket.items(), key=lambda kv: kv[0]):
        canonical = item["canonical_counter"].most_common(1)[0][0]
        primary_category = item["category_counter"].most_common(1)[0][0]
        primary_facet = item["facet_counter"].most_common(1)[0][0]
        primary_skill_class = item["skill_class_counter"].most_common(1)[0][0]
        domain_votes: dict[str, Any] = {}
        for domain, count in item["domain_counter"].items():
            conf_sum = float(item["domain_conf_sum"][domain])
            domain_votes[domain] = {
                "count": int(count),
                "avg_confidence": round(conf_sum / max(count, 1), 6),
            }
        terms_payload.append(
            {
                "term_key": term_key,
                "canonical": canonical,
                "primary_category": primary_category,
                "primary_facet": primary_facet,
                "primary_skill_class": primary_skill_class,
                "category_votes": dict(item["category_counter"]),
                "facet_votes": dict(item["facet_counter"]),
                "skill_class_votes": dict(item["skill_class_counter"]),
                "domain_votes": domain_votes,
                "source_refs_count": len(item["source_refs"]),
                "sample_phrases": sorted(item["phrases"])[:10],
            }
        )

    return {
        "version": 1,
        "generated_at": now_utc_iso(),
        "provider": provider,
        "model": model,
        "input": {
            "scanned_vacancies": scanned_vacancies,
            "processed_vacancies": processed_vacancies,
            "failed_vacancies": failed_vacancies,
            "term_rows": len(extracted_rows),
        },
        "terms": terms_payload,
    }
