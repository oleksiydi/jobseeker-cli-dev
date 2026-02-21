from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from app.core.config import load_app_config
from app.core.matching_config import resolve_matching_config
from app.db.mongo import get_database, resolve_collection_name
from app.matching.search import (
    ScoreWeights,
    aggregate_chunk_hits,
    build_compact_chunks,
    compute_scores,
    flatten_to_text,
)
from app.vector.embedder import build_text_embedder
from app.vector.qdrant_client import QdrantRestClient, parse_qdrant_settings

log = logging.getLogger(__name__)


PROFILE_SEARCH_FIELDS = (
    "key",
    "target_role",
    "skills",
    "tools",
    "stack",
    "experience",
    "seniority",
    "industries",
    "locations",
    "languages",
    "summary",
)

LOCATION_SEARCH_FIELDS = (
    "ort",
    "region",
    "land",
    "plz",
)


def _load_profile_doc(db: Any, collection_name: str, profile_key: str) -> dict[str, Any]:
    profile = db[collection_name].find_one({"key": profile_key})
    if not profile:
        raise RuntimeError(f"Profile with key='{profile_key}' was not found in collection='{collection_name}'")
    return profile


def _serialize_profile_for_search(profile_doc: dict[str, Any]) -> str:
    parts: list[str] = []
    for field_name in PROFILE_SEARCH_FIELDS:
        value = profile_doc.get(field_name)
        text = flatten_to_text(value)
        if text:
            parts.append(f"{field_name}: {text}")
    return " ".join(parts).strip()


