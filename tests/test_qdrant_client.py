from __future__ import annotations

from app.vector.qdrant_client import parse_qdrant_settings


def test_parse_qdrant_settings_uses_matching_config_values() -> None:
    matching_cfg = {
        "embeddings": {"vector_size": 768},
        "qdrant": {
            "url": "http://localhost:6333",
            "collection_name": "jobs_test",
            "distance": "Cosine",
            "hnsw_m": 24,
            "hnsw_ef_construct": 120,
            "timeout_seconds": 7,
        },
    }

    settings = parse_qdrant_settings(matching_cfg)

    assert settings.url == "http://localhost:6333"
    assert settings.collection_name == "jobs_test"
    assert settings.vector_size == 768
    assert settings.distance == "Cosine"
    assert settings.hnsw_m == 24
    assert settings.hnsw_ef_construct == 120
    assert settings.timeout_seconds == 7

