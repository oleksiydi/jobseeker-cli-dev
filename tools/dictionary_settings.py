"""Настройки автономного словаря: отдельные модели, независимые от анализа вакансий."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from app.core.config import load_app_config

DEFAULTS = {
    "extraction_provider": "openrouter", "extraction_model": "mistralai/mistral-nemo",
    "review_provider": "openrouter", "review_model": "xiaomi/mimo-v2.6-flash",
    "escalation_provider": "openrouter", "escalation_model": "xiaomi/mimo-v2.6-pro",
    "max_vacancies": 0,
    "reference_first": True,
}
ENV_FIELDS = {
    "extraction_provider": "TAXONOMY_LLM_PROVIDER", "extraction_model": "TAXONOMY_LLM_MODEL",
    "review_provider": "SYNONYMS_REVIEW_PROVIDER", "review_model": "SYNONYMS_REVIEW_MODEL",
    "escalation_provider": "SYNONYMS_ESCALATION_PROVIDER",
    "escalation_model": "SYNONYMS_ESCALATION_MODEL",
}


def dictionary_settings() -> dict[str, Any]:
    load_dotenv()
    result = {**DEFAULTS, **load_app_config().get("dictionary_pipeline", {})}
    for key, variable in ENV_FIELDS.items():
        if os.getenv(variable):
            result[key] = os.environ[variable]
    flag = os.getenv('DICTIONARY_REFERENCE_FIRST', '').strip().lower()
    if flag:
        if flag not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValueError('DICTIONARY_REFERENCE_FIRST must be true or false')
        result['reference_first'] = flag in {'true', '1', 'yes'}
    if type(result['reference_first']) is not bool:
        raise ValueError('dictionary_pipeline.reference_first must be boolean')
    return result
