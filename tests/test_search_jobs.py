from app.matching.search import (
    aggregate_chunk_hits,
    build_compact_chunks,
    compute_scores,
    flatten_to_text,
    lexical_overlap_score,
)
from app.search_jobs import (
    _ensure_chunk_texts,
    _parse_score_weights,
    _serialize_location_for_search,
    _serialize_profile_for_search,
)


def test_flatten_to_text_handles_nested_profile() -> None:
    profile = {
        "key": "default",
        "skills": ["python", "spark"],
        "meta": {"location": "Regensburg", "lang": "de"},
    }
    text = flatten_to_text(profile)
    assert "skills: python spark" in text
    assert "location: Regensburg" in text


def test_lexical_overlap_score_returns_zero_for_empty() -> None:
    assert lexical_overlap_score("", "python") == 0.0
    assert lexical_overlap_score("python", "") == 0.0


def test_aggregate_chunk_hits_groups_by_referenznummer() -> None:
    hits = [
        {
            "score": 0.7,
            "payload": {
                "referenznummer": "R-1",
                "chunk_id": "R-1:0",
                "chunk_index": 0,
                "section_label": "requirements",
                "chunk_text": "Python Spark",
            },
        },
        {
            "score": 0.5,
            "payload": {
                "referenznummer": "R-1",
                "chunk_id": "R-1:1",
                "chunk_index": 1,
                "section_label": "benefits",
                "chunk_text": "Urlaub",
            },
        },
    ]
    grouped = aggregate_chunk_hits(hits)
    assert "R-1" in grouped
    assert grouped["R-1"].chunk_score == 0.7
    assert len(grouped["R-1"].chunk_hits) == 2


def test_compute_scores_populates_pre_llm_and_rerank() -> None:
    grouped = aggregate_chunk_hits(
        [
            {
                "score": 0.8,
                "payload": {
                    "referenznummer": "R-2",
                    "chunk_id": "R-2:0",
                    "chunk_index": 0,
                    "section_label": "requirements",
                    "chunk_text": "Python SQL Databricks",
                },
            }
        ]
    )
    aggregate = grouped["R-2"]
    scores = compute_scores(
        aggregate=aggregate,
        query_text="Python Databricks",
        profile_text="Python Spark",
        title_text="Data Engineer",
        role_text="Python Entwickler",
        location_text="Regensburg",
    )
    assert scores.pre_llm_score > 0.0
    assert scores.rerank_score > 0.0
    assert 0.0 <= scores.confidence <= 1.0


def test_build_compact_chunks_limits_output() -> None:
    grouped = aggregate_chunk_hits(
        [
            {
                "score": 0.9,
                "payload": {
                    "referenznummer": "R-3",
                    "chunk_id": "R-3:0",
                    "chunk_index": 0,
                    "section_label": "requirements",
                    "chunk_text": "A",
                },
            },
            {
                "score": 0.8,
                "payload": {
                    "referenznummer": "R-3",
                    "chunk_id": "R-3:1",
                    "chunk_index": 1,
                    "section_label": "tasks",
                    "chunk_text": "B",
                },
            },
        ]
    )
    chunks = build_compact_chunks(grouped["R-3"], top_chunks=1)
    assert len(chunks) == 1
    assert chunks[0]["chunk_id"] == "R-3:0"


def test_ensure_chunk_texts_adds_fallback_when_empty() -> None:
    top_chunks = [
        {
            "chunk_id": "R-4:0",
            "chunk_index": 0,
            "section_label": "unknown",
            "chunk_score": 0.7,
            "chunk_text": "",
        }
    ]
    patched = _ensure_chunk_texts(
        top_chunks,
        title="Data Engineer",
        role="Ingenieur/in",
        arbeitgeber="ACME",
    )
    assert "TITLE: Data Engineer" in patched[0]["chunk_text"]


def test_serialize_profile_for_search_uses_known_fields() -> None:
    profile = {
        "key": "default",
        "skills": ["python", "spark"],
        "languages": ["de", "en"],
        "ignored_block": {"secret": "value"},
    }
    text = _serialize_profile_for_search(profile)
    assert "skills: python spark" in text
    assert "languages: de en" in text
    assert "ignored_block" not in text


def test_serialize_location_for_search_prefers_known_location_keys() -> None:
    location = {
        "ort": "Regensburg",
        "region": "Bayern",
        "land": "DE",
        "extra": "not_used",
    }
    text = _serialize_location_for_search(location)
    assert "Regensburg" in text
    assert "Bayern" in text
    assert "DE" in text
    assert "not_used" not in text


def test_parse_score_weights_reads_rerank_config() -> None:
    weights = _parse_score_weights(
        {
            "weights": {
                "role": 0.5,
                "chunk": 0.2,
                "profile": 0.2,
                "metadata": 0.1,
                "rerank_pre_llm": 0.8,
                "rerank_lexical": 0.2,
            }
        }
    )
    assert weights.role == 0.5
    assert weights.rerank_lexical == 0.2
