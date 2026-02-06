from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def utcnow_naive() -> datetime:
    """Naive UTC time, kept for backward compatibility with existing fields."""
    return datetime.utcnow()


def pad_fraction_to_6(value: str) -> str:
    """
    Normalize datetime fraction to microseconds for robust ISO parsing.
    """
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})?$",
        value or "",
    )
    if not match:
        return value

    base, fraction, tz = match.group(1), match.group(2), match.group(3)
    if fraction:
        digits = (fraction[1:] + "000000")[:6]
        fraction = f".{digits}"
    else:
        fraction = ""
    return f"{base}{fraction}{tz or ''}"


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse known datetime formats into timezone-aware UTC datetime."""
    if not value:
        return None

    normalized = pad_fraction_to_6(value.strip()).replace("z", "Z")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            continue
    return None

