from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.jobcenter.plz import (
    is_country_wide_bundesland,
    parse_plz_list,
    resolve_bundesland_to_plz,
    resolve_city_to_plz,
)


@dataclass(frozen=True)
class PhysicalListQuery:
    shard_id: str
    plz: str | None
    params: dict[str, Any]


@dataclass(frozen=True)
class QueryExpansionResult:
    mode: str
    logical_query_mode: str
    dry_run: bool
    max_pages_per_physical_query: int | None
    resolved_plz: list[str]
    physical_queries: list[PhysicalListQuery]
    scope_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "logical_query_mode": self.logical_query_mode,
            "dry_run": self.dry_run,
            "max_pages_per_physical_query": self.max_pages_per_physical_query,
            "resolved_plz": list(self.resolved_plz),
            "physical_queries": [asdict(query) for query in self.physical_queries],
            "scope_payload": self.scope_payload,
        }


def expand_list_query(list_api_cfg: dict[str, Any]) -> QueryExpansionResult:
    base_params = dict(list_api_cfg.get("params", {}))
    search_area = dict(list_api_cfg.get("search_area", {}))
    mode = str(search_area.get("mode", "legacy")).strip().lower() or "legacy"
    dry_run = bool(search_area.get("dry_run", False))
    if mode not in {"legacy", "plz"}:
        raise ValueError(f"Unsupported search_area.mode={mode!r}. Expected 'legacy' or 'plz'.")

    if mode != "plz":
        return QueryExpansionResult(
            mode="legacy",
            logical_query_mode="legacy",
            dry_run=dry_run,
            max_pages_per_physical_query=None,
            resolved_plz=[],
            physical_queries=[PhysicalListQuery(shard_id="legacy", plz=None, params=base_params)],
            scope_payload={
                "mode": "legacy",
                "base_params": base_params,
            },
        )

    country = str(search_area.get("country", "de")).strip() or "de"
    max_plz_per_run = max(1, int(search_area.get("max_plz_per_run", 200)))
    plz_query_umkreis = int(search_area.get("plz_query_umkreis", 0))
    max_pages_per_physical_query = max(1, int(search_area.get("max_pages_per_physical_query", 50)))
    explicit_plz = parse_plz_list(str(search_area.get("plz", "")))
    bundesland = str(search_area.get("bundesland", "")).strip()
    city = str(search_area.get("city", "")).strip()

    if explicit_plz:
        logical_query_mode = "explicit_plz"
        resolved_plz = explicit_plz
    elif bundesland and is_country_wide_bundesland(bundesland):
        logical_query_mode = "countrywide"
        resolved_plz = resolve_bundesland_to_plz(bundesland, country=country)
    elif bundesland and city:
        logical_query_mode = "bundesland_city"
        resolved_plz = resolve_city_to_plz(city=city, bundesland=bundesland, country=country)
    elif bundesland:
        logical_query_mode = "bundesland"
        resolved_plz = resolve_bundesland_to_plz(bundesland, country=country)
    else:
        raise ValueError("PLZ mode requires either explicit plz, bundesland, or bundesland+city.")

    if not resolved_plz:
        raise ValueError("PLZ resolution returned no postal codes for the configured search area.")
    if len(resolved_plz) > max_plz_per_run:
        raise ValueError(
            f"Resolved {len(resolved_plz)} PLZ, which exceeds max_plz_per_run={max_plz_per_run}."
        )

    physical_queries = [
        PhysicalListQuery(
            shard_id=f"plz-{index + 1:04d}-{plz}",
            plz=plz,
            params=_build_plz_params(base_params, plz=plz, umkreis=plz_query_umkreis),
        )
        for index, plz in enumerate(resolved_plz)
    ]

    return QueryExpansionResult(
        mode="plz",
        logical_query_mode=logical_query_mode,
        dry_run=dry_run,
        max_pages_per_physical_query=max_pages_per_physical_query,
        resolved_plz=resolved_plz,
        physical_queries=physical_queries,
        scope_payload={
            "mode": "plz",
            "logical_query_mode": logical_query_mode,
            "country": country,
            "resolved_plz": resolved_plz,
            "plz_query_umkreis": plz_query_umkreis,
            "max_pages_per_physical_query": max_pages_per_physical_query,
            "base_params_without_location": _strip_legacy_location_params(base_params),
        },
    )


def _build_plz_params(base_params: dict[str, Any], *, plz: str, umkreis: int) -> dict[str, Any]:
    params = dict(base_params)
    params["wo"] = plz
    params["umkreis"] = umkreis
    return params


def _strip_legacy_location_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    result.pop("wo", None)
    result.pop("umkreis", None)
    return result
