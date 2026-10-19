"""Checks for complete ranked exports, safe publication, and real PDF/RTF output."""
import json
import hashlib
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.matching.intelligence import RankingAccumulator
from app.matching.intelligence_data import all_job_batches, normalize_job
from app.matching.vacancy_documents import (
    DocumentRenderer, export_settings, source_text, vacancy_html, write_documents,
)
from app.matching.vacancy_readable import readable_vacancy_html


def _job(ref='one', score=80, status='needs_review'):
    source = {
        'stellenangebote': {'referenznummer': ref, 'titel': 'Python Entwickler',
                           'arbeitgeber': 'Müller & Söhne', 'arbeitsort': {'ort': 'Berlin'}},
        'jobdetails': {'stellenangebotsBeschreibung': '<p>Python erforderlich.</p><p>Полный текст — Ende.</p>',
                       'verguetung': '60.000 €', 'kontakt': {'name': 'Frau Weiß', 'email': 'jobs@example.org'},
                       'weitere_orte': ['Bonn', 'Köln'], 'api_key': 'EXAMPLE_SECRET'},
    }
    return {**normalize_job(source, include_source=True), 'score': score, 'status': status,
            'role_type': 'direct', 'decision_input_hash': 'decision-' + ref,
            'scores': {'role': .8, 'skills': .7, 'semantic': .6, 'gap_penalty': .08},
            'matched_skills': [{'label': 'Python', 'job_evidence': [{'quote': 'Python erforderlich.'}],
                                'profile_evidence': [{'evidence_quote': 'Разрабатывал на Python', 'source_path': 'skills[0]'}]}],
            'missing_required': [{'label': 'SQL', 'job_evidence': [{'quote': 'SQL erforderlich'}]}],
            'unconfirmed_skills': [], 'constraints': [],
            'unknowns': [{'quote': f'Unbekannt {i}', 'reason': 'manual check'} for i in range(7)]}


def _report(jobs=None):
    return {'schema_version': 1, 'engine_version': 'test', 'results': jobs or [_job()],
            'scope': {'source': 'file', 'vacancy_scope': 'file', 'captured_at': 'first', 'scan_complete': True},
            'profile': {'key': 'test', 'profile_hash': 'hash', 'raw_profile': 'PRIVATE_CV'},
            'application': {'candidate': {'full_name': 'Alex Beispiel'}},
            'metrics': {'candidates_evaluated': 300, 'wall_seconds': 3, 'cache_hits': 0},
            'llm': {'calls': 0}, 'references': [{'source': 'esco', 'version': 'test'}]}


class FakeRenderer:
    identity = {'test': 'renderer-v1'}

    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def render(self, content, prefix, *, formats=('pdf', 'rtf')):
        self.calls.append((content, prefix.name))
        if len(self.calls) == self.fail_at:
            raise ValueError('conversion failed')
        for suffix in ('.' + fmt for fmt in formats):
            prefix.with_suffix(suffix).write_text(content, encoding='utf-8')
            prefix.with_suffix(suffix).chmod(0o600)


NOW = datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)


def test_full_source_survives_scan_and_global_top_without_growing_role_storage(tmp_path):
    path = tmp_path / 'jobs.json'
    rows = [{'ref': str(i), 'title': f'Job {i}', 'description': f'First\nLast {i}',
             'salary': {'max': i * 1000}} for i in range(6)]
    path.write_text(json.dumps(rows), encoding='utf-8')
    batches, _ = all_job_batches(None, {}, path=path, batch_size=2, include_source=True)
    ranked = RankingAccumulator(2, retain_documents=True)
    compact = RankingAccumulator(2)
    for batch in batches:
        for job in batch:
            job.update(score=int(job['ref']), status='no_known_conflict', role_type='direct')
        ranked.add(batch)
        compact.add(batch)
    assert [j['ref'] for j in ranked.results] == ['5', '4']
    assert ranked.results[0]['source_data']['salary']['max'] == 5000
    assert ranked.results[0]['description'] == 'First\nLast 5'
    assert all('source_data' not in group['best'] and 'description' not in group['best']
               for group in ranked.roles.values())
    assert all('source_data' not in j and 'description' not in j for j in compact.results)


