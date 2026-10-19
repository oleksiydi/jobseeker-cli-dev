#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dry-run probe for Jobcenter list API pagination.

The script intentionally does not write to MongoDB. It reuses the same list API
configuration as app.job_list_collector and prints page-level statistics so we
can inspect API result-window behavior safely.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.jobcenter.throttle import RequestJitter, request_jitter_from_config, sleep_with_jitter


def _extract_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    status_code = _extract_status_code(exc)
    if status_code == 429:
        return True
    return isinstance(status_code, int) and 500 <= status_code < 600


def _fetch_page(
    *,
    session: requests.Session,
    api_url: str,
    headers: dict[str, Any],
    params: dict[str, Any],
    page: int,
    timeout_seconds: int,
    max_attempts: int,
    base_backoff_seconds: float,
    log: Any,
) -> dict[str, Any]:
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(api_url, headers=headers, params=params, timeout=timeout_seconds)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if not _is_retryable_error(exc) or attempt >= attempts:
                raise
            delay = base_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 0.3)
            log.warning(
                "page=%s temporary_error=%s retry=%s/%s sleep=%.2fs",
                page,
                exc,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)


def _normalize_ref(item: dict[str, Any]) -> str | None:
    ref = item.get("referenznummer") or item.get("refnr")
    if ref is None:
        return None
    return str(ref).strip() or None


def _parse_extra_param(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--param must be KEY=VALUE")
    key, raw_value = value.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("--param key must not be empty")
    return key, raw_value.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe Jobcenter list API pages without writing to MongoDB."
    )
    parser.add_argument("--start-page", type=int, default=1, help="First page to fetch.")
    parser.add_argument(
        "--interval-pages",
        type=int,
        default=5,
        help="How many consecutive pages to fetch in this probe run.",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        type=_parse_extra_param,
        help="Override or add request param as KEY=VALUE. Can be used multiple times.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--no-jitter", action="store_true", help="Disable request jitter for this probe.")
    args = parser.parse_args()

    if args.start_page < 1:
        raise ValueError("--start-page must be >= 1")
    if args.interval_pages < 1:
        raise ValueError("--interval-pages must be >= 1")

    log = get_logger("probe_job_list_pages")
    cfg = load_app_config()
    list_api = cfg["jobcenter"]["list_api"]

    api_url = list_api["api_url"]
    api_key = os.getenv("JOBCENTER_LIST_API_KEY", str(list_api.get("api_key", ""))).strip()
    base_params = dict(list_api.get("params", {}))
    for key, value in args.param:
        base_params[key] = value

    headers = dict(list_api.get("headers", {}))
    if api_key:
        headers["X-API-Key"] = api_key

    retry_attempts = max(1, int(list_api.get("retry_attempts", 3)))
    retry_backoff = max(0.0, float(list_api.get("retry_backoff_seconds", 1.0)))
    jitter = (
        RequestJitter(0.0, 0.0)
        if args.no_jitter
        else request_jitter_from_config(
            list_api,
            env_var="JOBCENTER_LIST_REQUEST_JITTER_SECONDS",
            default=RequestJitter(0.5, 5.0),
        )
    )

    end_page = args.start_page + args.interval_pages - 1
    log.info(
        "probe_started pages=%s-%s api_url=%s params=%s jitter=%.2f-%.2fs",
        args.start_page,
        end_page,
        api_url,
        base_params,
        jitter.min_seconds,
        jitter.max_seconds,
    )

    session = requests.Session()
    total_seen = 0
    unique_refs: set[str] = set()
    completed_pages = 0

    for page in range(args.start_page, end_page + 1):
        params = {**base_params, "page": page}
        try:
            data = _fetch_page(
                session=session,
                api_url=api_url,
                headers=headers,
                params=params,
                page=page,
                timeout_seconds=args.timeout_seconds,
                max_attempts=retry_attempts,
                base_backoff_seconds=retry_backoff,
                log=log,
            )
        except Exception as exc:
            status_code = _extract_status_code(exc)
            log.error("page=%s status=%s error=%s", page, status_code, exc)
            break

        # BA API v6 uses `ergebnisliste`; keep v4 support for historical probes.
        items = data.get("ergebnisliste")
        if items is None:
            items = data.get("stellenangebote")
        if not isinstance(items, list):
            items = []
        refs = [_normalize_ref(item) for item in items]
        refs = [ref for ref in refs if ref]
        total_seen += len(items)
        unique_refs.update(refs)
        completed_pages += 1

        print(
            "page={page} seen={seen} refs={refs_count} unique_refs_total={unique_refs_total} "
            "maxErgebnisse={max_results}".format(
                page=page,
                seen=len(items),
                refs_count=len(refs),
                unique_refs_total=len(unique_refs),
                max_results=data.get("maxErgebnisse"),
            )
        )

        if not items:
            log.info("page=%s empty_results stop", page)
            break

        if page < end_page:
            sleep_with_jitter(jitter)

    print(
        "summary start_page={start} end_page={end} completed_pages={completed} "
        "seen={seen} unique_refs={unique_refs}".format(
            start=args.start_page,
            end=end_page,
            completed=completed_pages,
            seen=total_seen,
            unique_refs=len(unique_refs),
        )
    )


if __name__ == "__main__":
    main()
