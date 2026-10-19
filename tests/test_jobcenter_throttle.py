from app.jobcenter.throttle import (
    RequestJitter,
    parse_request_jitter,
    request_jitter_from_config,
    sleep_with_jitter,
)


def test_parse_request_jitter_from_dict():
    jitter = parse_request_jitter({"min": 0.25, "max": 1.5}, default=RequestJitter(0.5, 2.0))

    assert jitter == RequestJitter(0.25, 1.5)


def test_parse_request_jitter_from_env_style_string():
    jitter = parse_request_jitter("0.5,5.0", default=RequestJitter(0.0, 0.0))

    assert jitter == RequestJitter(0.5, 5.0)


def test_parse_request_jitter_zero_disables_sleep():
    sleeps: list[float] = []
    jitter = parse_request_jitter("0", default=RequestJitter(0.5, 2.0))

    delay = sleep_with_jitter(jitter, sleeper=sleeps.append, uniform=lambda _a, _b: 1.0)

    assert delay == 0.0
    assert sleeps == []


def test_sleep_with_jitter_uses_configured_range():
    sleeps: list[float] = []
    uniform_calls: list[tuple[float, float]] = []

    def fake_uniform(min_seconds: float, max_seconds: float) -> float:
        uniform_calls.append((min_seconds, max_seconds))
        return 1.25

    delay = sleep_with_jitter(
        RequestJitter(0.5, 5.0),
        sleeper=sleeps.append,
        uniform=fake_uniform,
    )

    assert uniform_calls == [(0.5, 5.0)]
    assert delay == 1.25
    assert sleeps == [1.25]


def test_request_jitter_from_config_prefers_endpoint_env(monkeypatch):
    monkeypatch.setenv("JOBCENTER_REQUEST_JITTER_SECONDS", "1,2")
    monkeypatch.setenv("JOBCENTER_LIST_REQUEST_JITTER_SECONDS", "3,4")

    jitter = request_jitter_from_config(
        {"request_jitter_seconds": {"min": 5, "max": 6}},
        env_var="JOBCENTER_LIST_REQUEST_JITTER_SECONDS",
        default=RequestJitter(0.5, 2.0),
    )

    assert jitter == RequestJitter(3.0, 4.0)
