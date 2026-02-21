import app.build_job_index as build_job_index_module
from app.build_job_index import (
    IndexVersions,
    _build_change_flags,
    _build_chunk_records,
    _build_chunk_records_with_runtime,
    _build_point_id,
    _build_qdrant_points,
    _compute_embedding_input_hash,
    _split_text_to_sentences,
    _warn_once,
)
from app.matching.models import ChunkRecord
from app.matching.models import MergedVacancyNormalized


class _FakeEngine:
    def infer_section_label(self, chunk_text: str) -> tuple[str, float, str]:
        assert chunk_text
        return ("requirements", 0.9, "fake")


class _FakeEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lower = text.lower()
            if "oracle" in lower or "sql" in lower or "postgres" in lower:
                vectors.append([1.0, 0.0])
            elif "urlaub" in lower or "benefits" in lower:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.8, 0.2])
        return vectors


class _FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        del add_special_tokens
        return list(range(len(text.split())))

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        del skip_special_tokens
        return " ".join([f"tok{value}" for value in ids])

    def __call__(
        self,
        text: str,
        add_special_tokens: bool = False,
        return_offsets_mapping: bool = False,
    ) -> dict[str, list[tuple[int, int]]]:
        del add_special_tokens
        if not return_offsets_mapping:
            return {}
        offsets: list[tuple[int, int]] = []
        start = 0
        for token in text.split():
            token_start = text.find(token, start)
            token_end = token_start + len(token)
            offsets.append((token_start, token_end))
            start = token_end
        return {"offset_mapping": offsets}


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


def test_build_chunk_records_semantic_splits_text_by_similarity() -> None:
    vacancy = _vacancy(
        description=(
            "Wir suchen Oracle Erfahrung. SQL Kenntnisse sind wichtig. "
            "Wir bieten 30 Tage Urlaub. Benefits und Zuschuesse sind vorhanden."
        )
    )
    records = _build_chunk_records(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
        chunking_cfg={
            "strategy": "semantic",
            "semantic_similarity_threshold": 0.8,
            "semantic_sentence_window": 1,
            "semantic_min_sentences": 1,
            "semantic_max_sentences": 8,
            "hard_max_chunk_chars": 4000,
            "hard_min_chunk_chars": 1,
            "fallback_overlap_chars": 50,
        },
        embedder=_FakeEmbedder(),
        embedding_model="intfloat/multilingual-e5-large",
    )
    assert len(records) == 2
    assert "Oracle Erfahrung" in records[0].text
    assert "Benefits" in records[1].text


def test_build_chunk_records_semantic_falls_back_when_embedder_missing() -> None:
    vacancy = _vacancy(description="Aufgaben mit SQL. Benefits und Urlaub.")
    records = _build_chunk_records(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
        chunking_cfg={"strategy": "semantic"},
        embedder=None,
        embedding_model="intfloat/multilingual-e5-large",
    )
    assert len(records) == 1
    assert records[0].chunk_index == 0


def test_build_chunk_records_semantic_applies_token_aware_splitting(monkeypatch) -> None:
    monkeypatch.setattr(
        build_job_index_module,
        "_get_transformers_tokenizer",
        lambda _model_name: _FakeTokenizer(),
    )
    vacancy = _vacancy(
        description="eins zwei drei vier funf sechs sieben acht neun zehn elf zwolf",
    )
    records = _build_chunk_records(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
        chunking_cfg={
            "strategy": "semantic",
            "token_aware_enabled": True,
            "max_tokens_per_chunk": 5,
            "overlap_tokens": 1,
            "hard_max_chunk_chars": 9999,
            "hard_min_chunk_chars": 1,
        },
        embedder=None,
        embedding_model="intfloat/multilingual-e5-large",
    )
    assert len(records) == 3
    assert records[0].text == "eins zwei drei vier funf"
    assert records[1].text == "funf sechs sieben acht neun"
    assert records[2].text == "neun zehn elf zwolf"


def test_split_text_to_sentences_uses_regex_when_spacy_pipeline_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(build_job_index_module, "_get_spacy_pipeline", lambda _model_name: None)
    parts = _split_text_to_sentences(
        "Erster Satz. Zweiter Satz.",
        chunking_cfg={"sentence_splitter": "spacy", "spacy_model": "de_core_news_md"},
    )
    assert parts == ["Erster Satz.", "Zweiter Satz."]


def test_build_chunk_records_with_runtime_exposes_fallback_reason() -> None:
    vacancy = _vacancy(description="Nur ein Satz ohne Trennung")
    records, runtime = _build_chunk_records_with_runtime(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
        chunking_cfg={"strategy": "semantic", "sentence_splitter": "regex"},
        embedder=None,
        embedding_model="intfloat/multilingual-e5-large",
    )
    assert len(records) == 1
    assert runtime.sentence_splitter_used == "regex"
    assert "embedder_unavailable_for_semantic" in runtime.fallback_reasons


def test_build_chunk_records_with_runtime_respects_char_token_splitter() -> None:
    vacancy = _vacancy(description="eins zwei drei vier funf sechs sieben")
    records, runtime = _build_chunk_records_with_runtime(
        vacancy=vacancy,
        rule_flags={},
        engine=_FakeEngine(),
        chunking_cfg={
            "strategy": "semantic",
            "sentence_splitter": "regex",
            "token_aware_enabled": True,
            "token_splitter": "char",
            "max_tokens_per_chunk": 2,
        },
        embedder=None,
        embedding_model="intfloat/multilingual-e5-large",
    )
    assert len(records) == 1
    assert runtime.token_splitter_used == "char"
    assert runtime.token_aware_applied is False


def test_warn_once_logs_only_first_time(caplog) -> None:
    caplog.set_level("WARNING")
    key = "test_warn_once_logs_only_first_time"
    build_job_index_module._WARNED_KEYS.discard(key)
    _warn_once(key, "single warning")
    _warn_once(key, "single warning")
    assert sum(1 for line in caplog.messages if line == "single warning") == 1


def test_split_text_to_sentences_uses_syntok_when_enabled(monkeypatch) -> None:
    class _FakeToken:
        def __init__(self, spacing: str, value: str) -> None:
            self.spacing = spacing
            self.value = value

    class _FakeSegmenter:
        @staticmethod
        def process(_text: str):
            return [[[ _FakeToken("", "Hallo"), _FakeToken(" ", "Welt.") ]]]

    monkeypatch.setitem(__import__("sys").modules, "syntok", type("_M", (), {"segmenter": _FakeSegmenter})())
    parts = _split_text_to_sentences(
        "Hallo Welt.",
        chunking_cfg={"sentence_splitter": "syntok"},
    )
    assert parts == ["Hallo Welt."]


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
