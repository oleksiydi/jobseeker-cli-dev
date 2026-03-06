"""Shared normalization dictionary loaders."""

from .loader import (
    DEFAULT_SYNONYMS_AUTO_PATH,
    DEFAULT_SYNONYMS_RELEASE_PATH,
    DEFAULT_TAXONOMY_AUTO_PATH,
    DEFAULT_TAXONOMY_RELEASE_PATH,
    load_merged_normalization_dicts,
    load_merged_synonyms,
    load_merged_taxonomy,
)
from .runtime import normalize_term_key, resolve_canonical_term_and_category

__all__ = [
    "DEFAULT_SYNONYMS_AUTO_PATH",
    "DEFAULT_SYNONYMS_RELEASE_PATH",
    "DEFAULT_TAXONOMY_AUTO_PATH",
    "DEFAULT_TAXONOMY_RELEASE_PATH",
    "load_merged_normalization_dicts",
    "load_merged_synonyms",
    "load_merged_taxonomy",
    "normalize_term_key",
    "resolve_canonical_term_and_category",
]