def test_dossier_preserves_long_source_all_questions_and_escapes_markup():
    job = _job()
    original = 'Вступление\n' + ('Очень длинное описание.\n' * 1000) + 'КОНЕЦ {\\} <T>'
    job['source_data']['jobdetails']['stellenangebotsBeschreibung'] = original
    job['source_data']['jobdetails']['custom'] = '<img src="https://example.org/track"><script>bad()</script>'
    rendered = vacancy_html(job, 1, '2026-10-18')
    assert original.replace('<T>', '&lt;T&gt;').replace('\n', '<br>') in rendered
    assert '60.000 €' in rendered and 'Frau Weiß' in rendered and 'Köln' in rendered
    assert 'Unbekannt 6' in rendered
    assert 'EXAMPLE_SECRET' not in rendered
    assert '<img ' not in rendered and '<script>' not in rendered
    assert 'Разрабатывал на Python' in rendered and 'SQL erforderlich' in rendered


def test_html_description_keeps_paragraphs_lists_and_cplusplus():
    assert source_text('<p>C++ &amp; C#</p><ul><li>Eins</li><li>Zwei</li></ul>') == 'C++ & C#\n\n• Eins\n\n• Zwei'
    assert source_text('C++ <T>\nA < B') == 'C++ <T>\nA < B'


def test_dossier_explains_disabled_embeddings_and_formats_metadata():
    job = _job()
    job['location'] = 'DEUTSCHLAND|BAYERN|Regensburg|93053'
    job['schedule'] = 'vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false'
    rendered = vacancy_html(job, 1, '2026-10-18T12:00:00+02:00', semantic_enabled=False)
    assert '93053 Regensburg, Bayern, Deutschland' in rendered
    assert 'Полная занятость; Бессрочный договор' in rendered
    assert '18.10.2026, 12:00 CEST' in rendered
    assert 'Эмбеддинги отключены' in rendered
    assert 'Отключена' in rendered


def test_export_200_ranked_jobs_is_private_complete_and_reuses_identical_inputs(tmp_path):
    jobs = [_job(str(i), 200 - i) for i in range(200)]
    report, renderer = _report(jobs), FakeRenderer()
    first = write_documents(report, tmp_path, renderer=renderer, now=NOW)
    directory = first['directory']
    assert directory.name == '2026-10-19'  # Berlin date, not UTC date.
    manifest = json.loads(first['manifest'].read_text())
    assert len(list(directory.rglob('*.pdf'))) == 801
    assert len(list(directory.rglob('*.rtf'))) == 401
    assert len(manifest['files']) == 1202
    assert len(manifest['cover_letters']) == 200
    assert (directory / '00_Список.pdf').is_file()
    assert (directory / '01' / 'Оценка.pdf').is_file()
    assert (directory / '01' / 'Оценка.rtf').is_file()
    assert (directory / '200' / 'Вакансия.pdf').is_file()
    assert (directory / '200' / 'Anschreiben.pdf').is_file()
    assert (directory / '200' / 'Anschreiben.rtf').is_file()
    assert (directory / '200' / 'Рекомендации.pdf').is_file()
    assert manifest['summary']['metrics']['candidates_evaluated'] == 300
    assert manifest['vacancies'][-1]['ref'] == '199'
    assert 'EXAMPLE_SECRET' not in first['manifest'].read_text()
    assert 'PRIVATE_CV' not in first['manifest'].read_text()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
               for p in directory.rglob('*'))
    before = {str(p): p.stat().st_mtime_ns for p in directory.rglob('*')}
    report['metrics'].update(cache_hits=300, wall_seconds=1)
    report['scope']['captured_at'] = 'second'
    second = write_documents(report, tmp_path, renderer=renderer, now=NOW)
    assert second['reused'] and second['directory'] == directory
    assert len(renderer.calls) == 801
    assert before == {str(p): p.stat().st_mtime_ns for p in directory.rglob('*')}


def test_conversion_failure_never_publishes_partial_folder(tmp_path):
    with pytest.raises(ValueError, match='conversion failed'):
        write_documents(_report(), tmp_path, renderer=FakeRenderer(fail_at=2), now=NOW)
    assert not list(tmp_path.rglob('*.pdf'))
    assert not list(tmp_path.rglob('.documents-*'))
    assert not (tmp_path / '2026-10-19').exists()


