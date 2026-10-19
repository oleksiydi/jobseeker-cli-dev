"""Conservative checks for explicit job constraints against a normalized profile."""

from __future__ import annotations

import re
from typing import Any


_CLAUSE_SPLIT = re.compile(r"[;.!?\n\r•●▪◦]+")
_REQUIRED = re.compile(r"\b(?:erforderlich|zwingend|vorausgesetzt|notwendig|muss|müssen)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(?:nicht\s+(?:zwingend\s+)?(?:erforderlich|vorausgesetzt|notwendig)|"
    r"keine\s+pflicht|keine\b.{0,100}(?:kenntnisse|erfahrung)\b)",
    re.I,
)
_OPTIONAL = re.compile(r"\b(?:optional|wünschenswert|idealerweise|von\s+vorteil|nice.to.have)\b", re.I)
_HEADERS = re.compile(r"\b(?:sie\s+bringen\s+mit|ihr\s+profil)\b", re.I)
_MODALITY_PATTERNS = (
    ("negated", _NEGATIVE),
    ("preferred", _OPTIONAL),
    ("required", _REQUIRED),
)
_MODALITY_PRIORITY = {"negated": 0, "preferred": 1, "required": 2}
_CEFR_RANK = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
_LANGUAGE = re.compile(r"\b(deutsch|englisch)\w*\b", re.I)
_CEFR = re.compile(r"\b(A1|A2|B1|B2|C1|C2)\b", re.I)


def _job_sources(job: dict[str, Any]) -> list[tuple[str, str]]:
    sources = []
    for field in ("title", "role", "description", "location", "schedule"):
        value = job.get(field)
        if isinstance(value, str) and value.strip():
            sources.append((field, value))
    return sources


def _evidence(source: str, text: str, start: int, end: int) -> tuple[str, list[int]]:
    left = max((m.end() for m in _CLAUSE_SPLIT.finditer(text) if m.end() <= start), default=0)
    right = next((m.start() for m in _CLAUSE_SPLIT.finditer(text) if m.start() >= end), len(text))
    return text[left:right].strip(), [start, end]


def _modality_at(text: str, start: int, end: int) -> str:
    """Honor a profile heading for adjacent bullets, stopping at section changes."""
    left = max((m.end() for m in _CLAUSE_SPLIT.finditer(text) if m.end() <= start), default=0)
    right = next((m.start() for m in _CLAUSE_SPLIT.finditer(text) if m.start() >= end), len(text))
    local = text[left:right]
    local_start = start - left
    local_end = end - left
    cues = []
    for modality, pattern in _MODALITY_PATTERNS:
        for match in pattern.finditer(local):
            if match.end() < local_start:
                distance = local_start - match.end()
            elif local_end < match.start():
                distance = match.start() - local_end
            else:
                distance = 0
            cues.append((distance, _MODALITY_PRIORITY[modality], modality))
    if cues:
        return min(cues)[2]
    if _HEADERS.search(local):
        return "required"
    lines = text.splitlines(keepends=True)
    line_start = 0
    index = 0
    for index, line in enumerate(lines):
        if line_start <= start < line_start + len(line):
            break
        line_start += len(line)
    for previous in reversed(lines[:index]):
        stripped = previous.strip("\r\n •●▪◦\t")
        if not stripped:
            continue
        if re.match(r"^\s*(?:aufgaben|wir\s+bieten|benefits?)\s*:?\s*$", stripped, re.I):
            return "unknown"
        if _HEADERS.search(stripped):
            return "required"
        if re.match(r"^\s*(?:[-*•●▪◦]|\d+[.)])\s+", previous):
            continue
        return "unknown"
    return "unknown"


def _record(
    kind: str,
    profile_value: Any,
    status: str,
    quote: str,
    source: str,
    span: list[int],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "required": True,
        "profile_value": profile_value,
        "status": status,
        "quote": quote,
        "source": source,
        "text_span": span,
    }


def _explicit_matches(pattern: re.Pattern[str], sources: list[tuple[str, str]]):
    for source, text in sources:
        for match in pattern.finditer(text):
            quote, span = _evidence(source, text, match.start(), match.end())
            modality = _modality_at(text, match.start(), match.end())
            if modality == "required":
                yield source, text, match, quote, span


def _profile_language(profile: dict[str, Any], language: str) -> tuple[Any, str | None]:
    key = "de" if language == "de" else "en"
    value = (profile.get("languages") or {}).get(key)
    if not isinstance(value, dict):
        return None, None
    cefr = value.get("cefr")
    return cefr, value.get("description")


