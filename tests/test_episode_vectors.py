"""Offline chunking and cache behavior for project episode vectors."""

from pathlib import Path

import numpy as np
import pytest

from app.matching.episode_vectors import EpisodeVectors
from app.matching.intelligence_vectors import model_snapshot


MODEL_DIR = Path("models/embeddings")


class FakeCache:
    def __init__(self):
        self.values = {}
        self.reads = []
        self.writes = []

    def get(self, key):
        self.reads.append(key)
        return self.values.get(key)

    def put(self, key, value, kind=None):
        self.writes.append((key, kind))
        self.values[key] = value


class FakeEmbedder:
    def __init__(self, vector_factory=None):
        self.calls = []
        self.vector_factory = vector_factory or (lambda _text: [0.25] * 384)

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [self.vector_factory(text) for text in texts]


def make_vectorizer(cache=None, embedder=None):
    vectors = EpisodeVectors.__new__(EpisodeVectors)
    vectors.cache = cache or FakeCache()
    vectors.identity = {"model": "synthetic-minilm-identity"}
    vectors._embedder = embedder or FakeEmbedder()
    vectors.model_dir = Path("synthetic-model-dir")
    vectors.threads = 1
    vectors.counters = {"vectors_computed": 0, "vectors_reused": 0,
                        "chunks": 0, "max_chunk_tokens": 0, "llm_calls": 0}
    return vectors


def test_offline_tokenizer_chunks_preserve_long_unicode_text_within_budget():
    try:
        snapshot = model_snapshot(MODEL_DIR)
    except ValueError as exc:
        pytest.skip(str(exc))
    pytest.importorskip("fastembed")

    vectors = EpisodeVectors(FakeCache(), MODEL_DIR)
    text = ("Überprüfung der Schnittstellen – Grüße aus München; 中文测试, "
            "café, naïve, emoji 🚀 und CI/CD. " * 180)
    chunks = vectors.chunks(text)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(chunk.strip() for chunk in chunks)
    assert all(len(vectors.tokenizer.encode(chunk).ids) <= 128 for chunk in chunks)
    assert snapshot == vectors.snapshot


def test_embedding_cache_reuses_values_and_only_changed_text_recomputes():
    cache = FakeCache()
    embedder = FakeEmbedder()
    vectors = make_vectorizer(cache, embedder)

    first = vectors.embed(["synthetic alpha", "synthetic beta", "synthetic alpha"])
    assert first[0] == first[2]
    assert len(embedder.calls) == 1
    assert embedder.calls[0] == ["synthetic alpha", "synthetic beta"]
    assert vectors.counters["vectors_computed"] == 2
    assert len(cache.writes) == 2

    second = vectors.embed(["synthetic alpha", "synthetic beta"])
    assert second == first[:2]
    assert len(embedder.calls) == 1
    assert vectors.counters["vectors_reused"] == 2

    vectors.embed(["synthetic alpha", "synthetic changed"])
    assert len(embedder.calls) == 2
    assert embedder.calls[-1] == ["synthetic changed"]
    assert vectors.counters["vectors_computed"] == 3


@pytest.mark.parametrize("invalid_vector", [[0.0, 1.0], [float("nan")] * 384])
def test_invalid_embedding_vectors_are_rejected_without_caching(invalid_vector):
    cache = FakeCache()
    embedder = FakeEmbedder(lambda _text: invalid_vector)
    vectors = make_vectorizer(cache, embedder)

    with pytest.raises(ValueError, match="Invalid MiniLM vector"):
        vectors.embed(["synthetic invalid vector"])
    assert cache.writes == []
