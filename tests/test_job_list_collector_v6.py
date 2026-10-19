from datetime import datetime, timezone

from app.job_list_collector import (
    _add_legacy_v4_aliases,
    _build_diff_update,
    _extract_list_items,
    _normalize_list_updated_at,
)


def test_extract_list_items_reads_v6_ergebnisliste():
    payload = {"ergebnisliste": [{"referenznummer": "V6-1"}]}

    assert _extract_list_items(payload) == [{"referenznummer": "V6-1"}]


def test_extract_list_items_keeps_legacy_v4_compatibility():
    payload = {"stellenangebote": [{"referenznummer": "V4-1"}]}

    assert _extract_list_items(payload) == [{"referenznummer": "V4-1"}]


def test_normalize_list_updated_at_maps_v6_date_to_details_contract():
    item = {"aenderungsdatum": "2026-08-04T07:48:02.933"}

    _normalize_list_updated_at(item)

    assert item["modifikationsTimestamp"] == item["aenderungsdatum"]
    assert item["modifikationsTimestamp_dt"] is not None


def test_v6_keeps_legacy_aliases_and_preserves_fields_absent_from_v6_response():
    v6_item = {
        "stellenangebotsTitel": "Neue Titelbezeichnung",
        "firma": "Neue Firma GmbH",
        "hauptberuf": "Softwareentwickler/in",
        "stellenlokationen": [{"adresse": {"plz": "93051", "ort": "Regensburg"}}],
    }

    _add_legacy_v4_aliases(v6_item)

    assert v6_item["titel"] == "Neue Titelbezeichnung"
    assert v6_item["arbeitgeber"] == "Neue Firma GmbH"
    assert v6_item["beruf"] == "Softwareentwickler/in"
    assert v6_item["arbeitsort"] == {"plz": "93051", "ort": "Regensburg"}

    update = _build_diff_update(
        {"titel": "Alt", "legacy_only_field": "behalten"},
        v6_item,
        "new-hash",
        datetime.now(timezone.utc),
        preserve_fields_missing_from_response=True,
    )
    assert "legacy_only_field" not in update.get("$unset", {})
