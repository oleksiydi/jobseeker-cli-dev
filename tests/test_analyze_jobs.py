import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId

import app.analyze_jobs as analyze_jobs


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []
        self.indexes = []

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    def find_one(self, query, projection=None, sort=None):
        docs = list(self.docs)
        if sort:
            for field, direction in reversed(sort):
                reverse = direction == -1
                docs.sort(key=lambda item: item.get(field), reverse=reverse)
        for doc in docs:
            if all(doc.get(k) == v for k, v in query.items()):
                if projection:
                    return {k: v for k, v in doc.items() if projection.get(k, 1) != 0}
                return doc
        return None

    def aggregate(self, pipeline):
        docs = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
                if match == {"is_active": {"$ne": False}}:
                    docs = [doc for doc in docs if doc.get("is_active") is not False]
        return iter(docs)

    def insert_one(self, doc):
        self.inserted.append(doc)

    def update_one(self, query, update):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                set_doc = update.get("$set", {})
                doc.update(set_doc)
                return
        raise AssertionError("No document found for update_one")

    def update_many(self, query, update):
        set_doc = update.get("$set", {})
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(set_doc)


class FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, key):
        return self._collections[key]


class FakeOpenAIResponse:
    def __init__(self, content):
        self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})})]


class FakeOpenAIClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.chat = type(
            "Chat",
            (),
            {"completions": type("Completions", (), {"create": self._create})()},
        )

    def _create(self, **kwargs):
        self.calls += 1
        return self._response


class RetryableError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status={status_code}")
        self.status_code = status_code


class SequenceOpenAIClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.chat = type(
            "Chat",
            (),
            {"completions": type("Completions", (), {"create": self._create})()},
        )

    def _create(self, **kwargs):
        self.calls += 1
        if not self.results:
            raise AssertionError("No more prepared results")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeLog:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


@pytest.fixture
def base_cfg():
    return {
        "mongodb": {"db_name": "db"},
        "collections": {
            "list": "list",
            "details": "details",
            "analysis": "analysis",
            "profiles": "profiles",
            "analysis_prompts": "analysis_prompts",
        },
        "analyzer": {"profile_key": "p1", "model": "m1", "prompt_key": "default", "prompt_version": None},
    }


@pytest.fixture
def default_prompt_doc():
    return {
        "key": "default",
        "version": 1,
        "name": "Default",
        "description": "desc",
        "system_prompt": "SYSTEM",
        "analysis_task": "TASK",
        "prompt_hash": "phash-default-1",
        "is_active": True,
    }


def test_extract_numbers_basic():
    text = "Rate 12,5 then 3.2 and finally 99 points"
    nums = analyze_jobs.extract_numbers(text)
    assert nums == [12.5, 3.2, 99.0]


def test_ensure_indexes_uses_versioned_analysis_indexes(base_cfg):
    db = FakeDB(
        {
            "analysis": FakeCollection(),
            "analysis_prompts": FakeCollection(),
        }
    )

    analyze_jobs.ensure_indexes(db, base_cfg)

    analysis_indexes = db["analysis"].indexes
    assert (([("referenznummer", 1), ("input_hash", 1)],), {"unique": True}) in analysis_indexes
    assert (([("referenznummer", 1), ("is_latest", 1)],), {}) in analysis_indexes


def test_extract_numbers_limit():
    text = "Values 1 2 3 4"
    nums = analyze_jobs.extract_numbers(text, limit=2)
    assert nums == [1.0, 2.0]


def test_create_chat_completion_retries_then_succeeds(monkeypatch):
    # Erst 429, danach 503, dann Erfolg: insgesamt 3 Versuche.
    response = FakeOpenAIResponse('{"ok": true}')
    client = SequenceOpenAIClient([RetryableError(429), RetryableError(503), response])
    fake_log = FakeLog()
    sleeps = []

    monkeypatch.setattr(analyze_jobs.time, "sleep", lambda seconds: sleeps.append(seconds))

    out = analyze_jobs._create_chat_completion_with_retries(
        client=client,
        req={"model": "m1", "messages": []},
        log=fake_log,
        ref="ref-retry-ok",
        max_attempts=3,
        base_delay_seconds=0.5,
    )

    assert out is response
    assert client.calls == 3
    assert sleeps == [0.5, 1.0]
    assert len(fake_log.warnings) == 2


