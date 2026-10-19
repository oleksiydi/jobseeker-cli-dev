from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from tools import build_synonyms_from_taxonomy as mod


class FakeCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict]] = []
        self.updates: list[dict] = []
        self.inserts: list[dict] = []
        self.docs: list[dict] = []

    def create_index(self, keys: list[tuple[str, int]], **kwargs: dict) -> None:
        self.indexes.append((keys, kwargs))

    def update_one(self, key_filter: dict, update: dict, upsert: bool = False):
        self.updates.append({"filter": key_filter, "update": update, "upsert": upsert})
        stored = next((doc for doc in self.docs if self._matches(doc, key_filter)), None)
        if stored is not None:
            stored.update(update.get("$set", {}))
            return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1, upserted_id=None)
        if upsert and not any(self._matches(doc, key_filter) for doc in self.docs):
            stored = {
                key: expected
                for key, expected in key_filter.items()
                if not isinstance(expected, dict)
            }
            stored.update(update.get("$setOnInsert", {}))
            stored.setdefault("_id", f"fake-{len(self.docs) + 1}")
            self.docs.append(stored)
            self.inserts.append(stored)
            return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0, upserted_id=stored["_id"])
        return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0, upserted_id=None)

    def find_one(self, key_filter: dict, projection: dict | None = None):
        stored = next((doc for doc in self.docs if self._matches(doc, key_filter)), None)
        if stored is None:
            return None
        if projection:
            return {key: stored[key] for key in projection if key in stored}
        return dict(stored)

    @staticmethod
    def _matches(document: dict, key_filter: dict) -> bool:
        for key, expected in key_filter.items():
            if isinstance(expected, dict) and "$exists" in expected:
                if (key in document) != expected["$exists"]:
                    return False
            elif document.get(key) != expected:
                return False
        return True

    def insert_one(self, document: dict):
        self.inserts.append(document)

        class _Result:
            acknowledged = True

        return _Result()


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    aggregate_json = tmp_path / "taxonomy_domain_aggregate.json"
    terms_jsonl = tmp_path / "vacancy_terms.jsonl"
    snapshot_jsonl = tmp_path / "synonyms_state_snapshot.jsonl"

    aggregate_json.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "term_key": "k8s",
                        "canonical": "Kubernetes",
                        "primary_category": "stack",
                        "primary_facet": "technology_stack",
                        "primary_skill_class": "not_applicable",
                        "domain_votes": {"it_software_daten": {"count": 2, "avg_confidence": 0.9}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    terms_jsonl.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "term": "k8s",
                        "term_key": "k8s",
                        "canonical": "k8s",
                        "category": "stack",
                        "facet": "technology_stack",
                        "skill_class": "not_applicable",
                        "domain_hint": "it_software_daten",
                        "domain_confidence": 0.9,
                        "context_phrase": "Erfahrung mit k8s",
                    }
                ),
                json.dumps(
                    {
                        "term": "K8S",
                        "term_key": "k8s",
                        "canonical": "k8s",
                        "category": "stack",
                        "facet": "technology_stack",
                        "skill_class": "not_applicable",
                        "domain_hint": "it_software_daten",
                        "domain_confidence": 0.8,
                        "context_phrase": "K8S Cluster Betrieb",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    base = {
        "aggregate_json": aggregate_json,
        "terms_jsonl": terms_jsonl,
        "snapshot_jsonl": snapshot_jsonl,
        "max_candidates": 0,
        "allow_learning": True,
        "run_id": "syn-test-run",
        "dry_run": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_builder_writes_state_and_audit(monkeypatch, tmp_path):
    args = _args(tmp_path)
    state_collection = FakeCollection()
    audit_collection = FakeCollection()
    fake_db = {
        "normalization_synonyms_state": state_collection,
        "normalization_synonyms_audit": audit_collection,
    }

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)

    mod.main()

    assert len(state_collection.updates) == 1
    assert len(state_collection.docs) == 1
    assert len(audit_collection.updates) == 1
    assert len(audit_collection.docs) == 1
    snapshot_lines = [json.loads(line) for line in args.snapshot_jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(snapshot_lines) == 1
    assert snapshot_lines[0]["canonical"] == "Kubernetes"


def test_builder_replay_preserves_reviewed_state_and_one_audit(monkeypatch, tmp_path):
    args = _args(tmp_path)
    state_collection = FakeCollection()
    audit_collection = FakeCollection()
    fake_db = {
        "normalization_synonyms_state": state_collection,
        "normalization_synonyms_audit": audit_collection,
    }
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)

    mod.main()
    saved = state_collection.docs[0]
    saved.update(
        {
            "processing_status": "published",
            "decision_source": "human_adjudication",
            "adjudication": {"primary": {"status": "done", "decision": "accept"}},
            "updated_at": "2026-10-01T12:00:00Z",
        }
    )
    saved_snapshot = dict(saved)

    mod.main()

    assert len(state_collection.docs) == 1
    assert len(state_collection.updates) == 1
    assert state_collection.docs[0] == saved_snapshot
    assert state_collection.docs[0]["processing_status"] == "published"
    assert len(audit_collection.docs) == 1
    assert len(audit_collection.updates) == 2


def test_builder_retry_recreates_audit_missing_after_state_write(monkeypatch, tmp_path):
    args = _args(tmp_path)
    state_collection = FakeCollection()

    class FailFirstAuditWrite(FakeCollection):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        def update_one(self, key_filter: dict, update: dict, upsert: bool = False):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("simulated interruption before audit write")
            return super().update_one(key_filter, update, upsert)

    audit_collection = FailFirstAuditWrite()
    fake_db = {
        "normalization_synonyms_state": state_collection,
        "normalization_synonyms_audit": audit_collection,
    }
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)

    try:
        mod.main()
    except RuntimeError as exc:
        assert "simulated interruption" in str(exc)
    else:
        raise AssertionError("first audit write should simulate interruption")

    assert len(state_collection.docs) == 1
    assert audit_collection.docs == []
    mod.main()

    assert len(state_collection.docs) == 1
    assert len(audit_collection.docs) == 1
    assert len(audit_collection.updates) == 1


def test_builder_llm_records_primary_and_pending_review_and_replay_preserves_rejection(
    monkeypatch, tmp_path
):
    args = _args(
        tmp_path,
        mode="llm",
        provider="primary-provider",
        model="primary-model",
        review_provider="review-provider",
        review_model="review-model",
        escalation_provider="pro-provider",
        escalation_model="pro-model",
        cache_path=tmp_path / "synonyms-cache.sqlite3",
        block_size=8,
        timeout_seconds=30,
    )
    state_collection = FakeCollection()
    audit_collection = FakeCollection()
    fake_db = {
        "normalization_synonyms_state": state_collection,
        "normalization_synonyms_audit": audit_collection,
    }
    proposals = [
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "confidence": 0.95,
            "source_refs_count": 2,
            "sample_phrases": ["Erfahrung mit k8s"],
            "requires_review": False,
            "reviewed_by_strong": False,
            "provider": "primary-provider",
            "model": "primary-model",
            "reason": "clear abbreviation",
        },
        {
            "alias": "kube",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "confidence": 0.65,
            "source_refs_count": 1,
            "sample_phrases": ["Kube cluster"],
            "requires_review": True,
            "reviewed_by_strong": False,
            "provider": "primary-provider",
            "model": "primary-model",
            "reason": "ambiguous abbreviation",
        },
    ]
    calls = []

    def fake_build_proposals(raw_terms, **kwargs):
        calls.append((raw_terms, kwargs))
        return proposals

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    monkeypatch.setattr(mod, "build_proposals", fake_build_proposals)

    mod.main()

    assert len(calls) == 1
    assert calls[0][1]["provider"] == "primary-provider"
    assert calls[0][1]["model"] == "primary-model"
    assert calls[0][1]["review_provider"] == "review-provider"
    assert calls[0][1]["review_model"] == "review-model"
    accepted = next(doc for doc in state_collection.docs if doc["alias"] == "k8s")
    pending = next(doc for doc in state_collection.docs if doc["alias"] == "kube")
    assert accepted["adjudication"]["primary"]["provider"] == "primary-provider"
    assert accepted["adjudication"]["primary"]["model"] == "primary-model"
    assert pending["processing_status"] == "needs_adjudication"
    assert pending["adjudication"]["escalated"]["status"] == "pending"
    assert len(audit_collection.docs) == 2

    pending.update(
        {
            "processing_status": "adjudicated_rejected",
            "decision_source": "human_adjudication",
            "adjudication": {
                "primary": {
                    "status": "done",
                    "decision": "reject",
                    "confidence": 0.99,
                    "provider": "human",
                    "model": "reviewer",
                },
                "escalated": {"status": "done", "decision": "reject"},
            },
            "updated_at": "2026-10-01T12:00:00Z",
        }
    )
    rejected_snapshot = dict(pending)

    mod.main()

    assert len(state_collection.docs) == 2
    assert len(state_collection.updates) == 2
    assert pending == rejected_snapshot
    assert pending["processing_status"] == "adjudicated_rejected"
    assert len(audit_collection.docs) == 2
    assert len(audit_collection.updates) == 4
    snapshot_rows = [
        json.loads(line)
        for line in args.snapshot_jsonl.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert next(row for row in snapshot_rows if row["alias"] == "kube")["processing_status"] == "adjudicated_rejected"


def test_builder_dry_run_does_not_require_mongo(monkeypatch, tmp_path):
    args = _args(tmp_path, dry_run=True)

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})

    def _db_not_expected(_cfg):
        raise AssertionError("get_database should not be called in dry-run mode")

    monkeypatch.setattr(mod, "get_database", _db_not_expected)

    mod.main()

    snapshot_lines = [json.loads(line) for line in args.snapshot_jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(snapshot_lines) == 1
    assert snapshot_lines[0]["alias_key"] == "k8s"
