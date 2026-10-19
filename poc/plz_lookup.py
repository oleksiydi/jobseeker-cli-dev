#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Simple POC: resolve German city name to PLZ list via pgeocode."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.error
from typing import Any

import pandas as pd
import pgeocode


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    return " ".join(text.split())


def _to_frame(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.Series):
        return result.to_frame().T
    if isinstance(result, pd.DataFrame):
        return result
    return pd.DataFrame()


def resolve_city_to_plz(city: str, country: str = "de", limit: int = 200) -> list[str]:
    city_clean = (city or "").strip()
    if not city_clean:
        return []
    if limit < 1:
        return []

    nominatim = pgeocode.Nominatim(country)
    frame = _to_frame(nominatim.query_location(city_clean, top_k=limit))
    if frame.empty or "postal_code" not in frame.columns:
        return []

    query_norm = _normalize_text(city_clean)
    place_col = frame.get("place_name")
    if place_col is not None:
        place_norm = place_col.astype(str).map(_normalize_text)
        strict = frame[place_norm.str.contains(query_norm, na=False)]
        if not strict.empty:
            frame = strict

    postal_codes = (
        frame["postal_code"]
        .dropna()
        .astype(str)
        .str.strip()
    )
    postal_codes = postal_codes[postal_codes.str.len() > 0]
    return sorted(set(postal_codes.tolist()))


def resolve_bundesland_to_plz(bundesland: str, country: str = "de") -> list[str]:
    bundesland_clean = (bundesland or "").strip()
    if not bundesland_clean:
        return []

    nominatim = pgeocode.Nominatim(country)
    frame = nominatim._data
    if frame.empty or "postal_code" not in frame.columns:
        return []

    query_norm = _normalize_text(bundesland_clean)
    all_country_aliases = {"deutschland", "deutchland", "germany", "de"}

    if query_norm in all_country_aliases:
        filtered = frame
    else:
        state_col = frame.get("state_name")
        if state_col is None:
            return []
        state_norm = state_col.astype(str).map(_normalize_text)
        filtered = frame[state_norm.str.contains(query_norm, na=False)]

    if filtered.empty:
        return []

    postal_codes = (
        filtered["postal_code"]
        .dropna()
        .astype(str)
        .str.strip()
    )
    postal_codes = postal_codes[postal_codes.str.len() > 0]
    return sorted(set(postal_codes.tolist()))


def main() -> int:
    parser = argparse.ArgumentParser(description="POC city/bundesland -> PLZ lookup via pgeocode")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--city", help="City name, e.g. 'Muenchen' or 'München'")
    mode.add_argument("--bundesland", help="Bundesland name, e.g. 'Bayern' or 'Deutschland'")
    parser.add_argument("--country", default="de", help="Country code (default: de)")
    parser.add_argument("--limit", type=int, default=200, help="Top-K rows from pgeocode query (default: 200)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print JSON output")
    args = parser.parse_args()

    city = (args.city or "").strip()
    bundesland = (args.bundesland or "").strip()
    if args.city and not city:
        print("Error: --city must not be empty.", file=sys.stderr)
        return 1
    if args.bundesland and not bundesland:
        print("Error: --bundesland must not be empty.", file=sys.stderr)
        return 1
    if args.limit < 1:
        print("Error: --limit must be >= 1.", file=sys.stderr)
        return 1

    try:
        if city:
            postal_codes = resolve_city_to_plz(city=city, country=args.country, limit=args.limit)
        else:
            postal_codes = resolve_bundesland_to_plz(bundesland=bundesland, country=args.country)
    except urllib.error.URLError as exc:
        print(
            f"Error: unable to load pgeocode data (network issue): {exc}.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Error: lookup failed: {exc}", file=sys.stderr)
        return 1

    if not postal_codes:
        target = city if city else bundesland
        print(f"No PLZ found for '{target}' (country={args.country}).", file=sys.stderr)
        return 1

    if args.as_json:
        payload = {
            "city": city or None,
            "bundesland": bundesland or None,
            "country": args.country,
            "postal_codes": postal_codes,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    label = city if city else bundesland
    print(f"PLZ for {label}:")
    for code in postal_codes:
        print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