def test_create_chat_completion_retries_and_raises_after_limit(monkeypatch):
    # Nur retrybare Fehler -> nach max_attempts wird der letzte Fehler geworfen.
    client = SequenceOpenAIClient([RetryableError(500), RetryableError(503), RetryableError(429)])
    fake_log = FakeLog()
    sleeps = []

    monkeypatch.setattr(analyze_jobs.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(RetryableError, match="status=429"):
        analyze_jobs._create_chat_completion_with_retries(
            client=client,
            req={"model": "m1", "messages": []},
            log=fake_log,
            ref="ref-retry-fail",
            max_attempts=3,
            base_delay_seconds=0.25,
        )

    assert client.calls == 3
    assert sleeps == [0.25, 0.5]
    assert len(fake_log.warnings) == 2


def test_json_default_objectid_datetime():
    oid = ObjectId("64d2f8f2f2f2f2f2f2f2f2f2")
    dt = datetime(2024, 1, 2, 3, 4, 5)
    assert analyze_jobs.json_default(oid) == str(oid)
    assert analyze_jobs.json_default(dt).endswith("+00:00")


def test_make_json_safe_removes_service_fields():
    oid = ObjectId("64d2f8f2f2f2f2f2f2f2f2f2")
    dt = datetime(2024, 1, 2, 3, 4, 5)
    payload = {
        "_id": oid,
        "old": True,
        "name": "ok",
        "nested": {
            "_id": oid,
            "value": dt,
        },
        "items": (oid, {"old": False, "keep": 1}),
    }
    out = analyze_jobs.make_json_safe(payload)
    assert "_id" not in out
    assert "old" not in out
    assert out["name"] == "ok"
    assert out["nested"]["value"] == dt.replace(tzinfo=timezone.utc).isoformat()
    assert out["items"][0] == str(oid)
    assert out["items"][1] == {"keep": 1}


def test_main_skips_already_analyzed(base_cfg, default_prompt_doc):
    profile = {"key": "p1", "name": "User"}
    meta = {
        "referenznummer": "ref1",
        "stellenangebotsTitel": "Dev",
        "arbeitgeber": "ACME",
        "details": {"desc": "x"},
        "is_active": True,
    }
    safe_merged = analyze_jobs.make_json_safe({"meta": {k: v for k, v in meta.items() if k != "details"}, "details": meta["details"]})
    input_hash = analyze_jobs._compute_input_hash(
        {
            "meta": analyze_jobs._strip_input_hash_service_fields(safe_merged.get("meta", {})),
            "details": analyze_jobs._strip_input_hash_service_fields(safe_merged.get("details", {})),
            "profile": analyze_jobs._strip_input_hash_service_fields(profile),
            "prompt": {
                "key": default_prompt_doc["key"],
                "version": default_prompt_doc["version"],
                "hash": default_prompt_doc["prompt_hash"],
                "system": default_prompt_doc["system_prompt"],
                "task": default_prompt_doc["analysis_task"],
            },
            "model": "m1",
        }
    )
    col_list = FakeCollection(
        [meta]
    )
    col_an = FakeCollection([{"_id": "x1", "referenznummer": "ref1", "input_hash": input_hash}])
    col_prof = FakeCollection([profile])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([default_prompt_doc]),
        }
    )

    fake_client = FakeOpenAIClient(FakeOpenAIResponse("{}"))

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert len(col_an.inserted) == 0


def test_main_inserts_analysis(base_cfg, default_prompt_doc):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref2",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "x"},
                "is_active": True,
            }
        ]
    )
    col_an = FakeCollection()
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([default_prompt_doc]),
        }
    )

    response_payload = {
        "score": 78,
        "decision": "shortlist",
        "reasons": ["r1", "r2", "r3", "r4", "r5", "r6"],
        "risks": ["risk1"],
        "missing_evidence": ["m1", "m2"],
        "notes": "ok",
    }
    fake_response = FakeOpenAIResponse(json.dumps(response_payload))
    fake_client = FakeOpenAIClient(fake_response)

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert len(col_an.inserted) == 1
    saved = col_an.inserted[0]
    assert saved["referenznummer"] == "ref2"
    assert saved["score"] == 78.0
    assert saved["score_"] == 78
    assert saved["decision"] == "shortlist"
    assert saved["reasons"] == ["r1", "r2", "r3", "r4", "r5"]
    assert saved["risks"] == ["risk1"]
    assert saved["missing_evidence"] == ["m1", "m2"]
    assert saved["notes"] == "ok"
    assert saved["prompt_key"] == "default"
    assert saved["prompt_version"] == 1
    assert saved["prompt_hash"] == "phash-default-1"
    assert saved["prompt_snapshot"]["system"] == "SYSTEM"
    assert saved["prompt_snapshot"]["task"] == "TASK"