def test_private_contacts_change_letters_and_snapshot_but_not_public_reports(tmp_path):
    from app.matching.intelligence_report import _report_json
    report = _report()
    application = {'candidate': {'full_name': 'PRIVATE_SENDER Example', 'email': 'private@example.org'},
                   'recipients': {}}
    first = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW,
                            application=application)
    original = json.loads(first['manifest'].read_text())
    assert original['application']['candidate']['full_name'] == 'PRIVATE_SENDER Example'
    assert 'PRIVATE_SENDER' in (first['directory'] / '01/Anschreiben.rtf').read_text()
    assert 'PRIVATE_SENDER' not in json.dumps(_report_json(report))
    application['candidate']['full_name'] = 'UPDATED_SENDER Example'
    second = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW,
                             application=application)
    current = json.loads(second['manifest'].read_text())
    assert current['input_hash'] != original['input_hash']
    assert current['summary']['profile'] == original['summary']['profile']
    assert len(list((tmp_path / '.history').rglob('Anschreiben.rtf'))) == 1
    letter = second['directory'] / '01/Anschreiben.rtf'
    letter.write_text('User edited letter')
    with pytest.raises(ValueError, match='preserve edits'):
        write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW, application=application)
    assert letter.read_text() == 'User edited letter'


def test_disabled_letters_do_not_capture_private_contacts(tmp_path):
    report = _report()
    report['configuration'] = {'document_export': {'cover_letter': {'enabled': False}}}
    result = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW,
                             application={'candidate': {'full_name': 'PRIVATE_SENDER Example'}})
    manifest = json.loads(result['manifest'].read_text())
    assert len(manifest['files']) == 5 and not manifest['cover_letters']
    assert 'application' not in manifest
    assert 'PRIVATE_SENDER' not in result['manifest'].read_text()
    assert not list(result['directory'].rglob('Anschreiben.*'))


def test_missing_company_skips_only_its_letter_and_preserves_recommendations(tmp_path):
    jobs = [_job('one'), _job('two')]
    jobs[1]['employer'] = ''
    result = write_documents(_report(jobs), tmp_path, renderer=FakeRenderer(), now=NOW)
    directory = result['directory']
    assert (directory / '01/Anschreiben.pdf').is_file()
    assert not list((directory / '02').glob('Anschreiben.*'))
    assert (directory / '02/Оценка.pdf').is_file()
    assert 'название организации' in (directory / '02/Рекомендации.pdf').read_text()
    manifest = json.loads(result['manifest'].read_text())
    assert [item['status'] for item in manifest['cover_letters']] == ['ready', 'skipped']
    assert manifest['cover_letters'][1]['critical_missing'] == ['recipient.company']


def test_recommendation_failure_preserves_previous_complete_export(tmp_path):
    report = _report()
    original = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW)
    before = original['manifest'].read_bytes()
    report['application']['candidate']['phone'] = '+49 941 1234567'
    with pytest.raises(ValueError, match='conversion failed'):
        write_documents(report, tmp_path, renderer=FakeRenderer(fail_at=5), now=NOW)
    assert original['manifest'].read_bytes() == before
    assert not list(tmp_path.rglob('.documents-*'))


def test_user_edited_rtf_is_never_overwritten(tmp_path):
    result = write_documents(_report(), tmp_path, renderer=FakeRenderer(), now=NOW)
    document = result['directory'] / '01' / 'Оценка.rtf'
    document.write_text('Мои правки', encoding='utf-8')
    with pytest.raises(ValueError, match='preserve edits'):
        write_documents(_report(), tmp_path, renderer=FakeRenderer(), now=NOW)
    assert document.read_text() == 'Мои правки'


def test_changed_source_updates_daily_root_and_archives_previous(tmp_path):
    report = _report()
    first = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW)
    report['results'][0]['source_data']['jobdetails']['verguetung'] = '70.000 €'
    second = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW)
    assert first['directory'] == second['directory']
    archived = list((tmp_path / '.history').rglob('manifest.json'))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())['vacancies'][0]['source_data']['jobdetails']['verguetung'] == '60.000 €'
    assert json.loads(second['manifest'].read_text())['vacancies'][0]['source_data']['jobdetails']['verguetung'] == '70.000 €'


