from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import app.job_details_collector as job_details_collector


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class FakeListCollection:
    def __init__(self, docs):
        self.docs = docs
        self.find_query = None
        self.find_projection = None

    def find(self, query=None, projection=None):
        self.find_query = query
        self.find_projection = projection
        refs = query.get("referenznummer", {}).get("$in", []) if query else []
        ref_set = set(refs)
        return [doc for doc in self.docs if doc.get("referenznummer") in ref_set]


class FakeListHistoryCollection:
    def __init__(self, docs):
        self.docs = docs
        self.find_query = None
        self.find_projection = None

    def find(self, query=None, projection=None):
        self.find_query = query
        self.find_projection = projection
        return list(self.docs)


class FakeStateCollection:
    def __init__(self, doc):
        self.doc = doc
        self.find_one_query = None

    def find_one(self, query=None, projection=None, sort=None):
        self.find_one_query = query
        return self.doc


class FakeDetailsCollection:
    def __init__(self, docs):
        self.docs = docs
        self.find_queries = []
        self.update_calls = []
        self.insert_calls = []

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None, projection=None):
        self.find_queries.append(query)
        refs = query.get("referenznummer", {}).get("$in", []) if query else []
        ref_set = set(refs)
        return [doc for doc in self.docs if doc.get("referenznummer") in ref_set]

    def update_one(self, query, update):
        self.update_calls.append((query, update))
        return None

    def insert_one(self, doc):
        self.insert_calls.append(doc)
        return None


class FakeHistoryCollection:
    def __init__(self):
        self.insert_calls = []

    def create_index(self, *args, **kwargs):
        return None

    def insert_one(self, doc):
        self.insert_calls.append(doc)
        return None


class FakeDB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, key):
        return self.collections[key]


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


def test_prefilter_skips_guaranteed_and_calls_api_only_for_candidates():
    list_col = FakeListCollection(
        [
            {
                "referenznummer": "skip-ref",
                "modifikationsTimestamp": "2026-02-10T08:00:00",
                "modifikationsTimestamp_dt": datetime(2026, 2, 10, 7, 0, tzinfo=timezone.utc),
            },
            {
                "referenznummer": "verify-ref",
                "modifikationsTimestamp": "2026-02-10T08:00:00",
                "modifikationsTimestamp_dt": datetime(2026, 2, 10, 7, 0, tzinfo=timezone.utc),
            },
        ]
    )
    list_history_col = FakeListHistoryCollection(
        [
            {"referenznummer": "skip-ref"},
            {"referenznummer": "verify-ref"},
            {"referenznummer": "old-active-ref"},
        ]
    )
    state_col = FakeStateCollection(
        {"key": "job_list_collector_query_scope", "last_run_id": "list-1", "last_completed_full_scan": True}
    )
    det_col = FakeDetailsCollection(
        [
            {
                "_id": "skip-id",
                "referenznummer": "skip-ref",
                "details_hash": "hash-skip",
                "last_seen_remote_updated_at_dt": datetime(2026, 2, 10, 7, 0, tzinfo=timezone.utc),
            },
            {
                "_id": "verify-id",
                "referenznummer": "verify-ref",
                "details_hash": "old-hash",
                "titel": "old-title",
                "last_seen_remote_updated_at_dt": datetime(2026, 2, 10, 6, 0, tzinfo=timezone.utc),
            },
        ]
    )
    history_col = FakeHistoryCollection()
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "stellenangebote_history": list_history_col,
            "jobdetails": det_col,
            "jobdetails_history": history_col,
            "pipeline_state": state_col,
        }
    )
    cfg = {
        "collections": {"list": "stellenangebote", "details": "jobdetails"},
        "history_collections": {"list": "stellenangebote_history", "details": "jobdetails_history"},
        "jobcenter": {"details_api": {"url_template": "https://example/{ref_b64}", "headers": {}}},
    }

    fake_session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "aenderungsdatum": "2026-02-10T08:00:00",
                    "titel": "new-title",
                },
            )
        ]
    )

    with (
        patch.object(job_details_collector, "load_app_config", return_value=cfg),
        patch.object(job_details_collector, "get_database", return_value=db),
        patch.object(job_details_collector, "get_logger", return_value=FakeLogger()),
        patch.object(job_details_collector.requests, "Session", return_value=fake_session),
        patch.object(job_details_collector.time, "sleep", return_value=None),
        patch.object(job_details_collector.random, "uniform", return_value=1.0),
    ):
        job_details_collector.main()

    assert state_col.find_one_query == {"key": "job_list_collector_query_scope"}
    assert list_history_col.find_query == {"event_type": "list_seen", "run_id": "list-1"}
    assert list_col.find_query == {"referenznummer": {"$in": ["old-active-ref", "skip-ref", "verify-ref"]}}
    assert det_col.find_queries == [{"referenznummer": {"$in": ["skip-ref", "verify-ref"]}}]
    assert len(fake_session.calls) == 1
    assert "dmVyaWZ5LXJlZg" in fake_session.calls[0]["url"]
    assert len(det_col.update_calls) == 1


def test_retry_delay_seconds_uses_predictable_exponential_backoff(monkeypatch):
    monkeypatch.setattr(job_details_collector.random, "uniform", lambda a, b: 0.3)
    assert job_details_collector._retry_delay_seconds(0, base_delay_seconds=1.0) == 1.3
    assert job_details_collector._retry_delay_seconds(1, base_delay_seconds=1.0) == 2.3
    assert job_details_collector._retry_delay_seconds(2, base_delay_seconds=1.0) == 4.3


def test_verify_candidates_continues_after_single_mongo_write_error():
    class WriteFailingDetailsCollection:
        def __init__(self):
            self.inserted = []

        def insert_one(self, doc):
            if doc.get("referenznummer") == "bad-ref":
                raise RuntimeError("temporary mongo write error")
            self.inserted.append(doc)
            return None

        def update_one(self, query, update):
            return None

    class HistoryCollection:
        def __init__(self):
            self.inserted = []

        def insert_one(self, doc):
            self.inserted.append(doc)
            return None

    class SequenceSession:
        def __init__(self, responses):
            self._responses = list(responses)

        def get(self, url, headers=None, timeout=None):
            return self._responses.pop(0)

    det_col = WriteFailingDetailsCollection()
    history_col = HistoryCollection()
    session = SequenceSession(
        [
            FakeResponse(200, {"aenderungsdatum": "2026-02-10T08:00:00", "titel": "bad"}),
            FakeResponse(200, {"aenderungsdatum": "2026-02-10T08:00:00", "titel": "good"}),
        ]
    )
    candidates = [
        {"referenznummer": "bad-ref", "list_modifikations_timestamp": "2026-02-10T08:00:00", "current": None},
        {"referenznummer": "good-ref", "list_modifikations_timestamp": "2026-02-10T08:00:00", "current": None},
    ]
    log = FakeLogger()

    with (
        patch.object(job_details_collector.time, "sleep", return_value=None),
        patch.object(job_details_collector.random, "uniform", return_value=0.0),
    ):
        inserted, updated = job_details_collector._verify_and_apply_candidates(
            session=session,
            candidates=candidates,
            det_col=det_col,
            det_history_col=history_col,
            history_collection_name="jobdetails_history",
            headers={},
            url_tpl="https://example/{ref_b64}",
            log=log,
        )

    assert inserted == 1
    assert updated == 0
    assert [doc["referenznummer"] for doc in det_col.inserted] == ["good-ref"]
