from datetime import timezone

from app.core.time_utils import parse_remote_dt


def test_parse_remote_dt_interprets_naive_as_berlin_winter():
    dt = parse_remote_dt("2026-01-22T07:53:46.502")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.isoformat() == "2026-01-22T06:53:46.502000+00:00"


def test_parse_remote_dt_interprets_naive_as_berlin_summer():
    dt = parse_remote_dt("2026-07-22T07:53:46.502")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.isoformat() == "2026-07-22T05:53:46.502000+00:00"
