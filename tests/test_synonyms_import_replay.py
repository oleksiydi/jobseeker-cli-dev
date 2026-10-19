from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from tools import import_synonyms_state_snapshot as mod


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.indexes: list[Any] = []
        self.deleted = False

    def create_index(self, keys, **kwargs) -> str:
        self.indexes.append((keys, kwargs))
        return kwargs.get("name", "idx")

    def delete_many(self, _selector: dict[str, Any]) -> None:
        self.deleted = True
        self.docs = []

    def insert_many(self, docs: list[dict[str, Any]], ordered: bool = True) -> None:
        self.docs.extend(docs)


def _args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base = {
        "state_jsonl": tmp_path / "state.jsonl",
        "audit_jsonl": tmp_path / "audit.jsonl",
        "skip_audit": False,
        "replace_existing": True,
        "validate": True,
        "dry_run": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_import_snapshot_replaces_state_and_audit(monkeypatch, tmp_path: Path):
    args = _args(tmp_path)
    _write(
        args.state_jsonl,
        (
            '{"snapshot_run_id":"snap-1","exported_at":"2026-01-01T00:00:00Z","state_id":"state-1","alias":"k8s","alias_key":"k8s","canonical":"Kubernetes","domain":"it_software_daten","category":"stack","facet":"technology_stack","skill_class":"not_applicable","processing_status":"accepted","decision_source":"auto_gate","confidence":0.93,"source":"rule","source_refs_count":3,"sample_phrases":["Erfahrung mit k8s"],"run_id":"syn-1","lease_owner":null,"lease_expires_at":null,"retry_count":0,"last_error":null,"adjudication_policy":{"mode":"machine"},"adjudication":{"primary":{"status":"pending"}},"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:01Z"}\n'
        ),
    )
    _write(
        args.audit_jsonl,
        (
            '{"snapshot_run_id":"snap-1","exported_at":"2026-01-01T00:00:00Z","state_id":"state-1","event_type":"candidate_built","run_id":"syn-1","payload":{"decision":"accepted"},"created_at":"2026-01-01T00:00:02Z"}\n'
        ),
    )

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

    assert state_collection.deleted is True
    assert audit_collection.deleted is True
    assert len(state_collection.docs) == 1
    assert state_collection.docs[0]["_id"] == "state-1"
    assert state_collection.docs[0]["canonical"] == "Kubernetes"
    assert len(audit_collection.docs) == 1
    assert audit_collection.docs[0]["state_id"] == "state-1"


def test_import_snapshot_requires_replace_existing_unless_dry_run(monkeypatch, tmp_path: Path):
    args = _args(tmp_path, replace_existing=False, dry_run=False)
    _write(args.state_jsonl, "")
    _write(args.audit_jsonl, "")

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    with pytest.raises(ValueError, match="replace-only"):
        mod.main()


def test_import_snapshot_dry_run_skips_writes(monkeypatch, tmp_path: Path, capsys):
    args = _args(tmp_path, replace_existing=False, dry_run=True)
    _write(
        args.state_jsonl,
        (
            '{"snapshot_run_id":"snap-1","exported_at":"2026-01-01T00:00:00Z","state_id":"state-1","alias":"k8s","alias_key":"k8s","canonical":"Kubernetes","domain":"it_software_daten","category":"stack","facet":"technology_stack","skill_class":"not_applicable","processing_status":"accepted","decision_source":"auto_gate","confidence":0.93,"source":"rule","source_refs_count":3,"sample_phrases":[],"run_id":"syn-1","lease_owner":null,"lease_expires_at":null,"retry_count":0,"last_error":null,"adjudication_policy":{"mode":"machine"},"adjudication":{"primary":{"status":"pending"}},"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:01Z"}\n'
        ),
    )
    _write(args.audit_jsonl, "")

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

    out = capsys.readouterr().out
    assert "dry_run=true" in out
    assert state_collection.docs == []
    assert audit_collection.docs == []
