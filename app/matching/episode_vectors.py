"""Offline MiniLM vectors for short evidence units, cached in existing Mongo."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.matching.intelligence_data import digest
from app.matching.intelligence_vectors import MODEL, model_identity, model_snapshot
from app.vector.embedder import FastEmbedSettings, FastEmbedTextEmbedder


class EpisodeVectors:
    """Reuse installed CPU weights; never download or call a remote model."""

    def __init__(self, cache, model_dir: Path, threads: int = 2):
        from fastembed.common.preprocessor_utils import load_tokenizer

        self.cache = cache
        snapshot = model_snapshot(model_dir)
        self.identity = {**model_identity(model_dir), 'input': 'short requirement / individual project fact',
                         'version': 2, 'chunk_tokens': 128, 'aggregation': 'max-fact-cosine'}
        self.tokenizer, _ = load_tokenizer(snapshot)
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        self._embedder = None
        self.model_dir, self.snapshot, self.threads = model_dir, snapshot, threads
        self.counters = {'vectors_computed': 0, 'vectors_reused': 0,
                         'chunks': 0, 'max_chunk_tokens': 0, 'llm_calls': 0}

    def chunks(self, text: str) -> list[str]:
        """Split at actual tokenizer offsets, retaining every non-space character."""
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = tokens.offsets
        if not offsets:
            return []
        chunks, left = [], 0
        # Two special tokens fit within the explicit 128-token input budget.
        for start in range(0, len(offsets), 126):
            stop = min(start + 126, len(offsets))
            right = offsets[stop][0] if stop < len(offsets) else len(text)
            piece = text[left:right]
            count = len(self.tokenizer.encode(piece).ids)
            # Token boundaries can retokenize differently in a substring.
            if count > 128:
                middle = offsets[start + (stop - start) // 2][0]
                if middle <= left or middle >= right:
                    raise ValueError('Cannot split evidence within token budget')
                chunks.extend(self.chunks(text[left:middle]))
                chunks.extend(self.chunks(text[middle:right]))
            elif piece.strip():
                chunks.append(piece)
                self.counters['max_chunk_tokens'] = max(self.counters['max_chunk_tokens'], count)
            left = right
        return chunks

    def embed(self, texts: list[str]) -> list[list[float]]:
        unique = list(dict.fromkeys(texts))
        found, missing = {}, []
        for text in unique:
            key = digest(['episode-vector-v1', self.identity, text])
            vector = self.cache.get(key)
            if vector is None:
                missing.append((text, key))
            else:
                found[text] = vector
                self.counters['vectors_reused'] += 1
        if missing:
            if self._embedder is None:
                self._embedder = FastEmbedTextEmbedder(FastEmbedSettings(
                    MODEL, batch_size=8, cache_dir=str(self.model_dir), model_path=str(self.snapshot),
                    threads=self.threads, local_files_only=True))
            for start in range(0, len(missing), 8):
                batch = missing[start:start + 8]
                vectors = self._embedder.embed_texts([item[0] for item in batch])
                for (text, key), vector in zip(batch, vectors, strict=True):
                    if len(vector) != 384 or not np.isfinite(vector).all():
                        raise ValueError('Invalid MiniLM vector')
                    self.cache.put(key, vector, kind='episode_vector')
                    found[text] = vector
                    self.counters['vectors_computed'] += 1
        return [found[text] for text in texts]

    def similarities(self, queries: list[str], documents: list[str]) -> np.ndarray:
        """Max chunk cosine with explicit splitting on both sides."""
        if not queries or not documents:
            return np.zeros((len(queries), len(documents)))
        qchunks = [self.chunks(text) for text in queries]
        dchunks = [self.chunks(text) for text in documents]
        groups = qchunks + dchunks
        flat = [text for group in groups for text in group]
        if any(not group for group in groups):
            raise ValueError('Cannot embed an empty requirement or fact')
        self.counters['chunks'] += len(flat)
        vectors = np.asarray(self.embed(flat), dtype=np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        grouped, offset = [], 0
        for group in groups:
            grouped.append(vectors[offset:offset + len(group)])
            offset += len(group)
        return np.asarray([[float(np.max(q @ d.T)) for d in grouped[len(queries):]]
                           for q in grouped[:len(queries)]])


class PreparedFactVectors:
    """Prepare profile vectors once; retain only one vacancy's query matrix."""

    def __init__(self, vectors: EpisodeVectors, texts: list[str]):
        self.vectors, self.identity = vectors, vectors.identity
        chunks = [vectors.chunks(text) for text in texts]
        if any(not group for group in chunks):
            raise ValueError('Cannot embed an empty professional fact')
        self.documents = self._encode(chunks)

    def _encode(self, groups):
        flat = [text for group in groups for text in group]
        self.vectors.counters['chunks'] += len(flat)
        values = np.asarray(self.vectors.embed(flat), dtype=np.float32)
        values /= np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)
        result, offset = [], 0
        for group in groups:
            result.append(values[offset:offset + len(group)])
            offset += len(group)
        return result

    def similarities(self, queries: list[str]) -> np.ndarray:
        chunks = [self.vectors.chunks(text) for text in queries]
        if any(not group for group in chunks):
            raise ValueError('Cannot embed an empty requirement')
        encoded = self._encode(chunks)
        return np.asarray([[float(np.max(q @ d.T)) for d in self.documents] for q in encoded])
