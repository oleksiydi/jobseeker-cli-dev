#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Auto-update profile taxonomy from unknown terms log.

Input format (JSONL) is intentionally flexible, examples:
{"term":"k8s","source_field":"stack","context":"...","count":4}
{"value":"jira","field":"tools"}
"Docker"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_ALIAS_CANONICAL: dict[str, str] = {
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "js": "JavaScript",
    "node": "Node.js",
    "nodejs": "Node.js",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mongo": "MongoDB",
    "mssql": "Microsoft SQL Server",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "aws lambda": "AWS Lambda",
    "gcp": "Google Cloud Platform",
    "azure": "Microsoft Azure",
    "vscode": "Visual Studio Code",
    "intellij": "IntelliJ IDEA",
}

DEFAULT_CATEGORY_BY_CANONICAL: dict[str, str] = {
    "Jira": "tools",
    "Jenkins": "tools",
    "TeamCity": "tools",
    "Bugzilla": "tools",
    "TestLink": "tools",
    "Wireshark": "tools",
    "Visual Studio Code": "tools",
    "IntelliJ IDEA": "tools",
    "Maven": "tools",
    "Git": "tools",
    "Docker": "stack",
    "Kubernetes": "stack",
    "Helm": "stack",
    "AWS": "stack",
    "AWS Lambda": "stack",
    "Microsoft Azure": "stack",
    "Digital Ocean": "stack",
    "MongoDB": "stack",
    "PostgreSQL": "stack",
    "Java": "stack",
    "Scala": "stack",
    "Bash": "stack",
    "SQL": "stack",
    "Spark SQL": "stack",
    "Node.js": "stack",
    "CI/CD": "skills",
    "Troubleshooting": "skills",
    "Incident Response": "skills",
    "Requirements Engineering": "skills",
}

CATEGORY_FIELDS = {"skills", "tools", "stack"}
REVIEW_STATUS_AUTO = "auto_suggested"
REVIEW_CODE_AUTO = 0


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_dry_run(*, dry_run: bool, allow_learning: bool) -> bool:
    return dry_run or (not allow_learning)