def test_main_updates_analysis_when_input_changed(base_cfg, default_prompt_doc):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref3",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "new"},
                "is_active": True,
            }
        ]
    )
    col_an = FakeCollection(
        [{"_id": "x3", "referenznummer": "ref3", "version": 1, "is_latest": True, "input_hash": "old-hash", "notes": "old"}]
    )
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([default_prompt_doc]),
        }
    )

    response_payload = {
        "score": 81,
        "decision": "shortlist",
        "reasons": ["r1", "r2", "r3", "r4", "r5"],
        "risks": ["risk1"],
        "missing_evidence": ["m1", "m2"],
        "notes": "updated",
    }
    fake_response = FakeOpenAIResponse(json.dumps(response_payload))
    fake_client = FakeOpenAIClient(fake_response)

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert len(col_an.inserted) == 1
    previous = col_an.docs[0]
    saved = col_an.inserted[0]
    assert previous["is_latest"] is False
    assert saved["referenznummer"] == "ref3"
    assert saved["version"] == 2
    assert saved["is_latest"] is True
    assert saved["notes"] == "updated"
    assert "input_hash" in saved


def test_main_creates_next_version_and_flips_latest_marker(base_cfg, default_prompt_doc):
    # Mehrere alte Versionen vorhanden: neue Version muss latest+1 sein.
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref-versioned",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "changed"},
                "is_active": True,
            }
        ]
    )
    col_an = FakeCollection(
        [
            {"_id": "v1", "referenznummer": "ref-versioned", "version": 1, "is_latest": False, "input_hash": "h1"},
            {"_id": "v2", "referenznummer": "ref-versioned", "version": 2, "is_latest": True, "input_hash": "h2"},
        ]
    )
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([default_prompt_doc]),
        }
    )

    response_payload = {
        "score": 90,
        "decision": "shortlist",
        "reasons": ["r1"],
        "risks": [],
        "missing_evidence": [],
        "notes": "new version",
    }
    fake_client = FakeOpenAIClient(FakeOpenAIResponse(json.dumps(response_payload)))

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert fake_client.calls == 1
    assert len(col_an.inserted) == 1
    # Alle bisherigen Versionen werden auf is_latest=False gesetzt.
    assert all(doc["is_latest"] is False for doc in col_an.docs)
    new_doc = col_an.inserted[0]
    assert new_doc["referenznummer"] == "ref-versioned"
    assert new_doc["version"] == 3
    assert new_doc["is_latest"] is True


def test_main_skips_inactive_vacancies(base_cfg, default_prompt_doc):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "inactive-1",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "x"},
                "is_active": False,
            }
        ]
    )
    col_an = FakeCollection()
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([default_prompt_doc]),
        }
    )
    fake_client = FakeOpenAIClient(FakeOpenAIResponse("{}"))

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert len(col_an.inserted) == 0


def test_main_uses_latest_active_prompt_when_version_not_set(base_cfg):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref-latest",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "x"},
                "is_active": True,
            }
        ]
    )
    col_an = FakeCollection()
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    col_prompts = FakeCollection(
        [
            {
                "key": "default",
                "version": 1,
                "system_prompt": "SYSTEM V1",
                "analysis_task": "TASK V1",
                "prompt_hash": "hash-v1",
                "is_active": True,
            },
            {
                "key": "default",
                "version": 2,
                "system_prompt": "SYSTEM V2",
                "analysis_task": "TASK V2",
                "prompt_hash": "hash-v2",
                "is_active": True,
            },
        ]
    )
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": col_prompts,
        }
    )
    response_payload = {
        "score": 78,
        "decision": "shortlist",
        "reasons": ["r1", "r2", "r3", "r4", "r5"],
        "risks": ["risk1"],
        "missing_evidence": ["m1", "m2"],
        "notes": "ok",
    }
    fake_client = FakeOpenAIClient(FakeOpenAIResponse(json.dumps(response_payload)))

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI", return_value=fake_client),
    ):
        analyze_jobs.main()

    assert len(col_an.inserted) == 1
    saved = col_an.inserted[0]
    assert saved["prompt_version"] == 2
    assert saved["prompt_hash"] == "hash-v2"
    assert saved["prompt_snapshot"]["system"] == "SYSTEM V2"


def test_main_raises_if_default_prompt_missing(base_cfg):
    col_list = FakeCollection([])
    col_an = FakeCollection()
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
            "analysis_prompts": FakeCollection([]),
        }
    )

    with (
        patch.object(analyze_jobs, "load_app_config", return_value=base_cfg),
        patch.object(analyze_jobs, "get_database", return_value=db),
        patch.object(analyze_jobs, "ensure_indexes"),
        patch.object(analyze_jobs, "load_dotenv"),
        patch.object(analyze_jobs, "OpenAI"),
    ):
        with pytest.raises(RuntimeError, match="Prompt nicht gefunden"):
            analyze_jobs.main()
