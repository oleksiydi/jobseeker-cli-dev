import pytest
from pathlib import Path

from app.job_intelligence import main


def test_invalid_output_language_fails_before_database(monkeypatch):
    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    monkeypatch.setenv('JOBSEEKER_OUTPUT_LANGUAGE', 'invalid')
    monkeypatch.setattr('app.job_intelligence.get_database',
                        lambda *_: pytest.fail('Invalid language must not access MongoDB'))
    assert main(['--no-llm', '--no-embeddings']) == 1


@pytest.mark.parametrize('args', [
    ['--all-vacancies', '--candidate-limit', '100'],
    ['--vacancy-scope', 'all-stored'],
    ['--all-vacancies', '--batch-size', '0'],
    ['--all-vacancies', '--batch-size', '1001'],
    ['--all-vacancies', '--vacancies-file', 'jobs.jsonl', '--vacancy-scope', 'active'],
    ['--export-documents', '--limit', '2001'],
    ['--export-documents', '--limit', '0'],
    ['--export-documents', '--candidate-limit', '100'],
    ['--documents-dir', 'somewhere'],
    ['--application-file', 'private.json'],
])
def test_invalid_full_scan_combinations_fail_before_database_connection(monkeypatch, args):
    def unexpected_database(*_args):
        pytest.fail('Invalid arguments must not access MongoDB')

    monkeypatch.setattr('app.job_intelligence.get_database', unexpected_database)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('cli,env,expected,required', [
    (None, None, 'conf/profiles/application.private.json', False),
    (None, 'env.private.json', 'env.private.json', True),
    ('cli.private.json', 'env.private.json', 'cli.private.json', True),
])
def test_application_file_precedence_and_validation_before_database(monkeypatch, cli, env, expected, required):
    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    if env:
        monkeypatch.setenv('JOB_APPLICATION_FILE', env)
    else:
        monkeypatch.delenv('JOB_APPLICATION_FILE', raising=False)
    monkeypatch.setattr('app.matching.vacancy_documents.DocumentRenderer', lambda *_: object())
    def check_file(path, **kwargs):
        assert path == Path(expected) and kwargs['required'] == required
        raise ValueError('Application test stops before database')
    monkeypatch.setattr('app.job_intelligence.load_application_details', check_file)
    monkeypatch.setattr('app.job_intelligence.get_database', lambda *_: pytest.fail('Unexpected database access'))
    args = ['--export-documents'] + (['--application-file', cli] if cli else [])
    assert main(args) == 1
