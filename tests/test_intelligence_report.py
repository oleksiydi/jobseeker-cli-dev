import csv
import json

from app.matching.intelligence_report import all_vacancies_csv, write_reports
import pytest


def _report() -> dict:
    return {
        "schema_version": "1.0",
        "profile": {"key": "demo", "profile_hash": "profile-hash", "source": "demo profile", "search_text_length": 142},
        "scope": {"region": "Berlin", "captured_input_hash": "scope-input-hash"},
        "references": [
            {
                "source": "ESCO",
                "version": "v1",
                "snapshot_date": "2026-10-01",
                "attribution": "This service uses the ESCO classification of the European Commission.",
                "sha256": "internal-hash",
                "records": [{"large": "internal manifest"}],
            }
        ],
        "model": {"provider": "local", "model": "test-model", "api_key": "do-not-export"},
        "llm": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "metrics": {"evaluated": 1},
        "raw_profile": {"name": "private name"},
        "results": [
            {
                "ref": "job-1",
                "title": "<script>alert(1)</script>",
                "role": "Data Engineer",
                "location": "Berlin",
                "url": "javascript:alert(1)",
                "score": 0.72,
                "status": "needs_review",
                "role_type": "core",
                "scores": {"skills": 0.8},
                "matched_skills": [
                    {
                        "label": "Python",
                        "profile_evidence": [{"source_path": "skills[0]", "evidence_quote": "Built data pipelines"}],
                        "job_evidence": [{"quote": "Python required", "source": "job description", "modality": "required"}],
                    }
                ],
                "missing_required": [{"label": "Kubernetes", "job_evidence": [{"quote": "Kubernetes experience required"}]}],
                "unconfirmed_skills": [{"label": "Airflow", "job_evidence": []}],
                "constraints": [],
                "unknowns": [{"quote": "on-call duties", "reason": "frequency not specified"}],
                "occupation_refs": ["esco:123"],
            }
        ],
        "adjacent_roles": [
            {
                "role": "Analytics Engineer",
                "transferable_skills": ["Python"],
                "missing_required": ["dbt"],
                "unknown_requirements": ["schedule"],
                "ref": "esco:456",
                "url": "https://example.org/role",
                "reason": "Data skills transfer",
            }
        ],
    }


def test_report_renders_evidence_gaps_unknowns_and_safe_links(tmp_path) -> None:
    paths = write_reports(_report(), tmp_path / "candidate")
    markdown = paths["markdown"].read_text(encoding="utf-8")

    assert "Относительная оценка" in markdown
    assert "не вероятность найма" in markdown
    assert "&lt;script&gt;" in markdown
    assert "Python required" in markdown
    assert "Kubernetes" in markdown
    assert "frequency not specified" in markdown
    assert "javascript:" not in markdown
    assert "[Открыть вакансию]" not in markdown
    assert "This service uses the ESCO classification" in markdown
    assert "internal-hash" not in markdown
    assert "private name" not in markdown
    assert "[Источник](https://example.org/role)" in markdown


def test_json_is_projected_and_csv_formula_values_are_escaped(tmp_path) -> None:
    report = _report()
    report["results"][0]["title"] = "=HYPERLINK(\"https://evil.invalid\")"
    report["results"][0]["score"] = "@SUM(1,1)"
    paths = write_reports(report, tmp_path / "candidate")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    serialized = paths["json"].read_text(encoding="utf-8")
    with paths["csv"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))

    assert payload["results"][0]["url"] is None
    assert payload["references"][0]["attribution"].startswith("This service uses the ESCO")
    assert "internal-hash" in serialized
    assert "internal manifest" in serialized
    assert "api_key" not in serialized
    assert "raw_profile" not in serialized
    assert rows[1][1].startswith("'=")
    assert rows[1][5].startswith("'@")


