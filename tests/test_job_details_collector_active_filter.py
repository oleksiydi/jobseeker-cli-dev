from __future__ import annotations

from unittest.mock import patch
import pytest

import app.job_details_collector as job_details_collector


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class FakeListCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.find_query = None
        self.find_projection = None

    def find(self, query=None, projection=None):
        self.find_query = query
        self.find_projection = projection
        refs = query.get("referenznummer", {}).get("$in", []) if query else []
        ref_set = set(refs)
        return [doc for doc in self.docs if doc.get("referenznummer") in ref_set]


class FakeListHistoryCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.find_query = None
        self.find_projection = None

    def find(self, query=None, projection=None):
        self.find_query = query
        self.find_projection = projection
        return list(self.docs)


class FakeStateCollection:
    def __init__(self, doc=None):
        self.doc = doc
        self.find_one_query = None

    def find_one(self, query=None, projection=None, sort=None):
        self.find_one_query = query
        return self.doc


class FakeCollection:
    def find(self, query=None, projection=None):
        return []

    def create_index(self, *args, **kwargs):
        return None

    def insert_one(self, doc):
        return None

    def update_one(self, query, update):
        return None


class FakeResponse:
    status_code = 404

    def json(self):
        return {}


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()


class FakeDB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, key):
        return self.collections[key]


@pytest.mark.parametrize('limited', [False, True])
def test_main_reads_only_refs_from_last_successful_list_run(limited):
    list_col = FakeListCollection(
        [
            {"referenznummer": "A", "modifikationsTimestamp": "2026-05-30T10:00:00"},
            {"referenznummer": "B", "modifikationsTimestamp": "2026-05-30T10:00:00"},
            {"referenznummer": "OLD", "modifikationsTimestamp": "2026-05-01T10:00:00"},
        ]
    )
    list_history_col = FakeListHistoryCollection(
        [
            {"referenznummer": "B"},
            {"referenznummer": "A"},
            {"referenznummer": "A"},
        ]
    )
    state_col = FakeStateCollection(
        {"key": "job_list_collector_query_scope", "last_run_id": "list-1",
         "last_completed_full_scan": not limited,
         **({'last_run_usable': True} if limited else {})}
    )
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "stellenangebote_history": list_history_col,
            "jobdetails": FakeCollection(),
            "jobdetails_history": FakeCollection(),
            "pipeline_state": state_col,
        }
    )
    cfg = {
        "collections": {"list": "stellenangebote", "details": "jobdetails"},
        "history_collections": {"list": "stellenangebote_history", "details": "jobdetails_history"},
        "jobcenter": {"details_api": {"url_template": "https://example/{ref_b64}", "headers": {}}},
    }
    fake_session = FakeSession()

    with (
        patch.object(job_details_collector, "load_app_config", return_value=cfg),
        patch.object(job_details_collector, "get_database", return_value=db),
        patch.object(job_details_collector, "get_logger", return_value=FakeLogger()),
        patch.object(job_details_collector.requests, "Session", return_value=fake_session),
        patch.object(job_details_collector.time, "sleep", return_value=None),
        patch.object(job_details_collector.random, "uniform", return_value=0.0),
    ):
        job_details_collector.main()

    assert state_col.find_one_query == {"key": "job_list_collector_query_scope"}
    assert list_history_col.find_query == {"event_type": "list_seen", "run_id": "list-1"}
    assert list_col.find_query == {"referenznummer": {"$in": ["A", "B"]}}


@pytest.mark.parametrize('state_flags', [
    {'last_completed_full_scan': False},
    {'last_completed_full_scan': True, 'last_run_usable': False},
])
def test_main_skips_when_last_list_run_was_not_completed(state_flags):
    list_col = FakeListCollection()
    list_history_col = FakeListHistoryCollection()
    state_col = FakeStateCollection(
        {"key": "job_list_collector_query_scope", "last_run_id": "list-2", **state_flags}
    )
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "stellenangebote_history": list_history_col,
            "jobdetails": FakeCollection(),
            "jobdetails_history": FakeCollection(),
            "pipeline_state": state_col,
        }
    )
    cfg = {
        "collections": {"list": "stellenangebote", "details": "jobdetails"},
        "history_collections": {"list": "stellenangebote_history", "details": "jobdetails_history"},
        "jobcenter": {"details_api": {"url_template": "https://example/{ref_b64}", "headers": {}}},
    }

    with (
        patch.object(job_details_collector, "load_app_config", return_value=cfg),
        patch.object(job_details_collector, "get_database", return_value=db),
        patch.object(job_details_collector, "get_logger", return_value=FakeLogger()),
    ):
        job_details_collector.main()

    assert state_col.find_one_query == {"key": "job_list_collector_query_scope"}
    assert list_history_col.find_query is None
    assert list_col.find_query is None
