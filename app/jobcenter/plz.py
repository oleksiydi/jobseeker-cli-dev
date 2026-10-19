from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import pandas as pd
import pgeocode


_PLZ_RE = re.compile(r"^\d{5}$")
_COUNTRY_WIDE_ALIASES = {"deutschland", "deutchland", "germany", "de"}
_BUNDESLAND_ALIAS_GROUPS = {
    "baden-wuerttemberg": {"baden-württemberg", "baden wuerttemberg", "baden-wuerttemberg"},
    "bayern": {"bayern", "bavaria"},
    "berlin": {"berlin", "land berlin"},
    "brandenburg": {"brandenburg"},
    "bremen": {"bremen"},
    "hamburg": {"hamburg"},
    "hessen": {"hessen", "hesse"},
    "mecklenburg-vorpommern": {
        "mecklenburg-vorpommern",
        "mecklenburg western pomerania",
        "mecklenburg-western pomerania",
    },
    "niedersachsen": {"niedersachsen", "lower saxony"},
    "nordrhein-westfalen": {"nordrhein-westfalen", "north rhine-westphalia"},
    "rheinland-pfalz": {"rheinland-pfalz", "rhineland-palatinate"},
    "saarland": {"saarland"},
    "sachsen": {"sachsen", "saxony"},
    "sachsen-anhalt": {"sachsen-anhalt", "saxony-anhalt"},
    "schleswig-holstein": {"schleswig-holstein"},
    "thueringen": {"thüringen", "thueringen", "thuringia"},
}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    return " ".join(text.split())


def parse_plz_list(raw: str) -> list[str]:
    if not raw:
        return []
    result: list[str] = []
    for part in str(raw).split():
        value = part.strip()
        if not value or not _PLZ_RE.fullmatch(value):
            continue
        if value not in result:
            result.append(value)
    return sorted(result)


def is_country_wide_bundesland(value: str) -> bool:
    return normalize_text(value) in _COUNTRY_WIDE_ALIASES


def resolve_city_to_plz(city: str, *, bundesland: str | None = None, country: str = "de") -> list[str]:
    city_norm = normalize_text(city)
    if not city_norm:
        return []

    frame = _load_pgeocode_frame(country)
    if frame.empty:
        return []

    city_variants = _lookup_variants(city)
    city_mask = _normalized_series(frame, "place_name").isin(city_variants) | _normalized_series(
        frame, "community_name"
    ).isin(city_variants)
    filtered = frame[city_mask]
    if bundesland:
        filtered = _filter_by_bundesland(filtered, bundesland)
    return _postal_codes_from_frame(filtered)


def resolve_bundesland_to_plz(bundesland: str, *, country: str = "de") -> list[str]:
    bundesland_norm = normalize_text(bundesland)
    if not bundesland_norm:
        return []

    frame = _load_pgeocode_frame(country)
    if frame.empty:
        return []

    if is_country_wide_bundesland(bundesland_norm):
        return _postal_codes_from_frame(frame)

    return _postal_codes_from_frame(_filter_by_bundesland(frame, bundesland))


def _filter_by_bundesland(frame: pd.DataFrame, bundesland: str) -> pd.DataFrame:
    aliases = _bundesland_aliases(bundesland)
    if not aliases:
        return frame.iloc[0:0]
    state_mask = _normalized_series(frame, "state_name").isin(aliases)
    return frame[state_mask]


def _postal_codes_from_frame(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "postal_code" not in frame.columns:
        return []
    postal_codes = frame["postal_code"].dropna().astype(str).str.strip()
    postal_codes = postal_codes[postal_codes.map(lambda value: bool(_PLZ_RE.fullmatch(value)))]
    return sorted(set(postal_codes.tolist()))


def _normalized_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).map(normalize_text)


def _bundesland_aliases(value: str) -> set[str]:
    # BA autocomplete adds a territory-type suffix to browser URLs; the postal
    # reference uses the state name alone. Only normalize this lookup value.
    lookup = re.sub(r'\s*\(Bundesland\)\s*$', '', value, flags=re.IGNORECASE)
    normalized = normalize_text(lookup)
    aliases = {normalized}
    aliases.update(_lookup_variants(lookup))
    for group in _BUNDESLAND_ALIAS_GROUPS.values():
        normalized_group = {normalize_text(item) for item in group}
        if normalized in normalized_group or aliases & normalized_group:
            aliases.update(normalized_group)
    return aliases


def _lookup_variants(value: str) -> set[str]:
    normalized = normalize_text(value)
    variants = {normalized}
    replacements = (
        ("ae", "ä"),
        ("oe", "ö"),
        ("ue", "ü"),
        ("ss", "ß"),
    )
    candidate = normalized
    for source, target in replacements:
        candidate = candidate.replace(source, target)
    variants.add(candidate)
    return {item for item in variants if item}


@lru_cache(maxsize=4)
def _load_pgeocode_frame_cached(country: str) -> pd.DataFrame:
    nominatim = pgeocode.Nominatim(country)
    frame = getattr(nominatim, "_data", None)
    if frame is None:
        return pd.DataFrame()
    return frame.copy()


def _load_pgeocode_frame(country: str) -> pd.DataFrame:
    return _load_pgeocode_frame_cached(country).copy()
