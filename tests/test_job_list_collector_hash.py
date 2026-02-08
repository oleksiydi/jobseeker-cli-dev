from datetime import timezone

from app.job_list_collector import _compute_meta_hash
from app.core.time_utils import parse_remote_dt


def test_compute_meta_hash_accepts_datetime_values():
    dt = parse_remote_dt("2026-01-22T07:53:46.502")
    assert dt is not None
    assert dt.tzinfo == timezone.utc

    payload = {
        "referenznummer": "R-1",
        "modifikationsTimestamp": "2026-01-22T07:53:46.502",
        "modifikationsTimestamp_dt": dt,
    }
    result = _compute_meta_hash(payload)

    assert isinstance(result, str)
    assert len(result) == 64
