"""Small throttling helpers for Jobcenter API collectors.

The first safety layer is request jitter: collectors wait a randomized delay
between API calls, so large runs avoid bursty traffic against the upstream API.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import random
import time
from typing import Any, Callable


@dataclass(frozen=True)
class RequestJitter:
    min_seconds: float
    max_seconds: float

    @property
    def enabled(self) -> bool:
        return self.max_seconds > 0


def parse_request_jitter(value: Any, *, default: RequestJitter) -> RequestJitter:
    """Parse jitter from config/ENV.

    Accepted forms:
    - `{"min": 0.5, "max": 5.0}`
    - `[0.5, 5.0]` or `(0.5, 5.0)`
    - `"0.5,5.0"` or `"0.5:5.0"`
    - `0` to disable jitter
    """
    if value is None:
        return default

    if isinstance(value, dict):
        min_value = value.get("min", value.get("min_seconds", default.min_seconds))
        max_value = value.get("max", value.get("max_seconds", default.max_seconds))
        return _build_jitter(min_value, max_value)

    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError("request_jitter_seconds list must contain exactly 2 values")
        return _build_jitter(value[0], value[1])

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        separator = "," if "," in raw else ":"
        parts = [part.strip() for part in raw.split(separator)]
        if len(parts) == 1:
            return _build_jitter(0.0, parts[0])
        if len(parts) != 2:
            raise ValueError("request jitter string must be 'min,max' or 'min:max'")
        return _build_jitter(parts[0], parts[1])

    return _build_jitter(0.0, value)


def request_jitter_from_config(
    api_config: dict[str, Any],
    *,
    env_var: str,
    default: RequestJitter,
) -> RequestJitter:
    """Resolve jitter with priority: endpoint ENV -> global ENV -> config -> default."""
    env_value = os.getenv(env_var)
    if env_value is not None:
        return parse_request_jitter(env_value, default=default)

    global_env_value = os.getenv("JOBCENTER_REQUEST_JITTER_SECONDS")
    if global_env_value is not None:
        return parse_request_jitter(global_env_value, default=default)

    return parse_request_jitter(api_config.get("request_jitter_seconds"), default=default)


def sleep_with_jitter(
    jitter: RequestJitter,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    """Sleep for a random jitter interval and return the chosen delay."""
    if not jitter.enabled:
        return 0.0
    delay = uniform(jitter.min_seconds, jitter.max_seconds)
    if delay > 0:
        sleeper(delay)
    return delay


def _build_jitter(min_value: Any, max_value: Any) -> RequestJitter:
    min_seconds = max(0.0, float(min_value))
    max_seconds = max(0.0, float(max_value))
    if max_seconds < min_seconds:
        raise ValueError("request_jitter_seconds max must be >= min")
    return RequestJitter(min_seconds=min_seconds, max_seconds=max_seconds)
