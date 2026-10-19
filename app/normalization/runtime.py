from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

UNKNOWN_DOMAIN = "unknown"
UNKNOWN_CONTEXT_SOURCE = "unknown"
DEFAULT_CONTEXT_CONFIDENCE_THRESHOLD = 0.7


def normalize_term_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


@dataclass(frozen=True)
class NormalizationContext:
    """Document-level context used by future domain-aware synonym resolution."""

    domain: str = UNKNOWN_DOMAIN
    domain_confidence: float = 0.0
    facet_hint: str | None = None
    context_source: str = UNKNOWN_CONTEXT_SOURCE

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "NormalizationContext":
        if not value:
            return cls()
        confidence = value.get("domain_confidence", 0.0)
        try:
            parsed_confidence = float(confidence)
        except (TypeError, ValueError):
            parsed_confidence = 0.0
        return cls(
            domain=str(value.get("domain") or UNKNOWN_DOMAIN).strip() or UNKNOWN_DOMAIN,
            domain_confidence=max(0.0, min(1.0, parsed_confidence)),
            facet_hint=str(value["facet_hint"]).strip() if value.get("facet_hint") else None,
            context_source=str(value.get("context_source") or UNKNOWN_CONTEXT_SOURCE).strip() or UNKNOWN_CONTEXT_SOURCE,
        )


@dataclass(frozen=True)
class NormalizationResolution:
    """Structured result for deterministic runtime normalization."""

    input: str
    alias_key: str
    canonical: str
    category: str
    domain: str
    facet: str | None
    resolution_status: str
    resolution_source: str
    domain_scope: str = "unknown"
    facet_scope: str = "unknown"
    resolution_confidence: float = 0.0


