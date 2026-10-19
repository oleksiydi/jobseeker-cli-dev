"""Неизменные опорные данные ESCO/DKZ: чтение только полностью загруженных снимков."""

from __future__ import annotations

from typing import Any

from app.normalization.runtime import normalize_term_key


DEFAULT_ENTRIES_COLLECTION = "reference_entries"
DEFAULT_SOURCES_COLLECTION = "reference_sources"


class ReferenceStore:
    """Ищет название по справочникам, сохраняя источник и исходную запись.

    Совпадение поискового слова DKZ служит подсказкой для анализа, а не
    автоматическим доказательством, что два навыка являются синонимами.
    """

    def __init__(self, db: Any, storage: dict[str, Any] | None = None) -> None:
        storage = storage or {}
        self.entries = db[storage.get("entries_collection", DEFAULT_ENTRIES_COLLECTION)]
        self.sources = db[storage.get("sources_collection", DEFAULT_SOURCES_COLLECTION)]

    def lookup(self, term: str, *, source: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Возвращает исходные записи для точного нормализованного названия."""
        if limit <= 0:
            raise ValueError("lookup limit must be positive")
        key = normalize_term_key(term)
        if not key:
            return []
        selector = {"_id": source} if source else {}
        manifests = list(self.sources.find(selector))
        snapshots = [snapshot for manifest in manifests
                     for snapshot in manifest.get("active_snapshots", [])]
        if not snapshots:
            return []
        return list(self.entries.find({"snapshot_id": {"$in": snapshots}, "lookup_keys": key})
                    .sort([("source", 1), ("dataset", 1), ("ordinal", 1)]).limit(limit))
