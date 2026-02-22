from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import app.job_list_collector as job_list_collector


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []
        self._next_id = 1000

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        out = [doc for doc in self.docs if _match(doc, query)]
        if projection:
            projected = []
            for doc in out:
                projected.append({k: v for k, v in doc.items() if projection.get(k)})
            return projected
        return out

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            query = op._filter
            update = op._doc
            upsert = bool(op._upsert)
            target = next((doc for doc in self.docs if _match(doc, query)), None)

            if target is None:
                if not upsert:
                    continue
                target = {}
                for key, value in query.items():
                    if not isinstance(value, dict):
                        target[key] = value
                target["_id"] = self._next_id
                self._next_id += 1
                self.docs.append(target)
                for key, value in update.get("$setOnInsert", {}).items():
                    target[key] = value

            for key, value in update.get("$set", {}).items():
                target[key] = value
            for key in update.get("$unset", {}):
                target.pop(key, None)

    def insert_many(self, docs, ordered=False):
        self.inserted.extend(docs)

    def find_one(self, query=None, projection=None, sort=None):
        query = query or {}
        for doc in self.docs:
            if _match(doc, query):
                if projection:
                    return {k: v for k, v in doc.items() if projection.get(k)}
                return doc
        return None

    def update_one(self, query, update, upsert=False):
        target = next((doc for doc in self.docs if _match(doc, query)), None)
        if target is None:
            if not upsert:
                return None
            target = {}
            for key, value in query.items():
                if not isinstance(value, dict):
                    target[key] = value
            target["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(target)

        for key, value in update.get("$set", {}).items():
            target[key] = value
        for key in update.get("$unset", {}):
            target.pop(key, None)
        return None


class FakeDB:
    def __init__(self, collections: dict[str, FakeCollection]):
        self.collections = collections

    def __getitem__(self, key: str) -> FakeCollection:
        return self.collections[key]


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_main_marks_missing_records_inactive():
    now = datetime.now(timezone.utc)
    seen_payload = {"referenznummer": "A", "titel": "Job A"}
    seen_hash = job_list_collector._compute_meta_hash({"referenznummer": "A", "titel": "Job A"})

    list_col = FakeCollection(
        [
            {
                "_id": 1,
                "referenznummer": "A",
                "titel": "Job A",
                "meta_hash": seen_hash,
                "is_active": True,
                "updated_at": now,
            },
            {
                "_id": 2,
                "referenznummer": "B",
                "titel": "Job B",
                "meta_hash": "hash-b",
                "is_active": True,
                "updated_at": now,
            },
        ]
    )
    history_col = FakeCollection()
    state_col = FakeCollection()
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "stellenangebote_history": history_col,
            "pipeline_state": state_col,
        }
    )

    cfg = {
        "collections": {"list": "stellenangebote"},
        "history_collections": {"list": "stellenangebote_history"},
        "jobcenter": {
            "list_api": {
                "api_url": "https://example.test/list",
                "headers": {},
                "params": {},
                "max_requests": 5,
            }
        },
    }

    fake_session = FakeSession(
        [
            FakeResponse({"stellenangebote": [seen_payload]}),
            FakeResponse({"stellenangebote": []}),
        ]
    )

    with (
        patch.object(job_list_collector, "load_app_config", return_value=cfg),
        patch.object(job_list_collector, "get_database", return_value=db),
        patch.object(job_list_collector, "get_logger", return_value=FakeLogger()),
        patch.object(job_list_collector.requests, "Session", return_value=fake_session),
        patch.object(job_list_collector.time, "sleep"),
        patch.object(job_list_collector.random, "uniform", return_value=0.0),
    ):
        job_list_collector.main()

    by_ref = {doc["referenznummer"]: doc for doc in list_col.docs}
    assert by_ref["A"]["is_active"] is True
    assert by_ref["B"]["is_active"] is False
    assert by_ref["B"].get("missing_since") is not None

    event_types = {doc.get("event_type") for doc in history_col.inserted}
    assert "list_seen" in event_types
    assert "list_missing" in event_types


def test_main_skips_missing_mark_when_query_scope_changed():
    now = datetime.now(timezone.utc)
    seen_payload = {"referenznummer": "A", "titel": "Job A"}
    seen_hash = job_list_collector._compute_meta_hash({"referenznummer": "A", "titel": "Job A"})

    list_col = FakeCollection(
        [
            {
                "_id": 1,
                "referenznummer": "A",
                "titel": "Job A",
                "meta_hash": seen_hash,
                "is_active": True,
                "updated_at": now,
            },
            {
                "_id": 2,
                "referenznummer": "B",
                "titel": "Job B",
                "meta_hash": "hash-b",
                "is_active": True,
                "updated_at": now,
            },
        ]
    )
    history_col = FakeCollection()
    state_col = FakeCollection(
        [
            {
                "_id": 10,
                "key": "job_list_collector_query_scope",
                "query_hash": "old-hash",
            }
        ]
    )
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "stellenangebote_history": history_col,
            "pipeline_state": state_col,
        }
    )

    cfg = {
        "collections": {"list": "stellenangebote"},
        "history_collections": {"list": "stellenangebote_history"},
        "jobcenter": {
            "list_api": {
                "api_url": "https://example.test/list",
                "headers": {},
                "params": {"wo": "Regensburg"},
                "max_requests": 5,
            }
        },
    }

    fake_session = FakeSession(
        [
            FakeResponse({"stellenangebote": [seen_payload]}),
            FakeResponse({"stellenangebote": []}),
        ]
    )

    with (
        patch.object(job_list_collector, "load_app_config", return_value=cfg),
        patch.object(job_list_collector, "get_database", return_value=db),
        patch.object(job_list_collector, "get_logger", return_value=FakeLogger()),
        patch.object(job_list_collector.requests, "Session", return_value=fake_session),
        patch.object(job_list_collector.time, "sleep"),
        patch.object(job_list_collector.random, "uniform", return_value=0.0),
    ):
        job_list_collector.main()

    by_ref = {doc["referenznummer"]: doc for doc in list_col.docs}
    assert by_ref["A"]["is_active"] is True
    assert by_ref["B"]["is_active"] is True
    assert by_ref["B"].get("missing_since") is None

    event_types = {doc.get("event_type") for doc in history_col.inserted}
    assert "list_seen" in event_types
    assert "list_missing" not in event_types
