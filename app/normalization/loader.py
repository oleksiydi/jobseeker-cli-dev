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
    aliases_raw = raw.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        aliases_raw = {}
    aliases: dict[str, str] = {}
    for alias, canonical in aliases_raw.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        alias_key = _normalize_term_key(alias)
        canonical_key = _normalize_term_key(canonical)
        if alias_key and canonical_key:
            aliases[alias_key] = canonical_key
    return {"aliases": aliases}


def load_merged_synonyms(*, release_path: Path, auto_path: Path) -> dict[str, Any]:
    """
    Load synonyms from release + auto layers.

    Current schema is flat aliases mapping. Domain-aware runtime mapping will
    be built on top of this contract in a separate step.
    """

    release = _normalize_synonyms_shape(_load_json(release_path))
    auto = _normalize_synonyms_shape(_load_json(auto_path))
    merged_aliases = dict(release["aliases"])
    merged_aliases.update(auto["aliases"])
    return {"aliases": merged_aliases}


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
    }
