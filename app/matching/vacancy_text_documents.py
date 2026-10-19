"""Write complete JSON/Markdown dossiers without office or PDF converters."""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from app.core.i18n import current_language, tr

from app.matching.cover_letters import letter_html, recommendations_html
from app.matching.intelligence_report import _markdown
from app.matching.vacancy_documents import vacancy_html
from app.matching.vacancy_readable import readable_vacancy_html
from app.jobcenter.search_provenance import coverage_html


class _BodyMarkdown(HTMLParser):
    """Convert our own document body; source text stays escaped and inert."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.in_body = True
        if not self.in_body:
            return
        if tag in {'p', 'div', 'ul', 'ol', 'table', 'tr', 'blockquote'}:
            self.parts.append('\n\n')
        elif tag == 'br':
            self.parts.append('  \n')
        elif tag == 'li':
            self.parts.append('\n- ')
        elif re.fullmatch(r'h[1-6]', tag):
            self.parts.append('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag in {'strong', 'b'}:
            self.parts.append('**')
        elif tag in {'td', 'th'}:
            self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag == 'body':
            self.in_body = False
        if not self.in_body:
            return
        if tag in {'p', 'div', 'ul', 'ol', 'table', 'tr', 'blockquote'} or re.fullmatch(r'h[1-6]', tag):
            self.parts.append('\n\n')
        elif tag in {'strong', 'b'}:
            self.parts.append('**')

    def handle_data(self, data):
        if self.in_body:
            escaped = html.escape(data, quote=False)
            self.parts.append(re.sub(r'([\\`*_{}\[\]()#+!|>~])', r'\\\1', escaped))


def _body_markdown(document: str) -> str:
    parser = _BodyMarkdown()
    parser.feed(document)
    return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip() + '\n'


def _pair(directory: Path, name: str, record: dict, markdown: str) -> None:
    for suffix, content in (
        ('.json', json.dumps(record, ensure_ascii=False, indent=2) + '\n'),
        ('.md', markdown),
    ):
        path = directory / (name + suffix)
        path.write_text(content, encoding='utf-8')
        path.chmod(0o600)


def write_text_overview(directory: Path, summary: dict) -> None:
    """Save the same top ranking used by the other export views."""
    _pair(directory, tr('00_Список'), summary, _markdown(summary))


def write_text_position(directory: Path, job: dict, summary: dict, rank: int,
                        evaluated_at: str, letter: dict | None) -> None:
    """Write four matching JSON/Markdown pairs, including skipped-letter status."""
    metadata = {'schema_version': 1, 'output_language': current_language(), 'rank': rank, 'ref': job['ref'],
                'evaluated_at': evaluated_at, 'timezone': 'Europe/Berlin'}
    vacancy = {**metadata, 'title': job.get('title'), 'description': job.get('description'),
               'normalized': {key: job.get(key) for key in (
                   'ref', 'title', 'role', 'location', 'schedule', 'url', 'details_status',
                   'occupation_refs', 'title_source', 'source_input_hash') if key in job},
               'source_data': job.get('source_data', {})}
    vacancy['normalized']['url'] = summary['results'][rank - 1].get('url')
    vacancy_markdown = _body_markdown(readable_vacancy_html(job, rank, evaluated_at))
    # The reader-friendly view can group/hide internal metadata. Keep every
    # captured non-secret source field available alongside it, without truncation.
    vacancy_markdown += (tr('\n## Полные исходные поля\n\n```json\n')
                         + json.dumps(vacancy['source_data'], ensure_ascii=False, indent=2)
                         + '\n```\n')
    _pair(directory, tr('Вакансия'), vacancy, vacancy_markdown)

    result = summary['results'][rank - 1]
    assessment = {**metadata, 'result': result,
                  **{key: value for key, value in summary.items() if key != 'results'}}
    assessment_markdown = _body_markdown(vacancy_html(
        job, rank, evaluated_at, semantic_enabled=bool(summary.get('model'))))
    assessment_markdown += _body_markdown('<body>' + coverage_html(summary.get('scope', {})) + '</body>')
    _pair(directory, tr('Оценка'), assessment, assessment_markdown)

    if letter is None:
        letter = {'status': 'disabled', 'language': 'de', 'ref': job['ref'],
                  'title': job.get('title', ''), 'critical_missing': [],
                  'recommendations': [{'code': 'cover_letter.disabled',
                                       'message': tr(
                                           'Создание Anschreiben выключено: '
                                           'document_export.cover_letter.enabled=false.',
                                       )}]}
    if letter['status'] == 'ready':
        body = _body_markdown(letter_html(letter))
        recommendations = _body_markdown(recommendations_html(letter))
    else:
        messages = '\n'.join('- ' + item['message'] for item in letter['recommendations'])
        if letter['status'] == 'skipped':
            messages = _body_markdown(recommendations_html(letter))
        body = (tr('# Anschreiben\n\nПисьмо не создано. Статус: ')
                + letter['status'] + '.\n\n' + messages + '\n')
        recommendations = (_body_markdown(recommendations_html(letter))
                           if letter['status'] == 'skipped'
                           else tr('# Рекомендации\n\n') + messages + '\n')
    _pair(directory, 'Anschreiben', {**metadata, **letter, 'body_markdown': body}, body)
    advice = {**metadata, 'letter_status': letter['status'],
              'critical_missing': letter['critical_missing'],
              'recommendations': letter['recommendations']}
    _pair(directory, tr('Рекомендации'), advice, recommendations)
