import pandas as pd
import pytest

from app.jobcenter import plz as plz_module


def test_parse_plz_list_removes_invalid_values_and_duplicates():
    result = plz_module.parse_plz_list("80331 80333 x 80331 1234 93047")

    assert result == ["80331", "80333", "93047"]


def test_is_country_wide_bundesland_accepts_known_aliases():
    assert plz_module.is_country_wide_bundesland("Deutschland") is True
    assert plz_module.is_country_wide_bundesland("Deutchland") is True
    assert plz_module.is_country_wide_bundesland("Germany") is True
    assert plz_module.is_country_wide_bundesland("Bayern") is False


def test_resolve_city_to_plz_filters_strict_city_and_bundesland(monkeypatch):
    frame = pd.DataFrame(
        [
            {"postal_code": "80331", "place_name": "München", "community_name": "München", "state_name": "Bayern"},
            {"postal_code": "80333", "place_name": "München", "community_name": "München", "state_name": "Bayern"},
            {"postal_code": "76125", "place_name": "Aachener und Münchener Versicherungen", "community_name": "", "state_name": "Baden-Württemberg"},
            {"postal_code": "99999", "place_name": "München", "community_name": "München", "state_name": "Sachsen"},
        ]
    )
    monkeypatch.setattr(plz_module, "_load_pgeocode_frame", lambda country: frame.copy())

    result = plz_module.resolve_city_to_plz("Muenchen", bundesland="Bayern")

    assert result == ["80331", "80333"]


@pytest.mark.parametrize('name', ['Bayern', 'Bayern (Bundesland)'])
def test_resolve_bundesland_to_plz_accepts_german_aliases(monkeypatch, name):
    frame = pd.DataFrame(
        [
            {"postal_code": "80331", "place_name": "München", "community_name": "München", "state_name": "Bavaria"},
            {"postal_code": "90402", "place_name": "Nürnberg", "community_name": "Nürnberg", "state_name": "Bayern"},
            {"postal_code": "10115", "place_name": "Berlin", "community_name": "Berlin", "state_name": "Berlin"},
        ]
    )
    monkeypatch.setattr(plz_module, "_load_pgeocode_frame", lambda country: frame.copy())

    result = plz_module.resolve_bundesland_to_plz(name)

    assert result == ["80331", "90402"]


def test_resolve_bundesland_to_plz_countrywide_returns_all_unique_plz(monkeypatch):
    frame = pd.DataFrame(
        [
            {"postal_code": "80331", "place_name": "München", "community_name": "München", "state_name": "Bayern"},
            {"postal_code": "10115", "place_name": "Berlin", "community_name": "Berlin", "state_name": "Berlin"},
            {"postal_code": "80331", "place_name": "München", "community_name": "München", "state_name": "Bayern"},
        ]
    )
    monkeypatch.setattr(plz_module, "_load_pgeocode_frame", lambda country: frame.copy())

    result = plz_module.resolve_bundesland_to_plz("Deutschland")

    assert result == ["10115", "80331"]
