"""Both output languages preserve ranking, technical IDs and literal evidence."""
import ast
import json
import re
import string
from copy import deepcopy
from pathlib import Path

import pytest

from app.core.i18n import (
    catalog, current_language, language_scope, localize_message, message_record,
    resolve_language, tr,
)
from app.jobcenter.adaptive_search import AdaptiveSearch
from app.jobcenter.search_url import parse_search_url
from app.matching.output_localization import localize_result


@pytest.mark.parametrize('cli,env,selected,global_value,expected', [
    (None, None, None, None, 'ru'),
    (None, None, None, 'de', 'de'),
    (None, None, 'ru', 'de', 'ru'),
    (None, 'de', 'ru', 'ru', 'de'),
    ('ru', 'de', 'de', 'de', 'ru'),
    ('de', 'ru', 'ru', 'ru', 'de'),
    (None, ' ', 'de', 'ru', 'de'),
])
def test_language_precedence(monkeypatch, cli, env, selected, global_value, expected):
    monkeypatch.delenv('JOBSEEKER_OUTPUT_LANGUAGE', raising=False)
    if env is not None:
        monkeypatch.setenv('JOBSEEKER_OUTPUT_LANGUAGE', env)
    config = {'output_language': selected}
    fallback = {'output_language': global_value} if global_value else {}
    assert resolve_language(config, cli=cli, fallback=fallback) == expected


@pytest.mark.parametrize('value', ['', 'en', 'DE', 'de ', [], False])
def test_invalid_language_has_safe_error(monkeypatch, value):
    monkeypatch.delenv('JOBSEEKER_OUTPUT_LANGUAGE', raising=False)
    with pytest.raises(ValueError, match='must be ru or de'):
        resolve_language({'output_language': value})


def test_catalog_translations_and_format_parameters_are_complete():
    formatter = string.Formatter()
    for source, target in catalog().items():
        assert target and not re.search('[А-Яа-яЁё]', target), source
        source_fields = [(name, spec, conv) for _, name, spec, conv in formatter.parse(source) if name is not None]
        target_fields = [(name, spec, conv) for _, name, spec, conv in formatter.parse(target) if name is not None]
        assert sorted(source_fields) == sorted(target_fields), source


def test_every_marked_russian_template_has_translation():
    files = ['intelligence_report', 'vacancy_documents', 'vacancy_readable',
             'vacancy_text_documents', 'cover_letters', 'rtf_styles', 'episode_report', 'skill_report']
    paths = [Path('app/matching') / (f + '.py') for f in files]
    paths += [Path('app/jobcenter/adaptive_search.py'), Path('app/jobcenter/search_provenance.py')]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    text = node.args[0].value
                    if re.search('[А-Яа-яЁё]', text):
                        assert text in catalog(), (path, text)


def test_context_is_restored_even_after_error():
    assert current_language() == 'ru'
    with pytest.raises(RuntimeError):
        with language_scope('de'):
            assert tr('Оценка') == 'Bewertung'
            raise RuntimeError
    assert current_language() == 'ru'
    assert tr('Оценка') == 'Оценка'


def test_new_and_old_warnings_can_be_rendered_in_another_language():
    source = 'Достигнут предел API {p0} (найдено {p1}); полнота прямой выдачи не гарантирована.'
    with language_scope('ru'):
        record = {'code': 'result_window_reached', **message_record(tr(source, p0=10000, p1=12000))}
    with language_scope('de'):
        translated = localize_message(record)
        assert 'API-Grenze' in translated['message']
        assert '12000' in translated['message']
        legacy = localize_message({'code': record['code'], 'message': record['message']})
        assert legacy['message'] == translated['message']
    assert translated['params'] == {'p0': 10000, 'p1': 12000}
    assert record['message'].startswith('Достигнут')


def test_homeoffice_warning_switches_language_without_changing_search():
    search = parse_search_url('https://www.arbeitsagentur.de/jobsuche/suche?wo=Bayern&homeoffice=prozentual_50')
    with language_scope('ru'):
        ru = AdaptiveSearch(search, {}, 10000).preview()
    with language_scope('de'):
        de = AdaptiveSearch(search, {}, 10000).preview()
    assert ru['search_id'] == de['search_id']
    assert ru['params'] == de['params']
    assert ru['warnings'][0]['message_id'] == de['warnings'][0]['message_id']
    assert not re.search('[А-Яа-яЁё]', de['warnings'][0]['message'])


