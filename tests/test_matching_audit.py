from __future__ import annotations

from typing import Any

from app.matching.audit import build_matching_audit_entry, write_matching_audit_entry
from app.matching.models import MergedVacancyNormalized, RuleEvaluationResult


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(document)


def test_build_matching_audit_entry_contains_rule_decision_fields() -> None:
    vacancy = MergedVacancyNormalized(referenznummer="10001-ABC")
    evaluation = RuleEvaluationResult(
        rule_set_version="v1",
        applied_rules=["normalize.alt_roles.drop_pseudo_empty"],
        decision_reasons=["description_missing"],
        flags={"description_missing": True},
        needs_reindex=True,
        skip_reason=None,
    )

    doc = build_matching_audit_entry(
        run_id="run-1",
        vacancy=vacancy,
        evaluation=evaluation,
    )
    assert doc["event_type"] == "matching_rule_decision"
    assert doc["run_id"] == "run-1"
    assert doc["referenznummer"] == "10001-ABC"
    assert doc["needs_reindex"] is True
    assert doc["flags"]["description_missing"] is True


def test_write_matching_audit_entry_inserts_document_into_collection() -> None:
    vacancy = MergedVacancyNormalized(referenznummer="10001-XYZ")
    evaluation = RuleEvaluationResult(needs_reindex=False, skip_reason="same_hash")
    collection = FakeCollection()

    doc = write_matching_audit_entry(
        collection,
        run_id="run-2",
        vacancy=vacancy,
        evaluation=evaluation,
    )
    assert len(collection.docs) == 1
    assert collection.docs[0]["referenznummer"] == "10001-XYZ"
    assert doc["skip_reason"] == "same_hash"

