"""Offline experimental mode, separate files and no changes to main outputs."""
import json
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.job_intelligence import main
from app.matching.episode_vectors import PreparedFactVectors
from app.matching.skill_report import write_skill_reports


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    monkeypatch.setattr('app.job_intelligence.load_app_config', lambda: {})
    monkeypatch.delenv('JOB_INTELLIGENCE_SKILL_MATCHING', raising=False)
    monkeypatch.delenv('JOBSEEKER_OUTPUT_LANGUAGE', raising=False)
    monkeypatch.setattr('app.db.mongo.get_database', lambda *_: pytest.fail('File mode must not open Mongo'))
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'skills': ['Concourse'], 'experience_projects': [{
        'name': 'Release platform', 'timeframe': '2021–2022',
        'responsibilities': ['CI/CD-Pipelines mit Concourse betreut.'], 'tech_stack': ['Concourse'],
    }]}))
    vacancies = tmp_path / 'vacancies.json'
    vacancies.write_text(json.dumps([
        {'ref': 'positive', 'title': 'Release Engineer', 'description': 'Dein Profil\n- CI/CD-Pipelines betreuen'},
        {'ref': 'negative', 'title': 'Buchhaltung', 'description': 'Dein Profil\n- Steuerrecht'},
        {'ref': 'missing', 'title': 'Unklar', 'description': ''},
    ]))
    def make_vectors(cache, model_dir, threads, facts):
        class Vectors:
            identity = {'name': 'synthetic', 'revision': 'test'}

            def similarities(self, queries):
                return np.asarray([[.9 if 'CI/CD' in query and f['kind'] == 'action' else .05
                                    for f in facts] for query in queries])
        return Vectors(), SimpleNamespace(counters={'vectors_computed': len(facts)})
    monkeypatch.setattr('app.skill_matching.make_vectors', make_vectors)
    return profile, vacancies


def args_for(inputs, prefix):
    profile, vacancies = inputs
    return ['--experimental-skill-matching', '--profile-file', str(profile),
            '--vacancies-file', str(vacancies), '--output', str(prefix), '--no-llm']


