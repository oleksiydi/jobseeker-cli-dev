"""Isolated CLI contract tests with synthetic profiles and vacancies."""

import json
import stat

import pytest

import app.application_evidence as application_evidence


class FakeDatabase:
    def __init__(self):
        self.client = self
        self.closed = False

    def close(self):
        self.closed = True


class FakeCache:
    def __init__(self, _database):
        self.values = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if key in self.values:
            self.hits += 1
            return self.values[key]
        self.misses += 1
        return None

    def put(self, key, value, kind=None):
        self.values[key] = value


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def synthetic_profile(**overrides):
    return {
        "experience_projects": [{
            "name": "Synthetic platform rollout",
            "company": "Example Test Org",
            "role_title": "Platform engineer",
            "timeframe": "2022–2024",
            "responsibilities": ["Automated synthetic deployment checks"],
            "deliverables": ["Reduced synthetic release errors"],
            "tech_stack": ["Python"],
        }],
        **overrides,
    }


def synthetic_vacancy(ref="synthetic-job-1", requirement_count=1):
    description = "Experience with synthetic deployment checks."
    quote = "synthetic deployment checks"
    start = description.index(quote)
    requirement = {
        "quote": quote, "text_span": [start, start + len(quote)],
        "source": "description", "modality": "unknown", "context_role": "requirement",
    }
    job = {
        "ref": ref, "title": "Synthetic Engineer", "role": "Engineer",
        "location": "Synthetic City", "description": description,
        "schedule": "", "employer": "Example Employer", "url": "",
        "source_input_hash": "synthetic-job-hash", "details_status": "available",
    }
    return {"job": job, "requirements": [requirement] * requirement_count,
            "category": "synthetic"}


def install_cli_fakes(monkeypatch, *, references=None):
    database = FakeDatabase()
    monkeypatch.setattr(application_evidence, "load_app_config", lambda: {})
    monkeypatch.setattr(application_evidence, "get_database", lambda _config: database)
    monkeypatch.setattr(application_evidence, "load_reference_terms",
                        lambda _database: ([], references if references is not None else [{"manifest": "synthetic"}]))
    monkeypatch.setattr(application_evidence, "DerivedCache", FakeCache)
    return database


def make_inputs(tmp_path, *, vacancies=None, profile=None, policy=None):
    profile_path = write_json(tmp_path / "synthetic-profile.json", profile or synthetic_profile())
    vacancy_path = write_json(
        tmp_path / "synthetic-vacancies.json",
        {"annotation_status": "synthetic", "vacancies": vacancies or [synthetic_vacancy()]},
    )
    policy_path = write_json(tmp_path / "synthetic-policy.json", policy) if policy else None
    return profile_path, vacancy_path, policy_path


def run_cli(profile_path, vacancy_path, output, *extra):
    args = [
        "--profile-file", str(profile_path), "--vacancies-file", str(vacancy_path),
        "--output", str(output), "--no-embeddings", *map(str, extra),
    ]
    return application_evidence.main(args)


def test_cli_runs_synthetic_inputs_and_writes_owner_only_reports(tmp_path, monkeypatch, capsys):
    database = install_cli_fakes(monkeypatch)
    profile, vacancies, _ = make_inputs(tmp_path)
    output = tmp_path / "private" / "application-evidence"

    assert run_cli(profile, vacancies, output) == 0
    assert database.closed
    result = json.loads(capsys.readouterr().out)
    report_paths = [output.with_suffix(suffix) for suffix in (".json", ".md", ".episodes.json")]
    assert set(result["reports"]) == {"json", "markdown", "projection"}
    assert all(path.is_file() for path in report_paths)
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in report_paths)


