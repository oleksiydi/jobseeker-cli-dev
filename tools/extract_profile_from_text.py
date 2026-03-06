#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract a runtime-compatible profile JSON from raw resume text via local llama.cpp.

This tool is intentionally kept separate from production pipelines:
- it enables dry-run evaluation before integration;
- it validates and normalizes output into the fields used by app.search_jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# При запуске как `python tools/...` добавляем корень репозитория в sys.path,
# чтобы импорты `app.*` работали независимо от текущей рабочей директории.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.llm.client import LLMChatRequest, chat_completion
from app.llm.factory import resolve_provider_name
from app.normalization.loader import (
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    load_merged_taxonomy,
)
from app.normalization.runtime import resolve_canonical_term_and_category

RUNTIME_PROFILE_FIELDS = (
    "key",
    "target_role",
    "summary",
    "skills",
    "tools",
    "stack",
    "experience",
    "seniority",
    "industries",
    "locations",
    "languages",
)

SENIORITY_ALLOWED = {"junior", "mid", "senior", "lead"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s()./-]{6,}\d)")
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _sanitize_contact_data(text: str) -> str:
    clean = EMAIL_RE.sub("", text)
    clean = PHONE_RE.sub("", clean)
    return _collapse_whitespace(clean)


def _normalize_string(value: Any) -> str:
    if value is None:
        return ""
    return _sanitize_contact_data(str(value))


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    items: list[Any]
    if isinstance(value, list):
        items = value
    else:
        items = [value]

    dedup: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_string(item)
        if not text:
            continue
        marker = text.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        dedup.append(text)
    return dedup


