from app.build_job_index import (
    IndexVersions,
    _build_change_flags,
    _build_chunk_records,
    _compute_embedding_input_hash,
)
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
    }
    flags = _build_change_flags(state_doc, new_hash="h1", versions=versions)
    assert flags == {
        "hash_changed": False,
        "embedding_model_changed": False,
        "chunking_version_changed": False,
        "preprocess_version_changed": False,
    }


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

