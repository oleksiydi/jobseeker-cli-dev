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

    def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                if projection:
                    return {k: v for k, v in doc.items() if projection.get(k, 1) != 0}
                return doc
        return None

    def aggregate(self, pipeline):
        return iter(self.docs)

    def insert_one(self, doc):
        self.inserted.append(doc)


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
        self.chat = type(
            "Chat",
            (),
            {"completions": type("Completions", (), {"create": self._create})()},
        )

    def _create(self, **kwargs):
        return self._response


@pytest.fixture
def base_cfg():
    return {
        "mongodb": {"db_name": "db"},
        "collections": {
            "list": "list",
            "details": "details",
            "analysis": "analysis",
            "profiles": "profiles",
        },
        "analyzer": {"profile_key": "p1", "model": "m1"},
    }


def test_extract_numbers_basic():
    text = "Rate 12,5 then 3.2 and finally 99 points"
    nums = analyze_jobs.extract_numbers(text)
    assert nums == [12.5, 3.2, 99.0]


def test_extract_numbers_limit():
    text = "Values 1 2 3 4"
    nums = analyze_jobs.extract_numbers(text, limit=2)
    assert nums == [1.0, 2.0]


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


def test_main_skips_already_analyzed(base_cfg):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref1",
                "stellenangebotsTitel": "Dev",
                "arbeitgeber": "ACME",
                "details": {"desc": "x"},
            }
        ]
    )
    col_an = FakeCollection([{"referenznummer": "ref1"}])
    col_prof = FakeCollection([{"key": "p1", "name": "User"}])
    db = FakeDB(
        {
            "list": col_list,
            "details": FakeCollection(),
            "analysis": col_an,
            "profiles": col_prof,
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


def test_main_inserts_analysis(base_cfg):
    col_list = FakeCollection(
        [
            {
                "referenznummer": "ref2",
                "stellenangebotsTitel": "Engineer",
                "arbeitgeber": "Example",
                "details": {"desc": "x"},
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
