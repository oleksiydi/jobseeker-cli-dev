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
        "cache_path": tmp_path / "taxonomy-cache.sqlite3",
        "process_log": tmp_path / "process_log.jsonl",
        "process_log_level": "full",
        "retry_on_length": 1,
        "checkpoint_every": 0,
        "overwrite_output": False,
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
                    "context_phrase": "Kubernetes",
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


def test_old_checkpoint_does_not_skip_without_validated_cache(monkeypatch, tmp_path):
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
        lambda _db, _cfg, max_vacancies: [_vacancy("R-1"), _vacancy("R-1")],
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
                    "context_phrase": "Monitoring",
                }
            ],
        }
        return payload, _response(finish_reason="stop", response_id="resume-ok")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()

    assert calls["n"] == 1
    terms_rows = [json.loads(line) for line in args.out_terms_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(terms_rows) == 2
    assert all(row["referenznummer"] == "R-1" for row in terms_rows)
    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(row.get("event") == "vacancy_skipped_checkpoint" for row in process_rows)
    assert next(row for row in process_rows if row.get("event") == "run_finished")["cache_hits"] == 1


def test_repeated_run_reuses_cache_and_overwrite_forces_fresh_call(monkeypatch, tmp_path):
    args = _args(tmp_path)
    vacancy = _vacancy("R-1")
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(mod, "_iter_active_vacancies", lambda _db, _cfg, max_vacancies: [vacancy])
    calls = {"n": 0}

    def fake_request_extraction(**_kwargs):
        calls["n"] += 1
        return {
            "vacancy_domains": [{"domain": "it_software_daten", "confidence": 0.9}],
            "terms": [
                {
                    "term": "Kubernetes",
                    "category": "skills",
                    "facet": "technology_stack",
                    "domain_hint": "it_software_daten",
                    "domain_confidence": 0.9,
                    "context_phrase": "Kubernetes",
                }
            ],
        }, _response(finish_reason="stop", response_id=f"cached-{calls['n']}")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()
    mod.main()
    args.overwrite_output = True
    mod.main()

    assert calls["n"] == 2
    terms_rows = [json.loads(line) for line in args.out_terms_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(terms_rows) == 1
    aggregate = json.loads(args.out_aggregate_json.read_text(encoding="utf-8"))
    assert aggregate["input"]["term_rows"] == 1
    assert len(aggregate["terms"]) == 1
    assert aggregate["terms"][0]["canonical"] == "Kubernetes"
    assert aggregate["terms"][0]["source_refs_count"] == 1
    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    finished = [row for row in process_rows if row.get("event") == "run_finished"]
    assert [row["cache_hits"] for row in finished] == [0, 1, 0]


def test_changed_source_or_model_misses_cache(monkeypatch, tmp_path):
    args = _args(tmp_path)
    vacancy = _vacancy("R-1")
    vacancies = [vacancy]
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(mod, "_iter_active_vacancies", lambda _db, _cfg, max_vacancies: vacancies)
    calls = {"n": 0}

    def fake_request_extraction(**_kwargs):
        calls["n"] += 1
        return {
            "vacancy_domains": [{"domain": "it_software_daten", "confidence": 0.9}],
            "terms": [
                {
                    "term": "Monitoring",
                    "category": "skills",
                    "facet": "skill_requirement",
                    "domain_hint": "it_software_daten",
                    "domain_confidence": 0.8,
                    "context_phrase": "Monitoring",
                }
            ],
        }, _response(finish_reason="stop", response_id=f"fresh-{calls['n']}")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()
    vacancy[1]["stellenangebotsBeschreibung"] = "Python und Monitoring"
    mod.main()
    args.model = "gpt-4o"
    mod.main()

    assert calls["n"] == 3
    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    finished = [row for row in process_rows if row.get("event") == "run_finished"]
    assert [row["cache_hits"] for row in finished] == [0, 0, 0]


def test_hallucinated_context_phrase_is_rejected(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(mod, "_iter_active_vacancies", lambda _db, _cfg, max_vacancies: [_vacancy("R-1")])

    def fake_request_extraction(**_kwargs):
        return {
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
        }, _response(finish_reason="stop", response_id="hallucinated-context")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    mod.main()

    assert args.out_terms_jsonl.read_text(encoding="utf-8") == ""
    aggregate = json.loads(args.out_aggregate_json.read_text(encoding="utf-8"))
    assert aggregate["terms"] == []
    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    finished = [row for row in process_rows if row.get("event") == "run_finished"]
    assert finished[-1]["rejected_terms"] == 1


def test_unfinished_length_response_is_not_marked_success_or_cached(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: object())
    monkeypatch.setattr(mod, "_iter_active_vacancies", lambda _db, _cfg, max_vacancies: [_vacancy("R-1")])
    calls = {"n": 0}

    def fake_request_extraction(**_kwargs):
        calls["n"] += 1
        return {
            "vacancy_domains": [{"domain": "it_software_daten", "confidence": 0.9}],
            "terms": [],
        }, _response(finish_reason="length", response_id=f"truncated-{calls['n']}")

    monkeypatch.setattr(mod, "request_extraction", fake_request_extraction)
    for _ in range(2):
        try:
            mod.main()
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("unfinished responses should fail the run")

    assert calls["n"] == 4
    assert not args.checkpoint_path.exists()
    process_rows = [json.loads(line) for line in args.process_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any(row.get("status") == "ok" for row in process_rows)
    assert sum(row.get("event") == "vacancy_failed" for row in process_rows) == 2


def test_dry_run_returns_before_database_api_or_runtime_file_writes(monkeypatch, tmp_path):
    args = _args(tmp_path, dry_run=True)
    monkeypatch.setattr(mod, "parse_args", lambda: args)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("dry-run must not access the database or call the API")

    monkeypatch.setattr(mod, "get_database", unexpected)
    monkeypatch.setattr(mod, "request_extraction", unexpected)

    mod.main()

    runtime_artifacts = (
        args.out_terms_jsonl,
        args.out_aggregate_json,
        args.llm_calls_log,
        args.process_log,
        args.cache_path,
        args.checkpoint_path,
    )
    assert all(not path.exists() for path in runtime_artifacts)
