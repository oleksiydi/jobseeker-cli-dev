#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build term/domain taxonomy artifacts from real vacancies in MongoDB.

Goal of this stage:
- extract terms from vacancies with LLM (LLM-first approach);
- keep domain hint per term;
- produce two artifacts:
  1) term rows JSONL (one row per extracted term);
  2) aggregated JSON with votes by category/domain.

This script is intentionally additive and does not mutate production collections.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow "python tools/..." execution with imports from project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name
from app.llm.client import LLMChatRequest, chat_completion
from app.llm.factory import resolve_provider_name
from app.matching.models import MergedVacancyRaw, MergedVacancyNormalized
from app.normalization.loader import (
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    load_merged_taxonomy,
)
from app.normalization.runtime import resolve_canonical_term_and_category

LOGGER = logging.getLogger("tools.build_taxonomy_from_vacancies")


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
CATEGORY_ALLOWED = {"skills", "tools", "stack"}
MAX_CONTEXT_PHRASE_CHARS = 200
FACET_ALLOWED = {
    "skill_requirement",
    "tool_or_platform",
    "technology_stack",
    "task_responsibility",
    "benefit",
    "work_condition",
    "language_requirement",
    "certification",
    "interpersonal_competency",
    "other",
}
SKILL_CLASS_ALLOWED = {"professional", "interpersonal", "non_skill_conditions", "not_applicable"}
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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_term_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", lowered)


def _normalize_domain(value: str) -> str:
    domain = _normalize_term_key(value)
    domain = DOMAIN_ALIASES.get(domain, domain)
    domain = domain.replace(" ", "_")
    return domain if domain else "sonstiges"


def _load_domains(path: Path) -> list[str]:
    if not path.exists():
        return DEFAULT_DOMAIN_LIST[:]
    payload = json.loads(path.read_text(encoding="utf-8"))
    domains = payload.get("domains", [])
    if not isinstance(domains, list):
        return DEFAULT_DOMAIN_LIST[:]
    normalized = [_normalize_domain(str(item)) for item in domains if _normalize_domain(str(item))]
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_dry_run(*, dry_run: bool, allow_learning: bool) -> bool:
    return dry_run or (not allow_learning)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("LLM returned empty content.")

    if stripped.startswith("```"):
        match = JSON_BLOCK_RE.search(stripped)
        if match:
            stripped = match.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM output is not valid JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON output must be an object.")
    return parsed


def _content_preview(value: str, max_chars: int = 1200) -> str:
    cleaned = value.replace("\r", " ").replace("\n", " ").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + " ...<truncated>"


def _load_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Schema must be JSON object: {path}")
    return payload


def _build_system_prompt(domain_list: list[str], max_terms_per_vacancy: int) -> str:
    domains = ", ".join(domain_list)
    return (
        "You extract taxonomy candidates from a German job vacancy.\n"
        "Return only one JSON object.\n"
        "No markdown, no explanations.\n"
        f"Use only domains from this list: {domains}.\n"
        "Use domain='sonstiges' only when no domain clue exists in title/role/description.\n"
        "Category mapping is strict:\n"
        "- tools: named products/platforms/software/services (e.g., Jira, SAP, Concourse CI)\n"
        "- stack: technologies/languages/frameworks/infrastructure (e.g., Java, SQL, Kubernetes)\n"
        "- skills: competencies/tasks/requirements/conditions/benefits\n"
        "Do NOT put technologies/products into skills.\n"
        "If a term looks like technology name, language, framework, cloud or infra keyword, prefer stack/tools.\n"
        "Use skills only for competencies, tasks, benefits, work conditions and interpersonal competencies.\n"
        "Set facet for each term. Use 'benefit' for compensation/perks and "
        "'work_condition' for schedule/remote/shift/location constraints.\n"
        "Keep context_phrase short: max 200 chars, fragment only (not full sentence).\n"
        "Prefer concise terms (1-5 words).\n"
        f"Return at most {max_terms_per_vacancy} terms.\n"
        "Prefer meaningful phrases over isolated generic words.\n"
        "Keep category strictly in: skills|tools|stack.\n"
    )


def _build_user_prompt(vacancy: MergedVacancyNormalized, max_input_chars: int) -> str:
    canonical = vacancy.canonical_text()
    clipped = canonical[:max_input_chars]
    return (
        "Extract term/domain candidates for taxonomy bootstrap.\n"
        "Output JSON with keys: vacancy_domains, terms.\n"
        "For each term include: term, category, facet, domain_hint, domain_confidence, context_phrase.\n\n"
        f"Vacancy text:\n{clipped}"
    )


