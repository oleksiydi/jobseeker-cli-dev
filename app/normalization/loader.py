from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_TAXONOMY_RELEASE_PATH = Path("conf/normalization/taxonomy.release.json")
DEFAULT_TAXONOMY_AUTO_PATH = Path("conf/normalization/taxonomy.auto.json")
DEFAULT_SYNONYMS_RELEASE_PATH = Path("conf/normalization/synonyms.release.json")
DEFAULT_SYNONYMS_AUTO_PATH = Path("conf/normalization/synonyms.auto.json")

_TAXONOMY_CATEGORIES = ("skills", "tools", "stack")


def _normalize_string(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_term_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return raw if isinstance(raw, dict) else {}


def _normalize_taxonomy_shape(raw: dict[str, Any]) -> dict[str, Any]:
    aliases_raw = raw.get("aliases", {})
    categories_raw = raw.get("categories", {})
    if not isinstance(aliases_raw, dict):
        aliases_raw = {}
    if not isinstance(categories_raw, dict):
        categories_raw = {}

    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        alias_key = _normalize_term_key(alias)
        canonical_text = _normalize_string(canonical)
        if alias_key and canonical_text:
            aliases[alias_key] = canonical_text

    categories: dict[str, set[str]] = {}
    for category in _TAXONOMY_CATEGORIES:
        values = categories_raw.get(category, [])
        if not isinstance(values, list):
            values = []
        categories[category] = {
            _normalize_string(item) for item in values if isinstance(item, str) and _normalize_string(item)
        }

    return {"aliases": aliases, "categories": categories}


def _merge_taxonomy(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged_aliases = dict(base["aliases"])
    merged_aliases.update(overlay["aliases"])

    merged_categories: dict[str, set[str]] = {}
    for category in _TAXONOMY_CATEGORIES:
        merged_categories[category] = set(base["categories"][category]) | set(overlay["categories"][category])

    return {"aliases": merged_aliases, "categories": merged_categories}


def load_merged_taxonomy(*, release_path: Path, auto_path: Path) -> dict[str, Any]:
    """
    Load runtime taxonomy from release + auto layers.

    Merge rules:
    - aliases: auto overrides release on identical alias key;
    - categories: union of release and auto values.
    """

    release = _normalize_taxonomy_shape(_load_json(release_path))
    auto = _normalize_taxonomy_shape(_load_json(auto_path))
    return _merge_taxonomy(release, auto)


def _normalize_synonyms_shape(raw: dict[str, Any]) -> dict[str, Any]:
    entries_raw = raw.get("entries", [])
    aliases_raw = raw.get("aliases", {})
    ambiguous_aliases_raw = raw.get("ambiguous_aliases", [])
    if not isinstance(entries_raw, list):
        entries_raw = []
    if not isinstance(aliases_raw, dict):
        aliases_raw = {}
    if not isinstance(ambiguous_aliases_raw, list):
        ambiguous_aliases_raw = []

    entries: list[dict[str, Any]] = []
    categories: dict[str, set[str]] = {category: set() for category in _TAXONOMY_CATEGORIES}
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        alias = _normalize_string(item.get("alias"))
        canonical = _normalize_string(item.get("canonical"))
        domain = _normalize_string(item.get("domain"))
        category = _normalize_string(item.get("category"))
        facet = _normalize_string(item.get("facet"))
        skill_class = _normalize_string(item.get("skill_class"))
        if not alias or not canonical:
            continue
        row: dict[str, Any] = {
            "alias": alias,
            "canonical": canonical,
            "domain": domain,
            "category": category,
            "facet": facet,
            "skill_class": skill_class,
        }
        if _normalize_string(item.get("domain_scope")):
            row["domain_scope"] = _normalize_string(item.get("domain_scope"))
        if _normalize_string(item.get("facet_scope")):
            row["facet_scope"] = _normalize_string(item.get("facet_scope"))
        if item.get("confidence") is not None:
            row["confidence"] = item.get("confidence")
        if item.get("source_refs_count") is not None:
            row["source_refs_count"] = item.get("source_refs_count")
        if _normalize_string(item.get("source")):
            row["source"] = _normalize_string(item.get("source"))
        if _normalize_string(item.get("updated_at")):
            row["updated_at"] = _normalize_string(item.get("updated_at"))
        entries.append(row)
        if category in categories:
            categories[category].add(canonical)

    aliases_legacy: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        alias_key = _normalize_term_key(alias)
        canonical_text = _normalize_string(canonical)
        if alias_key and canonical_text:
            aliases_legacy[alias_key] = canonical_text
    ambiguous_aliases = {
        _normalize_term_key(str(item)) for item in ambiguous_aliases_raw if _normalize_term_key(str(item))
    }
    return {
        "aliases_legacy": aliases_legacy,
        "entries": entries,
        "categories": categories,
        "ambiguous_aliases": ambiguous_aliases,
    }


def _build_synonym_indexes(entries: list[dict[str, Any]]) -> dict[str, Any]:
    aliases_global_safe: dict[str, str] = {}
    aliases_by_domain: dict[str, dict[str, str]] = {}
    aliases_by_domain_facet: dict[str, dict[str, dict[str, str]]] = {}
    ambiguous_aliases: set[str] = set()

    by_alias_global: dict[str, dict[str, str]] = {}
    by_alias_all: dict[str, dict[str, str]] = {}
    by_alias_domain: dict[str, dict[str, dict[str, str]]] = {}
    by_alias_domain_facet: dict[str, dict[str, dict[str, dict[str, str]]]] = {}

    for item in entries:
        alias = _normalize_string(item.get("alias"))
        canonical = _normalize_string(item.get("canonical"))
        domain = _normalize_string(item.get("domain"))
        facet = _normalize_string(item.get("facet"))
        domain_scope = _normalize_string(item.get("domain_scope")) or "specific"
        facet_scope = _normalize_string(item.get("facet_scope")) or "specific"
        alias_key = _normalize_term_key(alias)
        canonical_key = _normalize_term_key(canonical)
        if not alias_key or not canonical_key:
            continue
        by_alias_all.setdefault(alias_key, {})[canonical_key] = canonical
        if domain_scope == "global_safe":
            by_alias_global.setdefault(alias_key, {})[canonical_key] = canonical
        if domain:
            by_alias_domain.setdefault(alias_key, {}).setdefault(domain, {})[canonical_key] = canonical
        if domain and facet and facet_scope != "unstable":
            by_alias_domain_facet.setdefault(alias_key, {}).setdefault(domain, {}).setdefault(facet, {})[
                canonical_key
            ] = canonical

    for alias_key, canonical_map in by_alias_global.items():
        if len(canonical_map) == 1:
            aliases_global_safe[alias_key] = next(iter(canonical_map.values()))
        else:
            ambiguous_aliases.add(alias_key)

    for alias_key, canonical_map in by_alias_all.items():
        if len(canonical_map) > 1:
            ambiguous_aliases.add(alias_key)
            aliases_global_safe.pop(alias_key, None)

    for alias_key, domain_map in by_alias_domain.items():
        aliases_by_domain[alias_key] = {}
        for domain, canonical_map in domain_map.items():
            if len(canonical_map) == 1:
                aliases_by_domain[alias_key][domain] = next(iter(canonical_map.values()))

    for alias_key, domain_map in by_alias_domain_facet.items():
        aliases_by_domain_facet[alias_key] = {}
        for domain, facet_map in domain_map.items():
            aliases_by_domain_facet[alias_key][domain] = {}
            for facet, canonical_map in facet_map.items():
                if len(canonical_map) == 1:
                    aliases_by_domain_facet[alias_key][domain][facet] = next(iter(canonical_map.values()))

    return {
        "aliases_global_safe": dict(sorted(aliases_global_safe.items(), key=lambda item: item[0])),
        "aliases_by_domain": aliases_by_domain,
        "aliases_by_domain_facet": aliases_by_domain_facet,
        "ambiguous_aliases": ambiguous_aliases,
    }


def load_merged_synonyms(*, release_path: Path, auto_path: Path) -> dict[str, Any]:
    """
    Load synonyms from release + auto layers.

    Current schema is flat aliases mapping. Domain-aware runtime mapping will
    be built on top of this contract in a separate step.
    """

    release = _normalize_synonyms_shape(_load_json(release_path))
    auto = _normalize_synonyms_shape(_load_json(auto_path))
    merged_entries = list(release["entries"])
    merged_entries.extend(auto["entries"])

    merged_categories: dict[str, set[str]] = {}
    for category in _TAXONOMY_CATEGORIES:
        merged_categories[category] = set(release["categories"][category]) | set(auto["categories"][category])

    indexes = _build_synonym_indexes(merged_entries)
    merged_legacy_aliases = dict(release["aliases_legacy"])
    merged_legacy_aliases.update(auto["aliases_legacy"])

    explicit_ambiguous = set(release["ambiguous_aliases"]) | set(auto["ambiguous_aliases"])
    indexes["ambiguous_aliases"] |= explicit_ambiguous
    for alias_key in explicit_ambiguous:
        indexes["aliases_global_safe"].pop(alias_key, None)

    for alias_key, canonical in merged_legacy_aliases.items():
        if alias_key in indexes["ambiguous_aliases"]:
            continue
        indexes["aliases_global_safe"].setdefault(alias_key, canonical)

    return {
        "aliases": dict(sorted(indexes["aliases_global_safe"].items(), key=lambda item: item[0])),
        "aliases_global_safe": dict(sorted(indexes["aliases_global_safe"].items(), key=lambda item: item[0])),
        "aliases_by_domain": indexes["aliases_by_domain"],
        "aliases_by_domain_facet": indexes["aliases_by_domain_facet"],
        "entries": merged_entries,
        "categories": merged_categories,
        "ambiguous_aliases": sorted(indexes["ambiguous_aliases"]),
    }


def build_runtime_taxonomy(*, taxonomy: dict[str, Any], synonyms: dict[str, Any]) -> dict[str, Any]:
    """
    Compose the runtime taxonomy overlay used by profile/vacancy normalization.

    For the current runtime path we flatten the accepted synonyms layer into the
    existing taxonomy alias/category structure, so both profiles and vacancies
    use one deterministic resolver without LLM.
    """

    merged_aliases = dict(taxonomy.get("aliases", {}))
    for alias_key, canonical in synonyms.get("aliases_global_safe", synonyms.get("aliases", {})).items():
        merged_aliases[alias_key] = canonical

    taxonomy_categories = taxonomy.get("categories", {})
    synonyms_categories = synonyms.get("categories", {})
    merged_categories: dict[str, set[str]] = {}
    for category in _TAXONOMY_CATEGORIES:
        merged_categories[category] = set(taxonomy_categories.get(category, set())) | set(
            synonyms_categories.get(category, set())
        )

    return {
        "aliases": merged_aliases,
        "categories": merged_categories,
        "entries": list(synonyms.get("entries", [])),
        "aliases_global_safe": dict(synonyms.get("aliases_global_safe", synonyms.get("aliases", {}))),
        "aliases_by_domain": dict(synonyms.get("aliases_by_domain", {})),
        "aliases_by_domain_facet": dict(synonyms.get("aliases_by_domain_facet", {})),
        "ambiguous_aliases": list(synonyms.get("ambiguous_aliases", [])),
    }


def load_runtime_taxonomy(
    *,
    taxonomy_release_path: Path = DEFAULT_TAXONOMY_RELEASE_PATH,
    taxonomy_auto_path: Path = DEFAULT_TAXONOMY_AUTO_PATH,
    synonyms_release_path: Path = DEFAULT_SYNONYMS_RELEASE_PATH,
    synonyms_auto_path: Path = DEFAULT_SYNONYMS_AUTO_PATH,
) -> dict[str, Any]:
    taxonomy = load_merged_taxonomy(
        release_path=taxonomy_release_path,
        auto_path=taxonomy_auto_path,
    )
    synonyms = load_merged_synonyms(
        release_path=synonyms_release_path,
        auto_path=synonyms_auto_path,
    )
    return build_runtime_taxonomy(taxonomy=taxonomy, synonyms=synonyms)


def load_merged_normalization_dicts(
    *,
    taxonomy_release_path: Path = DEFAULT_TAXONOMY_RELEASE_PATH,
    taxonomy_auto_path: Path = DEFAULT_TAXONOMY_AUTO_PATH,
    synonyms_release_path: Path = DEFAULT_SYNONYMS_RELEASE_PATH,
    synonyms_auto_path: Path = DEFAULT_SYNONYMS_AUTO_PATH,
) -> dict[str, Any]:
    return {
        "taxonomy": load_merged_taxonomy(
            release_path=taxonomy_release_path,
            auto_path=taxonomy_auto_path,
        ),
        "synonyms": load_merged_synonyms(
            release_path=synonyms_release_path,
            auto_path=synonyms_auto_path,
        ),
        "runtime_taxonomy": load_runtime_taxonomy(
            taxonomy_release_path=taxonomy_release_path,
            taxonomy_auto_path=taxonomy_auto_path,
            synonyms_release_path=synonyms_release_path,
            synonyms_auto_path=synonyms_auto_path,
        ),
    }
