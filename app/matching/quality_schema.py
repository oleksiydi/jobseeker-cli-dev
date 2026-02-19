from __future__ import annotations

from typing import Any

import pandas as pd
import pandera as pa
from pandera.errors import SchemaErrors
from pydantic import BaseModel, Field

from app.matching.models import ChunkRecord


# Разрешенные метки секций в текущей версии пайплайна.
# unknown оставляем осознанно: это безопасный fallback, когда классификатор секции
# не смог уверенно определить тип фрагмента.
KNOWN_SECTION_LABELS = {"tasks", "requirements", "benefits", "about", "unknown"}


class QualityReport(BaseModel):
    """
    Итоговый отчет quality-check шага.

    Используется в nightly-режиме для прозрачной диагностики качества чанков:
    - hard checks: критичные нарушения формата/данных;
    - soft checks: предупреждения о деградации качества.
    """

    total_rows: int = 0
    hard_passed: bool = False
    soft_passed: bool = False
    hard_errors: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int] = Field(default_factory=dict)


HARD_SCHEMA = pa.DataFrameSchema(
    {
        "referenznummer": pa.Column(str, nullable=False, checks=[pa.Check.str_length(min_value=1)]),
        "chunk_id": pa.Column(str, nullable=False, checks=[pa.Check.str_length(min_value=1)]),
        "chunk_index": pa.Column(int, nullable=False, checks=[pa.Check.greater_than_or_equal_to(0)]),
        "text": pa.Column(str, nullable=False),
        "section_label": pa.Column(str, nullable=False),
        "section_confidence": pa.Column(
            float,
            nullable=False,
            checks=[pa.Check.greater_than_or_equal_to(0.0), pa.Check.less_than_or_equal_to(1.0)],
        ),
        "description_missing": pa.Column(bool, nullable=False),
    },
    checks=[
        # Если description_missing=False, текст чанка обязан быть непустым.
        pa.Check(
            lambda df: (
                (df["description_missing"])
                | (df["text"].fillna("").astype(str).str.strip().str.len() > 0)
            ).all(),
            error="text must be non-empty when description_missing=false",
        )
    ],
    coerce=True,
    strict=False,
)


def _as_chunk_record(value: ChunkRecord | dict[str, Any]) -> ChunkRecord:
    if isinstance(value, ChunkRecord):
        return value
    return ChunkRecord.model_validate(value)


def chunk_records_to_dataframe(records: list[ChunkRecord | dict[str, Any]]) -> pd.DataFrame:
    """
    Преобразовать чанки в DataFrame для Pandera-проверок.

    Почему через отдельную функцию:
    - единая точка преобразования для кода и тестов;
    - меньше дублирования в nightly/report шагах.
    """
    rows: list[dict[str, Any]] = []
    for raw in records:
        record = _as_chunk_record(raw)
        rows.append(record.model_dump())
    return pd.DataFrame(rows)


def run_quality_checks(
    records: list[ChunkRecord | dict[str, Any]],
    *,
    unknown_label_threshold: float = 0.60,
    min_text_len: int = 40,
) -> QualityReport:
    """
    Выполнить hard/soft quality checks для batch-а чанков.

    Hard checks:
    - типы/границы колонок;
    - обязательные поля;
    - непустой text, если description_missing=False.

    Soft checks:
    - доля section_label=unknown не выше порога;
    - слишком короткие тексты в чанках без fallback.
    """
    dataframe = chunk_records_to_dataframe(records)
    if dataframe.empty:
        return QualityReport(
            total_rows=0,
            hard_passed=True,
            soft_passed=True,
            metrics={
                "unknown_label_ratio": 0.0,
                "short_text_non_missing_ratio": 0.0,
                "short_text_non_missing_count": 0,
            },
        )

    hard_errors: list[str] = []
    hard_passed = True
    try:
        HARD_SCHEMA.validate(dataframe, lazy=True)
    except SchemaErrors as exc:
        hard_passed = False
        failure_cases = exc.failure_cases
        # Ограничиваем количество ошибок, чтобы отчет оставался читаемым.
        max_errors = 20
        for _, row in failure_cases.head(max_errors).iterrows():
            hard_errors.append(
                f"{row.get('column', 'unknown')}: {row.get('failure_case', 'invalid')}"
            )

    unknown_ratio = float((dataframe["section_label"] == "unknown").mean())
    non_missing_mask = ~dataframe["description_missing"]
    short_non_missing_mask = (
        non_missing_mask
        & (dataframe["text"].fillna("").astype(str).str.strip().str.len() < min_text_len)
    )
    short_non_missing_count = int(short_non_missing_mask.sum())
    total_non_missing = int(non_missing_mask.sum())
    short_non_missing_ratio = (
        float(short_non_missing_count / total_non_missing) if total_non_missing else 0.0
    )

    soft_warnings: list[str] = []
    if unknown_ratio > unknown_label_threshold:
        soft_warnings.append(
            f"unknown label ratio {unknown_ratio:.2%} exceeds threshold {unknown_label_threshold:.2%}"
        )
    if short_non_missing_count > 0:
        soft_warnings.append(
            f"{short_non_missing_count} non-missing chunks shorter than {min_text_len} chars"
        )

    unknown_labels = sorted(set(dataframe["section_label"].astype(str)) - KNOWN_SECTION_LABELS)
    if unknown_labels:
        soft_warnings.append(
            f"found non-standard section labels: {', '.join(unknown_labels)}"
        )

    soft_passed = len(soft_warnings) == 0
    return QualityReport(
        total_rows=len(dataframe),
        hard_passed=hard_passed,
        soft_passed=soft_passed,
        hard_errors=hard_errors,
        soft_warnings=soft_warnings,
        metrics={
            "unknown_label_ratio": unknown_ratio,
            "short_text_non_missing_ratio": short_non_missing_ratio,
            "short_text_non_missing_count": short_non_missing_count,
        },
    )

