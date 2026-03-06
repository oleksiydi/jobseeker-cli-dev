from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from tools import build_taxonomy_from_vacancies as mod


def _write_schema(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "vacancy_domains": {"type": "array"},
                    "terms": {"type": "array"},
                },
            }
        ),
        encoding="utf-8",
    )


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    extraction_schema = tmp_path / "vacancy.schema.json"
    aggregate_schema = tmp_path / "aggregate.schema.json"
    _write_schema(extraction_schema)
    _write_schema(aggregate_schema)

    base = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": None,
        "api_key": None,
        "timeout_seconds": 120,
        "max_vacancies": 10,
        "max_terms_per_vacancy": 10,
        "llm_max_tokens": 900,
        "max_input_chars": 3000,
        "preset": "cpu_safe",
        "extraction_schema": extraction_schema,
        "aggregate_schema": aggregate_schema,
        "out_terms_jsonl": tmp_path / "vacancy_terms.jsonl",
        "out_aggregate_json": tmp_path / "taxonomy_domain_aggregate.json",
        "llm_calls_log": tmp_path / "llm_calls.jsonl",
        "process_log": tmp_path / "process_log.jsonl",
        "process_log_level": "full",
        "retry_on_length": 1,
        "checkpoint_every": 0,
        "overwrite_output": True,
        "domains_file": tmp_path / "domains.json",
        "domain_list": "it_software_daten,sonstiges",
        "allow_learning": True,
        "checkpoint_path": tmp_path / "checkpoint.jsonl",
        "resume_from_checkpoint": False,
        "apply_runtime_taxonomy": False,
        "taxonomy_release_path": tmp_path / "taxonomy.release.json",
        "taxonomy_auto_path": tmp_path / "taxonomy.auto.json",
        "dry_run": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _vacancy(ref: str) -> tuple[dict, dict]:
    meta = {"referenznummer": ref, "titel": "DevOps Engineer", "beruf": "Engineer"}
    details = {
        "referenznummer": ref,
        "stellenangebotsTitel": "DevOps Engineer",
        "hauptberuf": "Engineer",
        "stellenangebotsBeschreibung": "Kubernetes und Monitoring",
    }
    return meta, details


def _response(*, finish_reason: str, response_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        finish_reason=finish_reason,
        provider="openai",
        model="gpt-4o-mini",
        response_id=response_id,
        elapsed_ms=100.0,
        started_at="2026-03-06T00:00:00+00:00",
        usage={},
        timings={},
        content='{"ok":true}',
    )


def test_retry_on_length_then_success(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(mod, "_iter_active_vacancies", lambda _db, _cfg, max_vacancies: [_vacancy("R-1")])

    calls = {"n": 0}

    def fake_request_extraction(**_kwargs):
        calls["n"] += 1
        payload = {
            "vacancy_domains": [{"domain": "it_software_daten", "confidence": 0.9}],
            "terms": [
                {
                    "term": "Kubernetes",
                    "category": "skills",
                    "facet": "technology_stack",
                    "domain_hint": "it_software_daten",
                    "domain_confidence": 0.9,
                    "context_phrase": "Kubernetes cluster",
                }
            ],
        }
        if calls["n"] == 1:
            return payload, _response(finish_reason="length", response_id="r1")
        return payload, _response(finish_reason="stop", response_id="r2")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()

    assert calls["n"] == 2
    terms_rows = [json.loads(line) for line in args.out_terms_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(terms_rows) == 1
    assert terms_rows[0]["attempt"] == 2

    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    retry_rows = [row for row in process_rows if row.get("event") == "retry_on_length"]
    assert len(retry_rows) == 1
    assert retry_rows[0]["referenznummer"] == "R-1"


def test_resume_from_checkpoint_skips_completed_vacancy(monkeypatch, tmp_path):
    args = _args(tmp_path, resume_from_checkpoint=True)
    args.checkpoint_path.write_text(
        json.dumps({"run_id": "old", "referenznummer": "R-1", "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(
        mod,
        "_iter_active_vacancies",
        lambda _db, _cfg, max_vacancies: [_vacancy("R-1"), _vacancy("R-2")],
    )

    calls = {"n": 0}

    def fake_request_extraction(**_kwargs):
        calls["n"] += 1
        payload = {
            "vacancy_domains": [{"domain": "it_software_daten", "confidence": 0.9}],
            "terms": [
                {
                    "term": "Monitoring",
                    "category": "skills",
                    "facet": "skill_requirement",
                    "domain_hint": "it_software_daten",
                    "domain_confidence": 0.8,
                    "context_phrase": "System Monitoring",
                }
            ],
        }
        return payload, _response(finish_reason="stop", response_id="resume-ok")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()

    assert calls["n"] == 1
    terms_rows = [json.loads(line) for line in args.out_terms_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(terms_rows) == 1
    assert terms_rows[0]["referenznummer"] == "R-2"

    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    skipped_rows = [row for row in process_rows if row.get("event") == "vacancy_skipped_checkpoint"]
    assert len(skipped_rows) == 1
    assert skipped_rows[0]["referenznummer"] == "R-1"