def test_german_output_preserves_episode_inputs_and_hashes(tmp_path, monkeypatch):
    import re

    install_cli_fakes(monkeypatch)
    profile, vacancies, _ = make_inputs(tmp_path)
    before = (profile.read_bytes(), vacancies.read_bytes())
    prefix = tmp_path / 'report'
    for language in ('ru', 'de'):
        assert run_cli(profile, vacancies, prefix, '--output-language', language) == 0
    ru = json.loads(prefix.with_suffix('.json').read_text())
    de_prefix = tmp_path / 'de/report'
    de = json.loads(de_prefix.with_suffix('.json').read_text())
    assert de['output_language'] == 'de'
    assert ru['input_hash'] == de['input_hash']
    assert prefix.with_suffix('.episodes.json').read_bytes() == de_prefix.with_suffix('.episodes.json').read_bytes()
    for suffix in ('.json', '.md'):
        assert not re.search('[А-Яа-яЁё]', de_prefix.with_suffix(suffix).read_text())
    assert (profile.read_bytes(), vacancies.read_bytes()) == before


def test_cli_rejects_input_output_collision_before_connecting_to_mongo(tmp_path, monkeypatch):
    profile, vacancies, _ = make_inputs(tmp_path)
    database_calls = []
    monkeypatch.setattr(application_evidence, "get_database", lambda _config: database_calls.append(True))
    output = profile.with_suffix("")

    assert run_cli(profile, vacancies, output) == 1
    assert database_calls == []


def test_cli_rejects_policy_bound_to_another_profile_before_connecting(tmp_path, monkeypatch):
    policy = {"profile_sha256": "stale synthetic hash",
              "resolution": "listed_projects_permitted", "basis": "user_statement"}
    profile, vacancies, policy_path = make_inputs(tmp_path, policy=policy)
    database_calls = []
    monkeypatch.setattr(application_evidence, "get_database", lambda _config: database_calls.append(True))

    assert run_cli(profile, vacancies, tmp_path / "report", "--usage-policy", policy_path) == 1
    assert database_calls == []


def test_selected_vacancies_are_capped_at_ten_and_missing_refs_are_rejected(tmp_path):
    vacancy_path = write_json(
        tmp_path / "many-vacancies.json",
        {"vacancies": [synthetic_vacancy(f"synthetic-{index}") for index in range(11)]},
    )
    selected = application_evidence.load_selected(vacancy_path, [f"synthetic-{i}" for i in range(10)])
    assert len(selected) == 10
    with pytest.raises(ValueError, match="1..10"):
        application_evidence.load_selected(vacancy_path, None)
    with pytest.raises(ValueError, match="missing"):
        application_evidence.load_selected(vacancy_path, ["synthetic-absent"])

    empty_path = write_json(tmp_path / "empty.json", {"vacancies": []})
    with pytest.raises(ValueError, match="1..10"):
        application_evidence.load_selected(empty_path, None)


def test_cli_accepts_100_requirements_and_rejects_101(tmp_path, monkeypatch):
    install_cli_fakes(monkeypatch)
    profile, _, _ = make_inputs(tmp_path)
    output_dir = tmp_path / "vacancies"
    output_dir.mkdir()
    at_limit = write_json(output_dir / "at-limit.json", {
        "vacancies": [synthetic_vacancy(requirement_count=100)]
    })
    over_limit = write_json(output_dir / "over-limit.json", {
        "vacancies": [synthetic_vacancy(requirement_count=101)]
    })

    assert run_cli(profile, at_limit, tmp_path / "limit-report") == 0
    assert run_cli(profile, over_limit, tmp_path / "over-limit-report") == 1
    assert not (tmp_path / "over-limit-report.json").exists()


def test_cli_does_not_silently_switch_when_local_model_is_missing(tmp_path, monkeypatch):
    install_cli_fakes(monkeypatch)
    profile, vacancies, _ = make_inputs(tmp_path)
    attempted = []

    class MissingModel:
        def __init__(self, *_args, **_kwargs):
            attempted.append(True)
            raise ValueError("Local MiniLM missing")

    from app.matching import episode_vectors

    monkeypatch.setattr(episode_vectors, "EpisodeVectors", MissingModel)
    output = tmp_path / "model-required-report"
    args = ["--profile-file", str(profile), "--vacancies-file", str(vacancies),
            "--output", str(output)]

    assert application_evidence.main(args) == 1
    assert attempted == [True]
    assert not output.with_suffix(".json").exists()
