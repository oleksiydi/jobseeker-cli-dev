from __future__ import annotations

from app.matching.models import (
    MergedVacancyNormalized,
    MergedVacancyRaw,
    normalize_alt_roles,
    normalize_text,
)


def test_normalize_text_compacts_whitespace_and_lines() -> None:
    raw = "  Hallo   Welt \r\n\r\n  zweite   zeile  "
    normalized = normalize_text(raw)
    assert normalized == "Hallo Welt\n\nzweite zeile"


def test_normalize_alt_roles_drops_pseudo_empty_values() -> None:
    values = ["false", "", "  ", None, "null", "IT-Administrator/in", "IT-Administrator/in"]
    assert normalize_alt_roles(values) == ["IT-Administrator/in"]


def test_merged_raw_from_sources_uses_field_priority_and_fallbacks() -> None:
    meta = {
        "referenznummer": "R-1",
        "titel": "Meta Titel",
        "beruf": "Meta Beruf",
        "arbeitsort": {"land": "Deutschland", "region": "Bayern", "ort": "Regensburg", "plz": "93053"},
    }
    details = {
        "stellenangebotsTitel": "Details Titel",
        "hauptberuf": "Details Beruf",
        "alternativBeruf1": "false",
        "alternativBeruf2": "Datenbankadministrator/in",
        "stellenangebotsBeschreibung": "  Eine   Beschreibung ",
        "arbeitszeitVollzeit": True,
        "vertragsdauer": "UNBEFRISTET",
    }

    raw = MergedVacancyRaw.from_sources(meta=meta, details=details)
    assert raw.title == "Details Titel"
    assert raw.role == "Details Beruf"
    assert raw.referenznummer == "R-1"
    assert raw.description == "Eine Beschreibung"
    assert raw.alt_roles_raw == ["false", "Datenbankadministrator/in"]


def test_normalized_model_builds_canonical_text_and_description_missing() -> None:
    raw = MergedVacancyRaw(
        referenznummer="R-2",
        title="DBA Administrator",
        role="Datenbankadministrator/in",
        alt_roles_raw=["false", "IT-Administrator/in", ""],
        location_summary="DE|Bayern|Regensburg|93047",
        employment_summary="vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false",
        description="",
    )
    normalized = MergedVacancyNormalized.from_raw(raw)

    assert normalized.description_missing is True
    assert normalized.alt_roles == ["IT-Administrator/in"]

    expected = (
        "TITLE: DBA Administrator\n"
        "ROLE: Datenbankadministrator/in\n"
        "ALT_ROLES: IT-Administrator/in\n"
        "LOCATION: DE|Bayern|Regensburg|93047\n"
        "EMPLOYMENT: vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false\n"
        "DESCRIPTION:\n"
    )
    assert normalized.canonical_text() == expected

