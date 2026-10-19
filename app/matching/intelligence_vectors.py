"""Compact CPU embeddings in the existing Qdrant service, with input-hash reuse."""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.matching.intelligence_data import DerivedCache, digest
from app.vector.embedder import FastEmbedSettings, FastEmbedTextEmbedder
from app.vector.qdrant_client import QdrantRestClient, parse_qdrant_settings

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'


def model_snapshot(model_dir: Path) -> Path:
    paths = sorted(model_dir.glob('models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/snapshots/*'))
    if not paths or not (paths[-1] / 'model_optimized.onnx').is_file():
        raise ValueError('Local MiniLM missing: run app.job_intelligence --prepare-model or use --no-embeddings')
    return paths[-1]


def model_identity(model_dir: Path) -> dict:
    snapshot = model_snapshot(model_dir)
    hasher = hashlib.sha256()
    for path in sorted(snapshot.rglob('*')):
        if path.is_file():
            hasher.update(path.relative_to(snapshot).as_posix().encode())
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(block)
    return {'name': MODEL, 'revision': snapshot.name, 'files_sha256': hasher.hexdigest(),
            'runtime': f'fastembed:{importlib.metadata.version("fastembed")}',
            'dimensions': 384, 'pooling': 'mean', 'prefixes': 'none', 'provider': 'CPUExecutionProvider',
            'input': 'job title + occupation; separate short candidate role/skill queries', 'version': 1}


class SemanticIndex:
    """A small derived index; the legacy job_chunks_v1 collection is untouched."""
    def __init__(self, cache: DerivedCache, matching: dict, settings: dict, model_dir: Path):
        self.cache, self.settings, self.model_dir = cache, settings, model_dir
        self.identity = model_identity(model_dir)
        collection = settings['vector_collection']
        if collection == matching.get('qdrant', {}).get('collection_name', 'job_chunks_v1'):
            raise ValueError('Intelligence projection must not overwrite the existing chunk collection')
        qconfig = {**matching, 'embeddings': {'model_name': MODEL, 'vector_size': 384},
                   'qdrant': {**matching.get('qdrant', {}), 'collection_name': collection}}
        self.qdrant = QdrantRestClient(parse_qdrant_settings(qconfig))
        self.qdrant.ensure_collection()
        self._embedder = None
        self.counters = {'vectors_computed': 0, 'vectors_reused': 0, 'query_vectors_reused': 0,
                         'qdrant_upserts': 0, 'llm_calls': 0}

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            self._embedder = FastEmbedTextEmbedder(FastEmbedSettings(
                MODEL, batch_size=self.settings['embedding_batch_size'], cache_dir=str(self.model_dir),
                model_path=str(model_snapshot(self.model_dir)), threads=self.settings['embedding_threads'],
                local_files_only=True))
        self.counters['vectors_computed'] += len(texts)
        return self._embedder.embed_texts(texts)

    def score(self, jobs: list[dict], profile: dict, query: str = '') -> dict[str, float]:
        if not jobs:
            return {}
        documents = []
        for job in jobs:
            point_id = str(uuid5(NAMESPACE_URL, 'job-intelligence-role-v1:' + job['ref']))
            text = (job['title'] + '\n' + job['role'])[:700]
            documents.append((point_id, job['ref'], text, digest([self.identity, text])))
        existing = {}
        for start in range(0, len(documents), 100):
            existing.update({p['id']: p['payload'] for p in
                             self.qdrant.retrieve_points([d[0] for d in documents[start:start + 100]])})
        missing = [d for d in documents if existing.get(d[0], {}).get('input_hash') != d[3]]
        self.counters['vectors_reused'] += len(documents) - len(missing)
        for start in range(0, len(missing), self.settings['embedding_batch_size']):
            batch = missing[start:start + self.settings['embedding_batch_size']]
            vectors = self._embed([d[2] for d in batch])
            self.qdrant.upsert_points([{'id': d[0], 'vector': vector, 'payload': {
                'referenznummer': d[1], 'input_hash': d[3], 'model_identity': digest(self.identity)}}
                for d, vector in zip(batch, vectors, strict=True)])
            self.counters['qdrant_upserts'] += len(batch)
        roles = profile.get('target_roles') or profile.get('roles', [])
        queries = [(query or ' / '.join(roles))[:600]]
        # Multiple career tracks remain separate instead of truncating a whole CV.
        queries.extend(role[:400] for role in roles[:5])
        labels = list(dict.fromkeys(s['label'] for s in profile.get('skills', [])))
        if labels:
            queries.append(', '.join(labels)[:650])
        queries = list(dict.fromkeys(q for q in queries if q.strip()))
        if not queries:
            return {}
        key = digest(['query-vectors-v1', self.identity, queries])
        vectors = self.cache.get(key)
        if vectors is None:
            vectors = self._embed(queries)
            self.cache.put(key, vectors, kind='query_vectors')
        else:
            self.counters['query_vectors_reused'] += len(vectors)
        scores = {}
        for vector in vectors:
            for hit in self.qdrant.search_points(query_vector=vector, limit=len(jobs),
                                                  reference_ids=[j['ref'] for j in jobs]):
                ref = hit['payload']['referenznummer']
                scores[ref] = max(scores.get(ref, 0.0), float(hit['score']))
        return scores