def test_owned_explanations_translate_but_source_quotes_never_do():
    source = {'ref': 'test', 'score': 70, 'decision_input_hash': 'same-hash',
              'source_data': {'description': 'Не указано'},
              'matched_skills': [{'label': 'Python', 'profile_evidence': [{'evidence_quote': 'Не указано'}],
                                  'job_evidence': [{'quote': 'Не указано', 'reason': 'No explicit modality cue in the same sentence or list item'}]}],
              'related_skills': [{'relation_explanation': 'Группа компетенций DKZ'}]}
    original = deepcopy(source)
    with language_scope('de'):
        exported = localize_result(source)
    assert source == original
    assert exported['source_data'] == original['source_data']
    evidence = exported['matched_skills'][0]
    assert evidence['profile_evidence'][0]['evidence_quote'] == 'Не указано'
    assert evidence['job_evidence'][0]['quote'] == 'Не указано'
    assert not re.search('[А-Яа-яЁё]', exported['related_skills'][0]['relation_explanation'])
    assert exported['score'] == 70 and exported['decision_input_hash'] == 'same-hash'


def test_language_does_not_change_ranking_or_matcher_input_hash():
    from test_job_intelligence import _matcher, _settings, _job

    results = []
    for language in ('ru', 'de'):
        matcher, _ = _matcher(settings={**_settings(), 'output_language': language})
        with language_scope(language):
            results.append(matcher.rank([_job('one', 'Linux erforderlich.'),
                                         _job('two', 'Python erforderlich.')]))
    assert results[0] == results[1]


def test_german_ready_dossier_is_complete_reusable_and_keeps_russian_export(tmp_path):
    from test_vacancy_documents import _report, FakeRenderer, NOW
    from app.matching.vacancy_documents import write_documents

    report = _report()
    job = report['results'][0]
    job['description'] = 'Python erforderlich. Ende.'
    job['source_data']['jobdetails']['stellenangebotsBeschreibung'] = job['description']
    job['matched_skills'][0]['profile_evidence'][0]['evidence_quote'] = 'Mit Python entwickelt.'
    report['configuration'] = {'output_language': 'ru'}
    ru = write_documents(report, tmp_path / 'text', now=NOW, text_only=True)
    ru_before = {p: p.read_bytes() for p in ru['directory'].rglob('*') if p.is_file()}
    report['configuration']['output_language'] = 'de'
    de = write_documents(report, tmp_path / 'text', now=NOW, text_only=True)
    pdf = write_documents(report, tmp_path / 'documents', now=NOW, renderer=FakeRenderer())
    for export in (de, pdf):
        assert export['directory'].parent.name == 'de'
        for path in export['directory'].rglob('*'):
            if path.is_file():
                assert not re.search('[А-Яа-яЁё]', path.name + path.read_text()), path
    letter = json.loads((de['directory'] / '01/Anschreiben.json').read_text())
    assert letter['status'] == 'ready' and letter['output_language'] == 'de'
    assert 'Mit freundlichen Grüßen' in letter['body_markdown']
    assert (de['directory'] / '01/Empfehlungen.md').is_file()
    assert write_documents(report, tmp_path / 'text', now=NOW, text_only=True)['reused']
    assert all(path.read_bytes() == content for path, content in ru_before.items())
    assert report['results'][0]['score'] == 80


def test_real_german_pdf_rtf_use_report_layout_and_german_footer(tmp_path):
    import shutil
    import subprocess
    from test_vacancy_documents import _report, NOW
    from app.matching.vacancy_documents import DocumentRenderer, overview_html
    from app.matching.intelligence_report import _report_json
    from app.matching.rtf_styles import _escape

    pytest.importorskip('weasyprint')
    if not shutil.which('pandoc') or not shutil.which('pdftotext'):
        pytest.skip('Pandoc/pdftotext not installed')
    report = _report()
    # Overview contains no original body/quote, so it must be fully German.
    report['configuration'] = {'output_language': 'de'}
    with language_scope('de'):
        content = overview_html(_report_json(report), NOW.isoformat())
    DocumentRenderer().render(content, tmp_path / 'übersicht')
    rtf = (tmp_path / 'übersicht.rtf').read_text()
    assert r'\deflang1031' in rtf and r'\footer' in rtf
    assert r'\margl1020\margr1020' in rtf  # Reports keep report margins in German.
    assert _escape('LOKALE BEWERTUNG') in rtf
    for command in (
        ['pdftotext', str(tmp_path / 'übersicht.pdf'), '-'],
        ['pandoc', '-f', 'rtf', '-t', 'plain', str(tmp_path / 'übersicht.rtf')],
    ):
        text = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout
        assert 'Python Entwickler' in text
        assert not re.search('[А-Яа-яЁё]', text)
