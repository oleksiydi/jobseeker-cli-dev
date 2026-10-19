from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from copy import deepcopy

import pytest

import app.job_list_collector as job_list_collector
import requests


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
            for key, value in update.get('$setOnInsert', {}).items():
                target[key] = value

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
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize('capped', [False, True])
def test_main_marks_missing_records_inactive(capped):
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
                'result_window_limit': 1 if capped else 10000,
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
    assert by_ref["B"]["is_active"] is capped
    assert bool(by_ref["B"].get("missing_since")) is not capped

    event_types = {doc.get("event_type") for doc in history_col.inserted}
    assert "list_seen" in event_types
    assert ('list_missing' in event_types) is not capped


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


def test_main_retries_temporary_list_api_error():
    list_col = FakeCollection()
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
                "retry_attempts": 3,
                "retry_backoff_seconds": 0.1,
            }
        },
    }

    fake_session = FakeSession(
        [
            requests.Timeout("temporary timeout"),
            FakeResponse({"stellenangebote": [{"referenznummer": "A", "titel": "Job A"}]}),
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

    assert fake_session.calls == 3
    assert any(doc.get("referenznummer") == "A" for doc in list_col.docs)


def test_main_plz_mode_marks_missing_only_after_all_shards_complete():
    now = datetime.now(timezone.utc)
    hash_a = job_list_collector._compute_meta_hash({"referenznummer": "A", "titel": "Job A"})
    list_col = FakeCollection(
        [
            {
                "_id": 1,
                "referenznummer": "A",
                "titel": "Job A",
                "meta_hash": hash_a,
                "is_active": True,
                "updated_at": now,
            },
            {
                "_id": 2,
                "referenznummer": "C",
                "titel": "Job C",
                "meta_hash": "hash-c",
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
                "params": {"size": "200"},
                "max_requests": 10,
                "search_area": {
                    "mode": "plz",
                    "plz": "80331 80333",
                    "plz_query_umkreis": 0,
                    "max_pages_per_physical_query": 5,
                },
            }
        },
    }
    fake_session = FakeSession(
        [
            FakeResponse({"stellenangebote": [{"referenznummer": "A", "titel": "Job A"}]}),
            FakeResponse({"stellenangebote": []}),
            FakeResponse({"stellenangebote": [{"referenznummer": "B", "titel": "Job B"}]}),
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
    assert by_ref["C"]["is_active"] is False

    list_seen_events = [doc for doc in history_col.inserted if doc.get("event_type") == "list_seen"]
    assert {doc.get("shard_id") for doc in list_seen_events} == {"plz-0001-80331", "plz-0002-80333"}
    assert {doc.get("plz") for doc in list_seen_events} == {"80331", "80333"}
    assert {doc.get("logical_query_mode") for doc in list_seen_events} == {"explicit_plz"}
    assert any(doc.get("event_type") == "list_missing" and doc.get("referenznummer") == "C" for doc in history_col.inserted)

    scope_doc = state_col.find_one({"key": "job_list_collector_query_scope"})
    assert scope_doc["last_completed_full_scan"] is True
    assert scope_doc["scope_payload"]["mode"] == "plz"
    assert scope_doc["physical_queries_count"] == 2


def test_main_plz_mode_skips_missing_mark_on_partial_shard_failure():
    now = datetime.now(timezone.utc)
    hash_a = job_list_collector._compute_meta_hash({"referenznummer": "A", "titel": "Job A"})
    list_col = FakeCollection(
        [
            {
                "_id": 1,
                "referenznummer": "A",
                "titel": "Job A",
                "meta_hash": hash_a,
                "is_active": True,
                "updated_at": now,
            },
            {
                "_id": 2,
                "referenznummer": "C",
                "titel": "Job C",
                "meta_hash": "hash-c",
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
                "params": {"size": "200"},
                "max_requests": 10,
                "retry_attempts": 1,
                "search_area": {
                    "mode": "plz",
                    "plz": "80331 80333",
                    "plz_query_umkreis": 0,
                    "max_pages_per_physical_query": 5,
                },
            }
        },
    }
    fake_session = FakeSession(
        [
            FakeResponse({"stellenangebote": [{"referenznummer": "A", "titel": "Job A"}]}),
            FakeResponse({"stellenangebote": []}),
            requests.Timeout("temporary timeout"),
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
    assert by_ref["C"]["is_active"] is True
    assert by_ref["C"].get("missing_since") is None
    assert not any(doc.get("event_type") == "list_missing" for doc in history_col.inserted)

    scope_doc = state_col.find_one({"key": "job_list_collector_query_scope"})
    assert scope_doc["last_completed_full_scan"] is False


@pytest.fixture
def collect_url(monkeypatch):
    """Exercise the real collector with deterministic API pages and in-memory DB."""
    def run(query, handler, **overrides):
        cfg = {'jobcenter': {'list_api': {
            'api_url': 'https://example.test/list',
            'search_url': 'https://www.arbeitsagentur.de/jobsuche/suche?' + query,
            'params': {'size': '2', 'wo': 'IGNORED', 'pav': False},
            'result_window_limit': 3, 'max_requests': 50, 'retry_attempts': 1,
            'search_area': {'mode': 'plz', 'plz': 'IGNORED',
                            'max_plz_per_run': 20, 'max_pages_per_physical_query': 10},
            **overrides}}}
        db = FakeDB({name: FakeCollection() for name in (
            'stellenangebote', 'stellenangebote_history', 'pipeline_state', 'job_search_runs')})
        db['stellenangebote'].docs.append({'_id': 42, 'referenznummer': 'OUTSIDE', 'is_active': True})
        calls = []

        class Session:
            def get(self, url, **kwargs):
                params = deepcopy(kwargs['params'])
                calls.append(params)
                return FakeResponse(handler(params))

        monkeypatch.delenv('JOBCENTER_SEARCH_URL', raising=False)
        monkeypatch.setattr(job_list_collector, 'load_app_config', lambda: cfg)
        monkeypatch.setattr(job_list_collector, 'get_database', lambda *_: db)
        monkeypatch.setattr(job_list_collector, 'get_logger', lambda *_: FakeLogger())
        monkeypatch.setattr(job_list_collector.requests, 'Session', Session)
        monkeypatch.setattr(job_list_collector, 'sleep_with_jitter', lambda *_: None)
        job_list_collector.main()
        state = db['pipeline_state'].find_one({'key': 'job_list_collector_query_scope'})
        assert db['stellenangebote'].find_one({'referenznummer': 'OUTSIDE'})['is_active']
        assert not any(e['event_type'] == 'list_missing' for e in db['stellenangebote_history'].inserted)
        snapshot = db['job_search_runs'].docs[0]
        assert snapshot['search_id'] == state['search_provenance']['search_id']
        assert snapshot['search_url'] == cfg['jobcenter']['list_api']['search_url']
        return state, calls, db
    return run


def _page(total, refs, mode=None):
    return {'maxErgebnisse': total,
            'ergebnisliste': [{'referenznummer': ref, 'titel': 'Job ' + ref} for ref in refs],
            **({'woOutput': {'suchmodus': mode}} if mode else {})}


def test_url_below_window_keeps_all_filters_and_multiple_locations(collect_url):
    state, calls, _ = collect_url(
        'arbeitsort=Passau;Tübingen&homeoffice=prozentual_45;nv_true&future=a;b',
        lambda p: _page(2, ['A', 'B']))
    assert len(calls) == 1
    assert calls[0]['arbeitsort'] == 'Passau;Tübingen'
    assert calls[0]['homeoffice'] == 'prozentual_45;nv_true'
    assert calls[0]['future'] == 'a;b'
    assert 'wo' not in calls[0] and 'pav' not in calls[0]
    assert state['last_run_usable'] and state['last_completed_full_scan']
    assert any(w['code'] == 'homeoffice_passthrough' for w in state['search_provenance']['warnings'])


def test_collector_german_warnings_retain_search_identity_and_restore_context(collect_url, monkeypatch):
    import re
    from app.core.i18n import current_language

    monkeypatch.setenv('JOBSEEKER_OUTPUT_LANGUAGE', 'de')
    state, calls, _ = collect_url(
        'wo=Passau&umkreis=10&homeoffice=prozentual_50;nv_true',
        lambda p: _page(3, ['PROBE']) if int(p.get('umkreis', 0)) else _page(1, ['A']),
    )
    warnings = state['search_provenance']['warnings']
    assert {'homeoffice_passthrough', 'result_window_reached'} <= {w['code'] for w in warnings}
    assert all(w['message_id'] and not re.search('[А-Яа-яЁё]', w['message']) for w in warnings)
    assert all(p['homeoffice'] == 'prozentual_50;nv_true' for p in calls)
    assert current_language() == 'ru'


@pytest.mark.parametrize('place', ['Passau', '94032'])
def test_url_radius_is_removed_at_exact_window_before_storing_probe(collect_url, place):
    def respond(p):
        return _page(3, ['RADIUS_ONLY']) if p['umkreis'] == '15' else _page(1, ['LOCAL'])

    state, calls, db = collect_url(f'wo={place}&umkreis=15&branche=11;29', respond)
    assert [p['umkreis'] for p in calls] == ['15', 0]
    assert all(p['branche'] == '11;29' for p in calls)
    assert db['stellenangebote'].find_one({'referenznummer': 'RADIUS_ONLY'}) is None
    assert db['stellenangebote'].find_one({'referenznummer': 'LOCAL'})
    coverage = state['search_provenance']
    assert coverage['original_geography'] == {'wo': place, 'umkreis': '15'}
    assert coverage['effective_geography'] == [{'wo': place, 'umkreis': 0}]
    assert coverage['geography_narrowed'] and not coverage['coverage_complete']
    assert state['last_run_usable'] and not state['last_completed_full_scan']


def test_url_city_overflow_uses_plz_and_capped_shard_does_not_stop_others(collect_url, monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_city_to_plz',
                        lambda *_: ['94032', '94034'])

    def respond(p):
        if p['wo'] == 'Passau':
            return _page(4, ['PROBE'])
        if p['wo'] == '94032':
            return _page(4, ['A', 'B'] if p['page'] == 1 else ['C'])
        return _page(1, ['D'])

    state, calls, db = collect_url('wo=Passau&umkreis=0&arbeitszeit=tz', respond)
    assert [p['wo'] for p in calls] == ['Passau', '94032', '94032', '94034']
    assert all(p['arbeitszeit'] == 'tz' for p in calls)
    assert not db['stellenangebote'].find_one({'referenznummer': 'PROBE'})
    assert db['stellenangebote'].find_one({'referenznummer': 'D'})
    assert state['last_run_usable'] and not state['last_completed_full_scan']
    assert [q['status'] for q in state['search_provenance']['queries']] == [
        'replaced', 'window_limit', 'complete']


@pytest.mark.parametrize('place,mode', [('Bayern', 'BUNDESLANDSUCHE'), ('Deutschland', 'LANDSUCHE')])
def test_url_region_shards_only_after_limit(collect_url, monkeypatch, place, mode):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032', '94034'])

    def respond(p):
        return _page(3, ['PROBE'], mode) if p['wo'] == place else _page(1, [p['wo']])

    state, calls, _ = collect_url('wo=' + place + '&befristung=1', respond)
    assert [p['wo'] for p in calls] == [place, '94032', '94034']
    assert all(p['befristung'] == '1' for p in calls)
    assert state['last_completed_full_scan']
    assert not state['search_provenance']['geography_narrowed']


@pytest.mark.parametrize('budget', [1, 2])
def test_url_request_budget_never_claims_unvisited_shards_complete(collect_url, monkeypatch, budget):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032', '94034'])
    state, calls, _ = collect_url('wo=Deutschland',
        lambda p: _page(3, ['PROBE']) if p['wo'] == 'Deutschland' else _page(1, [p['wo']]),
        max_requests=budget)
    assert len(calls) == budget
    assert not state['last_run_usable'] and not state['last_completed_full_scan']
    assert len(state['search_provenance']['effective_geography']) == budget - 1


def test_url_failure_does_not_stop_remaining_shards(collect_url, monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032', '94034'])

    def respond(p):
        if p['wo'] == 'Deutschland':
            return _page(3, ['PROBE'])
        if p['wo'] == '94032':
            raise requests.Timeout('EXAMPLE_SECRET')
        return _page(1, ['D'])

    state, calls, db = collect_url('wo=Deutschland', respond)
    assert [p['wo'] for p in calls] == ['Deutschland', '94032', '94034']
    assert db['stellenangebote'].find_one({'referenznummer': 'D'})
    assert not state['last_run_usable']


def test_url_multiple_places_split_only_when_overflowing(collect_url):
    def respond(p):
        if 'wo' not in p:
            return _page(3, ['PROBE'])
        return _page(1, [p['wo']])

    state, calls, db = collect_url('arbeitsort=Passau;Tübingen&branche=11;29', respond)
    assert len(calls) == 3
    assert [p['wo'] for p in calls[1:]] == ['Passau', 'Tübingen']
    assert all(p['umkreis'] == 0 for p in calls[1:])
    assert all(p['branche'] == '11;29' for p in calls)
    assert all(p['arbeitsort'] == 'Passau;Tübingen' for p in calls)
    assert not db['stellenangebote'].find_one({'referenznummer': 'PROBE'})
    assert state['last_run_usable']


def test_url_without_location_shards_germany(collect_url, monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032', '94034'])
    state, calls, _ = collect_url('branche=11;29', lambda p: (
        _page(3, ['PROBE']) if 'wo' not in p else _page(1, [p['wo']])))
    assert [p.get('wo') for p in calls] == [None, '94032', '94034']
    assert state['search_provenance']['original_geography'] == {'wo': 'Deutschland'}


def test_url_geocoder_failure_collects_limited_source_with_safe_warning(collect_url, monkeypatch):
    def fail(*_):
        raise requests.ConnectionError('EXAMPLE_SECRET')

    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz', fail)
    state, calls, _ = collect_url('wo=Deutschland', lambda p: (
        _page(4, ['A', 'B']) if p['page'] == 1 else _page(4, ['C'])))
    assert len(calls) == 2
    assert state['last_run_usable'] and not state['last_completed_full_scan']
    assert any(w['code'] == 'sharding_unavailable' for w in state['search_provenance']['warnings'])
    assert 'EXAMPLE_SECRET' not in str(state)


def test_url_region_under_limit_does_not_resolve_plz(collect_url, monkeypatch):
    def unexpected(*_):
        pytest.fail('A query below the window must not expand geography')

    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz', unexpected)
    state, calls, _ = collect_url('wo=Bayern&umkreis=15', lambda p: _page(1, ['A'], 'BUNDESLANDSUCHE'))
    assert len(calls) == 1 and state['last_completed_full_scan']
    assert calls[0]['umkreis'] == '15'


def test_url_missing_total_stops_at_received_window_with_warning(collect_url):
    state, calls, db = collect_url('wo=Passau&umkreis=15', lambda p: {
        'ergebnisliste': [{'referenznummer': ref, 'titel': ref} for ref in (
            ['A', 'B'] if p['page'] == 1 else ['C'])]})
    assert len(calls) == 2
    assert all(p['umkreis'] == '15' for p in calls)
    assert state['last_run_usable'] and not state['last_completed_full_scan']
    assert not state['search_provenance']['geography_narrowed']
    assert state['search_provenance']['queries'][0]['status'] == 'window_limit'


def test_url_combined_location_facet_is_preserved_when_sharding(collect_url, monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032'])
    state, calls, _ = collect_url('wo=Bayern&arbeitsort=Passau&arbeitszeit=tz', lambda p: (
        _page(3, ['PROBE'], 'BUNDESLANDSUCHE') if p['wo'] == 'Bayern' else _page(1, ['A'])))
    assert len(calls) == 2
    assert calls[1]['arbeitsort'] == 'Passau'
    assert calls[1]['arbeitszeit'] == 'tz'


@pytest.mark.parametrize('invalid', [[], {'error': 'EXAMPLE_SECRET'}])
def test_url_malformed_response_is_not_published_as_empty_complete_run(collect_url, invalid):
    state, _, _ = collect_url('wo=Passau', lambda p: invalid)
    assert not state['last_run_usable']
    assert state['search_provenance']['queries'][0]['status'] == 'invalid_response'


def test_url_database_write_failure_is_not_usable(collect_url, monkeypatch):
    def fail(*_, **__):
        raise RuntimeError('EXAMPLE_SECRET')

    monkeypatch.setattr(FakeCollection, 'bulk_write', fail)
    state, _, _ = collect_url('wo=Passau', lambda p: _page(1, ['A']))
    assert not state['last_run_usable']
    assert state['search_provenance']['queries'][0]['status'] == 'write_error'


def test_url_reaching_page_guard_warns_and_continues_other_shards(collect_url, monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['94032', '94034'])

    def respond(p):
        if p['wo'] == 'Deutschland':
            return _page(3, ['PROBE'])
        if p['wo'] == '94032':
            return _page(2, ['A'])
        return _page(1, ['D'])

    state, calls, db = collect_url('wo=Deutschland', respond, search_area={
        'max_plz_per_run': 20, 'max_pages_per_physical_query': 1})
    assert [p['wo'] for p in calls] == ['Deutschland', '94032', '94034']
    assert db['stellenangebote'].find_one({'referenznummer': 'D'})
    assert not state['last_run_usable']
    assert state['search_provenance']['queries'][1]['status'] == 'page_limit'


def test_url_preview_and_invalid_url_do_not_access_database(monkeypatch):
    def unexpected(*_):
        pytest.fail('Preview/invalid URL must not access HTTP or MongoDB')

    cfg = {'jobcenter': {'list_api': {
        'api_url': 'https://example.test/list',
        'search_url': 'https://www.arbeitsagentur.de/jobsuche/suche?wo=Deutschland',
        'search_area': {'dry_run': True},
    }}}
    monkeypatch.delenv('JOBCENTER_SEARCH_URL', raising=False)
    monkeypatch.setattr(job_list_collector, 'load_app_config', lambda: cfg)
    monkeypatch.setattr(job_list_collector, 'get_database', unexpected)
    monkeypatch.setattr(job_list_collector.requests, 'Session', unexpected)
    monkeypatch.setattr(job_list_collector, 'get_logger', lambda *_: FakeLogger())
    job_list_collector.main()
    cfg['jobcenter']['list_api']['search_url'] += '&password=EXAMPLE_SECRET'
    with pytest.raises(ValueError, match='forbidden') as error:
        job_list_collector.main()
    assert 'EXAMPLE_SECRET' not in str(error.value)
