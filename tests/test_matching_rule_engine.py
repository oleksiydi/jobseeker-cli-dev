from __future__ import annotations

from typing import Any

from app.core.config import load_app_config
from app.matching.models import MergedVacancyNormalized
from app.matching.rule_engine import MatchingRuleEngine, SimpleJsonLogicEvaluator


class _FakeAuditCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(document)


def _vacancy(description: str, alt_roles: list[str] | None = None) -> MergedVacancyNormalized:
    return MergedVacancyNormalized(
        referenznummer="R-42",
        title="DBA Administrator (m/w/d)",
        role="Datenbankadministrator/in",
        alt_roles=alt_roles or [],
        location_summary="DE|Bayern|Regensburg|93047",
        employment_summary="vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false",
        description=description,
        description_missing=description == "",
    )


def test_rule_engine_sets_fallback_for_missing_description() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)
    vacancy = _vacancy(description="")

    result = engine.evaluate_vacancy(vacancy, change_flags={"hash_changed": True})

    assert "fallback_synthetic_chunk_for_missing_description" in result.applied_rules
    assert result.flags["description_missing"] is True
    assert result.flags["create_synthetic_chunk"] is True
    assert "Role summary:" in result.flags["synthetic_chunk_text"]
    assert result.needs_reindex is True


def test_rule_engine_drops_reindex_when_no_changes() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)
    vacancy = _vacancy(description="Wir suchen eine Fachkraft.")

    result = engine.evaluate_vacancy(
        vacancy,
        change_flags={
            "hash_changed": False,
            "embedding_model_changed": False,
            "chunking_version_changed": False,
            "preprocess_version_changed": False,
        },
    )

    assert result.needs_reindex is False
    assert result.skip_reason == "unchanged_inputs"


def test_rule_engine_marks_alt_roles_empty() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)
    vacancy = _vacancy(description="Kurzbeschreibung", alt_roles=[])

    result = engine.evaluate_vacancy(vacancy, change_flags={"hash_changed": True})
    assert result.flags["alt_roles_empty"] is True
    assert "flag_alt_roles_empty" in result.applied_rules


def test_rule_engine_writes_audit_when_collection_and_run_id_are_provided() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)
    vacancy = _vacancy(description="Wir suchen eine Fachkraft.")
    audit_collection = _FakeAuditCollection()

    result = engine.evaluate_vacancy(
        vacancy,
        change_flags={"hash_changed": True},
        run_id="run-qa-1",
        audit_collection=audit_collection,
    )

    assert result.needs_reindex is True
    assert len(audit_collection.docs) == 1
    assert audit_collection.docs[0]["event_type"] == "matching_rule_decision"
    assert audit_collection.docs[0]["referenznummer"] == "R-42"
    assert audit_collection.docs[0]["run_id"] == "run-qa-1"


def test_infer_section_label_requirements() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)

    label, confidence, reason = engine.infer_section_label(
        "Ihr Profil: Abgeschlossene Ausbildung und relevante Qualifikation."
    )

    assert label == "requirements"
    assert confidence >= 0.8
    assert reason == "label_requirements"


def test_infer_section_label_unknown_when_no_markers() -> None:
    cfg = load_app_config()
    engine = MatchingRuleEngine(cfg)

    label, confidence, reason = engine.infer_section_label(
        "Wir sind ein dynamisches Unternehmen mit wertschätzender Kultur."
    )

    assert label == "unknown"
    assert 0.0 <= confidence <= 0.5
    assert reason == "no_section_markers_detected"


def test_simple_jsonlogic_evaluator_depth_limit() -> None:
    evaluator = SimpleJsonLogicEvaluator(max_depth=2)
    # Глубина выражения больше 2:
    # {"if": [ {"if": [ {"if": [ ... ] } ] } ] }
    expr = {"if": [{"if": [{"if": [True, 1, 0]}, 1, 0]}, 1, 0]}
    try:
        evaluator.evaluate(expr, {})
    except ValueError as exc:
        assert "too deep" in str(exc)
    else:
        raise AssertionError("Depth limit exception is expected")
