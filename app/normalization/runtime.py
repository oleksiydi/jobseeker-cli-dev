from __future__ import annotations

import re
from typing import Any


def normalize_term_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


def resolve_canonical_term_and_category(
    *,
    term: str,
    source_category: str,
    taxonomy: dict[str, Any],
) -> tuple[str, str]:
    aliases: dict[str, str] = taxonomy.get("aliases", {})
    categories: dict[str, set[str]] = taxonomy.get("categories", {})

    term_key = normalize_term_key(term)
    canonical = aliases.get(term_key, term).strip()
    if not canonical:
        return term, source_category

    for category in ("tools", "stack", "skills"):
        known = categories.get(category, set())
        if canonical in known:
            return canonical, category
    return canonical, source_category