def _normalize_term_key(value: str) -> str:
    # Нормализуем термин для ключей alias (стабильно для разных регистров/пробелов).
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _normalize_canonical_display(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _stable_signature(term: str, source_field: str, context: str) -> str:
    payload = f"{term}\n{source_field}\n{context}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _ensure_taxonomy_shape(taxonomy: dict[str, Any]) -> dict[str, Any]:
    version = int(taxonomy.get("version", 1))
    aliases = taxonomy.get("aliases")
    categories = taxonomy.get("categories")
    if not isinstance(aliases, dict):
        aliases = {}
    if not isinstance(categories, dict):
        categories = {}

    normalized_categories: dict[str, list[str]] = {}
    for category in CATEGORY_FIELDS:
        raw = categories.get(category, [])
        if not isinstance(raw, list):
            raw = []
        dedup: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            canon = _normalize_canonical_display(item)
            marker = canon.casefold()
            if not canon or marker in seen:
                continue
            seen.add(marker)
            dedup.append(canon)
        normalized_categories[category] = sorted(dedup, key=str.casefold)

    normalized_aliases: dict[str, str] = {}
    for alias, canonical in aliases.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        alias_key = _normalize_term_key(alias)
        canonical_name = _normalize_canonical_display(canonical)
        if alias_key and canonical_name:
            normalized_aliases[alias_key] = canonical_name

    return {
        "version": version,
        "aliases": dict(sorted(normalized_aliases.items(), key=lambda item: item[0])),
        "categories": normalized_categories,
    }


def _parse_unknown_line(line: str) -> dict[str, str]:
    raw = line.strip()
    if not raw:
        return {"term": "", "source_field": "", "context": ""}
    parsed: Any
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw

    if isinstance(parsed, str):
        term = parsed
        source_field = ""
        context = ""
    elif isinstance(parsed, dict):
        term = str(parsed.get("term") or parsed.get("value") or "").strip()
        source_field = str(parsed.get("source_field") or parsed.get("field") or "").strip().lower()
        context = str(parsed.get("context") or parsed.get("raw_context") or "").strip()
    else:
        term = str(parsed).strip()
        source_field = ""
        context = ""

    return {
        "term": term,
        "source_field": source_field if source_field in CATEGORY_FIELDS else "",
        "context": context,
    }


def suggest_term(
    *,
    term: str,
    source_field: str,
    context: str,
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    term_key = _normalize_term_key(term)
    canonical = _normalize_canonical_display(term)
    confidence = 0.55
    reasons: list[str] = []

    aliases: dict[str, str] = taxonomy["aliases"]

    # 1) Сильный сигнал: термин уже известен в alias.
    if term_key in aliases:
        canonical = aliases[term_key]
        confidence = 0.99
        reasons.append("known_alias")
    # 2) Полу-автомат: seed-словарь по частым синонимам.
    elif term_key in DEFAULT_ALIAS_CANONICAL:
        canonical = DEFAULT_ALIAS_CANONICAL[term_key]
        confidence = 0.95
        reasons.append("seed_alias")
    else:
        reasons.append("unknown_alias")

    category = DEFAULT_CATEGORY_BY_CANONICAL.get(canonical, "")
    if category:
        confidence = max(confidence, 0.94)
        reasons.append("seed_category")
    elif source_field:
        category = source_field
        confidence = max(confidence, 0.78)
        reasons.append("source_field_hint")
    else:
        category = "skills"
        reasons.append("fallback_skills")

    if context and len(context) > 8:
        confidence = min(1.0, confidence + 0.02)
        reasons.append("context_present")

    return {
        "term": term,
        "term_key": term_key,
        "canonical": canonical,
        "category": category,
        "confidence": round(confidence, 4),
        "reasons": reasons,
    }


def apply_suggestion(taxonomy: dict[str, Any], suggestion: dict[str, Any]) -> None:
    alias_key = suggestion["term_key"]
    canonical = suggestion["canonical"]
    category = suggestion["category"]

    taxonomy["aliases"][alias_key] = canonical

    items = taxonomy["categories"][category]
    marker = canonical.casefold()
    if marker not in {item.casefold() for item in items}:
        items.append(canonical)
        items.sort(key=str.casefold)


def _load_unknown_terms(path: Path, max_items: int) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Unknown terms file not found: {path}")
    records: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            parsed = _parse_unknown_line(raw)
            if not parsed["term"]:
                continue
            records.append(parsed)
            if max_items > 0 and len(records) >= max_items:
                break
    return records


def _build_pending_record(suggestion: dict[str, Any]) -> dict[str, Any]:
    # Pending-объект хранит не только модельное предложение, но и workflow-статус ручной проверки.
    # review_code:
    #   0 = auto_suggested (еще не проверено)
    #   1 = approved (принято человеком)
    #   2 = rejected (отклонено человеком)
    record = dict(suggestion)
    record["review_status"] = REVIEW_STATUS_AUTO
    record["review_code"] = REVIEW_CODE_AUTO
    record["review_note"] = ""
    record["reviewed_by"] = ""
    record["reviewed_at"] = ""
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-update taxonomy aliases/categories from unknown terms log."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("logs/normalization/unknown_terms.jsonl"),
        help="Unknown terms JSONL input.",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("conf/normalization/taxonomy.auto.json"),
        help="Taxonomy JSON file.",
    )
    parser.add_argument(
        "--pending-out",
        type=Path,
        default=Path("logs/normalization/taxonomy_pending.jsonl"),
        help="File for low-confidence suggestions.",
    )
    parser.add_argument(
        "--processed-index",
        type=Path,
        default=Path("logs/normalization/taxonomy_processed.json"),
        help="Processed signatures index for idempotent re-runs.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.9,
        help="Auto-apply threshold.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Process at most N terms (0 = all).",
    )
    parser.add_argument(
        "--allow-learning",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("ALLOW_PROFILE_LEARNING", False),
        help="Allow writing taxonomy/pending/processed outputs (default from ALLOW_PROFILE_LEARNING, off by default).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write taxonomy/index files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.min_confidence <= 1.0:
        raise ValueError("--min-confidence must be in [0,1]")

    taxonomy_raw = _load_json(args.taxonomy, {"version": 1, "aliases": {}, "categories": {}})
    taxonomy = _ensure_taxonomy_shape(taxonomy_raw)

    processed = _load_json(args.processed_index, {"signatures": []})
    signatures = set(processed.get("signatures", []))
    records = _load_unknown_terms(args.input, max_items=max(0, args.max_items))

    applied = 0
    pending = 0
    skipped = 0
    processed_new: list[str] = []
    pending_lines: list[str] = []

    for record in records:
        sig = _stable_signature(record["term"], record["source_field"], record["context"])
        if sig in signatures:
            skipped += 1
            continue

        suggestion = suggest_term(
            term=record["term"],
            source_field=record["source_field"],
            context=record["context"],
            taxonomy=taxonomy,
        )
        suggestion["source_field"] = record["source_field"]
        suggestion["context"] = record["context"]

        if suggestion["confidence"] >= args.min_confidence:
            apply_suggestion(taxonomy, suggestion)
            applied += 1
        else:
            pending += 1
            pending_payload = _build_pending_record(suggestion)
            pending_lines.append(json.dumps(pending_payload, ensure_ascii=False))

        processed_new.append(sig)

    effective_dry_run = _effective_dry_run(dry_run=args.dry_run, allow_learning=args.allow_learning)

    if not effective_dry_run:
        _dump_json(args.taxonomy, _ensure_taxonomy_shape(taxonomy))
        updated_signatures = sorted(signatures.union(processed_new))
        _dump_json(args.processed_index, {"signatures": updated_signatures})
        if pending_lines:
            args.pending_out.parent.mkdir(parents=True, exist_ok=True)
            with args.pending_out.open("a", encoding="utf-8") as handle:
                for line in pending_lines:
                    handle.write(line + "\n")
    elif not args.allow_learning:
        print("learning_disabled_skip_write=true")

    print(
        "DONE: "
        f"total={len(records)}, applied={applied}, pending={pending}, skipped={skipped}, "
        f"dry_run={effective_dry_run}, allow_learning={args.allow_learning}, taxonomy={args.taxonomy}"
    )


if __name__ == "__main__":
    main()
