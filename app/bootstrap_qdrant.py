from __future__ import annotations

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.core.matching_config import resolve_matching_config
from app.vector.qdrant_client import QdrantRestClient, parse_qdrant_settings


logger = get_logger(__name__)


def main() -> int:
    """
    Bootstrap Qdrant collection for local matching pipeline.

    Команда безопасна для повторного запуска:
    - если коллекция уже есть, просто сообщает `exists`;
    - если нет, создаёт её с параметрами из конфига.
    """
    cfg = load_app_config()
    matching_cfg = resolve_matching_config(cfg)
    settings = parse_qdrant_settings(matching_cfg)

    client = QdrantRestClient(settings)
    is_healthy = client.healthcheck()
    if not is_healthy:
        logger.error("Qdrant healthcheck failed for url=%s", settings.url)
        return 1

    result = client.ensure_collection()
    logger.info(
        "Qdrant bootstrap finished: result=%s collection=%s url=%s vector_size=%s distance=%s",
        result,
        settings.collection_name,
        settings.url,
        settings.vector_size,
        settings.distance,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

