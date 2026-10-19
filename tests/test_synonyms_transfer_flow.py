from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.normalization.loader import load_runtime_taxonomy
from tools import export_synonyms_state_snapshot as export_mod
from tools import import_synonyms_state_snapshot as import_mod
from tools import publish_synonyms_auto as publish_mod


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def __iter__(self):
        return iter(self.docs)

    def limit(self, n: int) -> "FakeCursor":
        return FakeCursor(self.docs[:n])


class FakeCollectionFindOnly:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, selector: dict[str, Any]) -> FakeCursor:
        if not selector:
            return FakeCursor(self.docs)
        status = selector.get("processing_status")
        if status is None:
            return FakeCursor(self.docs)
        return FakeCursor([doc for doc in self.docs if doc.get("processing_status") == status])


class FakeCollectionMutable:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.deleted = False
        self.indexes: list[Any] = []

    def create_index(self, keys, **kwargs) -> str:
        self.indexes.append((keys, kwargs))
        return kwargs.get("name", "idx")

    def delete_many(self, _selector: dict[str, Any]) -> None:
        self.deleted = True
        self.docs = []

    def insert_many(self, docs: list[dict[str, Any]], ordered: bool = True) -> None:
        self.docs.extend(docs)

    def find(self, selector: dict[str, Any]) -> FakeCursor:
        statuses = selector.get("processing_status", {}).get("$in", [])
        if not statuses:
            return FakeCursor(self.docs)
        return FakeCursor([doc for doc in self.docs if doc.get("processing_status") in statuses])


def _export_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        run_id="snap-transfer-1",
        out_state_jsonl=tmp_path / "synonyms_state_snapshot.full.jsonl",
        out_audit_jsonl=tmp_path / "synonyms_audit_snapshot.full.jsonl",
        status="",
        limit=0,
        skip_audit=False,
        validate=True,
    )


def _import_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        state_jsonl=tmp_path / "synonyms_state_snapshot.full.jsonl",
        audit_jsonl=tmp_path / "synonyms_audit_snapshot.full.jsonl",
        skip_audit=False,
        replace_existing=True,
        validate=True,
        dry_run=False,
    )


def _publish_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        out=tmp_path / "synonyms.auto.json",
        domains_file=Path("conf/normalization/domains.level1.de.json"),
        include_status="accepted,adjudicated_accepted",
        version=1,
        validate=True,
        min_domains_cross_domain=1,
        min_refs_cross_domain=1,
        min_domains_global_safe=1,
        min_refs_global_safe=1,
    )


