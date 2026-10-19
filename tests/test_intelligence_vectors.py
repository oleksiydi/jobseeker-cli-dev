from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.matching import intelligence_vectors as vectors


class FakeCache:
    def __init__(self):
        self.values = {}
        self.put_calls = []
        self.get_calls = []

    def get(self, key):
        self.get_calls.append(key)
        value = self.values.get(key)
        return deepcopy(value)

    def put(self, key, payload, kind="evidence"):
        self.put_calls.append((key, kind))
        self.values.setdefault(key, deepcopy(payload))


class FakeQdrant:
    def __init__(self, _settings):
        self.points = {}
        self.upsert_calls = []
        self.search_calls = []
        self.ensure_calls = 0

    def ensure_collection(self):
        self.ensure_calls += 1
        return "exists"

    def retrieve_points(self, point_ids):
        return [{"id": point_id, "payload": deepcopy(self.points[point_id]["payload"])}
                for point_id in point_ids if point_id in self.points]

    def upsert_points(self, points):
        self.upsert_calls.append(deepcopy(points))
        for point in points:
            self.points[point["id"]] = deepcopy(point)

    def search_points(self, *, query_vector, limit, reference_ids=None):
        selected = list(reference_ids or [])
        self.search_calls.append({"vector": list(query_vector), "limit": limit, "reference_ids": selected})
        return [{"id": ref, "score": 0.8, "payload": {"referenznummer": ref}}
                for ref in selected[:limit]]


class FakeEmbedder:
    instances = []

    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.__class__.instances.append(self)

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text)), float(index + 1)] for index, text in enumerate(texts)]


def _make_index(monkeypatch, *, cache=None, collection="job_intelligence_v1",
                legacy_collection="job_chunks_v1", identity=None, qdrant=None):
    cache = cache or FakeCache()
    qdrant = qdrant or FakeQdrant
    monkeypatch.setattr(vectors, "model_identity", lambda _path: deepcopy(identity or {
        "name": vectors.MODEL, "revision": "model-a", "files_sha256": "sha-a", "dimensions": 384,
    }))
    monkeypatch.setattr(vectors, "model_snapshot", lambda path: Path(path) / "snapshot")
    monkeypatch.setattr(vectors, "FastEmbedTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(vectors, "QdrantRestClient", qdrant)
    monkeypatch.setattr(vectors, "parse_qdrant_settings", lambda config: config)
    matching = {"qdrant": {"collection_name": legacy_collection, "url": "http://qdrant.invalid"}}
    settings = {"vector_collection": collection, "embedding_batch_size": 8, "embedding_threads": 1}
    index = vectors.SemanticIndex(cache, matching, settings, Path("/models"))
    return index, cache


def _jobs():
    return [
        {"ref": "job-1", "title": "Data analyst", "role": "Analytics"},
        {"ref": "job-2", "title": "Platform engineer", "role": "Infrastructure"},
    ]


def _profile():
    return {"target_roles": ["Data analyst", "Platform engineer"],
            "skills": [{"label": "Python"}, {"label": "SQL"}]}


def test_repeated_scoring_reuses_job_and_query_vectors_without_qdrant_upserts(monkeypatch):
    FakeEmbedder.instances.clear()
    qdrant = FakeQdrant(None)
    index, cache = _make_index(monkeypatch, qdrant=lambda _settings: qdrant)

    first = index.score(_jobs(), _profile(), query="data platform roles")
    first_embedding_calls = deepcopy(FakeEmbedder.instances[0].calls)
    first_cache_keys = list(cache.values)
    first_upserts = len(qdrant.upsert_calls)
    second = index.score(_jobs(), _profile(), query="data platform roles")

    assert set(first) == {"job-1", "job-2"}
    assert second == first
    assert len(FakeEmbedder.instances) == 1
    assert FakeEmbedder.instances[0].calls == first_embedding_calls
    assert len(qdrant.upsert_calls) == first_upserts == 1
    assert len(qdrant.upsert_calls[0]) == 2
    assert list(cache.values) == first_cache_keys
    assert index.counters["vectors_reused"] == 2
    assert index.counters["query_vectors_reused"] > 0
    assert index.counters["qdrant_upserts"] == 2
    assert all(call["reference_ids"] == ["job-1", "job-2"] for call in qdrant.search_calls)


def test_model_identity_change_invalidates_stored_job_and_query_vectors(monkeypatch):
    FakeEmbedder.instances.clear()
    qdrant = FakeQdrant(None)
    cache = FakeCache()
    current_identity = {"name": vectors.MODEL, "revision": "rev-1", "files_sha256": "hash-1"}
    monkeypatch.setattr(vectors, "model_identity", lambda _path: deepcopy(current_identity))
    monkeypatch.setattr(vectors, "model_snapshot", lambda path: Path(path) / "snapshot")
    monkeypatch.setattr(vectors, "FastEmbedTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(vectors, "QdrantRestClient", lambda _settings: qdrant)
    monkeypatch.setattr(vectors, "parse_qdrant_settings", lambda config: config)
    matching = {"qdrant": {"collection_name": "job_chunks_v1"}}
    settings = {"vector_collection": "job_intelligence_v1", "embedding_batch_size": 8,
                "embedding_threads": 1}

    first = vectors.SemanticIndex(cache, matching, settings, Path("/models"))
    first.score(_jobs(), _profile())
    first_cache_keys = set(cache.values)
    first_embedded = sum(map(len, FakeEmbedder.instances[0].calls))
    first_upsert_count = len(qdrant.upsert_calls)

    current_identity.update(revision="rev-2", files_sha256="hash-2")
    second = vectors.SemanticIndex(cache, matching, settings, Path("/models"))
    second.score(_jobs(), _profile())

    assert len(cache.values) == 2
    assert len(set(cache.values) - first_cache_keys) == 1
    assert len(first_cache_keys - set(cache.values)) == 0
    assert len(qdrant.upsert_calls) == first_upsert_count + 1
    assert second.counters["vectors_reused"] == 0
    assert second.counters["qdrant_upserts"] == 2
    assert sum(map(len, FakeEmbedder.instances[1].calls)) > 0
    assert first_embedded > 0


def test_semantic_index_refuses_to_reuse_legacy_chunk_collection(monkeypatch):
    FakeQdrant.instances = []
    monkeypatch.setattr(vectors, "model_identity", lambda _path: {"revision": "local"})

    with pytest.raises(ValueError, match="must not overwrite"):
        vectors.SemanticIndex(
            FakeCache(),
            {"qdrant": {"collection_name": "job_chunks_v1"}},
            {"vector_collection": "job_chunks_v1", "embedding_batch_size": 4,
             "embedding_threads": 1},
            Path("/models"),
        )


def test_search_is_filtered_to_selected_vacancy_refs(monkeypatch):
    qdrant = FakeQdrant(None)
    index, _cache = _make_index(monkeypatch, qdrant=lambda _settings: qdrant)
    selected = [_jobs()[0]]

    scores = index.score(selected, _profile())

    assert scores == {"job-1": 0.8}
    assert qdrant.search_calls
    assert all(call["reference_ids"] == ["job-1"] for call in qdrant.search_calls)