def test_legacy_folder_migrates_without_losing_original_documents(tmp_path):
    legacy = tmp_path / '2026-10-19' / 'оценка-previous'
    legacy.mkdir(parents=True)
    document = legacy / 'old.rtf'
    document.write_text('Предыдущая выгрузка', encoding='utf-8')
    manifest = {'input_hash': 'legacy', 'files': {'old.rtf': hashlib.sha256(document.read_bytes()).hexdigest()}}
    (legacy / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = write_documents(_report(), tmp_path, renderer=FakeRenderer(), now=NOW)
    assert (result['directory'] / '01' / 'Вакансия.pdf').is_file()
    archived = list((tmp_path / '.history').rglob('old.rtf'))
    assert len(archived) == 1 and archived[0].read_text() == 'Предыдущая выгрузка'


def test_failed_daily_publication_rolls_back_previous_export(tmp_path, monkeypatch):
    import app.matching.vacancy_documents as documents
    report = _report()
    first = write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW)
    original_manifest = first['manifest'].read_bytes()
    original_rename = documents.os.rename

    def fail_publication(source, destination):
        if Path(source).name.startswith('.documents-'):
            raise OSError('publication failed')
        original_rename(source, destination)

    monkeypatch.setattr(documents.os, 'rename', fail_publication)
    report['results'][0]['source_data']['jobdetails']['verguetung'] = '70.000 €'
    with pytest.raises(OSError, match='publication failed'):
        write_documents(report, tmp_path, renderer=FakeRenderer(), now=NOW)
    assert first['manifest'].read_bytes() == original_manifest
    assert (first['directory'] / '01' / 'Оценка.rtf').is_file()
    assert not list(tmp_path.glob('.documents-*'))


def test_readable_vacancy_groups_complete_business_data_without_analysis_or_internal_fields():
    job = _job()
    details = job['source_data']['jobdetails']
    details.update({
        'stellenangebotsBeschreibung': '### Aufgaben\n- Python entwickeln\n- Tests pflegen\n\n**Ende** vollständig.',
        'homeofficemoeglich': True,
        'stellenlokationen': [{'adresse': {'ort': 'Bonn', 'strasse': 'Hauptstraße', 'hausnummer': '7'}},
                             {'adresse': {'ort': 'Köln', 'plz': '50667'}}],
        'customBenefit': 'Sonderurlaub', 'details_hash': 'INTERNAL_HASH',
    })
    rendered = readable_vacancy_html(job, 1, '2026-10-18')
    for expected in ('Условия работы', 'Оплата', 'Места работы', 'Контакты и подача заявки',
                     '60.000 €', 'Frau Weiß', 'jobs@example.org', 'Bonn', 'Köln', 'Hauptstraße',
                     'Sonderurlaub', '<h3>Aufgaben</h3>', '<li>Python entwickeln</li>',
                     '<strong>Ende</strong> vollständig.'):
        assert expected in rendered
    assert 'EXAMPLE_SECRET' not in rendered and 'INTERNAL_HASH' not in rendered
    assert 'Разрабатывал на Python' not in rendered and 'Как получена оценка' not in rendered
    assert rendered.count('vollständig.') == 1


@pytest.mark.parametrize('values', [{'limit': 2001}, {'limit': 0}, {'limit': True}, {'enabled': 'false'},
                                  {'conversion_timeout_seconds': 0}, {'output_dir': ''},
                                  {'output_dir': '   '}, {'output_dir': False}, {'output_dir': 123}])
def test_invalid_document_config_is_rejected(values):
    with pytest.raises(ValueError):
        export_settings({'document_export': values})


