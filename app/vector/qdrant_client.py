from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class QdrantSettings:
    """
    Нормализованные параметры подключения/коллекции Qdrant.

    Это отдельная структура, чтобы:
    - не таскать "сырой" dict по коду;
    - получать понятные ошибки сразу при валидации конфигурации.
    """

    url: str
    collection_name: str
    vector_size: int
    distance: str
    hnsw_m: int
    hnsw_ef_construct: int
    timeout_seconds: int


class QdrantRestClient:
    """Минимальный REST-клиент Qdrant через requests.Session."""

    def __init__(self, settings: QdrantSettings) -> None:
        self.settings = settings
        self._base_url = settings.url.rstrip("/")
        self._session = requests.Session()

    def healthcheck(self) -> bool:
        """
        Проверка доступности Qdrant.

        Используем `/collections`, потому что endpoint стабилен и не требует
        специальных прав.
        """
        response = self._session.get(
            f"{self._base_url}/collections", timeout=self.settings.timeout_seconds
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("status") == "ok"

    def collection_exists(self) -> bool:
        response = self._session.get(
            f"{self._base_url}/collections/{self.settings.collection_name}",
            timeout=self.settings.timeout_seconds,
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def create_collection(self) -> None:
        """
        Создание коллекции с HNSW-настройками.

        Почему важно сделать это явно:
        - одна и та же форма индекса во всех окружениях;
        - меньше "магии" и расхождений между машинами команды.
        """
        payload = {
            "vectors": {
                "size": self.settings.vector_size,
                "distance": self.settings.distance,
            },
            "hnsw_config": {
                "m": self.settings.hnsw_m,
                "ef_construct": self.settings.hnsw_ef_construct,
            },
        }
        response = self._session.put(
            f"{self._base_url}/collections/{self.settings.collection_name}",
            json=payload,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()

    def ensure_collection(self) -> str:
        """
        Гарантировать наличие коллекции.

        Возвращает:
        - `created`, если коллекция была создана;
        - `exists`, если коллекция уже была.
        """
        if self.collection_exists():
            return "exists"
        self.create_collection()
        return "created"


def parse_qdrant_settings(matching_cfg: dict[str, Any]) -> QdrantSettings:
    qdrant_cfg = matching_cfg.get("qdrant", {})
    embeddings_cfg = matching_cfg.get("embeddings", {})

    return QdrantSettings(
        url=str(qdrant_cfg.get("url", "http://127.0.0.1:6333")),
        collection_name=str(qdrant_cfg.get("collection_name", "job_chunks_v1")),
        vector_size=int(embeddings_cfg.get("vector_size", 1024)),
        distance=str(qdrant_cfg.get("distance", "Cosine")),
        hnsw_m=int(qdrant_cfg.get("hnsw_m", 16)),
        hnsw_ef_construct=int(qdrant_cfg.get("hnsw_ef_construct", 100)),
        timeout_seconds=int(qdrant_cfg.get("timeout_seconds", 10)),
    )