def _serialize_location_for_search(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for field_name in LOCATION_SEARCH_FIELDS:
            text = flatten_to_text(value.get(field_name))
            if text:
                parts.append(text)
        if parts:
            return " ".join(parts)
    return flatten_to_text(value)


def _build_query_text(query: str, profile_text: str) -> str:
    return f"QUERY: {query.strip()}\nPROFILE:\n{profile_text}".strip()


def _fetch_meta_by_referenznummer_batch(
    col_list: Any,
    referenznummern: list[str],
) -> dict[str, dict[str, Any]]:
    if not referenznummern:
        return {}

    cursor = col_list.find(
        {"referenznummer": {"$in": referenznummern}},
        {
            "_id": 0,
            "referenznummer": 1,
            "titel": 1,
            "beruf": 1,
            "arbeitgeber": 1,
            "arbeitsort": 1,
        },
    )

    result: dict[str, dict[str, Any]] = {}
    for item in cursor:
        referenznummer = str(item.get("referenznummer") or "").strip()
        if not referenznummer:
            continue
        result[referenznummer] = item
    return result


def _build_chunk_text_fallback(*, title: str, role: str, arbeitgeber: str) -> str:
    parts = [
        f"TITLE: {title}".strip(),
        f"ROLE: {role}".strip(),
        f"ARBEITGEBER: {arbeitgeber}".strip(),
    ]
    return "\n".join(part for part in parts if part and not part.endswith(":"))


def _ensure_chunk_texts(
    top_chunks: list[dict[str, Any]],
    *,
    title: str,
    role: str,
    arbeitgeber: str,
) -> list[dict[str, Any]]:
    fallback_text = _build_chunk_text_fallback(
        title=title,
        role=role,
        arbeitgeber=arbeitgeber,
    )
    result: list[dict[str, Any]] = []
    for chunk in top_chunks:
        item = dict(chunk)
        text = str(item.get("chunk_text") or "").strip()
        if not text:
            item["chunk_text"] = fallback_text
        result.append(item)
    return result


def _parse_score_weights(rerank_cfg: dict[str, Any]) -> ScoreWeights:
    weights_cfg = rerank_cfg.get("weights", {})
    return ScoreWeights(
        role=float(weights_cfg.get("role", 0.40)),
        chunk=float(weights_cfg.get("chunk", 0.30)),
        profile=float(weights_cfg.get("profile", 0.20)),
        metadata=float(weights_cfg.get("metadata", 0.10)),
        rerank_pre_llm=float(weights_cfg.get("rerank_pre_llm", 0.75)),
        rerank_lexical=float(weights_cfg.get("rerank_lexical", 0.25)),
    )


def _retrieve_aggregates(
    *,
    matching_cfg: dict[str, Any],
    query_input_text: str,
    top_k: int,
) -> dict[str, Any]:
    embeddings_cfg = matching_cfg.get("embeddings", {})

    embedder = build_text_embedder(
        str(embeddings_cfg.get("model_name", "intfloat/multilingual-e5-large")),
        batch_size=int(embeddings_cfg.get("batch_size", 32)),
    )
    query_vector = embedder.embed_texts([f"query: {query_input_text}"])[0]

    qdrant_client = QdrantRestClient(parse_qdrant_settings(matching_cfg))
    hits = qdrant_client.search_points(query_vector=query_vector, limit=top_k)
    return aggregate_chunk_hits(hits)


def _build_ranked_results(
    *,
    aggregates: dict[str, Any],
    col_list: Any,
    query_text: str,
    profile_text: str,
    top_chunks_per_job: int,
    score_weights: ScoreWeights,
) -> list[dict[str, Any]]:
    referenznummern = list(aggregates.keys())
    meta_map = _fetch_meta_by_referenznummer_batch(col_list, referenznummern)

    scored: list[dict[str, Any]] = []
    for referenznummer, aggregate in aggregates.items():
        meta = meta_map.get(referenznummer, {})
        title = str(meta.get("titel") or "")
        role = str(meta.get("beruf") or "")
        arbeitgeber = str(meta.get("arbeitgeber") or "")
        location = _serialize_location_for_search(meta.get("arbeitsort") or {})

        scores = compute_scores(
            aggregate=aggregate,
            query_text=query_text,
            profile_text=profile_text,
            title_text=title,
            role_text=role,
            location_text=location,
            weights=score_weights,
        )
        top_chunks = build_compact_chunks(
            aggregate,
            top_chunks=max(1, int(top_chunks_per_job)),
        )
        top_chunks = _ensure_chunk_texts(
            top_chunks,
            title=title,
            role=role,
            arbeitgeber=arbeitgeber,
        )
        scored.append(
            {
                "referenznummer": referenznummer,
                "title": title,
                "role": role,
                "arbeitgeber": arbeitgeber,
                "pre_llm_score": scores.pre_llm_score,
                "rerank_score": scores.rerank_score,
                "chunk_score": scores.chunk_score,
                "role_score": scores.role_score,
                "profile_score": scores.profile_score,
                "metadata_score": scores.metadata_score,
                "confidence": scores.confidence,
                "top_chunks": top_chunks,
            }
        )
    scored.sort(key=lambda item: item["rerank_score"], reverse=True)
    return scored


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve and rerank vacancies from Qdrant for a profile + query."
    )
    parser.add_argument("--query", required=True, help="Search query text")
    parser.add_argument("--profile-key", default="default", help="Profile key from Mongo profiles")
    parser.add_argument("--limit", type=int, default=20, help="Final number of jobs in output")
    parser.add_argument(
        "--top-chunks-per-job",
        type=int,
        default=3,
        help="How many top chunks to keep for each vacancy",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    cfg = load_app_config()
    matching_cfg = resolve_matching_config(cfg)
    db = get_database(cfg)

    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    profiles_collection_name = resolve_collection_name(cfg, key="profiles", default="profiles")
    col_list = db[list_collection_name]

    profile_doc = _load_profile_doc(db, profiles_collection_name, args.profile_key)
    profile_text = _serialize_profile_for_search(profile_doc)
    query_input_text = _build_query_text(args.query, profile_text)

    retrieval_cfg = matching_cfg.get("retrieval", {})
    rerank_cfg = matching_cfg.get("rerank", {})
    top_k = int(retrieval_cfg.get("top_k", 100))
    top_n = int(rerank_cfg.get("top_n", 50))

    aggregates = _retrieve_aggregates(
        matching_cfg=matching_cfg,
        query_input_text=query_input_text,
        top_k=top_k,
    )
    scored = _build_ranked_results(
        aggregates=aggregates,
        col_list=col_list,
        query_text=args.query,
        profile_text=profile_text,
        top_chunks_per_job=args.top_chunks_per_job,
        score_weights=_parse_score_weights(rerank_cfg),
    )

    reranked = scored[:top_n]
    result = reranked[: max(1, int(args.limit))]

    payload = {
        "query": args.query,
        "profile_key": args.profile_key,
        "retrieval_top_k": top_k,
        "rerank_top_n": top_n,
        "returned": len(result),
        "results": result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