def _ensure_taxonomy_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "aliases": {},
        "categories": {"skills": [], "tools": [], "stack": []},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _apply_taxonomy(
    profile: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    unknown_terms: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    buckets: dict[str, list[str]] = {"skills": [], "tools": [], "stack": []}
    for field in ("skills", "tools", "stack"):
        for term in profile.get(field, []):
            canonical, target_field = resolve_canonical_term_and_category(
                term=term,
                source_category=field,
                taxonomy=taxonomy,
            )
            canonical = _normalize_string(canonical)
            if not canonical:
                continue

            if unknown_terms is not None:
                taxonomy_aliases = taxonomy.get("aliases", {})
                taxonomy_categories = taxonomy.get("categories", {})
                term_key = term.strip().lower().replace("_", " ").replace("-", " ")
                term_key = re.sub(r"\s+", " ", term_key).strip()
                has_alias = term_key in taxonomy_aliases
                category_known = any(canonical in taxonomy_categories.get(cat, set()) for cat in ("skills", "tools", "stack"))
                if not has_alias and not category_known:
                    unknown_terms.append(
                        {
                            "term": canonical,
                            "source_field": field,
                            "context": "",
                        }
                    )

            buckets[target_field].append(canonical)

    for field in ("skills", "tools", "stack"):
        profile[field] = _normalize_list(buckets[field])
    return profile


def _normalize_seniority(value: Any) -> str:
    raw = _normalize_string(value).casefold()
    if raw in SENIORITY_ALLOWED:
        return raw
    aliases = {
        "jr": "junior",
        "entry": "junior",
        "middle": "mid",
        "intermediate": "mid",
        "sr": "senior",
        "principal": "lead",
    }
    if raw in aliases:
        return aliases[raw]
    return "mid"


def normalize_runtime_profile(
    raw: dict[str, Any],
    *,
    profile_key: str,
    taxonomy: dict[str, Any] | None = None,
    unknown_terms: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    # Сводим ответ модели к стабильному runtime-формату для текущего search-контура.
    normalized: dict[str, Any] = {
        "key": _normalize_string(raw.get("key")) or profile_key,
        "target_role": _normalize_string(raw.get("target_role")),
        "summary": _normalize_string(raw.get("summary")),
        "skills": _normalize_list(raw.get("skills")),
        "tools": _normalize_list(raw.get("tools")),
        "stack": _normalize_list(raw.get("stack")),
        "experience": _normalize_string(raw.get("experience")),
        "seniority": _normalize_seniority(raw.get("seniority")),
        "industries": _normalize_list(raw.get("industries")),
        "locations": _normalize_list(raw.get("locations")),
        "languages": _normalize_list(raw.get("languages")),
    }
    if taxonomy is not None:
        normalized = _apply_taxonomy(normalized, taxonomy=taxonomy, unknown_terms=unknown_terms)
    return normalized


def validate_runtime_profile(profile: dict[str, Any]) -> None:
    missing = [field for field in RUNTIME_PROFILE_FIELDS if field not in profile]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    list_fields = {"skills", "tools", "stack", "industries", "locations", "languages"}
    for field in RUNTIME_PROFILE_FIELDS:
        value = profile[field]
        if field in list_fields:
            if not isinstance(value, list):
                raise ValueError(f"Field '{field}' must be a list.")
            if any(not isinstance(item, str) for item in value):
                raise ValueError(f"Field '{field}' must contain only strings.")
        else:
            if not isinstance(value, str):
                raise ValueError(f"Field '{field}' must be a string.")

    seniority = profile["seniority"].casefold()
    if seniority not in SENIORITY_ALLOWED:
        raise ValueError("Field 'seniority' must be one of: junior|mid|senior|lead.")


def validate_profile_content(profile: dict[str, Any]) -> None:
    """
    Дополнительный quality-gate для dry-run:
    профиль должен быть не только валидным по типам, но и содержательным.
    """
    if not profile["target_role"].strip():
        raise ValueError("Quality check failed: target_role is empty.")
    if not profile["summary"].strip():
        raise ValueError("Quality check failed: summary is empty.")

    tech_items = len(profile["skills"]) + len(profile["tools"]) + len(profile["stack"])
    if tech_items < 3:
        raise ValueError(
            "Quality check failed: too few technical items "
            f"(skills+tools+stack={tech_items}, expected >=3)."
        )
    if len(profile["languages"]) == 0:
        raise ValueError("Quality check failed: languages is empty.")


def build_system_prompt(*, strict_mode: bool = False) -> str:
    base = (
        "You are a strict resume parser. "
        "Return ONLY one valid JSON object and nothing else. "
        "Do not use markdown. "
        "Do not include contacts (email, phone, links). "
        "Do not invent facts. "
        "You MUST extract available facts from the resume text into the output fields."
    )
    if not strict_mode:
        return base
    return (
        base
        + " Do NOT return an empty object. "
        + "If data exists, required fields must be non-empty."
    )


def build_user_prompt(*, resume_text: str, profile_key: str, strict_mode: bool = False) -> str:
    quality_line = "- if field missing, use empty string or empty array\n"
    if strict_mode:
        quality_line = (
            "- if field missing, use empty string or empty array\n"
            "- NEVER return {} and NEVER keep target_role/summary empty when work experience exists\n"
            "- when technologies are present, extract at least 5 items into skills/tools/stack\n"
        )
    return (
        "/no_think\n"
        "Convert the resume text into a runtime profile JSON with EXACT keys:\n"
        "{\n"
        '  "key": "string",\n'
        '  "target_role": "string",\n'
        '  "summary": "string",\n'
        '  "skills": ["string"],\n'
        '  "tools": ["string"],\n'
        '  "stack": ["string"],\n'
        '  "experience": "string",\n'
        '  "seniority": "junior|mid|senior|lead",\n'
        '  "industries": ["string"],\n'
        '  "locations": ["string"],\n'
        '  "languages": ["string"]\n'
        "}\n"
        f'Rules:\n- set key="{profile_key}"\n'
        "- output only JSON object\n"
        f"{quality_line}"
        "- NEVER leave fields empty when corresponding data exists in the resume\n"
        "- target_role: infer from strongest recent experience and repeated responsibilities\n"
        "- summary: 2-4 concise sentences from real facts in text\n"
        "- skills/tools/stack: extract concrete technologies and methods from text lists and role descriptions\n"
        "- languages: extract from language section\n"
        "- locations: include current location and/or work locations mentioned in text\n"
        "- keep concise and factual\n\n"
        "RESUME TEXT:\n"
        f"{resume_text}"
    )


def _extract_text_content(response_json: dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected LLM response format (choices[0].message.content missing).") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content is empty.")
    return content.strip()


def _parse_json_from_llm_output(content: str) -> dict[str, Any]:
    payload = content
    fenced = JSON_BLOCK_RE.search(content)
    if fenced:
        payload = fenced.group(1).strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM output is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object.")
    return parsed


def build_response_schema() -> dict[str, Any]:
    list_field = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(RUNTIME_PROFILE_FIELDS),
        "properties": {
            "key": {"type": "string"},
            "target_role": {"type": "string"},
            "summary": {"type": "string"},
            "skills": list_field,
            "tools": list_field,
            "stack": list_field,
            "experience": {"type": "string"},
            "seniority": {"type": "string", "enum": sorted(SENIORITY_ALLOWED)},
            "industries": list_field,
            "locations": list_field,
            "languages": list_field,
        },
    }


def request_profile_from_llm(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
    resume_text: str,
    profile_key: str,
    timeout_seconds: int,
    temperature: float,
    max_tokens: int,
    strict_prompt: bool,
    disable_thinking: bool,
    use_schema_constraint: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "type": "json_object",
        "schema": build_response_schema(),
    }
    if use_schema_constraint:
        response_format = payload
    else:
        response_format = {"type": "json_object"}

    request = LLMChatRequest(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        chat_template_kwargs={"enable_thinking": False} if disable_thinking else None,
        messages=[
            {"role": "system", "content": build_system_prompt(strict_mode=strict_prompt)},
            {
                "role": "user",
                "content": build_user_prompt(
                    resume_text=resume_text,
                    profile_key=profile_key,
                    strict_mode=strict_prompt,
                ),
            },
        ],
    )
    response = chat_completion(request)
    parsed = _parse_json_from_llm_output(response.content)

    call_meta: dict[str, Any] = {
        "started_at": response.started_at,
        "elapsed_ms": response.elapsed_ms,
        "http_status": 200,
        "provider": response.provider,
        "model": response.model,
        "response_id": response.response_id,
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "timings": response.timings,
    }
    return parsed, call_meta


def _append_unknown_terms(path: Path, terms: list[dict[str, str]]) -> int:
    if not terms:
        return 0

    dedup: dict[tuple[str, str], dict[str, str]] = {}
    for item in terms:
        term = _normalize_string(item.get("term", ""))
        source_field = _normalize_string(item.get("source_field", "")).lower()
        if not term or source_field not in {"skills", "tools", "stack"}:
            continue
        key = (term.casefold(), source_field)
        dedup[key] = {"term": term, "source_field": source_field, "context": item.get("context", "")}

    if not dedup:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for item in dedup.values():
            payload = {
                "term": item["term"],
                "source_field": item["source_field"],
                "context": item.get("context", ""),
                "created_at": now,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return len(dedup)


def _append_llm_call_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract runtime profile JSON from raw resume text via local llama.cpp."
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="Path to input resume text file.")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("conf/profiles/test.generated.profile.json"),
        help="Output JSON file path.",
    )
    parser.add_argument("--profile-key", default="default", help="Profile key to write into output.")
    parser.add_argument(
        "--provider",
        default=os.getenv("LLM_PROVIDER", "llama_cpp"),
        help="LLM provider: llama_cpp | openai.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional LLM base URL override (provider-specific).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key override for external provider.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLAMA_CPP_MODEL_FILE", "Qwen3-8B-Q4_K_M.gguf"),
        help="Model name used in /v1/chat/completions.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("PROFILE_NORMALIZATION_TIMEOUT_SECONDS", "3600")),
        help="HTTP timeout in seconds for long offline profile extraction runs.",
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=700,
        help="Upper bound for generated completion tokens.",
    )
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=120_000,
        help="Safety cap for very long text files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print normalized JSON to stdout without writing output file.",
    )
    parser.add_argument(
        "--allow-empty-content",
        action="store_true",
        help="Disable strict content quality gate (for debugging prompt behavior).",
    )
    parser.add_argument(
        "--taxonomy-path",
        type=Path,
        default=None,
        help="Deprecated alias for --taxonomy-auto-path (kept for backward compatibility).",
    )
    parser.add_argument(
        "--taxonomy-release-path",
        type=Path,
        default=DEFAULT_TAXONOMY_RELEASE_PATH,
        help="Path to release taxonomy JSON layer.",
    )
    parser.add_argument(
        "--taxonomy-auto-path",
        type=Path,
        default=DEFAULT_TAXONOMY_AUTO_PATH,
        help="Path to auto taxonomy JSON layer.",
    )
    parser.add_argument(
        "--unknown-terms-path",
        type=Path,
        default=Path("logs/normalization/unknown_terms.jsonl"),
        help="JSONL file where unknown terms are appended automatically.",
    )
    parser.add_argument(
        "--disable-unknown-terms-log",
        action="store_true",
        help="Disable automatic append of unknown terms to JSONL log.",
    )
    parser.add_argument(
        "--llm-calls-log",
        type=Path,
        default=None,
        help="Optional JSONL path to append per-call LLM timings/usage metadata.",
    )
    parser.add_argument(
        "--retry-on-empty",
        type=int,
        default=1,
        help="Retry count when model returns empty/invalid profile content.",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Enable model thinking mode (disabled by default for structured extraction).",
    )
    parser.add_argument(
        "--no-schema-constraint",
        action="store_true",
        help="Use plain json_object response_format without JSON schema constraint.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")
    raw_text = args.input.read_text(encoding="utf-8")
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError(f"Input file is empty: {args.input}")
    if len(raw_text) > args.max_input_chars:
        raise ValueError(
            f"Input text exceeds max length ({len(raw_text)} > {args.max_input_chars}). "
            "Increase --max-input-chars if intentional."
        )
    if args.max_tokens <= 0:
        raise ValueError("--max-tokens must be > 0")
    if args.retry_on_empty < 0:
        raise ValueError("--retry-on-empty must be >= 0")
    provider_name = resolve_provider_name(args.provider)
    explicit_base_url = args.base_url if args.base_url else None
    explicit_api_key = args.api_key if args.api_key else None
    taxonomy_auto_path = args.taxonomy_path or args.taxonomy_auto_path
    _ensure_taxonomy_file(taxonomy_auto_path)
    taxonomy = load_merged_taxonomy(
        release_path=args.taxonomy_release_path,
        auto_path=taxonomy_auto_path,
    )
    retries_total = args.retry_on_empty
    profile: dict[str, Any] | None = None
    unknown_terms: list[dict[str, str]] = []
    last_error: Exception | None = None

    for attempt in range(retries_total + 1):
        strict_prompt = attempt > 0
        try:
            llm_raw_profile, call_meta = request_profile_from_llm(
                provider=provider_name,
                base_url=explicit_base_url or "",
                api_key=explicit_api_key,
                model=args.model,
                resume_text=raw_text,
                profile_key=args.profile_key,
                timeout_seconds=args.timeout_seconds,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                strict_prompt=strict_prompt,
                disable_thinking=not args.enable_thinking,
                use_schema_constraint=not args.no_schema_constraint,
            )
            if args.llm_calls_log is not None:
                call_meta.update(
                    {
                        "attempt": attempt + 1,
                        "attempt_total": retries_total + 1,
                        "strict_prompt": strict_prompt,
                        "disable_thinking": not args.enable_thinking,
                        "schema_constraint": not args.no_schema_constraint,
                    }
                )
                _append_llm_call_log(args.llm_calls_log, call_meta)
            attempt_unknown_terms: list[dict[str, str]] = []
            attempt_profile = normalize_runtime_profile(
                llm_raw_profile,
                profile_key=args.profile_key,
                taxonomy=taxonomy,
                unknown_terms=attempt_unknown_terms,
            )
            validate_runtime_profile(attempt_profile)
            if not args.allow_empty_content:
                validate_profile_content(attempt_profile)
            profile = attempt_profile
            unknown_terms = attempt_unknown_terms
            break
        except (ValueError, requests.RequestException) as exc:
            last_error = exc
            if attempt >= retries_total:
                raise
            print(
                f"WARN: extraction attempt {attempt + 1}/{retries_total + 1} failed: {exc}. Retrying...",
                file=sys.stderr,
            )

    if profile is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("Profile extraction failed with unknown error.")

    serialized = json.dumps(profile, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(serialized)
        unknown_count = 0
        if not args.disable_unknown_terms_log:
            unknown_count = _append_unknown_terms(args.unknown_terms_path, unknown_terms)
        print(
            "DRY-RUN: profile extracted and validated. "
            f"target_role='{profile['target_role']}', skills={len(profile['skills'])}, "
            f"unknown_terms_logged={unknown_count}"
        )
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    unknown_count = 0
    if not args.disable_unknown_terms_log:
        unknown_count = _append_unknown_terms(args.unknown_terms_path, unknown_terms)
    print(
        "OK: profile extracted and saved. "
        f"output={args.output}, target_role='{profile['target_role']}', skills={len(profile['skills'])}, "
        f"unknown_terms_logged={unknown_count}"
    )


if __name__ == "__main__":
    main()
