from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-я0-9_]{2,}", re.UNICODE)


def flatten_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(filter(None, (flatten_to_text(item) for item in value))).strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value.keys()):
            text = flatten_to_text(value.get(key))
            if text:
                parts.append(f"{key}: {text}")
        return " ".join(parts).strip()
    return str(value)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text or "")]


def lexical_overlap_score(left_text: str, right_text: str) -> float:
    left = set(tokenize(left_text))
    right = set(tokenize(right_text))
    if not left or not right:
        return 0.0
    overlap = left.intersection(right)
    union = left.union(right)
    if not union:
        return 0.0
    return len(overlap) / len(union)


@dataclass(frozen=True)
class ChunkHit:
    referenznummer: str
    score: float
    chunk_id: str
    chunk_index: int
    section_label: str
    chunk_text: str


@dataclass
class CandidateAggregate:
    referenznummer: str
    chunk_hits: list[ChunkHit] = field(default_factory=list)
    chunk_score: float = 0.0


@dataclass(frozen=True)
class ScoreWeights:
    role: float = 0.40
    chunk: float = 0.30
    profile: float = 0.20
    metadata: float = 0.10
    chunk_top1: float = 0.60
    chunk_top2: float = 0.30
    chunk_top3: float = 0.10
    rerank_pre_llm: float = 0.75
    rerank_lexical: float = 0.25
    role_mismatch_threshold: float = 0.08
    role_mismatch_penalty: float = 0.65


@dataclass(frozen=True)
class CandidateScores:
    role_score: float
    profile_score: float
    metadata_score: float
    chunk_score: float
    pre_llm_score: float
    rerank_score: float
    confidence: float


def aggregate_chunk_hits(hits: list[dict[str, Any]]) -> dict[str, CandidateAggregate]:
    grouped: dict[str, CandidateAggregate] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        referenznummer = str(payload.get("referenznummer") or "").strip()
        if not referenznummer:
            continue
        aggregate = grouped.setdefault(referenznummer, CandidateAggregate(referenznummer=referenznummer))
        aggregate.chunk_hits.append(
            ChunkHit(
                referenznummer=referenznummer,
                score=float(hit.get("score", 0.0)),
                chunk_id=str(payload.get("chunk_id") or ""),
                chunk_index=int(payload.get("chunk_index", 0) or 0),
                section_label=str(payload.get("section_label") or "unknown"),
                chunk_text=str(payload.get("chunk_text") or ""),
            )
        )

    for aggregate in grouped.values():
        aggregate.chunk_hits.sort(key=lambda item: item.score, reverse=True)
        if aggregate.chunk_hits:
            aggregate.chunk_score = aggregate.chunk_hits[0].score
    return grouped


def _aggregate_chunk_score(aggregate: CandidateAggregate, weights: ScoreWeights) -> float:
    top_scores = [hit.score for hit in aggregate.chunk_hits[:3]]
    if not top_scores:
        return 0.0
    coeffs = [weights.chunk_top1, weights.chunk_top2, weights.chunk_top3]
    weighted_sum = 0.0
    coeff_sum = 0.0
    for score, coeff in zip(top_scores, coeffs, strict=False):
        if coeff <= 0:
            continue
        weighted_sum += score * coeff
        coeff_sum += coeff
    if coeff_sum <= 0:
        return top_scores[0]
    return weighted_sum / coeff_sum


def compute_scores(
    *,
    aggregate: CandidateAggregate,
    query_text: str,
    profile_text: str,
    title_text: str,
    role_text: str,
    location_text: str,
    weights: ScoreWeights | None = None,
) -> CandidateScores:
    score_weights = weights or ScoreWeights()
    chunk_score = _aggregate_chunk_score(aggregate, score_weights)
    role_score = max(
        lexical_overlap_score(query_text, title_text),
        lexical_overlap_score(query_text, role_text),
    )
    profile_score = lexical_overlap_score(query_text, profile_text)
    metadata_score = lexical_overlap_score(query_text, location_text)

    pre_llm = (
        score_weights.role * role_score
        + score_weights.chunk * chunk_score
        + score_weights.profile * profile_score
        + score_weights.metadata * metadata_score
    )
    # Легкий rerank baseline без внешних моделей.
    top_chunk_text = aggregate.chunk_hits[0].chunk_text if aggregate.chunk_hits else ""
    lexical_boost = lexical_overlap_score(query_text, top_chunk_text)
    rerank = score_weights.rerank_pre_llm * pre_llm + score_weights.rerank_lexical * lexical_boost
    if role_score < score_weights.role_mismatch_threshold:
        rerank *= score_weights.role_mismatch_penalty
    confidence = min(1.0, max(0.0, math.sqrt(max(rerank, 0.0))))

    return CandidateScores(
        role_score=role_score,
        profile_score=profile_score,
        metadata_score=metadata_score,
        chunk_score=chunk_score,
        pre_llm_score=pre_llm,
        rerank_score=rerank,
        confidence=confidence,
    )


def build_compact_chunks(aggregate: CandidateAggregate, *, top_chunks: int = 3) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for hit in aggregate.chunk_hits[:top_chunks]:
        result.append(
            {
                "chunk_id": hit.chunk_id,
                "chunk_index": hit.chunk_index,
                "section_label": hit.section_label,
                "chunk_score": hit.score,
                "chunk_text": hit.chunk_text,
            }
        )
    return result
