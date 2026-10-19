from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.matching.models import MergedVacancyNormalized, RuleEvaluationResult


class MongoInsertOneLike(Protocol):
    """Минимальный протокол для Mongo-коллекции (duck typing для тестов)."""

    def insert_one(self, document: dict[str, Any]) -> Any:
        ...


class MatchingAuditEntry(BaseModel):
    """
    Аудит-запись решения rules-слоя для одной вакансии.

    Цель:
    - прозрачно видеть, почему вакансия попала в skip/reindex;
    - сохранять стабильный и сериализуемый формат события.
    """

    event_type: str = "matching_rule_decision"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str = Field(min_length=1)
    stage: str = "rule_engine"
    referenznummer: str = Field(min_length=1)
    rule_set_version: str = "v1"
    applied_rules: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)
    needs_reindex: bool = False
    skip_reason: str | None = None


def build_matching_audit_entry(
    *,
    run_id: str,
    vacancy: MergedVacancyNormalized,
    evaluation: RuleEvaluationResult,
    stage: str = "rule_engine",
) -> dict[str, Any]:
    """
    Собрать audit-документ из результата rule-engine.

    Почему отдельная функция:
    - единая структура события для всех мест записи;
    - простое переиспользование в batch-пайплайне и тестах.
    """
    entry = MatchingAuditEntry(
        run_id=run_id,
        stage=stage,
        referenznummer=vacancy.referenznummer,
        rule_set_version=evaluation.rule_set_version,
        applied_rules=evaluation.applied_rules,
        decision_reasons=evaluation.decision_reasons,
        flags=evaluation.flags,
        needs_reindex=evaluation.needs_reindex,
        skip_reason=evaluation.skip_reason,
    )
    return entry.model_dump(mode="json")


def write_matching_audit_entry(
    collection: MongoInsertOneLike,
    *,
    run_id: str,
    vacancy: MergedVacancyNormalized,
    evaluation: RuleEvaluationResult,
    stage: str = "rule_engine",
) -> dict[str, Any]:
    """
    Записать audit-документ в коллекцию и вернуть вставленный payload.
    """
    document = build_matching_audit_entry(
        run_id=run_id,
        vacancy=vacancy,
        evaluation=evaluation,
        stage=stage,
    )
    collection.insert_one(document)
    return document