@pytest.mark.parametrize('language', ['ru', 'de'])
def test_file_mode_ranks_full_source_preserves_primary_outputs_and_writes_private_files(
        inputs, tmp_path, capsys, language):
    prefix = tmp_path / 'private/report'
    prefix.parent.mkdir()
    sentinels = []
    for suffix in ('.json', '.md', '.csv', '.pdf', '.rtf'):
        path = prefix.with_suffix(suffix)
        path.write_text('PRIMARY_OUTPUT')
        sentinels.append(path)
    assert main(args_for(inputs, prefix) + ['--output-language', language, '--limit', '2']) == 0
    paths = json.loads(capsys.readouterr().out)['reports']
    report = json.loads(Path(paths['skills_json']).read_text())
    assert report['metrics']['vacancies'] == 3 and len(report['results']) == 2
    assert report['results'][0]['ref'] == 'positive'
    assert report['results'][1]['ref'] == 'negative'
    assert report['results'][1]['requirements'][0]['status'] == 'no_candidate'
    assert report['output_language'] == language
    assert len(Path(paths['skills_all_csv']).read_text().splitlines()) == 4
    for path in paths.values():
        path = Path(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        if language == 'de':
            assert path.parent.name == 'de'
            assert not re.search('[А-Яа-яЁё]', path.read_text())
    assert all(path.read_text() == 'PRIMARY_OUTPUT' for path in sentinels)
    assert 'Anschreiben' not in ''.join(Path(path).name for path in paths.values())


def test_language_does_not_change_experimental_hashes_or_signals(inputs, tmp_path):
    reports = []
    prefix = tmp_path / 'report'
    for language in ('ru', 'de'):
        assert main(args_for(inputs, prefix) + ['--output-language', language]) == 0
        reports.append(json.loads((prefix.parent / ('de' if language == 'de' else '.') /
                                   'report.skills.json').read_text()))
    assert reports[0]['input_hash'] == reports[1]['input_hash']
    assert [r['semantic_rank_signal'] for r in reports[0]['results']] == [
        r['semantic_rank_signal'] for r in reports[1]['results']]
    for a, b in zip(reports[0]['results'], reports[1]['results']):
        assert a['input_hash'] == b['input_hash']
        if a['candidate_requirements']:
            assert a['requirements'][0]['candidates'][0]['source_fact'] == b['requirements'][0]['candidates'][0]['source_fact']


@pytest.mark.parametrize('extra', [['--no-embeddings'], ['--query', 'Python'],
                                 ['--candidate-limit', '3'], ['--application-file', 'private.json'],
                                 ['--documents-dir', 'docs']])
def test_unsupported_combinations_fail_before_model_or_db(inputs, tmp_path, monkeypatch, extra):
    monkeypatch.setattr('app.skill_matching.make_vectors', lambda *_: pytest.fail('Unexpected embedding'))
    assert main(args_for(inputs, tmp_path / 'report') + extra) == 1


def test_input_output_collision_is_rejected_without_changing_input(inputs, tmp_path, monkeypatch):
    profile, vacancies = inputs
    collision = tmp_path / 'report.skills.json'
    collision.write_bytes(profile.read_bytes())
    before = collision.read_bytes()
    monkeypatch.setattr('app.skill_matching.make_vectors', lambda *_: pytest.fail('Unexpected embedding'))
    assert main(['--experimental-skill-matching', '--profile-file', str(collision),
                 '--vacancies-file', str(vacancies), '--output', str(tmp_path / 'report')]) == 1
    assert collision.read_bytes() == before


def test_existing_authorized_policy_resolves_restrictions_without_rewriting_profile(
        inputs, tmp_path, caplog):
    import hashlib

    profile, _ = inputs
    raw = json.loads(profile.read_text())
    raw['candidate'] = {'ethics_constraints': {'do_not_mention': ['Other unlisted projects']}}
    profile.write_text(json.dumps(raw))
    original = profile.read_bytes()
    prefix = tmp_path / 'report'
    assert main(args_for(inputs, prefix)) == 1
    assert '--usage-policy' in caplog.text
    policy = tmp_path / 'authorized-policy.json'
    policy.write_text(json.dumps({
        'profile_sha256': hashlib.sha256(original).hexdigest(),
        'resolution': 'listed_projects_permitted', 'basis': 'user_statement',
        'statement': 'The restriction applies to other projects not listed in the profile',
    }))
    assert main(args_for(inputs, prefix) + ['--usage-policy', str(policy)]) == 0
    assert profile.read_bytes() == original
    assert (tmp_path / 'report.skills.json').is_file()


def test_converter_failure_preserves_previous_reports(inputs, tmp_path, monkeypatch):
    assert main(args_for(inputs, tmp_path / 'report')) == 0
    report = json.loads((tmp_path / 'report.skills.json').read_text())
    before = {p: p.read_bytes() for p in tmp_path.glob('report*')}
    class Broken:
        def render(self, *args):
            raise ValueError('conversion failed')
    with pytest.raises(ValueError):
        write_skill_reports(report, tmp_path / 'report', renderer=Broken())
    assert all(p.read_bytes() == contents for p, contents in before.items())
    assert not list(tmp_path.glob('.skills-*'))


def test_standalone_entrypoint_enables_mode(inputs, tmp_path):
    from app.skill_matching import main as standalone
    args = args_for(inputs, tmp_path / 'report')[1:]
    assert standalone(args) == 0
    assert (tmp_path / 'report.skills.json').is_file()


def test_model_failure_has_no_silent_lexical_fallback(inputs, tmp_path, monkeypatch):
    def missing(*args):
        raise ValueError('Synthetic local model missing')
    monkeypatch.setattr('app.skill_matching.make_vectors', missing)
    assert main(args_for(inputs, tmp_path / 'report')) == 1
    assert not list(tmp_path.glob('report.skills.*'))


def test_pdf_rtf_paths_never_overwrite_main_documents(inputs, tmp_path, monkeypatch):
    from test_vacancy_documents import FakeRenderer
    monkeypatch.setattr('app.matching.vacancy_documents.DocumentRenderer', lambda *_: FakeRenderer())
    for suffix in ('.pdf', '.rtf'):
        (tmp_path / ('report' + suffix)).write_text('PRIMARY_DOCUMENT')
    assert main(args_for(inputs, tmp_path / 'report') + ['--export-documents']) == 0
    for suffix in ('.pdf', '.rtf'):
        assert (tmp_path / ('report' + suffix)).read_text() == 'PRIMARY_DOCUMENT'
        assert (tmp_path / ('report.skills' + suffix)).is_file()


def test_mongo_mode_preserves_source_coverage_and_closes_client(inputs, tmp_path, monkeypatch):
    import app.skill_matching as runner
    from app.matching.intelligence_data import all_job_batches
    database = SimpleNamespace(client=SimpleNamespace(close=lambda: closed.append(True)))
    closed = []
    monkeypatch.setattr('app.db.mongo.get_database', lambda *_: database)
    monkeypatch.setattr(runner, 'DerivedCache', lambda *_: runner.MemoryCache())
    monkeypatch.setattr(runner, 'load_reference_terms', lambda *_: ([], []))
    def batches(db, config, **kwargs):
        assert db is database and kwargs['vacancy_scope'] == 'latest-run'
        values, scope = all_job_batches(None, {}, path=inputs[1], batch_size=1)
        scope['collection_coverage'] = {
            'coverage_complete': False, 'warnings': [{'code': 'limited', 'message': 'Limited source'}]}
        return values, scope
    monkeypatch.setattr(runner, 'all_job_batches', batches)
    assert main(['--experimental-skill-matching', '--profile-file', str(inputs[0]),
                 '--output', str(tmp_path / 'report')]) == 0
    report = json.loads((tmp_path / 'report.skills.json').read_text())
    assert not report['scope']['collection_coverage']['coverage_complete']
    assert closed == [True]


def test_prepared_vectors_embed_profile_once_and_handle_long_fact_chunks():
    class Base:
        identity = {'synthetic': True}
        counters = {'chunks': 0}
        calls = []

        def chunks(self, text):
            return text.split('|')

        def embed(self, texts):
            self.calls.append(texts)
            return [[1, 0] if text == 'release' else [0, 1] for text in texts]
    base = Base()
    prepared = PreparedFactVectors(base, ['noise|release', 'accounts'])
    assert prepared.similarities(['release']).tolist() == [[1., 0.]]
    assert prepared.similarities(['accounts']).tolist() == [[1., 1.]]
    assert base.calls == [['noise', 'release', 'accounts'], ['release'], ['accounts']]
