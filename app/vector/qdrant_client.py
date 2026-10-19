from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any, Callable, TypeVar


T = TypeVar("T")


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
    max_retries: int
    retry_backoff_seconds: float


class QdrantRestClient:
    """
    Тонкий адаптер над официальным `qdrant-client` для нашего проекта.

    Почему оставляем адаптер:
    - бизнес-код не зависит от конкретной библиотеки напрямую;
    - проще тестировать и эволюционировать без переписывания pipeline-слоя.
    """

    def __init__(self, settings: QdrantSettings) -> None:
        self.settings = settings
        try:
            from qdrant_client import QdrantClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "qdrant-client is required. Install dependencies from requirements.txt"
            ) from exc

        self._client = QdrantClient(url=settings.url, timeout=settings.timeout_seconds)

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

    def _with_retry(self, operation_name: str, func: Callable[[], T]) -> T:
        attempts = max(1, 1 + int(self.settings.max_retries))
        base_backoff = max(0.0, float(self.settings.retry_backoff_seconds))

        for attempt in range(attempts):
            try:
                return func()
            except Exception as exc:
                if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
                    raise
                if attempt == attempts - 1:
                    raise RuntimeError(
                        f"Qdrant operation failed: {operation_name} after {attempts} attempts"
                    ) from exc
                sleep(base_backoff * (2**attempt))

        raise RuntimeError(f"Qdrant operation failed unexpectedly: {operation_name}")

    def healthcheck(self) -> bool:
        """
        Проверка доступности Qdrant.

        Делаем лёгкий вызов списка коллекций через официальный SDK.
        """

        self._with_retry("healthcheck", self._client.get_collections)
        return True

    def collection_exists(self) -> bool:
        collections = self._with_retry("collection_exists", self._client.get_collections)
        names = {collection.name for collection in collections.collections}
        return self.settings.collection_name in names

    def create_collection(self) -> None:
        """
        Создание коллекции с HNSW-настройками.

        Почему важно сделать это явно:
        - одна и та же форма индекса во всех окружениях;
        - меньше "магии" и расхождений между машинами команды.
        """
        from qdrant_client import models

        def _create() -> None:
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

        self._with_retry("create_collection", _create)

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
        from qdrant_client import models

        def _delete() -> None:
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

        self._with_retry("delete_points_by_referenznummer", _delete)

    def upsert_points(self, points: list[dict[str, Any]]) -> None:
        """Upsert chunk-точек в коллекцию."""
        if not points:
            return
        from qdrant_client import models

        point_structs = [
            models.PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
            for point in points
        ]

        def _upsert() -> None:
            self._client.upsert(
                collection_name=self.settings.collection_name,
                points=point_structs,
                wait=True,
            )

        self._with_retry("upsert_points", _upsert)

    def search_points(
        self,
        *,
        query_vector: list[float],
        limit: int,
        reference_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Выполнить vector search и вернуть унифицированный список hit-объектов.

        Формат результата:
        - `id`
        - `score`
        - `payload`
        """
        if not query_vector or limit <= 0:
            return []

        def _search() -> list[Any]:
            extra = {}
            if reference_ids is not None:
                from qdrant_client import models
                extra['query_filter'] = models.Filter(must=[models.FieldCondition(
                    key='referenznummer', match=models.MatchAny(any=reference_ids))])
            if hasattr(self._client, "query_points"):
                response = self._client.query_points(
                    collection_name=self.settings.collection_name,
                    query=query_vector,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                    **extra,
                )
                return getattr(response, "points", []) or []
            return self._client.search(
                collection_name=self.settings.collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
                **extra,
            )

        hits = self._with_retry("search_points", _search)
        result: list[dict[str, Any]] = []
        for hit in hits:
            result.append(
                {
                    "id": hit.id,
                    "score": float(hit.score),
                    "payload": hit.payload or {},
                }
            )
        return result

    def retrieve_points(self, point_ids: list[str]) -> list[dict[str, Any]]:
        """Read derived point hashes before computing/upserting changed vectors."""
        if not point_ids:
            return []
        records = self._with_retry('retrieve_points', lambda: self._client.retrieve(
            collection_name=self.settings.collection_name, ids=point_ids,
            with_payload=True, with_vectors=False))
        return [{'id': str(record.id), 'payload': record.payload or {}} for record in records]


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
        max_retries=int(qdrant_cfg.get("max_retries", 2)),
        retry_backoff_seconds=float(qdrant_cfg.get("retry_backoff_seconds", 0.5)),
    )