def test_transfer_flow_export_import_publish_runtime(monkeypatch, tmp_path: Path):
    source_state_docs = [
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
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        }
    ]
    source_audit_docs = [
        {
            "state_id": "state-1",
            "event_type": "candidate_built",
            "run_id": "syn-1",
            "payload": {"decision": "accepted"},
            "created_at": "2026-01-01T00:00:02Z",
        }
    ]

    export_db = {
        "normalization_synonyms_state": FakeCollectionFindOnly(source_state_docs),
        "normalization_synonyms_audit": FakeCollectionFindOnly(source_audit_docs),
    }
    export_args = _export_args(tmp_path)
    monkeypatch.setattr(export_mod, "parse_args", lambda: export_args)
    monkeypatch.setattr(export_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(export_mod, "get_database", lambda _cfg: export_db)
    export_mod.main()

    target_state = FakeCollectionMutable()
    target_audit = FakeCollectionMutable()
    import_db = {
        "normalization_synonyms_state": target_state,
        "normalization_synonyms_audit": target_audit,
    }
    import_args = _import_args(tmp_path)
    monkeypatch.setattr(import_mod, "parse_args", lambda: import_args)
    monkeypatch.setattr(import_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(import_mod, "get_database", lambda _cfg: import_db)
    import_mod.main()

    publish_args = _publish_args(tmp_path)
    monkeypatch.setattr(publish_mod, "parse_args", lambda: publish_args)
    monkeypatch.setattr(publish_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(publish_mod, "get_database", lambda _cfg: import_db)
    publish_mod.main()

    published = json.loads(publish_args.out.read_text(encoding="utf-8"))
    assert published["entries"][0]["alias"] == "k8s"
    assert published["entries"][0]["canonical"] == "Kubernetes"
    assert published["ambiguous_aliases"] == []

    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    taxonomy_release.write_text(
        json.dumps({"version": 1, "aliases": {}, "categories": {"skills": [], "tools": [], "stack": []}}) + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        json.dumps({"version": 1, "aliases": {}, "categories": {"skills": [], "tools": [], "stack": []}}) + "\n",
        encoding="utf-8",
    )
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_release.write_text(json.dumps({"version": 1, "entries": [], "aliases": {}}) + "\n", encoding="utf-8")

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=publish_args.out,
    )
    assert runtime_taxonomy["aliases"]["k8s"] == "Kubernetes"
    assert "Kubernetes" in runtime_taxonomy["categories"]["stack"]


def test_transfer_flow_preserves_ambiguous_alias_metadata(monkeypatch, tmp_path: Path):
    source_state_docs = [
        {
            "_id": "state-rom-it",
            "alias": "ROM",
            "alias_key": "rom",
            "canonical": "Read-Only Memory",
            "domain": "it_software_daten",
            "category": "skills",
            "facet": "technology_stack",
            "skill_class": "professional",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source": "rule",
            "source_refs_count": 2,
            "sample_phrases": ["ROM chip"],
            "run_id": "syn-1",
            "lease_owner": None,
            "lease_expires_at": None,
            "retry_count": 0,
            "last_error": None,
            "adjudication_policy": {"mode": "machine"},
            "adjudication": {"primary": {"status": "pending"}},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        },
        {
            "_id": "state-rom-med",
            "alias": "ROM",
            "alias_key": "rom",
            "canonical": "Range of Motion",
            "domain": "gesundheitswesen_pflege_medizin",
            "category": "skills",
            "facet": "skill_requirement",
            "skill_class": "professional",
            "processing_status": "adjudicated_accepted",
            "decision_source": "llm_adjudication",
            "confidence": 0.91,
            "source": "llm",
            "source_refs_count": 2,
            "sample_phrases": ["ROM der Schulter"],
            "run_id": "syn-1",
            "lease_owner": None,
            "lease_expires_at": None,
            "retry_count": 0,
            "last_error": None,
            "adjudication_policy": {"mode": "machine"},
            "adjudication": {"primary": {"status": "done"}},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        },
    ]
    export_db = {
        "normalization_synonyms_state": FakeCollectionFindOnly(source_state_docs),
        "normalization_synonyms_audit": FakeCollectionFindOnly([]),
    }
    export_args = _export_args(tmp_path)
    monkeypatch.setattr(export_mod, "parse_args", lambda: export_args)
    monkeypatch.setattr(export_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(export_mod, "get_database", lambda _cfg: export_db)
    export_mod.main()

    target_state = FakeCollectionMutable()
    target_audit = FakeCollectionMutable()
    import_db = {
        "normalization_synonyms_state": target_state,
        "normalization_synonyms_audit": target_audit,
    }
    import_args = _import_args(tmp_path)
    monkeypatch.setattr(import_mod, "parse_args", lambda: import_args)
    monkeypatch.setattr(import_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(import_mod, "get_database", lambda _cfg: import_db)
    import_mod.main()

    publish_args = _publish_args(tmp_path)
    monkeypatch.setattr(publish_mod, "parse_args", lambda: publish_args)
    monkeypatch.setattr(publish_mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(publish_mod, "get_database", lambda _cfg: import_db)
    publish_mod.main()

    published = json.loads(publish_args.out.read_text(encoding="utf-8"))
    assert len(published["entries"]) == 2
    assert "rom" not in published["aliases"]
    assert published["ambiguous_aliases"] == ["rom"]