def evaluate_constraints(job: dict, profile: dict) -> list[dict]:
    """Return auditable decisions for explicitly hard constraints in a job.

    Missing profile facts remain unknown. Each quote is taken from the job and
    each span indexes that quote's original source field.
    """
    sources = _job_sources(job)
    results: list[dict[str, Any]] = []

    for source, text in sources:
        for language_match in _LANGUAGE.finditer(text):
            clause_start = max((m.end() for m in _CLAUSE_SPLIT.finditer(text) if m.end() <= language_match.start()), default=0)
            clause_end = next((m.start() for m in _CLAUSE_SPLIT.finditer(text) if m.start() >= language_match.end()), len(text))
            clause = text[clause_start:clause_end]
            if _modality_at(text, language_match.start(), language_match.end()) != "required":
                continue
            local_start = language_match.start() - clause_start
            local_end = language_match.end() - clause_start
            cefr_match = min(_CEFR.finditer(clause), key=lambda m:
                             max(0, local_start - m.end(), m.start() - local_end), default=None)
            language = "de" if language_match.group(1).lower().startswith("deutsch") else "en"
            profile_cefr, profile_description = _profile_language(profile, language)
            span = [language_match.start(), language_match.end()]
            if cefr_match:
                span = [min(span[0], clause_start + cefr_match.start()),
                        max(span[1], clause_start + cefr_match.end())]
            status = "unknown"
            if cefr_match and profile_cefr:
                status = "met" if _CEFR_RANK.get(str(profile_cefr).upper(), 0) >= _CEFR_RANK[cefr_match.group(1).upper()] else "conflict"
            results.append(_record("language", {"cefr": profile_cefr, "description": profile_description}, status,
                                   text[slice(*span)], source, span))

    license_patterns = [
        ("license:B", re.compile(r"\bF[uü]hrerschein\s*(?:Klasse\s*)?B\b", re.I), ("B", "Klasse B", "Führerschein B")),
        ("license:CE", re.compile(r"\bF[uü]hrerschein\s*(?:Klasse\s*)?CE\b", re.I), ("CE", "Klasse CE", "Führerschein CE")),
        ("license:C", re.compile(r"\bF[uü]hrerschein\s*(?:Klasse\s*)?C\b", re.I), ("C", "Klasse C", "Führerschein C")),
        ("license:forklift", re.compile(r"\bStaplerschein\b", re.I), ("Staplerschein", "Gabelstaplerschein")),
    ]
    licenses = profile.get("licenses")
    for kind, pattern, aliases in license_patterns:
        for source, text, match, quote, span in _explicit_matches(pattern, sources):
            normalized = {str(value).casefold() for value in (licenses if isinstance(licenses, list) else [])}
            if any(alias.casefold() in normalized for alias in aliases):
                status = "met"
            elif licenses == []:
                status = "conflict"
            else:
                status = "unknown"
            results.append(_record(kind, licenses, status, match.group(0), source, [match.start(), match.end()]))

    work_patterns = [
        ("on_call", re.compile(r"\b(?:Bereitschaftsdienst|Rufbereitschaft)\b", re.I)),
        ("shift_work", re.compile(r"\b(?:Nachtschicht|Schichtarbeit|Schichtdienst)\b", re.I)),
        ("heavy_physical_work", re.compile(r"\b(?:schwere\s+körperliche\s+Arbeit|schweres\s+Heben|körperlich\s+schwere\s+Arbeit)\b", re.I)),
    ]
    constraints = profile.get("constraints") or {}
    for kind, pattern in work_patterns:
        for source, text, match, quote, span in _explicit_matches(pattern, sources):
            profile_value = constraints.get(kind)
            status = "unknown" if profile_value is None else ("met" if profile_value is True else "conflict")
            results.append(_record(kind, profile_value, status, match.group(0), source, [match.start(), match.end()]))

    # Certificates, education, and experience need an exact profile statement;
    # category-level similarity is not enough to claim a match.
    qualification_patterns = [
        ("certificate", re.compile(r"\b(?:Zertifikat|Zertifizierung|IHK\s*-?\s*Abschluss)\b", re.I)),
        ("education", re.compile(r"\b(?:Ausbildung|Studium|Hochschulabschluss|Berufsausbildung)\b", re.I)),
        ("experience", re.compile(r"\b(?:\d+\s+Jahre?\s+Berufserfahrung|Berufserfahrung)\b", re.I)),
    ]
    profile_text = " ".join(str(v) for v in (profile.get("description"), profile.get("summary"), profile.get("education"), profile.get("experience"), profile.get("certificates")) if v)
    for kind, pattern in qualification_patterns:
        for source, text, match, quote, span in _explicit_matches(pattern, sources):
            # Generic category words do not identify a concrete degree,
            # certificate, or duration of experience. Keep these unresolved.
            results.append(_record(kind, profile_text or None, "unknown", match.group(0), source, [match.start(), match.end()]))

    # City matching is only hard when the profile gives explicit places and no
    # radius is involved. Radius without geocoding cannot prove a conflict.
    locations = constraints.get("locations")
    radius = constraints.get("radius_km")
    job_location = job.get("location") if isinstance(job.get("location"), str) else ""
    if job_location and isinstance(locations, list):
        city_span = None
        if "|" in job_location:
            parts = job_location.split("|")
            if len(parts) >= 3 and parts[2].strip():
                city = parts[2].strip()
                start = job_location.find(parts[2]) + (len(parts[2]) - len(parts[2].lstrip()))
                city_span = [start, start + len(city)]
        else:
            city_match = re.search(r"\b([A-ZÄÖÜ][\wÄÖÜäöüß-]+(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß-]+)*)\b", job_location)
            if city_match:
                city = city_match.group(1)
                city_span = [city_match.start(1), city_match.end(1)]
        if city_span:
            if radius is not None:
                status = "unknown"
            else:
                status = "met" if any(city.casefold() == str(place).casefold() for place in locations) else "conflict"
            results.append(_record("location", locations, status, city, "location", city_span))
    return results
