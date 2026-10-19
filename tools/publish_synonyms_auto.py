#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.normalization.synonyms_contract import load_allowed_domains, validate_dictionary_payload
from tools.taxonomy_transform import normalize_term_key
from tools.taxonomy_io import write_json

DEFAULT_MIN_DOMAINS_CROSS_DOMAIN = 2
DEFAULT_MIN_REFS_CROSS_DOMAIN = 2
DEFAULT_MIN_DOMAINS_GLOBAL_SAFE = 3
DEFAULT_MIN_REFS_GLOBAL_SAFE = 10


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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _load_existing_version(path: Path) -> int:
    if not path.exists():
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 1
    version = payload.get("version")
    try:
        parsed = int(version)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def _iter_publishable(cursor: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in cursor:
        alias = _normalize_text(doc.get("alias"))
        canonical = _normalize_text(doc.get("canonical"))
        domain = _normalize_text(doc.get("domain"))
        category = _normalize_text(doc.get("category"))
        facet = _normalize_text(doc.get("facet"))
        skill_class = _normalize_text(doc.get("skill_class"))
        if not (alias and canonical and domain and category and facet and skill_class):
            continue
        if normalize_term_key(alias) == normalize_term_key(canonical):
            continue
        rows.append(
            {
                "alias": alias,
                "canonical": canonical,
                "domain": domain,
                "category": category,
                "facet": facet,
                "skill_class": skill_class,
                "confidence": _safe_float(doc.get("confidence"), default=0.0),
                "source_refs_count": _safe_int(doc.get("source_refs_count"), default=1),
                "source": "llm" if doc.get("decision_source") in {"llm_adjudication", "llm_pro_adjudication", "llm_cluster"} else "rule",
                "updated_at": _normalize_text(doc.get("updated_at")) or _now_utc_iso(),
            }
        )
    targets = defaultdict(set)
    for row in rows:
        scope = (normalize_term_key(row["alias"]), row["domain"], row["category"], row["facet"])
        targets[scope].add(normalize_term_key(row["canonical"]))
    rows = [row for row in rows if len(targets[(normalize_term_key(row["alias"]),
            row["domain"], row["category"], row["facet"])]) == 1]
    rows.sort(
        key=lambda row: (
            normalize_term_key(row["alias"]),
            normalize_term_key(row["canonical"]),
            row["domain"],
            row["category"],
            row["facet"],
        )
    )
    return rows


def _annotate_scopes(
    entries: list[dict[str, Any]],
    *,
    min_domains_cross_domain: int,
    min_refs_cross_domain: int,
    min_domains_global_safe: int,
    min_refs_global_safe: int,
) -> list[dict[str, Any]]:
    alias_to_canonicals: dict[str, set[str]] = {}
    pair_to_domains: dict[tuple[str, str], set[str]] = {}
    pair_to_facets: dict[tuple[str, str], set[str]] = {}
    pair_to_categories: dict[tuple[str, str], set[str]] = {}
    pair_to_skill_classes: dict[tuple[str, str], set[str]] = {}
    pair_to_source_refs: dict[tuple[str, str], int] = {}

    for row in entries:
        alias_key = normalize_term_key(row["alias"])
        canonical_key = normalize_term_key(row["canonical"])
        pair_key = (alias_key, canonical_key)
        source_refs_count = _safe_int(row.get("source_refs_count"), default=1)
        alias_to_canonicals.setdefault(alias_key, set()).add(canonical_key)
        pair_to_source_refs[pair_key] = pair_to_source_refs.get(pair_key, 0) + max(1, source_refs_count)
        if row["domain"]:
            pair_to_domains.setdefault(pair_key, set()).add(row["domain"])
        if row["facet"]:
            pair_to_facets.setdefault(pair_key, set()).add(row["facet"])
        if row["category"]:
            pair_to_categories.setdefault(pair_key, set()).add(row["category"])
        if row["skill_class"]:
            pair_to_skill_classes.setdefault(pair_key, set()).add(row["skill_class"])

    annotated: list[dict[str, Any]] = []
    for row in entries:
        alias_key = normalize_term_key(row["alias"])
        canonical_key = normalize_term_key(row["canonical"])
        pair_key = (alias_key, canonical_key)
        canonical_count = len(alias_to_canonicals.get(alias_key, set()))
        domain_count = len(pair_to_domains.get(pair_key, set()))
        facet_count = len(pair_to_facets.get(pair_key, set()))
        category_count = len(pair_to_categories.get(pair_key, set()))
        skill_class_count = len(pair_to_skill_classes.get(pair_key, set()))
        source_refs_count = pair_to_source_refs.get(pair_key, 0)
        has_enough_cross_domain_evidence = (
            domain_count >= min_domains_cross_domain and source_refs_count >= min_refs_cross_domain
        )
        has_enough_global_safe_evidence = (
            domain_count >= min_domains_global_safe and source_refs_count >= min_refs_global_safe
        )
        is_stable = facet_count == 1 and category_count == 1 and skill_class_count == 1

        domain_scope = "specific"
        if canonical_count == 1 and has_enough_global_safe_evidence and is_stable:
            domain_scope = "global_safe"
        elif has_enough_cross_domain_evidence:
            domain_scope = "cross_domain_candidate"

        facet_scope = "specific"
        if facet_count > 1:
            facet_scope = "unstable"

        enriched = dict(row)
        enriched["domain_scope"] = domain_scope
        enriched["facet_scope"] = facet_scope
        annotated.append(enriched)
    return annotated


def _build_aliases(entries: list[dict[str, Any]]) -> dict[str, str]:
    global_alias_to_canonicals: dict[str, dict[str, str]] = {}
    all_alias_to_canonicals: dict[str, dict[str, str]] = {}
    for row in entries:
        alias_key = normalize_term_key(row["alias"])
        canonical = _normalize_text(row["canonical"])
        canonical_key = normalize_term_key(canonical)
        if alias_key and canonical_key:
            all_alias_to_canonicals.setdefault(alias_key, {})[canonical_key] = canonical
            if row.get("domain_scope") == "global_safe":
                global_alias_to_canonicals.setdefault(alias_key, {})[canonical_key] = canonical

    aliases: dict[str, str] = {}
    ambiguous_aliases = [
        alias_key
        for alias_key, canonical_map in sorted(all_alias_to_canonicals.items(), key=lambda item: item[0])
        if len(canonical_map) > 1
    ]
    ambiguous_alias_keys = set(ambiguous_aliases)
    for alias_key, canonical_map in sorted(global_alias_to_canonicals.items(), key=lambda item: item[0]):
        if alias_key in ambiguous_alias_keys:
            continue
        if len(canonical_map) == 1:
            aliases[alias_key] = next(iter(canonical_map.values()))
        else:
            ambiguous_alias_keys.add(alias_key)
            ambiguous_aliases.append(alias_key)
    return aliases, ambiguous_aliases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish synonyms.auto.json from Mongo synonyms state.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("conf/normalization/synonyms.auto.json"),
        help="Output synonyms auto dictionary path.",
    )
    parser.add_argument(
        "--domains-file",
        type=Path,
        default=Path("conf/normalization/domains.level1.de.json"),
        help="Domain list for dictionary validation.",
    )
    parser.add_argument(
        "--include-status",
        default="accepted,adjudicated_accepted,escalated_accepted",
        help="Comma-separated statuses to publish.",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=0,
        help="Dictionary version override (0 means reuse existing version).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate dictionary payload before writing.",
    )
    parser.add_argument(
        "--min-domains-cross-domain",
        type=int,
        default=DEFAULT_MIN_DOMAINS_CROSS_DOMAIN,
        help="Minimum distinct domains to mark alias+canonical as cross_domain_candidate.",
    )
    parser.add_argument(
        "--min-refs-cross-domain",
        type=int,
        default=DEFAULT_MIN_REFS_CROSS_DOMAIN,
        help="Minimum source refs to mark alias+canonical as cross_domain_candidate.",
    )
    parser.add_argument(
        "--min-domains-global-safe",
        type=int,
        default=DEFAULT_MIN_DOMAINS_GLOBAL_SAFE,
        help="Minimum distinct domains to mark stable alias+canonical as global_safe.",
    )
    parser.add_argument(
        "--min-refs-global-safe",
        type=int,
        default=DEFAULT_MIN_REFS_GLOBAL_SAFE,
        help="Minimum source refs to mark stable alias+canonical as global_safe.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    statuses = [item.strip() for item in args.include_status.split(",") if item.strip()]
    if not statuses:
        raise ValueError("--include-status must contain at least one status")
    for name in (
        "min_domains_cross_domain",
        "min_refs_cross_domain",
        "min_domains_global_safe",
        "min_refs_global_safe",
    ):
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be > 0")

    cfg = load_app_config()
    db = get_database(cfg)
    state_collection_name = resolve_collection_name(
        cfg,
        key="normalization_synonyms_state",
        default="normalization_synonyms_state",
    )
    state_collection = db[state_collection_name]
    cursor = state_collection.find({"processing_status": {"$in": statuses}})
    entries = _annotate_scopes(
        _iter_publishable(cursor),
        min_domains_cross_domain=args.min_domains_cross_domain,
        min_refs_cross_domain=args.min_refs_cross_domain,
        min_domains_global_safe=args.min_domains_global_safe,
        min_refs_global_safe=args.min_refs_global_safe,
    )
    aliases, ambiguous_aliases = _build_aliases(entries)

    version = args.version if args.version > 0 else _load_existing_version(args.out)
    payload = {"version": version, "entries": entries, "aliases": aliases, "ambiguous_aliases": ambiguous_aliases}

    if args.validate:
        allowed_domains = load_allowed_domains(args.domains_file)
        errors = validate_dictionary_payload(payload, allowed_domains=allowed_domains)
        if errors:
            raise ValueError("; ".join(errors))

    write_json(args.out, payload)
    print(
        f"synonyms_auto_published out={args.out} version={version} entries={len(entries)} "
        f"aliases={len(aliases)} ambiguous_aliases={len(ambiguous_aliases)} statuses={','.join(statuses)} "
        f"scope_thresholds=cross_domain:{args.min_domains_cross_domain}/{args.min_refs_cross_domain},"
        f"global_safe:{args.min_domains_global_safe}/{args.min_refs_global_safe}"
    )


if __name__ == "__main__":
    main()
