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
    """
    Тонкий адаптер над официальным `qdrant-client` для нашего проекта.

    Почему оставляем адаптер:
    - бизнес-код не зависит от конкретной библиотеки напрямую;
    - проще тестировать и эволюционировать без переписывания pipeline-слоя.
    """

    def __init__(self, settings: QdrantSettings) -> None:
        self.settings = settings
        self._base_url = settings.url.rstrip("/")
        try:
            from qdrant_client import QdrantClient
        except ModuleNotFoundError:
            # Fallback для офлайн/ограниченных окружений: сохраняем работоспособность
            # через прямой REST. При установленном SDK всегда используется стандартный путь.
            self._sdk_enabled = False
            self._client = None
            self._session = requests.Session()
            return

        self._sdk_enabled = True
        self._client = QdrantClient(url=settings.url, timeout=settings.timeout_seconds)
        self._session = None

    @staticmethod
    def _distance_enum(distance: str) -> Any:
        from qdrant_client import models

        normalized = distance.strip().lower()
        mapping = {
            "cosine": models.Distance.COSINE,
            "dot": models.Distance.DOT,
            "euclid": models.Distance.EUCLID,
            "manhattan": models.Distance.MANHATTAN,
        }
        if normalized not in mapping:
            raise ValueError(f"Unsupported Qdrant distance: {distance}")
        return mapping[normalized]

    def healthcheck(self) -> bool:
        """
        Проверка доступности Qdrant.

        Делаем лёгкий вызов списка коллекций через официальный SDK.
        """
        if self._sdk_enabled:
            self._client.get_collections()
            return True

        response = self._session.get(
            f"{self._base_url}/collections",
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("status") == "ok"

    def collection_exists(self) -> bool:
        if self._sdk_enabled:
            collections = self._client.get_collections()
            names = {collection.name for collection in collections.collections}
            return self.settings.collection_name in names

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
        if self._sdk_enabled:
            from qdrant_client import models

            self._client.create_collection(
                collection_name=self.settings.collection_name,
                vectors_config=models.VectorParams(
                    size=self.settings.vector_size,
                    distance=self._distance_enum(self.settings.distance),
                ),
                hnsw_config=models.HnswConfigDiff(
                    m=self.settings.hnsw_m,
                    ef_construct=self.settings.hnsw_ef_construct,
                ),
            )
            return

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

    def delete_points_by_referenznummer(self, referenznummer: str) -> None:
        """
        Удалить все точки вакансии перед reindex.

        Почему удаляем сначала:
        - при изменении числа чанков не остается "хвостов" старых точек;
        - состояние индекса остается детерминированным.
        """
        if self._sdk_enabled:
            from qdrant_client import models

            self._client.delete(
                collection_name=self.settings.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="referenznummer",
                                match=models.MatchValue(value=referenznummer),
                            )
                        ]
                    )
                ),
                wait=True,
            )
            return

        payload = {
            "filter": {
                "must": [
                    {
                        "key": "referenznummer",
                        "match": {"value": referenznummer},
                    }
                ]
            }
        }
        response = self._session.post(
            f"{self._base_url}/collections/{self.settings.collection_name}/points/delete?wait=true",
            json=payload,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()

    def upsert_points(self, points: list[dict[str, Any]]) -> None:
        """Upsert chunk-точек в коллекцию."""
        if not points:
            return
        if self._sdk_enabled:
            from qdrant_client import models

            point_structs = [
                models.PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
                for point in points
            ]
            self._client.upsert(
                collection_name=self.settings.collection_name,
                points=point_structs,
                wait=True,
            )
            return

        payload = {"points": points}
        response = self._session.put(
            f"{self._base_url}/collections/{self.settings.collection_name}/points?wait=true",
            json=payload,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()


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