def test_real_pdf_and_rtf_preserve_unicode_and_last_page(tmp_path):
    pytest.importorskip('weasyprint')
    if not shutil.which('pandoc') or not shutil.which('pdftotext'):
        pytest.skip('Pandoc/pdftotext not installed')
    job = _job()
    tail = 'КОНЕЦ полного текста: Grüße, C++, C#, {скобки} и \\путь.'
    job['source_data']['jobdetails']['stellenangebotsBeschreibung'] = (
        'Строка с Unicode: ' + 'Содержание вакансии.\n' * 120 + tail)
    renderer = DocumentRenderer()
    prefix = tmp_path / 'dossier'
    renderer.render(vacancy_html(job, 1, '2026-10-18'), prefix)
    pdf_text = subprocess.run(['pdftotext', str(prefix.with_suffix('.pdf')), '-'],
                              capture_output=True, text=True, check=True, timeout=30).stdout
    rtf_text = subprocess.run(['pandoc', '-f', 'rtf', '-t', 'plain', str(prefix.with_suffix('.rtf'))],
                              capture_output=True, text=True, check=True, timeout=30).stdout
    for text in (pdf_text, rtf_text):
        compact = ' '.join(text.split())
        assert tail in compact
        assert '60.000 €' in text and 'Frau Weiß' in text
        assert 'Unbekannt 6' in text and 'Разрабатывал на Python' in compact
    assert not prefix.with_suffix('.html').exists()
    assert 'Строка с Unicode: Содержание вакансии.\n' in rtf_text
    assert rtf_text.count(job['title']) == pdf_text.count(job['title'])
    readable = tmp_path / 'vacancy'
    renderer.render(readable_vacancy_html(job, 1, '2026-10-18'), readable, formats=('pdf',))
    assert readable.with_suffix('.pdf').is_file() and not readable.with_suffix('.rtf').exists()
    text = subprocess.run(['pdftotext', str(readable.with_suffix('.pdf')), '-'],
                          capture_output=True, text=True, check=True, timeout=30).stdout
    assert tail in ' '.join(text.split())
    assert '60.000 €' in text and 'Frau Weiß' in text
    assert 'Разрабатывал на Python' not in text


def test_renderer_timeout_is_bounded_and_does_not_log_source(monkeypatch):
    renderer = object.__new__(DocumentRenderer)
    renderer.timeout = 2

    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 2
        raise subprocess.TimeoutExpired(args[0], 2, stderr=b'PRIVATE_SOURCE')

    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='timed out') as exc:
        renderer._run(['example'])
    assert 'PRIVATE_SOURCE' not in str(exc.value)


