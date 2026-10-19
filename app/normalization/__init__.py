"""Shared normalization dictionary loaders."""

from .loader import (
    DEFAULT_SYNONYMS_AUTO_PATH,
    DEFAULT_SYNONYMS_RELEASE_PATH,
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    build_runtime_taxonomy,
    load_merged_normalization_dicts,
    load_merged_synonyms,
    load_merged_taxonomy,
    load_runtime_taxonomy,
)
from .runtime import (
    DEFAULT_CONTEXT_CONFIDENCE_THRESHOLD,
    NormalizationContext,
    NormalizationResolution,
    build_profile_normalization_context,
    normalize_term_key,
    build_vacancy_normalization_context,
    resolve_canonical_term,
)
from .synonyms_state import (
    build_synonym_state_document,
    build_synonyms_audit_event,
    ensure_synonyms_state_indexes,
    upsert_synonym_state,
    write_synonyms_audit_event,
)

__all__ = [
    "DEFAULT_SYNONYMS_AUTO_PATH",
    "DEFAULT_SYNONYMS_RELEASE_PATH",
    "DEFAULT_TAXONOMY_AUTO_PATH",
    "DEFAULT_TAXONOMY_RELEASE_PATH",
    "DEFAULT_CONTEXT_CONFIDENCE_THRESHOLD",
    "build_runtime_taxonomy",
    "build_profile_normalization_context",
    "build_vacancy_normalization_context",
    "load_merged_normalization_dicts",
    "load_merged_synonyms",
    "load_merged_taxonomy",
    "load_runtime_taxonomy",
    "NormalizationContext",
    "NormalizationResolution",
    "normalize_term_key",
    "resolve_canonical_term",
    "build_synonym_state_document",
    "build_synonyms_audit_event",
    "ensure_synonyms_state_indexes",
    "upsert_synonym_state",
    "write_synonyms_audit_event",
]
