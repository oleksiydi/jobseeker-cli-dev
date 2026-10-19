from datetime import timezone

from app.job_list_collector import _compute_meta_hash, _compute_scope_hash
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


def test_compute_scope_hash_is_stable_for_same_scope():
    first = _compute_scope_hash(
        api_url="https://example.test/list",
        params={"wo": "Regensburg", "umkreis": 10},
    )
    second = _compute_scope_hash(
        api_url="https://example.test/list",
        params={"umkreis": 10, "wo": "Regensburg"},
    )
    assert first == second