def _safe_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _known_categories(taxonomy: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for category, values in taxonomy.get("categories", {}).items():
        if isinstance(values, set):
            result[str(category)] = values
        elif isinstance(values, list | tuple):
            result[str(category)] = {str(item) for item in values}
        else:
            result[str(category)] = set()
    return result


def _category_for_canonical(canonical: str, source_category: str, taxonomy: dict[str, Any]) -> str:
    categories = _known_categories(taxonomy)
    for category in ("tools", "stack", "skills"):
        if canonical in categories.get(category, set()):
            return category
    return source_category


def _ambiguous_aliases(taxonomy: dict[str, Any]) -> set[str]:
    raw = taxonomy.get("ambiguous_aliases", set())
    if isinstance(raw, set):
        return {normalize_term_key(str(item)) for item in raw}
    if isinstance(raw, list | tuple):
        return {normalize_term_key(str(item)) for item in raw}
    return set()


def _exact_domain_facet_match(
    *,
    alias_key: str,
    context: NormalizationContext,
    taxonomy: dict[str, Any],
) -> str:
    if context.domain == UNKNOWN_DOMAIN or not context.facet_hint:
        return ""
    by_domain_facet = taxonomy.get("aliases_by_domain_facet", {})
    alias_domains = by_domain_facet.get(alias_key, {})
    if not isinstance(alias_domains, dict):
        return ""
    facet_map = alias_domains.get(context.domain, {})
    if not isinstance(facet_map, dict):
        return ""
    value = facet_map.get(context.facet_hint, "")
    return str(value).strip()


def _domain_match(
    *,
    alias_key: str,
    context: NormalizationContext,
    taxonomy: dict[str, Any],
) -> str:
    if context.domain == UNKNOWN_DOMAIN:
        return ""
    by_domain = taxonomy.get("aliases_by_domain", {})
    alias_domains = by_domain.get(alias_key, {})
    if not isinstance(alias_domains, dict):
        return ""
    value = alias_domains.get(context.domain, "")
    return str(value).strip()


def _entry_scope(
    *,
    alias_key: str,
    canonical: str,
    domain: str | None,
    facet: str | None,
    taxonomy: dict[str, Any],
) -> tuple[str, str]:
    canonical_key = normalize_term_key(canonical)
    for item in taxonomy.get("entries", []):
        if not isinstance(item, dict):
            continue
        if normalize_term_key(str(item.get("alias") or "")) != alias_key:
            continue
        if normalize_term_key(str(item.get("canonical") or "")) != canonical_key:
            continue
        item_domain = str(item.get("domain") or "").strip()
        item_facet = str(item.get("facet") or "").strip()
        if domain and item_domain and item_domain != domain:
            continue
        if facet and item_facet and item_facet != facet:
            continue
        domain_scope = str(item.get("domain_scope") or "specific").strip() or "specific"
        facet_scope = str(item.get("facet_scope") or "specific").strip() or "specific"
        return domain_scope, facet_scope
    return "unknown", "unknown"


def build_vacancy_normalization_context(
    *,
    vacancy_domain_top: str,
    vacancy_domain_confidence: float,
    facet_hint: str | None = None,
    min_confidence: float = DEFAULT_CONTEXT_CONFIDENCE_THRESHOLD,
) -> NormalizationContext:
    confidence = _safe_confidence(vacancy_domain_confidence)
    if confidence < min_confidence:
        return NormalizationContext(
            domain=UNKNOWN_DOMAIN,
            domain_confidence=confidence,
            facet_hint=facet_hint.strip() if facet_hint else None,
            context_source="vacancy",
        )
    domain = str(vacancy_domain_top or UNKNOWN_DOMAIN).strip() or UNKNOWN_DOMAIN
    return NormalizationContext(
        domain=domain,
        domain_confidence=confidence,
        facet_hint=facet_hint.strip() if facet_hint else None,
        context_source="vacancy",
    )


def build_profile_normalization_context(
    profile: dict[str, Any],
    *,
    min_confidence: float = DEFAULT_CONTEXT_CONFIDENCE_THRESHOLD,
) -> NormalizationContext:
    explicit_context = profile.get("normalization_context")
    if isinstance(explicit_context, dict):
        return NormalizationContext.from_mapping(explicit_context)

    domain = str(profile.get("target_domain") or profile.get("domain") or UNKNOWN_DOMAIN).strip() or UNKNOWN_DOMAIN
    confidence = _safe_confidence(
        profile.get("target_domain_confidence", profile.get("domain_confidence", 0.0))
    )
    facet_hint = str(profile.get("facet_hint") or "").strip() or None
    if confidence < min_confidence or domain == UNKNOWN_DOMAIN:
        return NormalizationContext(
            domain=UNKNOWN_DOMAIN,
            domain_confidence=confidence,
            facet_hint=facet_hint,
            context_source="profile",
        )
    return NormalizationContext(
        domain=domain,
        domain_confidence=confidence,
        facet_hint=facet_hint,
        context_source="profile",
    )


def resolve_canonical_term(
    *,
    term: str,
    source_category: str,
    taxonomy: dict[str, Any],
    context: NormalizationContext | dict[str, Any] | None = None,
) -> NormalizationResolution:
    normalized_context = context if isinstance(context, NormalizationContext) else NormalizationContext.from_mapping(context)
    aliases: dict[str, str] = taxonomy.get("aliases_global_safe", taxonomy.get("aliases", {}))
    term_key = normalize_term_key(term)

    canonical = _exact_domain_facet_match(alias_key=term_key, context=normalized_context, taxonomy=taxonomy)
    if canonical:
        category = _category_for_canonical(canonical, source_category, taxonomy)
        domain_scope, facet_scope = _entry_scope(
            alias_key=term_key,
            canonical=canonical,
            domain=normalized_context.domain,
            facet=normalized_context.facet_hint,
            taxonomy=taxonomy,
        )
        return NormalizationResolution(
            input=term,
            alias_key=term_key,
            canonical=canonical,
            category=category,
            domain=normalized_context.domain,
            facet=normalized_context.facet_hint,
            resolution_status="resolved",
            resolution_source="domain_facet",
            domain_scope=domain_scope,
            facet_scope=facet_scope,
            resolution_confidence=0.95 if facet_scope != "unstable" else 0.65,
        )

    canonical = _domain_match(alias_key=term_key, context=normalized_context, taxonomy=taxonomy)
    if canonical:
        category = _category_for_canonical(canonical, source_category, taxonomy)
        domain_scope, facet_scope = _entry_scope(
            alias_key=term_key,
            canonical=canonical,
            domain=normalized_context.domain,
            facet=None,
            taxonomy=taxonomy,
        )
        return NormalizationResolution(
            input=term,
            alias_key=term_key,
            canonical=canonical,
            category=category,
            domain=normalized_context.domain,
            facet=normalized_context.facet_hint,
            resolution_status="resolved",
            resolution_source="domain",
            domain_scope=domain_scope,
            facet_scope=facet_scope,
            resolution_confidence=0.85 if facet_scope != "unstable" else 0.60,
        )

    if term_key in _ambiguous_aliases(taxonomy) and term_key not in aliases:
        return NormalizationResolution(
            input=term,
            alias_key=term_key,
            canonical=term,
            category=source_category,
            domain=normalized_context.domain,
            facet=normalized_context.facet_hint,
            resolution_status="unresolved",
            resolution_source="ambiguous_without_context",
            domain_scope="unknown",
            facet_scope="unknown",
            resolution_confidence=0.0,
        )

    canonical = aliases.get(term_key, term).strip()
    if not canonical:
        canonical = term

    category = _category_for_canonical(canonical, source_category, taxonomy)
    status = "resolved" if term_key in aliases else "raw"
    source = "flat_alias" if term_key in aliases else "raw"
    domain_scope, facet_scope = ("unknown", "unknown")
    confidence = 0.0
    if term_key in aliases:
        domain_scope, facet_scope = _entry_scope(
            alias_key=term_key,
            canonical=canonical,
            domain=None,
            facet=None,
            taxonomy=taxonomy,
        )
        confidence = 0.90 if domain_scope == "global_safe" else 0.70
    return NormalizationResolution(
        input=term,
        alias_key=term_key,
        canonical=canonical,
        category=category,
        domain=normalized_context.domain,
        facet=normalized_context.facet_hint,
        resolution_status=status,
        resolution_source=source,
        domain_scope=domain_scope,
        facet_scope=facet_scope,
        resolution_confidence=confidence,
    )