def _sanitize_term_record(
    *,
    raw: dict[str, Any],
    referenznummer: str,
    vacancy_domain_top: str,
    vacancy_domain_confidence: float,
) -> ExtractedTerm | None:
    term = _normalize_ws(str(raw.get("term") or ""))
    category = _normalize_term_key(str(raw.get("category") or ""))
    facet_key = _normalize_term_key(str(raw.get("facet") or ""))
    domain_hint = _normalize_domain(str(raw.get("domain_hint") or "other"))
    context_phrase = _normalize_ws(str(raw.get("context_phrase") or ""))
    domain_confidence = _safe_float(raw.get("domain_confidence"), default=0.0)

    if not term:
        return None
    if category not in CATEGORY_ALLOWED:
        return None
    if not facet_key:
        # Backward-compatible fallback when older prompts/models omit facet.
        facet = {
            "skills": "skill_requirement",
            "tools": "tool_or_platform",
            "stack": "technology_stack",
        }.get(category, "other")
    else:
        facet = FACET_KEY_TO_CANONICAL.get(facet_key, "other")
    if facet not in FACET_ALLOWED:
        facet = "other"
    if not context_phrase:
        context_phrase = term
    if len(context_phrase) > MAX_CONTEXT_PHRASE_CHARS:
        context_phrase = context_phrase[:MAX_CONTEXT_PHRASE_CHARS].rstrip()

    # Facet-level correction keeps backward-compatible categories but reduces drift.
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

    skill_class = _skill_class_for(category=category, facet=facet)

    return ExtractedTerm(
        referenznummer=referenznummer,
        term=term,
        term_key=_normalize_term_key(term),
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


def _skill_class_for(*, category: str, facet: str) -> str:
    # Secondary semantics for downstream analytics:
    # - keep category backward-compatible;
    # - add neutral skill_class labels for cross-domain usage.
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


def _apply_runtime_taxonomy_to_row(row: ExtractedTerm, taxonomy: dict[str, Any]) -> ExtractedTerm:
    canonical, category = resolve_canonical_term_and_category(
        term=row.canonical,
        source_category=row.category,
        taxonomy=taxonomy,
    )
    skill_class = _skill_class_for(category=category, facet=row.facet)
    return ExtractedTerm(
        referenznummer=row.referenznummer,
        term=row.term,
        term_key=_normalize_term_key(canonical),
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


def _extract_vacancy_domain_top(vacancy_domains: Any) -> tuple[str, float]:
    if not isinstance(vacancy_domains, list) or not vacancy_domains:
        return "sonstiges", 0.0
    best_domain = "sonstiges"
    best_conf = 0.0
    for item in vacancy_domains:
        if not isinstance(item, dict):
            continue
        domain = _normalize_domain(str(item.get("domain") or "other"))
        conf = _safe_float(item.get("confidence"), default=0.0)
        if conf > best_conf:
            best_domain = domain
            best_conf = conf
    return best_domain, best_conf


def _iter_active_vacancies(db: Any, cfg: dict[str, Any], max_vacancies: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    details_collection_name = resolve_collection_name(cfg, key="details", default="jobdetails")
    col_list = db[list_collection_name]
    col_details = db[details_collection_name]

    cursor = col_list.find({"is_active": True})
    if max_vacancies > 0:
        cursor = cursor.limit(max_vacancies)

    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for meta in cursor:
        ref = str(meta.get("referenznummer") or "").strip()
        if not ref:
            continue
        details = col_details.find_one({"referenznummer": ref}) or {}
        rows.append((meta, details))
    return rows


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_process_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row["ts"] = _now_utc_iso()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_aggregate(
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
        sample_phrases = sorted(item["phrases"])[:10]
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
                "sample_phrases": sample_phrases,
            }
        )

    return {
        "version": 1,
        "generated_at": _now_utc_iso(),
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build term/domain taxonomy artifacts from Mongo vacancies via LLM."
    )
    parser.add_argument("--provider", default=os.getenv("LLM_PROVIDER", "openai"), help="llama_cpp|openai|openrouter")
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "gpt-4o-mini"), help="LLM model id")
    parser.add_argument("--base-url", default=None, help="Optional LLM base URL override")
    parser.add_argument("--api-key", default=None, help="Optional LLM API key override")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="HTTP timeout per vacancy; set 0 to disable timeout.",
    )
    parser.add_argument("--max-vacancies", type=int, default=300, help="Max active vacancies to process (0=all)")
    parser.add_argument("--max-terms-per-vacancy", type=int, default=30, help="Max terms requested per vacancy")
    parser.add_argument("--llm-max-tokens", type=int, default=1100, help="Max LLM output tokens per vacancy")
    parser.add_argument("--max-input-chars", type=int, default=3000, help="Cap per-vacancy text before LLM call")
    parser.add_argument(
        "--extraction-schema",
        type=Path,
        default=Path("conf/normalization/schemas/vacancy_term_extraction.schema.json"),
        help="JSON schema for LLM extraction response",
    )
    parser.add_argument(
        "--aggregate-schema",
        type=Path,
        default=Path("conf/normalization/schemas/taxonomy_domain_aggregate.schema.json"),
        help="Schema path for aggregate contract documentation",
    )
    parser.add_argument(
        "--out-terms-jsonl",
        type=Path,
        default=Path("logs/normalization/vacancy_terms.jsonl"),
        help="Output JSONL with extracted term rows",
    )
    parser.add_argument(
        "--out-aggregate-json",
        type=Path,
        default=Path("logs/normalization/taxonomy_domain_aggregate.json"),
        help="Output aggregate JSON path",
    )
    parser.add_argument(
        "--llm-calls-log",
        type=Path,
        default=Path("logs/normalization/vacancy_taxonomy_llm_calls.jsonl"),
        help="JSONL log for per-call LLM metadata",
    )
    parser.add_argument(
        "--process-log",
        type=Path,
        default=Path("logs/normalization/vacancy_taxonomy_process.jsonl"),
        help="JSONL process log (start/normalization/error/end).",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=10,
        help="Write aggregate checkpoint every N successful vacancies (0=only final write).",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Truncate output JSONL files before run.",
    )
    parser.add_argument(
        "--domains-file",
        type=Path,
        default=Path("conf/normalization/domains.level1.de.json"),
        help="Path to level-1 domain vocabulary JSON.",
    )
    parser.add_argument(
        "--domain-list",
        default="",
        help="Optional comma-separated domain override (if empty, use --domains-file).",
    )
    parser.add_argument(
        "--allow-learning",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("ALLOW_VACANCY_LEARNING", False),
        help="Allow writing taxonomy artifacts (default from ALLOW_VACANCY_LEARNING, off by default).",
    )
    parser.add_argument(
        "--apply-runtime-taxonomy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply merged runtime taxonomy (release+auto) to extracted vacancy terms.",
    )
    parser.add_argument(
        "--taxonomy-release-path",
        type=Path,
        default=DEFAULT_TAXONOMY_RELEASE_PATH,
        help="Path to release taxonomy JSON layer for runtime mapping.",
    )
    parser.add_argument(
        "--taxonomy-auto-path",
        type=Path,
        default=DEFAULT_TAXONOMY_AUTO_PATH,
        help="Path to auto taxonomy JSON layer for runtime mapping.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write output files")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    if args.timeout_seconds < 0:
        raise ValueError("--timeout-seconds must be >= 0")
    if args.max_terms_per_vacancy <= 0:
        raise ValueError("--max-terms-per-vacancy must be > 0")
    if args.llm_max_tokens <= 0:
        raise ValueError("--llm-max-tokens must be > 0")
    if args.max_input_chars <= 200:
        raise ValueError("--max-input-chars must be > 200")
    effective_dry_run = _effective_dry_run(dry_run=args.dry_run, allow_learning=args.allow_learning)

    provider = resolve_provider_name(args.provider)
    if args.domain_list.strip():
        domain_list = [_normalize_domain(item) for item in args.domain_list.split(",") if _normalize_domain(item)]
    else:
        domain_list = _load_domains(args.domains_file)
    if not domain_list:
        domain_list = DEFAULT_DOMAIN_LIST[:]

    extraction_schema = _load_schema(args.extraction_schema)
    # aggregate schema is loaded for early contract check only.
    _ = _load_schema(args.aggregate_schema)

    cfg = load_app_config()
    db = get_database(cfg)
    vacancies = _iter_active_vacancies(db, cfg, max_vacancies=max(0, args.max_vacancies))
    scanned = len(vacancies)
    run_id = f"vac-taxo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    _append_process_log(
        args.process_log,
        {
            "event": "run_started",
            "run_id": run_id,
            "provider": provider,
            "model": args.model,
            "max_vacancies": args.max_vacancies,
            "max_terms_per_vacancy": args.max_terms_per_vacancy,
            "llm_max_tokens": args.llm_max_tokens,
            "max_input_chars": args.max_input_chars,
            "timeout_seconds": args.timeout_seconds,
            "allow_learning": args.allow_learning,
            "dry_run": args.dry_run,
            "effective_dry_run": effective_dry_run,
        },
    )
    LOGGER.info(
        "run_started run_id=%s provider=%s model=%s max_vacancies=%s max_terms=%s llm_max_tokens=%s max_input_chars=%s timeout_seconds=%s effective_dry_run=%s",
        run_id,
        provider,
        args.model,
        args.max_vacancies,
        args.max_terms_per_vacancy,
        args.llm_max_tokens,
        args.max_input_chars,
        args.timeout_seconds,
        effective_dry_run,
    )
    runtime_taxonomy = (
        load_merged_taxonomy(
            release_path=args.taxonomy_release_path,
            auto_path=args.taxonomy_auto_path,
        )
        if args.apply_runtime_taxonomy
        else None
    )

    extracted_rows: list[ExtractedTerm] = []
    processed = 0
    failed = 0
    checkpoint_every = max(0, args.checkpoint_every)

    if not effective_dry_run and args.overwrite_output:
        args.out_terms_jsonl.parent.mkdir(parents=True, exist_ok=True)
        args.llm_calls_log.parent.mkdir(parents=True, exist_ok=True)
        args.out_terms_jsonl.write_text("", encoding="utf-8")
        args.llm_calls_log.write_text("", encoding="utf-8")

    for meta, details in vacancies:
        raw = MergedVacancyRaw.from_sources(meta, details)
        vacancy = MergedVacancyNormalized.from_raw(raw)
        if not vacancy.referenznummer:
            continue
        response = None

        request = LLMChatRequest(
            provider=provider,
            base_url=args.base_url or "",
            api_key=args.api_key,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            temperature=0.0,
            max_tokens=args.llm_max_tokens,
            response_format={"type": "json_object", "schema": extraction_schema},
            messages=[
                {"role": "system", "content": _build_system_prompt(domain_list, args.max_terms_per_vacancy)},
                {"role": "user", "content": _build_user_prompt(vacancy, args.max_input_chars)},
            ],
            log_events=True,
        )

        try:
            response = chat_completion(request)
            payload = _extract_json_object(response.content)
            vacancy_domain_top, vacancy_domain_conf = _extract_vacancy_domain_top(payload.get("vacancy_domains"))
            raw_terms = payload.get("terms", [])
            if not isinstance(raw_terms, list):
                raw_terms = []
            vacancy_rows: list[ExtractedTerm] = []

            for term_item in raw_terms[: args.max_terms_per_vacancy]:
                if not isinstance(term_item, dict):
                    continue
                sanitized = _sanitize_term_record(
                    raw=term_item,
                    referenznummer=vacancy.referenznummer,
                    vacancy_domain_top=vacancy_domain_top,
                    vacancy_domain_confidence=vacancy_domain_conf,
                )
                if sanitized is not None:
                    before = {
                        "term": sanitized.term,
                        "term_key": sanitized.term_key,
                        "canonical": sanitized.canonical,
                        "category": sanitized.category,
                        "skill_class": sanitized.skill_class,
                    }
                    if runtime_taxonomy is not None:
                        sanitized = _apply_runtime_taxonomy_to_row(sanitized, runtime_taxonomy)
                    after = {
                        "term": sanitized.term,
                        "term_key": sanitized.term_key,
                        "canonical": sanitized.canonical,
                        "category": sanitized.category,
                        "skill_class": sanitized.skill_class,
                    }
                    _append_process_log(
                        args.process_log,
                        {
                            "event": "term_normalized",
                            "run_id": run_id,
                            "referenznummer": vacancy.referenznummer,
                            "before": before,
                            "after": after,
                            "facet": sanitized.facet,
                            "domain_hint": sanitized.domain_hint,
                        },
                    )
                    extracted_rows.append(sanitized)
                    vacancy_rows.append(sanitized)
            term_rows_payload = [
                {
                    "run_id": run_id,
                    "referenznummer": row.referenznummer,
                    "term": row.term,
                    "term_key": row.term_key,
                    "canonical": row.canonical,
                    "category": row.category,
                    "facet": row.facet,
                    "skill_class": row.skill_class,
                    "domain_hint": row.domain_hint,
                    "domain_confidence": row.domain_confidence,
                    "context_phrase": row.context_phrase,
                    "vacancy_domain_top": row.vacancy_domain_top,
                    "vacancy_domain_confidence": row.vacancy_domain_confidence,
                    "provider": provider,
                    "model": args.model,
                    "created_at": _now_utc_iso(),
                }
                for row in vacancy_rows
            ]

            processed += 1
            llm_meta_row = {
                "run_id": run_id,
                "status": "ok",
                "referenznummer": vacancy.referenznummer,
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
                "finish_reason": response.finish_reason,
                "elapsed_ms": response.elapsed_ms,
                "started_at": response.started_at,
                "usage": response.usage,
                "timings": response.timings,
            }
            if not effective_dry_run:
                _append_jsonl(args.out_terms_jsonl, term_rows_payload)
                _append_jsonl(args.llm_calls_log, [llm_meta_row])
                if checkpoint_every > 0 and processed % checkpoint_every == 0:
                    checkpoint = _build_aggregate(
                        extracted_rows=extracted_rows,
                        provider=provider,
                        model=args.model,
                        scanned_vacancies=scanned,
                        processed_vacancies=processed,
                        failed_vacancies=failed,
                    )
                    checkpoint["run_id"] = run_id
                    checkpoint["checkpoint"] = True
                    _write_json(args.out_aggregate_json, checkpoint)
        except Exception:
            failed += 1
            exc_type, exc_value, _ = sys.exc_info()
            _append_process_log(
                args.process_log,
                {
                    "event": "vacancy_failed",
                    "run_id": run_id,
                    "referenznummer": vacancy.referenznummer,
                    "error_type": exc_type.__name__ if exc_type else "Exception",
                    "error_message": str(exc_value)[:2000] if exc_value else "",
                },
            )
            LOGGER.exception("vacancy_failed run_id=%s referenznummer=%s", run_id, vacancy.referenznummer)
            if not effective_dry_run:
                error_row: dict[str, Any] = {
                    "run_id": run_id,
                    "status": "error",
                    "referenznummer": vacancy.referenznummer,
                    "provider": provider,
                    "model": args.model,
                    "error_type": exc_type.__name__ if exc_type else "Exception",
                    "error_message": str(exc_value)[:2000] if exc_value else "",
                    "created_at": _now_utc_iso(),
                }
                if response is not None:
                    error_row["response_finish_reason"] = response.finish_reason
                    error_row["response_id"] = response.response_id
                    error_row["response_content_preview"] = _content_preview(response.content)
                _append_jsonl(
                    args.llm_calls_log,
                    [error_row],
                )
            continue

    aggregate = _build_aggregate(
        extracted_rows=extracted_rows,
        provider=provider,
        model=args.model,
        scanned_vacancies=scanned,
        processed_vacancies=processed,
        failed_vacancies=failed,
    )
    aggregate["run_id"] = run_id
    aggregate["checkpoint"] = False

    if not effective_dry_run:
        _write_json(args.out_aggregate_json, aggregate)
    elif not args.allow_learning:
        print("learning_disabled_skip_write=true")

    _append_process_log(
        args.process_log,
        {
            "event": "run_finished",
            "run_id": run_id,
            "scanned": scanned,
            "processed": processed,
            "failed": failed,
            "term_rows": len(extracted_rows),
            "provider": provider,
            "model": args.model,
            "effective_dry_run": effective_dry_run,
        },
    )
    LOGGER.info(
        "run_finished run_id=%s scanned=%s processed=%s failed=%s term_rows=%s",
        run_id,
        scanned,
        processed,
        failed,
        len(extracted_rows),
    )

    print(
        f"DONE: scanned={scanned}, processed={processed}, failed={failed}, "
        f"term_rows={len(extracted_rows)}, provider={provider}, model={args.model}, "
        f"dry_run={effective_dry_run}, allow_learning={args.allow_learning}"
    )
    print(f"terms_jsonl={args.out_terms_jsonl}")
    print(f"aggregate_json={args.out_aggregate_json}")
    print(f"llm_calls_log={args.llm_calls_log}")
    print(f"process_log={args.process_log}")


if __name__ == "__main__":
    main()
