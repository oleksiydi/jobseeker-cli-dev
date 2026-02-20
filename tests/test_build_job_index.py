from app.build_job_index import (
    IndexVersions,
    _build_change_flags,
    _build_chunk_records,
    _build_point_id,
    _build_qdrant_points,
    _compute_embedding_input_hash,
)
from app.matching.models import ChunkRecord
from app.matching.models import MergedVacancyNormalized


class _FakeEngine:
    def infer_section_label(self, chunk_text: str) -> tuple[str, float, str]:
        assert chunk_text
        return ("requirements", 0.9, "fake")


def _vacancy(description: str, *, missing: bool = False) -> MergedVacancyNormalized:
    return MergedVacancyNormalized(
        referenznummer="R-1",
        title="DBA",
        role="Datenbankadministrator/in",
        alt_roles=[],
        location_summary="DE|BY|Regensburg|93047",
        employment_summary="vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false",
        description=description,
        description_missing=missing,
    )


def test_embedding_input_hash_is_stable_for_same_payload() -> None:
    payload = {"a": 1, "b": ["x", "y"]}
    first = _compute_embedding_input_hash(payload)
    second = _compute_embedding_input_hash(payload)
    assert first == second


def test_build_change_flags_for_new_record_marks_all_as_changed() -> None:
    versions = IndexVersions(
        embedding_model="m1",
        embedding_vector_size=1024,
        chunking_version="v1",
        preprocess_version="v1",
        ruleset_version="v1",
    )
    flags = _build_change_flags(None, new_hash="h1", versions=versions)
    assert flags == {
        "hash_changed": True,
        "embedding_model_changed": True,
        "chunking_version_changed": True,
        "preprocess_version_changed": True,
        "not_indexed_yet": True,
    }


def test_build_change_flags_for_same_state_marks_no_changes() -> None:
    versions = IndexVersions(
        embedding_model="m1",
        embedding_vector_size=1024,
        chunking_version="v1",
        preprocess_version="v1",
        ruleset_version="v1",
    )
    state_doc = {
        "embedding_input_hash": "h1",
        "embedding_model": "m1",
        "chunking_version": "v1",
        "preprocess_version": "v1",
        "index_status": "indexed",
    }
    flags = _build_change_flags(state_doc, new_hash="h1", versions=versions)
    assert flags == {
        "hash_changed": False,
        "embedding_model_changed": False,
        "chunking_version_changed": False,
        "preprocess_version_changed": False,
        "not_indexed_yet": False,
    }


def test_build_change_flags_for_normalized_only_marks_not_indexed_yet_true() -> None:
    versions = IndexVersions(
        embedding_model="m1",
        embedding_vector_size=1024,
        chunking_version="v1",
        preprocess_version="v1",
        ruleset_version="v1",
    )
    state_doc = {
        "embedding_input_hash": "h1",
        "embedding_model": "m1",
        "chunking_version": "v1",
        "preprocess_version": "v1",
        "index_status": "normalized_only",
    }
    flags = _build_change_flags(state_doc, new_hash="h1", versions=versions)
    assert flags["hash_changed"] is False
    assert flags["not_indexed_yet"] is True


def test_build_chunk_records_uses_synthetic_chunk_when_flag_set() -> None:
    vacancy = _vacancy(description="", missing=True)
    records = _build_chunk_records(
        vacancy=vacancy,
        rule_flags={
            "create_synthetic_chunk": True,
            "synthetic_chunk_text": "Role summary: DBA",
        },
        engine=_FakeEngine(),
    )
    assert len(records) == 1
    assert records[0].section_label == "unknown"
    assert records[0].text == "Role summary: DBA"


def test_build_chunk_records_uses_engine_label_for_regular_text() -> None:
    vacancy = _vacancy(description="Wir suchen Erfahrung mit Oracle und PostgreSQL.")
    records = _build_chunk_records(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
    )
    assert len(records) == 1
    assert records[0].section_label == "requirements"
    assert records[0].section_confidence == 0.9


def test_build_point_id_is_deterministic() -> None:
    first = _build_point_id("R-1", "R-1:0", "abc")
    second = _build_point_id("R-1", "R-1:0", "abc")
    assert first == second


def test_build_qdrant_points_builds_payload_and_vector() -> None:
    versions = IndexVersions(
        embedding_model="intfloat/multilingual-e5-large",
        embedding_vector_size=1024,
        chunking_version="v1",
        preprocess_version="v1",
        ruleset_version="v1",
    )
    chunks = [
        ChunkRecord(
            referenznummer="R-1",
            chunk_id="R-1:0",
            chunk_index=0,
            text="Text",
            section_label="requirements",
            section_confidence=0.8,
            description_missing=False,
        )
    ]
    vectors = [[0.1, 0.2, 0.3]]
    points = _build_qdrant_points(
        referenznummer="R-1",
        chunks=chunks,
        vectors=vectors,
        embedding_input_hash="hash-1",
        versions=versions,
    )
    assert len(points) == 1
    assert points[0]["payload"]["referenznummer"] == "R-1"
    assert points[0]["payload"]["section_label"] == "requirements"
    assert points[0]["vector"] == [0.1, 0.2, 0.3]
