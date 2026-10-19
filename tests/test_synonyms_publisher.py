from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools import publish_synonyms_auto as mod


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def __iter__(self):
        return iter(self.docs)


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, selector: dict[str, Any]) -> FakeCursor:
        statuses = selector.get("processing_status", {}).get("$in", [])
        if not statuses:
            return FakeCursor(self.docs)
        return FakeCursor([doc for doc in self.docs if doc.get("processing_status") in statuses])


def _args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base = {
        "out": tmp_path / "synonyms.auto.json",
        "domains_file": Path("conf/normalization/domains.level1.de.json"),
        "include_status": "accepted,adjudicated_accepted",
        "version": 1,
        "validate": True,
        "min_domains_cross_domain": 2,
        "min_refs_cross_domain": 2,
        "min_domains_global_safe": 3,
        "min_refs_global_safe": 10,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_publisher_writes_entries_and_aliases(monkeypatch, tmp_path):
    args = _args(tmp_path)
    docs = [
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source_refs_count": 1,
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
        {
            "alias": "kubectl",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "adjudicated_accepted",
            "decision_source": "llm_adjudication",
            "confidence": 0.88,
            "source_refs_count": 1,
            "updated_at": "2026-01-01T00:00:02+00:00",
        },
        {
            "alias": "xy",
            "canonical": "xy",
            "domain": "sonstiges",
            "category": "skills",
            "facet": "other",
            "skill_class": "professional",
            "processing_status": "rejected",
            "decision_source": "auto_gate",
            "confidence": 0.2,
            "updated_at": "2026-01-01T00:00:03+00:00",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["entries"]) == 2
    assert payload["aliases"] == {}
    assert payload["ambiguous_aliases"] == []
    assert payload["entries"][0]["domain_scope"] == "specific"
    assert payload["entries"][0]["facet_scope"] == "specific"
    assert payload["entries"][0]["source_refs_count"] == 1


def test_default_publication_includes_pro_acceptance_and_excludes_pro_queue(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["publish_synonyms_auto", "--out", str(tmp_path / "pro.json"), "--validate"])
    args = mod.parse_args()
    document = {"alias":"k8s", "canonical":"Kubernetes", "domain":"it_software_daten",
                "category":"stack", "facet":"technology_stack", "skill_class":"not_applicable",
                "confidence":0.95, "source_refs_count":2, "decision_source":"llm_pro_adjudication",
                "processing_status":"escalated_accepted", "updated_at":"2026-10-14T00:00:00+00:00"}
    docs = [document, {**document, "alias":"tf", "canonical":"Terraform", "processing_status":"needs_escalated_adjudication"},
            {**document, "alias":"other", "canonical":"Other", "processing_status":"unresolved"}]
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: {"normalization_synonyms_state":FakeCollection(docs)})
    mod.main()
    payload = json.loads(args.out.read_text())
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["alias"] == "k8s"
    assert payload["entries"][0]["source"] == "llm"


def test_publisher_reuses_existing_version_when_not_overridden(monkeypatch, tmp_path):
    out = tmp_path / "synonyms.auto.json"
    out.write_text(json.dumps({"version": 7, "entries": [], "aliases": {}}) + "\n", encoding="utf-8")
    args = _args(tmp_path, out=out, version=0)
    docs = [
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "updated_at": "2026-01-01T00:00:01+00:00",
        }
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 7


def test_publisher_excludes_ambiguous_alias_from_global_aliases(monkeypatch, tmp_path):
    args = _args(tmp_path)
    docs = [
        {
            "alias": "ROM",
            "canonical": "Read-Only Memory",
            "domain": "it_software_daten",
            "category": "skills",
            "facet": "technology_stack",
            "skill_class": "professional",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
        {
            "alias": "ROM",
            "canonical": "Range of Motion",
            "domain": "gesundheitswesen_pflege_medizin",
            "category": "skills",
            "facet": "skill_requirement",
            "skill_class": "professional",
            "processing_status": "adjudicated_accepted",
            "decision_source": "llm_adjudication",
            "confidence": 0.9,
            "updated_at": "2026-01-01T00:00:02+00:00",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 2
    assert "rom" not in payload["aliases"]
    assert payload["ambiguous_aliases"] == ["rom"]


def test_publisher_marks_cross_domain_and_unstable_scopes(monkeypatch, tmp_path):
    args = _args(tmp_path, min_domains_global_safe=2, min_refs_global_safe=2)
    docs = [
        {
            "alias": "Berufsausbildung",
            "canonical": "Ausbildung",
            "domain": "handwerk_bau_ausbau",
            "category": "skills",
            "facet": "work_condition",
            "skill_class": "non_skill_conditions",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.69,
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
        {
            "alias": "Berufsausbildung",
            "canonical": "Ausbildung",
            "domain": "verwaltung_oeffentlicher_dienst",
            "category": "skills",
            "facet": "skill_requirement",
            "skill_class": "non_skill_conditions",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.69,
            "updated_at": "2026-01-01T00:00:02+00:00",
        },
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "updated_at": "2026-01-01T00:00:03+00:00",
        },
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "ingenieurwesen_technik",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.91,
            "updated_at": "2026-01-01T00:00:04+00:00",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    by_alias_domain = {(entry["alias"], entry["domain"]): entry for entry in payload["entries"]}

    ausbildung = by_alias_domain[("Berufsausbildung", "handwerk_bau_ausbau")]
    assert ausbildung["domain_scope"] == "cross_domain_candidate"
    assert ausbildung["facet_scope"] == "unstable"

    k8s = by_alias_domain[("k8s", "it_software_daten")]
    assert k8s["domain_scope"] == "global_safe"
    assert k8s["facet_scope"] == "specific"
    assert payload["aliases"]["k8s"] == "Kubernetes"
    assert "berufsausbildung" not in payload["aliases"]


def test_publisher_excludes_identity_and_same_scope_conflicts_but_keeps_distinct_scopes(
    monkeypatch, tmp_path
):
    args = _args(tmp_path)
    base = {
        "category": "skills",
        "facet": "skill_requirement",
        "skill_class": "professional",
        "processing_status": "accepted",
        "decision_source": "auto_gate",
    }
    docs = [
        {
            **base,
            "alias": "Pflegeplanung",
            "canonical": "Pflegeplanung",
            "domain": "gesundheitswesen_pflege_medizin",
        },
        {
            **base,
            "alias": "Bank",
            "canonical": "Kreditinstitut",
            "domain": "finanzen_versicherung_recht",
        },
        {
            **base,
            "alias": "Bank",
            "canonical": "Sitzbank",
            "domain": "finanzen_versicherung_recht",
        },
        {
            **base,
            "alias": "ROM",
            "canonical": "Read-Only Memory",
            "domain": "it_software_daten",
        },
        {
            **base,
            "alias": "ROM",
            "canonical": "Range of Motion",
            "domain": "gesundheitswesen_pflege_medizin",
            "facet": "other",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    assert {(entry["alias"], entry["canonical"]) for entry in payload["entries"]} == {
        ("ROM", "Read-Only Memory"),
        ("ROM", "Range of Motion"),
    }
    assert payload["ambiguous_aliases"] == ["rom"]
    assert "rom" not in payload["aliases"]


def test_publisher_requires_thresholds_before_global_safe(monkeypatch, tmp_path):
    args = _args(tmp_path)
    docs = [
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source_refs_count": 4,
            "updated_at": "2026-01-01T00:00:03+00:00",
        },
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "ingenieurwesen_technik",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.91,
            "source_refs_count": 5,
            "updated_at": "2026-01-01T00:00:04+00:00",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    assert all(entry["domain_scope"] == "cross_domain_candidate" for entry in payload["entries"])
    assert payload["aliases"] == {}


def test_publisher_marks_global_safe_after_thresholds(monkeypatch, tmp_path):
    args = _args(tmp_path)
    docs = [
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "it_software_daten",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.93,
            "source_refs_count": 4,
            "updated_at": "2026-01-01T00:00:03+00:00",
        },
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "ingenieurwesen_technik",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.91,
            "source_refs_count": 3,
            "updated_at": "2026-01-01T00:00:04+00:00",
        },
        {
            "alias": "k8s",
            "canonical": "Kubernetes",
            "domain": "verwaltung_oeffentlicher_dienst",
            "category": "stack",
            "facet": "technology_stack",
            "skill_class": "not_applicable",
            "processing_status": "accepted",
            "decision_source": "auto_gate",
            "confidence": 0.91,
            "source_refs_count": 3,
            "updated_at": "2026-01-01T00:00:05+00:00",
        },
    ]
    fake_db = {"normalization_synonyms_state": FakeCollection(docs)}

    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {"collections": {}})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: fake_db)
    mod.main()

    payload = json.loads(args.out.read_text(encoding="utf-8"))
    assert all(entry["domain_scope"] == "global_safe" for entry in payload["entries"])
    assert payload["aliases"]["k8s"] == "Kubernetes"
