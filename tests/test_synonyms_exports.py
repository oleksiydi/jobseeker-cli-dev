from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools import export_synonyms_state_snapshot as mod


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def __iter__(self):
        return iter(self.docs)

    def limit(self, n: int) -> "FakeCursor":
        return FakeCursor(self.docs[:n])


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, selector: dict[str, Any]) -> FakeCursor:
        if not selector:
            return FakeCursor(self.docs)
        status = selector.get("processing_status")
        if status is None:
            return FakeCursor(self.docs)
        return FakeCursor([doc for doc in self.docs if doc.get("processing_status") == status])


def _args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base = {
        "run_id": "snap-run-1",
        "out_state_jsonl": tmp_path / "state.jsonl",
        "out_audit_jsonl": tmp_path / "audit.jsonl",
        "status": "",
        "limit": 0,
        "skip_audit": False,
        "validate": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_export_full_snapshot_writes_state_and_audit(monkeypatch, tmp_path):
    args = _args(tmp_path)
    state_docs = [
        {
            "_id": "state-1",
            "alias": "k8s",
            "alias_key": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source": "rule",
            "source_refs_count": 3,
            "sample_phrases": ["Erfahrung mit k8s"],
            "run_id": "syn-1",
            "lease_owner": None,
            "lease_expires_at": None,
            "retry_count": 0,
            "last_error": None,
            "adjudication_policy": {"mode": "machine"},
            "adjudication": {"primary": {"status": "pending"}},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:01+00:00",
        }
    ]
    audit_docs = [
        {
            "state_id": "k8s|Kubernetes|it_software_daten|stack|technology_stack",
            "event_type": "candidate_built",
            "run_id": "syn-1",
            "payload": {"decision": "accepted"},
            "created_at": "2026-01-01T00:00:02+00:00",
        }
    ]
    fake_db = {
        "normalization_synonyms_state": FakeCollection(state_docs),
        "normalization_synonyms_audit": FakeCollection(audit_docs),
    }

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    state_rows = [json.loads(line) for line in args.out_state_jsonl.read_text(encoding="utf-8").splitlines() if line]
    audit_rows = [json.loads(line) for line in args.out_audit_jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(state_rows) == 1
    assert len(audit_rows) == 1
    assert state_rows[0]["state_id"] == "state-1"
    assert state_rows[0]["alias_key"] == "k8s"
    assert audit_rows[0]["event_type"] == "candidate_built"


def test_export_filters_by_status(monkeypatch, tmp_path):
    args = _args(tmp_path, status="accepted", skip_audit=True)
    state_docs = [
        {
            "_id": "state-1",
            "alias": "k8s",
            "alias_key": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source": "rule",
            "source_refs_count": 3,
            "sample_phrases": [],
            "run_id": "syn-1",
            "lease_owner": None,
            "lease_expires_at": None,
            "retry_count": 0,
            "last_error": None,
            "adjudication_policy": {"mode": "machine"},
            "adjudication": {"primary": {"status": "pending"}},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
        {
            "_id": "state-2",
            "alias": "xy",
            "alias_key": "xy",
            "canonical": "xy",
            "domain": "sonstiges",
            "category": "skills",
            "facet": "other",
            "skill_class": "professional",
            "processing_status": "rejected",
            "decision_source": "auto_gate",
            "confidence": 0.2,
            "source": "rule",
            "source_refs_count": 1,
            "sample_phrases": [],
            "run_id": "syn-1",
            "lease_owner": None,
            "lease_expires_at": None,
            "retry_count": 0,
            "last_error": None,
            "adjudication_policy": {"mode": "machine"},
            "adjudication": {"primary": {"status": "pending"}},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
    ]
    fake_db = {
        "normalization_synonyms_state": FakeCollection(state_docs),
        "normalization_synonyms_audit": FakeCollection([]),
    }
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    state_rows = [json.loads(line) for line in args.out_state_jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(state_rows) == 1
    assert state_rows[0]["processing_status"] == "accepted"