@pytest.mark.parametrize('limit_source', ['cli', 'config'])
@pytest.mark.parametrize('directory_source', ['default', 'omitted', 'config', 'cli'])
@pytest.mark.parametrize('export_mode', ['pdf', 'json', 'both', 'json_env', 'json_config'])
@pytest.mark.parametrize('language', ['ru', 'de'])
def test_cli_exports_global_top_and_complete_source_without_llm(
        tmp_path, monkeypatch, capsys, limit_source, directory_source, export_mode, language):
    from app.job_intelligence import main

    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'key': 'test', 'roles': ['Python Entwickler'],
                                   'skills': ['Python']}), encoding='utf-8')
    vacancies = tmp_path / 'jobs.jsonl'
    rows = [
        {'ref': 'low', 'title': 'Lager', 'description': 'Lagerarbeit', 'salary': '30.000 €'},
        {'ref': 'best', 'title': 'Python Entwickler', 'description': 'Python erforderlich.\nVollständiger Text.', 'salary': '60.000 €'},
        {'ref': 'middle', 'title': 'Entwickler', 'description': 'Python erforderlich.', 'salary': '50.000 €'},
    ]
    vacancies.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    monkeypatch.setattr('app.job_intelligence.get_database', lambda *_: SimpleNamespace(
        client=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr('app.job_intelligence.load_reference_terms', lambda *_: (
        [], [{'source': 'test', 'version': '1'}]))

    class Cache:
        def __init__(self, *_):
            self.hits = self.misses = 0

        def get(self, *_):
            self.misses += 1

        def put(self, *_):
            pass

    monkeypatch.setattr('app.job_intelligence.DerivedCache', Cache)
    def renderer(*_):
        assert export_mode in {'pdf', 'both'}, 'JSON/Markdown must work without converters'
        return FakeRenderer()

    monkeypatch.setattr('app.matching.vacancy_documents.DocumentRenderer', renderer)
    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    monkeypatch.delenv('JOB_INTELLIGENCE_EXPORT_JSON_MD', raising=False)
    # Explicit CLI must win over both ENV and selected/global configuration.
    monkeypatch.setenv('JOBSEEKER_OUTPUT_LANGUAGE', 'ru' if language == 'de' else 'de')
    settings = json.loads(Path('conf/job-intelligence.json').read_text())
    settings['document_export']['cover_letter']['enabled'] = False
    settings['document_export']['json_md_enabled'] = export_mode == 'json_config'
    if export_mode == 'json_env':
        monkeypatch.setenv('JOB_INTELLIGENCE_EXPORT_JSON_MD', 'true')
    elif export_mode in {'json', 'both'}:
        monkeypatch.setenv('JOB_INTELLIGENCE_EXPORT_JSON_MD', 'false')
    output_prefix = tmp_path / 'top3' / 'report'
    expected_directory = output_prefix.parent / 'documents'
    directory_args = []
    if directory_source == 'omitted':
        settings['document_export'].pop('output_dir')
    elif directory_source in {'config', 'cli'}:
        settings['document_export']['output_dir'] = str(tmp_path / 'configured-documents')
        expected_directory = tmp_path / 'configured-documents'
    if directory_source == 'cli':
        expected_directory = tmp_path / 'cli-documents'
        directory_args = ['--documents-dir', str(expected_directory)]
    limit_args = ['--limit', '200']
    if limit_source == 'config':
        settings['document_export']['limit'] = 200
        limit_args = []
    config_path = tmp_path / 'settings.json'
    config_path.write_text(json.dumps(settings))
    export_args = []
    if export_mode in {'pdf', 'both'}:
        export_args.append('--export-documents')
    if export_mode in {'json', 'both'}:
        export_args.append('--export-json-md')
    result = main(['--profile-file', str(profile), '--vacancies-file', str(vacancies),
                   *export_args, '--config', str(config_path),
                   '--batch-size', '1', '--no-embeddings', '--no-llm',
                   '--output-language', language,
                   '--output', str(output_prefix), *directory_args, *limit_args])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    names = {'list': '00_Список', 'assessment': 'Оценка', 'vacancy': 'Вакансия', 'advice': 'Рекомендации'}
    if language == 'de':
        names = {'list': '00_Übersicht', 'assessment': 'Bewertung', 'vacancy': 'Stellenangebot', 'advice': 'Empfehlungen'}
    language_dir = Path('de') if language == 'de' else Path('.')
    if export_mode in {'pdf', 'both'}:
        documents_directory = Path(output['reports']['documents'])
        assert documents_directory.parent == expected_directory / language_dir
        assert Path(output['reports']['documents_manifest']) == documents_directory / 'manifest.json'
        for suffix in ('pdf', 'rtf'):
            assert (documents_directory / (names['list'] + '.' + suffix)).is_file()
            assert (documents_directory / '01' / (names['assessment'] + '.' + suffix)).is_file()
    if export_mode != 'pdf':
        documents_directory = Path(output['reports']['json_md_documents'])
        assert documents_directory.parent == expected_directory / 'json_md' / language_dir
        assert Path(output['reports']['json_md_manifest']) == documents_directory / 'manifest.json'
        for suffix in ('json', 'md'):
            assert (documents_directory / (names['list'] + '.' + suffix)).is_file()
            for name in (names['vacancy'], 'Anschreiben', names['assessment'], names['advice']):
                assert (documents_directory / '01' / f'{name}.{suffix}').is_file()
        letter = json.loads((documents_directory / '01' / 'Anschreiben.json').read_text())
        assert letter['status'] == 'disabled'
    manifest_path = documents_directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['vacancies'][0]['ref'] == 'best'
    assert len(manifest['vacancies']) == 3
    assert manifest['vacancies'][0]['source_data']['salary'] == '60.000 €'
    assert manifest['summary']['scope']['scan_complete']
    assert manifest['summary']['metrics']['candidates_evaluated'] == 3
    assert manifest['summary']['llm']['calls'] == 0
    report_path = output_prefix.parent / language_dir / (output_prefix.name + '.json')
    assert 'source_data' not in json.loads(report_path.read_text())['results'][0]
    assert manifest['output_language'] == language
    if language == 'de':
        import re
        for path in documents_directory.rglob('*'):
            if path.is_file():
                assert not re.search('[А-Яа-яЁё]', path.read_text()), path.name


def test_missing_pandoc_fails_before_database_scan(monkeypatch):
    from app.job_intelligence import main

    def unexpected(*_):
        pytest.fail('Missing converters must not scan MongoDB')

    monkeypatch.setattr('app.matching.vacancy_documents.shutil.which', lambda *_: None)
    monkeypatch.setattr('app.job_intelligence.get_database', unexpected)
    assert main(['--export-documents']) == 1


def test_text_dossier_full_source_letters_assessment_and_reuse(tmp_path, monkeypatch):
    def unexpected(*_):
        pytest.fail('Text export must not construct a PDF renderer')

    monkeypatch.setattr('app.matching.vacancy_documents.DocumentRenderer', unexpected)
    report = _report()
    original = 'Начало\n' + 'Vollständiger Text.\n' * 1000 + 'КОНЕЦ C++ C# <T>'
    report['results'][0]['source_data']['jobdetails'].update(
        stellenangebotsBeschreibung=original, custom={'array': ['Bonn', 'Köln'],
                                                     'password': 'HIDDEN_PASSWORD'})
    exported = write_documents(report, tmp_path, now=NOW, text_only=True)
    directory = exported['directory']
    assert directory.name == '2026-10-19'  # Europe/Berlin, not the UTC date.
    manifest = json.loads(exported['manifest'].read_text())
    assert len(manifest['files']) == 10
    assert manifest['renderers']['formats'] == ['json', 'markdown']
    position = directory / '01'
    vacancy = json.loads((position / 'Вакансия.json').read_text())
    assert vacancy['source_data']['jobdetails']['stellenangebotsBeschreibung'] == original
    assert vacancy['source_data']['jobdetails']['custom'] == {'array': ['Bonn', 'Köln']}
    assert 'КОНЕЦ C++ C# <T>' in (position / 'Вакансия.md').read_text()
    assessment = json.loads((position / 'Оценка.json').read_text())
    assert len(assessment['result']['unknowns']) == 7
    assert 'Unbekannt 6' in (position / 'Оценка.md').read_text()
    assert 'SQL erforderlich' in (position / 'Оценка.md').read_text()
    letter = json.loads((position / 'Anschreiben.json').read_text())
    assert letter['status'] == 'ready'
    assert letter['body_markdown'] == (position / 'Anschreiben.md').read_text()
    assert 'Alex Beispiel' in letter['body_markdown']
    assert 'Mit freundlichen Grüßen' in letter['body_markdown']
    assert 'Müller &amp; Söhne' in letter['body_markdown']
    assert 'Не указано' not in letter['body_markdown']
    recommendations = json.loads((position / 'Рекомендации.json').read_text())
    assert recommendations['letter_status'] == 'ready'
    assert recommendations['recommendations'] == letter['recommendations']
    for path in directory.rglob('*'):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
        if path.is_file():
            text = path.read_text()
            assert all(marker not in text for marker in (
                'PRIVATE_CV', 'EXAMPLE_SECRET', 'HIDDEN_PASSWORD'))
    before = {p: p.stat().st_mtime_ns for p in directory.rglob('*')}
    report['metrics'].update(wall_seconds=99, cache_hits=999)
    report['scope']['captured_at'] = 'second'
    assert write_documents(report, tmp_path, now=NOW, text_only=True)['reused']
    assert before == {p: p.stat().st_mtime_ns for p in directory.rglob('*')}


@pytest.mark.parametrize('missing', ['candidate', 'employer'])
@pytest.mark.parametrize('language', ['ru', 'de'])
def test_text_dossier_skipped_letter_is_explicit(tmp_path, missing, language):
    report = _report()
    report['configuration'] = {'output_language': language}
    if missing == 'candidate':
        report['application'] = {}
    else:
        report['results'][0]['employer'] = ''
        report['results'][0]['source_data']['stellenangebote']['arbeitgeber'] = ''
    exported = write_documents(report, tmp_path, now=NOW, text_only=True)
    position = exported['directory'] / '01'
    letter = json.loads((position / 'Anschreiben.json').read_text())
    assert letter['status'] == 'skipped'
    assert letter['critical_missing']
    notice = 'Письмо не создано' if language == 'ru' else 'Anschreiben wurde nicht erstellt'
    assert notice in letter['body_markdown']
    assert 'Mit freundlichen Grüßen' not in letter['body_markdown']
    assert len(list(position.iterdir())) == 8


def test_text_publication_preserves_previous_export_on_error_and_edits(tmp_path, monkeypatch):
    report = _report()
    first = write_documents(report, tmp_path, now=NOW, text_only=True)
    original_manifest = first['manifest'].read_bytes()
    report['results'][0]['source_data']['jobdetails']['custom'] = 'updated'

    def fail(*_):
        raise ValueError('text export failed')

    with monkeypatch.context() as patch:
        patch.setattr('app.matching.vacancy_text_documents.write_text_position', fail)
        with pytest.raises(ValueError, match='text export failed'):
            write_documents(report, tmp_path, now=NOW, text_only=True)
    assert first['manifest'].read_bytes() == original_manifest
    assert not list(tmp_path.glob('.documents-*'))
    second = write_documents(report, tmp_path, now=NOW, text_only=True)
    assert not second['reused']
    archived = list((tmp_path / '.history' / first['directory'].name).glob('*/manifest.json'))
    assert len(archived) == 1 and archived[0].read_bytes() == original_manifest
    edited = second['directory'] / '01' / 'Anschreiben.md'
    edited.write_text('Мои правки', encoding='utf-8')
    with pytest.raises(ValueError, match='changed or is incomplete'):
        write_documents(report, tmp_path, now=NOW, text_only=True)
    assert edited.read_text() == 'Мои правки'


@pytest.mark.parametrize('override,expected', [
    (None, True), ('', True), ('  ', True), ('false', False),
    ('0', False), ('no', False), (' YES ', True), ('1', True), ('true', True),
])
def test_json_md_environment_override(override, expected):
    settings = {'document_export': {'json_md_enabled': True}}
    assert export_settings(settings, json_md_override=override)['json_md_enabled'] is expected


@pytest.mark.parametrize('value', ['false', 1, None, []])
def test_json_md_config_requires_boolean(value):
    with pytest.raises(ValueError, match='json_md_enabled must be boolean'):
        export_settings({'document_export': {'json_md_enabled': value}})


def test_invalid_text_export_environment_fails_before_database(monkeypatch):
    from app.job_intelligence import main

    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    monkeypatch.setenv('JOB_INTELLIGENCE_EXPORT_JSON_MD', 'invalid')

    def unexpected(*_):
        pytest.fail('Invalid export settings must fail before reading MongoDB')

    monkeypatch.setattr('app.job_intelligence.get_database', unexpected)
    assert main(['--no-llm']) == 1


@pytest.mark.parametrize('text_only', [False, True])
def test_dossier_carries_original_url_effective_geography_and_warnings(tmp_path, text_only):
    report = _report()
    url = 'https://www.arbeitsagentur.de/jobsuche/suche?wo=Berlin&umkreis=15'
    report['scope']['collection_coverage'] = {
        'search_id': 'a' * 64, 'search_url': url, 'coverage_complete': False,
        'original_geography': {'wo': 'Berlin', 'umkreis': 15},
        'effective_geography': [{'wo': '10115', 'umkreis': 0}], 'geography_narrowed': True,
        'warnings': [{'code': 'radius_removed', 'message': 'Окружение исключено.'}],
    }
    exported = write_documents(report, tmp_path, now=NOW, text_only=text_only, renderer=FakeRenderer())
    suffix = '.md' if text_only else '.pdf'
    directory = exported['directory']
    for path in (directory / ('00_Список' + suffix), directory / '01' / ('Оценка' + suffix)):
        content = path.read_text()
        assert 'a' * 64 in content and '10115' in content
        assert 'Окружение исключено' in content
        assert 'Не подтверждена' in content
    manifest = json.loads(exported['manifest'].read_text())
    assert manifest['summary']['scope']['collection_coverage']['search_url'] == url