def test_json_preserves_audit_identity_and_untruncated_evidence(tmp_path) -> None:
    report = _report()
    report["engine_version"] = "engine-3"
    report["configuration"] = {"weights": {"skills": 0.4, "location": 0.2}, "api_key": "private"}
    report["model"].update(
        {
            "revision": "rev-7",
            "files_sha256": {"weights.bin": "model-sha"},
            "runtime": "onnx",
            "pooling": "mean",
            "prefixes": {"query": "query: "},
            "dimensions": 768,
            "input": "profile and vacancy text",
        }
    )
    result = report["results"][0]
    result.update(
        {
            "source_input_hash": "source-hash",
            "profile_input_hash": "profile-input-hash",
            "details_status": "complete",
            "decision_input_hash": "decision-hash",
            "constraints": [
                {
                    "kind": "on_call",
                    "required": True,
                    "profile_value": False,
                    "status": "conflict",
                    "quote": "Rufbereitschaft erforderlich",
                    "source": "job description",
                    "text_span": [100, 130],
                }
            ],
        }
    )
    result["matched_skills"][0]["job_evidence"][0].update(
        {
            "taxonomy_version": "esco-1.2",
            "model_version": "matcher-4",
            "matcher": "hybrid",
            "confidence": 0.9,
            "context_role": "required skill",
            "needs_review": False,
        }
    )
    result["matched_skills"][0]["profile_evidence"][0].update(
        {"status": "project_evidence", "last_used": "2013–2021"}
    )
    long_quote = "A" * 5001
    result["unknowns"] = [{"quote": long_quote, "reason": "unclear", "source": "description", "span": [3, 5004], "candidate_id": "u-1"}]
    paths = write_reports(report, tmp_path / "audit")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    markdown = paths["markdown"].read_text(encoding="utf-8")
    serialized = paths["json"].read_text(encoding="utf-8")

    assert payload["engine_version"] == "engine-3"
    assert payload["configuration"] == {"weights": {"skills": 0.4, "location": 0.2}}
    assert payload["model"]["revision"] == "rev-7"
    assert payload["model"]["files_sha256"]["weights.bin"] == "model-sha"
    assert payload["scope"]["captured_input_hash"] == "scope-input-hash"
    assert payload["profile"]["search_text_length"] == 142
    assert payload["results"][0]["source_input_hash"] == "source-hash"
    assert payload["results"][0]["decision_input_hash"] == "decision-hash"
    assert payload["results"][0]["matched_skills"][0]["job_evidence"][0]["taxonomy_version"] == "esco-1.2"
    assert payload["results"][0]["matched_skills"][0]["profile_evidence"][0]["last_used"] == "2013–2021"
    assert payload["results"][0]["constraints"][0]["text_span"] == [100, 130]
    assert payload["results"][0]["unknowns"][0]["span"] == [3, 5004]
    assert payload["results"][0]["unknowns"][0]["candidate_id"] == "u-1"
    assert long_quote in serialized
    assert "статус: **conflict**" in markdown
    assert "Период проекта: 2013–2021" in markdown
    assert "текущая готовность навыка не проверена" in markdown
    assert "rev-7" not in markdown
    assert "private" not in serialized


def test_http_and_https_urls_are_the_only_link_schemes(tmp_path) -> None:
    report = _report()
    report["results"][0]["url"] = "https://jobs.example/jobs/1"
    paths = write_reports(report, tmp_path / "links")
    markdown = paths["markdown"].read_text(encoding="utf-8")

    assert "[Открыть вакансию](https://jobs.example/jobs/1)" in markdown


def test_full_csv_streams_all_batches_and_never_publishes_partial_output(tmp_path):
    prefix = tmp_path / 'full'
    row = _report()['results'][0]
    row['title'] = '=UNTRUSTED()'
    with all_vacancies_csv(prefix) as (path, write):
        write([row])
        write([{**row, 'ref': 'second'}])
        assert not path.exists()
    with path.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]['title'].startswith("'=")
    original = path.read_bytes()
    with pytest.raises(RuntimeError):
        with all_vacancies_csv(prefix) as (_, write):
            write([row])
            raise RuntimeError('interrupted scan')
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['full.all.csv']
