#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.llm.client import LLMChatRequest, LLMChatResponse, chat_completion
from app.matching.models import MergedVacancyNormalized

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
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


def content_preview(value: str, max_chars: int = 1200) -> str:
    cleaned = value.replace("\r", " ").replace("\n", " ").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + " ...<truncated>"


def load_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Schema must be JSON object: {path}")
    return payload


def build_system_prompt(domain_list: list[str], max_terms_per_vacancy: int) -> str:
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


def build_user_prompt(vacancy: MergedVacancyNormalized, max_input_chars: int) -> str:
    canonical = vacancy.canonical_text()
    clipped = canonical[:max_input_chars]
    return (
        "Extract term/domain candidates for taxonomy bootstrap.\n"
        "Output JSON with keys: vacancy_domains, terms.\n"
        "For each term include: term, category, facet, domain_hint, domain_confidence, context_phrase.\n\n"
        f"Vacancy text:\n{clipped}"
    )


def request_extraction(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    timeout_seconds: int,
    llm_max_tokens: int,
    extraction_schema: dict[str, Any],
    domain_list: list[str],
    max_terms_per_vacancy: int,
    max_input_chars: int,
    vacancy: MergedVacancyNormalized,
) -> tuple[dict[str, Any], LLMChatResponse]:
    request = LLMChatRequest(
        provider=provider,
        base_url=base_url or "",
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        temperature=0.0,
        max_tokens=llm_max_tokens,
        response_format={"type": "json_object", "schema": extraction_schema},
        messages=[
            {"role": "system", "content": build_system_prompt(domain_list, max_terms_per_vacancy)},
            {"role": "user", "content": build_user_prompt(vacancy, max_input_chars)},
        ],
        log_events=True,
    )
    response = chat_completion(request)
    payload = extract_json_object(response.content)
    return payload, response
