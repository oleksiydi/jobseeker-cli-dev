from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# Псевдо-пустые значения, которые часто приходят из внешних источников
# как строки и не должны считаться реальными данными.
PSEUDO_EMPTY_VALUES = {"", "false", "none", "null", "n/a", "-", "nan"}


def normalize_text(value: Any) -> str:
    """
    Нормализация строкового поля к стабильному виду.

    Задача:
    - убрать лишние пробелы;
    - унифицировать переносы;
    - гарантировать детерминированный canonical text для hash.
    """
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(lines).strip()


def normalize_alt_roles(values: list[Any]) -> list[str]:
    """
    Очистить список альтернативных профессий.

    Важно для кейса пользователя:
    - значения вроде "false", "null", "" должны быть исключены.
    """
    normalized: list[str] = []
    for raw in values:
        value = normalize_text(raw)
        if value.lower() in PSEUDO_EMPTY_VALUES:
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized


def _build_location_summary(meta: dict[str, Any], details: dict[str, Any]) -> str:
    meta_location = (meta or {}).get("arbeitsort", {}) or {}
    details_locations = (details or {}).get("stellenlokationen", []) or []
    details_address = {}
    if details_locations and isinstance(details_locations[0], dict):
        details_address = details_locations[0].get("adresse", {}) or {}

    country = normalize_text(details_address.get("land") or meta_location.get("land"))
    region = normalize_text(details_address.get("region") or meta_location.get("region"))
    city = normalize_text(details_address.get("ort") or meta_location.get("ort"))
    plz = normalize_text(details_address.get("plz") or meta_location.get("plz"))
    if not any([country, region, city, plz]):
        return ""
    return "|".join([country, region, city, plz])


def _build_employment_summary(details: dict[str, Any]) -> str:
    details = details or {}
    vollzeit = bool(details.get("arbeitszeitVollzeit", False))
    teilzeit = bool(
        details.get("arbeitszeitTeilzeitVormittag", False)
        or details.get("arbeitszeitTeilzeitNachmittag", False)
        or details.get("arbeitszeitTeilzeitAbend", False)
        or details.get("arbeitszeitTeilzeitFlexibel", False)
    )
    minijob = bool(details.get("istGeringfuegigeBeschaeftigung", False))
    vertragsdauer = normalize_text(details.get("vertragsdauer"))
    return (
        f"vollzeit={str(vollzeit).lower()}; "
        f"teilzeit={str(teilzeit).lower()}; "
        f"vertragsdauer={vertragsdauer}; "
        f"minijob={str(minijob).lower()}"
    )


class MergedVacancyRaw(BaseModel):
    """Сырой merged-объект до строгой нормализации и canonical-сборки."""

    referenznummer: str = Field(min_length=1)
    title: str | None = None
    role: str | None = None
    alt_roles_raw: list[Any] = Field(default_factory=list)
    location_summary: str | None = None
    employment_summary: str | None = None
    description: str | None = None

    @classmethod
    def from_sources(cls, meta: dict[str, Any], details: dict[str, Any]) -> MergedVacancyRaw:
        """
        Построить raw-объект из source-документов `stellenangebote` и `jobdetails`.

        Приоритеты полей соответствуют архитектурному spec (title/role fallback).
        """
        meta = meta or {}
        details = details or {}
        alt_roles_raw: list[Any] = []
        for index in range(1, 6):
            key = f"alternativBeruf{index}"
            if key in details:
                alt_roles_raw.append(details.get(key))

        referenznummer = normalize_text(details.get("referenznummer") or meta.get("referenznummer"))
        return cls(
            referenznummer=referenznummer,
            title=normalize_text(details.get("stellenangebotsTitel") or meta.get("titel")),
            role=normalize_text(details.get("hauptberuf") or meta.get("beruf")),
            alt_roles_raw=alt_roles_raw,
            location_summary=_build_location_summary(meta=meta, details=details),
            employment_summary=_build_employment_summary(details=details),
            description=normalize_text(details.get("stellenangebotsBeschreibung")),
        )


class MergedVacancyNormalized(BaseModel):
    """Нормализованный объект, готовый к chunking/embedding."""

    referenznummer: str = Field(min_length=1)
    title: str = ""
    role: str = ""
    alt_roles: list[str] = Field(default_factory=list)
    location_summary: str = ""
    employment_summary: str = ""
    description: str = ""
    description_missing: bool = False

    @classmethod
    def from_raw(cls, raw: MergedVacancyRaw) -> MergedVacancyNormalized:
        title = normalize_text(raw.title)
        role = normalize_text(raw.role)
        description = normalize_text(raw.description)
        location = normalize_text(raw.location_summary)
        employment = normalize_text(raw.employment_summary)
        alt_roles = normalize_alt_roles(raw.alt_roles_raw)
        return cls(
            referenznummer=normalize_text(raw.referenznummer),
            title=title,
            role=role,
            alt_roles=alt_roles,
            location_summary=location,
            employment_summary=employment,
            description=description,
            description_missing=description == "",
        )

    def canonical_text(self) -> str:
        """
        Построить каноничный текст вакансии по фиксированному шаблону.
        """
        alt_roles_str = ", ".join(self.alt_roles)
        return (
            f"TITLE: {self.title}\n"
            f"ROLE: {self.role}\n"
            f"ALT_ROLES: {alt_roles_str}\n"
            f"LOCATION: {self.location_summary}\n"
            f"EMPLOYMENT: {self.employment_summary}\n"
            "DESCRIPTION:\n"
            f"{self.description}"
        )


class ChunkRecord(BaseModel):
    """Структура чанка до/после векторизации."""

    referenznummer: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = ""
    section_label: str = "unknown"
    section_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description_missing: bool = False


class RuleEvaluationResult(BaseModel):
    """Результат работы rules-слоя для одной вакансии."""

    rule_set_version: str = "v1"
    applied_rules: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)
    needs_reindex: bool = False
    skip_reason: str | None = None

