"""Publish complete, private vacancy dossiers using WeasyPrint and Pandoc.

The two renderers receive the same escaped HTML. Source data comes from the
ranking scan, never a second Mongo read. A manifest makes completed exports
verifiable and reusable without overwriting documents edited by the user.
"""
from __future__ import annotations

import hashlib
import html
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from app.core.i18n import (
    catalog_hash, current_language, localized_directory, report_language, tr, translated,
)
from app.core.logging import get_logger
from app.matching.intelligence_data import digest
from app.matching.intelligence_report import MAX_RESULTS, _reason, _report_json, _safe_url, _STATUS_RU
from app.matching.output_localization import localize_result

LOGGER = get_logger('vacancy_documents')
VERSION = 'vacancy-documents-v9'
EXPORTER_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
BERLIN = ZoneInfo('Europe/Berlin')
DEFAULTS = {
    'enabled': False, 'json_md_enabled': False, 'limit': 50, 'output_dir': None,
    'conversion_timeout_seconds': 60,
}
_SECRET = re.compile(
    r'api.?key|token|secret|password|authorization|credential|mongodb_uri|raw.?profile|full.?cv', re.I)
_LABELS = {
    'stellenangebote': 'Сведения списка вакансий', 'jobdetails': 'Подробные сведения',
    'referenznummer': 'Номер вакансии', 'titel': 'Название',
    'stellenangebotsTitel': 'Название', 'stellenangebotsBeschreibung': 'Описание',
    'arbeitgeber': 'Работодатель', 'arbeitsorte': 'Места работы', 'arbeitsort': 'Место работы',
    'arbeitszeit': 'Рабочее время', 'verguetung': 'Оплата', 'befristung': 'Срок договора',
    'eintrittsdatum': 'Дата начала', 'externeUrl': 'Внешняя ссылка',
    'aenderungsdatum': 'Изменено в источнике', 'modifikationsTimestamp': 'Изменён список',
    'kontaktdaten': 'Контакты', 'ansprechpartner': 'Контактное лицо',
}
_SCORE_LABELS = {
    'role': 'Совпадение роли', 'skills': 'Подтверждения навыков в профиле',
    'semantic': 'Смысловая близость названия и профессии',
    'required_coverage': 'Покрытие распознанных обязательных требований',
    'gap_penalty': 'Штраф за требования без подтверждения',
}
_CONSTRAINT_LABELS = {
    'met': 'совпадает', 'conflict': 'противоречие', 'unknown': 'нужно уточнить',
}
_CONSTRAINT_NAMES = {
    'on_call': 'Дежурства', 'shift_work': 'Сменная работа', 'heavy_physical_work': 'Физическая нагрузка',
    'location': 'Место работы', 'german': 'Немецкий язык', 'german_cefr': 'Уровень немецкого',
    'driving_license_b': 'Водительские права B', 'forklift_license': 'Права на погрузчик',
    'education': 'Образование', 'qualification': 'Квалификация',
    'language': 'Язык', 'license:B': 'Водительские права B', 'license:C': 'Водительские права C',
    'license:CE': 'Водительские права CE', 'license:forklift': 'Права на погрузчик',
    'certificate': 'Сертификат', 'experience': 'Опыт работы',
}
_ROLE_LABELS = {
    'direct': 'Совпадает целевая роль', 'adjacent_candidate': 'Есть смежные навыки',
    'weak': 'Слабое совпадение',
}
CSS = """
@page { size: A4; margin: 20mm 18mm 20mm;
  @bottom-left { content: 'JOBSEEKER / ЛОКАЛЬНАЯ ОЦЕНКА'; color: #607080; font-size: 8pt; }
  @bottom-right { content: counter(page) ' / ' counter(pages); font-size: 8pt; color: #607080; }
}
body { font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; line-height: 1.5; color: #233344; }
h1 { font-size: 23pt; line-height: 1.2; color: #142e48; margin: 0 0 7mm; }
h2 { font-size: 14pt; color: #087e83; border-bottom: 1pt solid #c8dddd;
     margin-top: 8mm; padding-bottom: 2mm; break-after: avoid; }
h3 { font-size: 11pt; color: #142e48; break-after: avoid; }
p { margin: 2mm 0; }
a { color: #087e83; text-decoration: none; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 4mm 0; font-size: 9pt; table-layout: fixed; }
th, td { padding: 2.5mm; border-bottom: 0.5pt solid #dce5eb; vertical-align: top; overflow-wrap: anywhere; }
th { background: #edf4f7; text-align: left; }
blockquote { border-left: 2pt solid #c8dddd; margin: 3mm 0 3mm 3mm; padding-left: 3mm; }
.eyebrow { color: #087e83; font-size: 9pt; font-weight: bold; letter-spacing: 1pt; }
.verdict { background: #edf4f7; padding: 4mm; margin: 5mm 0; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; }
.muted { color: #607080; font-size: 9pt; }
.source-label { font-size: 9pt; color: #607080; break-after: avoid; overflow-wrap: anywhere; }
.source { margin: 3mm 0; }
.overview th:nth-child(1) { width: 8%; }
.overview th:nth-child(2) { width: 27%; }
.overview th:nth-child(3) { width: 20%; }
.overview th:nth-child(4) { width: 14%; }
.overview th:nth-child(5) { width: 9%; }
.overview th:nth-child(6) { width: 22%; }
"""


def export_settings(settings: dict, *, json_md_override: str | None = None) -> dict:
    """Resolve optional JSON settings; CLI overrides are applied by the caller."""
    value = settings.get('document_export', {})
    if not isinstance(value, dict):
        raise ValueError('document_export must be an object')
    result = {**DEFAULTS, **value}
    if json_md_override is not None and json_md_override.strip():
        override = json_md_override.strip().lower()
        if override not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValueError('JOB_INTELLIGENCE_EXPORT_JSON_MD must be true or false')
        result['json_md_enabled'] = override in {'true', '1', 'yes'}
    if not isinstance(result['json_md_enabled'], bool):
        raise ValueError('document_export.json_md_enabled must be boolean')
    if not isinstance(result['enabled'], bool):
        raise ValueError('document_export.enabled must be boolean')
    if type(result['limit']) is not int or not 1 <= result['limit'] <= MAX_RESULTS:
        raise ValueError(f'document_export.limit must be in 1..{MAX_RESULTS}')
    timeout = result['conversion_timeout_seconds']
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('document_export.conversion_timeout_seconds must be in 1..600')
    directory = result['output_dir']
    if directory is not None and (not isinstance(directory, str) or not directory.strip()):
        raise ValueError('document_export.output_dir must be null or a nonempty path')
    from app.matching.cover_letters import DEFAULT_DETAILS_FILE
    letter = value.get('cover_letter', {})
    if not isinstance(letter, dict):
        raise ValueError('document_export.cover_letter must be an object')
    letter = {'enabled': True, 'max_skills': 3, 'details_file': DEFAULT_DETAILS_FILE, **letter}
    if not isinstance(letter['enabled'], bool):
        raise ValueError('cover_letter.enabled must be boolean')
    if type(letter['max_skills']) is not int or not 1 <= letter['max_skills'] <= 5:
        raise ValueError('cover_letter.max_skills must be in 1..5')
    if not isinstance(letter['details_file'], str) or not letter['details_file'].strip():
        raise ValueError('cover_letter.details_file must be a nonempty path')
    result['cover_letter'] = letter
    return result


class DocumentRenderer:
    """Fail before scanning if dependencies are missing; bound each conversion."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.pandoc = shutil.which('pandoc')
        if not self.pandoc:
            raise ValueError('PDF/RTF export requires Pandoc; install pandoc and retry')
        try:
            import weasyprint
            self.identity = {'weasyprint': weasyprint.__version__}
        except (ImportError, OSError):
            raise ValueError(
                'PDF export requires WeasyPrint/Pango; install requirements.txt and system Pango') from None
        completed = self._run([self.pandoc, '--version'])
        self.identity['pandoc'] = completed.stdout.decode('utf-8').splitlines()[0]
        # Record the layout dependencies as well as the application template.
        self.identity['pydyf'] = importlib.metadata.version('pydyf')
        self.identity['rtf_styles_sha256'] = _sha(Path(__file__).with_name('rtf_styles.py'))

    def _run(self, command: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  check=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ValueError('Document conversion timed out; no dossier folder was published') from None
        except (subprocess.CalledProcessError, OSError):
            # stderr may quote private source contents; never put it in logs.
            raise ValueError('Document conversion failed; no dossier folder was published') from None

    def render(self, content: str, prefix: Path, *, formats: tuple[str, ...] = ('pdf', 'rtf')) -> None:
        if not formats or any(fmt not in {'pdf', 'rtf'} for fmt in formats):
            raise ValueError('Document formats must be pdf and/or rtf')
        prefix = prefix.absolute()
        source = prefix.with_suffix('.html')
        source.write_text(content, encoding='utf-8')
        source.chmod(0o600)
        try:
            if 'pdf' in formats:
                self._run([sys.executable, '-m', 'weasyprint', '--allowed-protocols', 'data',
                           str(source), str(prefix.with_suffix('.pdf'))])
            if 'rtf' in formats:
                from app.matching.rtf_styles import render_rtf
                styled = render_rtf(content, self.pandoc, self._run)
                prefix.with_suffix('.rtf').write_text(styled, encoding='ascii')
            for fmt in formats:
                signature = b'%PDF-' if fmt == 'pdf' else b'{\\rtf'
                path = prefix.with_suffix('.' + fmt)
                with path.open('rb') as stream:
                    if not stream.read(8).startswith(signature):
                        raise ValueError('Document converter produced an invalid file')
                path.chmod(0o600)
        finally:
            source.unlink(missing_ok=True)


class _TextParser(HTMLParser):
    """Preserve source paragraphs and lists without executing source markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if not self.hidden and tag in {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n' + ('• ' if tag == 'li' else ''))

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def source_text(value) -> str:
    """Keep all plain text; clean structural HTML only when it is present."""
    text = '' if value is None else str(value)
    if re.search(r'</?(?:p|div|br|li|ul|ol|span|h[1-6]|table|td|th|a|strong|em|b|i|script|style)\b', text, re.I):
        parser = _TextParser()
        parser.feed(text)
        return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines()).strip()
    return html.unescape(text)


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if not _SECRET.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _e(value) -> str:
    return html.escape('' if value is None else str(value), quote=True)


def _p(value, cls: str = '') -> str:
    text = _lines(value) if cls == 'text' else _e(value)
    return f'<p class="{cls}">{text}</p>'


def _lines(value) -> str:
    # <br> сохраняется и Pandoc; одного CSS white-space для RTF недостаточно.
    return _e(value).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def _condition_value(value) -> str:
    if isinstance(value, bool):
        return tr('Да') if value else tr('Нет')
    if isinstance(value, dict):
        labels = {'cefr': tr('Уровень CEFR'), 'description': tr('Описание')}
        return '; '.join(f'{labels.get(k, k)}: {_condition_value(v)}' for k, v in value.items())
    if isinstance(value, list):
        return ', '.join(_condition_value(item) for item in value) or tr('Не указано')
    return str(value) if value is not None else tr('Не указано')


def _display_date(value) -> str:
    if not value:
        return tr('Не указано')
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if parsed.tzinfo:
        parsed = parsed.astimezone(BERLIN)
    return parsed.strftime('%d.%m.%Y, %H:%M') + (f' {parsed.tzname()}' if parsed.tzinfo else '')


def _display_location(value: str) -> str:
    parts = value.split('|')
    if len(parts) != 4:
        return value or tr('Не указано')
    country, region, city, postal_code = parts
    return ', '.join(part for part in (' '.join(filter(None, (postal_code, city))),
                                      region.title(), country.title()) if part) or tr('Не указано')


def _display_schedule(value: str) -> str:
    if not value.startswith('vollzeit='):
        return value or tr('Не указано')
    fields = dict(part.strip().split('=', 1) for part in value.split(';') if '=' in part)
    labels = [('vollzeit', tr('Полная занятость')), ('teilzeit', tr('Частичная занятость')),
              ('minijob', 'Minijob')]
    parts = [label for key, label in labels if fields.get(key) == 'true']
    contract = fields.get('vertragsdauer', '')
    if contract:
        parts.append({'UNBEFRISTET': tr(
            'Бессрочный договор',
        ), 'BEFRISTET': tr(
            'Срочный договор',
        )}.get(contract, contract))
    return '; '.join(parts) or tr('График не указан')


def _table(rows: list[tuple], headings: tuple = ()) -> str:
    head = '<thead><tr>' + ''.join(f'<th>{_e(x)}</th>' for x in headings) + '</tr></thead>' if headings else ''
    body = ''.join('<tr>' + ''.join(f'<td>{_e(x)}</td>' for x in row) + '</tr>' for row in rows)
    return f'<table>{head}<tbody>{body}</tbody></table>'


def _page(title: str, body: str) -> str:
    css = CSS.replace('JOBSEEKER / ЛОКАЛЬНАЯ ОЦЕНКА', tr('JOBSEEKER / ЛОКАЛЬНАЯ ОЦЕНКА'))
    return (f'<!doctype html><html lang="{current_language()}"><head><meta charset="utf-8">'
            f'<title>{_e(title)}</title><style>{css}</style></head><body>{body}</body></html>')


def _source_fields(value, path: tuple = ()) -> str:
    if isinstance(value, dict) and value:
        return ''.join(_source_fields(v, (*path, str(k))) for k, v in value.items())
    if isinstance(value, list) and value:
        return ''.join(_source_fields(v, (*path, f'[{i}]')) for i, v in enumerate(value, 1))
    label = ' / '.join(translated(_LABELS).get(k, k) for k in path)
    original = '.'.join(path)
    if value is None:
        text = tr('Не указано')
    elif isinstance(value, (dict, list, bool)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = source_text(value)
    return (f'<div class="source"><p class="source-label"><strong>{_e(label)}</strong>'
            f'<br>{_e(original)}</p><div class="text">{_lines(text)}</div></div>')


def _skills(job: dict, field: str, heading: str, empty: str) -> str:
    body = '<h2>' + heading + '</h2>'
    if not job.get(field):
        return body + _p(empty)
    for item in job[field]:
        body += '<h3>' + _e(item.get('label', tr('Требование'))) + '</h3>'
        if item.get('relation_path'):
            body += _p(tr('Связь с «') + item['target_concept']['label'] + '»: '
                       + item['relation_explanation'] + tr('. Требуемый навык не считается подтверждённым.'))
            for proof in item['relation_path']:
                body += _p(tr('Источник связи: ') + proof['source'] + '; ' + proof['type']
                           + tr('; версия ') + proof['version'], 'muted')
        for evidence in item.get('job_evidence', []):
            quote = evidence.get('context_quote') or evidence.get('quote')
            body += '<blockquote>' + _p(tr('Вакансия: ') + str(quote or '')) + '</blockquote>'
        for evidence in item.get('profile_evidence', []):
            body += _p(tr('Профиль: ') + str(evidence.get('evidence_quote') or evidence.get('label') or ''))
            if evidence.get('source_path'):
                body += _p(tr('Источник факта: ') + str(evidence['source_path']), 'muted')
            if evidence.get('last_used'):
                body += _p(tr(
                    'Период: ',
                ) + str(evidence['last_used']) + tr(
                    '. Текущая готовность не проверена.',
                ), 'muted')
    return body


def vacancy_html(job: dict, rank: int, evaluated_at: str, *, semantic_enabled: bool = True) -> str:
    """Render one full dossier, including every non-secret source field."""
    title = job['title']
    body = _p(tr('ВАКАНСИЯ {p0:02d} / ОЦЕНКА {p1}', p0=rank, p1=_display_date(evaluated_at)), 'eyebrow')
    body += f'<h1>{_e(title)}</h1>'
    status = tr(_STATUS_RU.get(job['status'], job['status']))
    body += '<div class="verdict">' + _p(tr(
        'Место {p0:02d} · Балл {p1:.2f} / 100 · {p2}',
        p0=rank,
        p1=job['score'],
        p2=status,
    ))
    body += _p(translated(_ROLE_LABELS).get(job.get('role_type'), tr('Роль требует проверки'))) + '</div>'
    body += _p(tr(
        'Балл служит для сравнения вакансий и не является вероятностью найма. Отсутствие '
        'подтверждения в профиле не доказывает отсутствие навыка.',
    ), 'muted')
    body += _table([
        (tr('Работодатель'), job.get('employer') or tr('Не указано')),
        (tr('Профессия'), job.get('role') or tr('Не указано')),
        (tr('Место работы'), _display_location(job.get('location', ''))),
        (tr('График / договор'), _display_schedule(job.get('schedule', ''))),
        (tr('Номер вакансии'), job['ref']),
        (tr('Дата изменения списка'), _display_date(job.get('source_modified_at'))),
        (tr('Дата изменения деталей'), _display_date(job.get('details_modified_at'))),
    ])
    url = _safe_url(job.get('url'))
    if url:
        body += tr('<p>Источник: <a href="{p0}">{p1}</a></p>', p0=_e(url), p1=_e(url))
    if job.get('title_source') == 'occupation_fallback':
        body += _p(tr('Название отсутствует в источнике; показано название профессии.'))
    body += tr('<h2>Как получена оценка</h2>')
    signals = []
    for key, label in translated(_SCORE_LABELS).items():
        value = job.get('scores', {}).get(key)
        text = tr('Не определено') if value is None else f'{value:.3f}'
        if key == 'semantic' and not semantic_enabled:
            text = tr('Отключена')
        signals.append((label, text))
    body += _table(signals, (tr('Сигнал'), tr('Значение')))
    semantic_note = (tr('Эмбеддинги сопоставляют название и профессию. ') if semantic_enabled else
                     tr('Эмбеддинги отключены; используются лексическое совпадение и правила. '))
    body += _p(semantic_note + tr(
        'Требования полного текста проверяются словарём и правилами. Сначала идут вакансии без '
        'выявленного конфликта условий, затем по убыванию балла.',
    ), 'muted')
    body += _skills(job, 'matched_skills', tr('Почему вакансия может подойти'),
                    tr('Подтверждённых совпадений навыков не найдено.'))
    if job.get('related_skills'):
        body += _skills(job, 'related_skills', tr('Дополнительный смежный опыт для письма'), '')
    if job.get('relation_context_skills'):
        body += _skills(job, 'relation_context_skills', tr('Связи для проверки, не включённые в письмо'), '')
    body += _skills(job, 'missing_required', tr('Обязательные требования без подтверждения в профиле'),
                    tr('Среди распознанных требований таких пробелов не найдено.'))
    body += _skills(job, 'unconfirmed_skills', tr('Другие навыки для уточнения'),
                    tr('Дополнительных распознанных навыков для уточнения нет.'))
    body += tr('<h2>Условия и вопросы для проверки</h2>')
    if job.get('requirement_modality_unknown'):
        body += _p(tr('Для части навыков не установлено, обязательны ли они. Требуется уточнение.'))
    for item in job.get('constraints', []):
        name = translated(_CONSTRAINT_NAMES).get(item.get('kind'), item.get('kind'))
        body += _p(f"{name}: {translated(_CONSTRAINT_LABELS).get(item.get('status'), item.get('status'))}")
        for key, label in (('quote', tr(
            'Вакансия',
        )), ('required', tr(
            'Требование',
        )), ('profile_value', tr(
            'Профиль',
        ))):
            if item.get(key) is not None:
                body += _p(f'{label}: {_condition_value(item[key])}', 'text')
    for item in job.get('unknowns', []):
        body += _p(_reason(str(item.get('reason', tr('Нужно проверить')))))
        if item.get('quote'):
            body += '<blockquote>' + _p(item['quote'], 'text') + '</blockquote>'
    if not job.get('constraints') and not job.get('unknowns'):
        body += _p(tr('Явных противоречий среди проверенных условий не найдено.'))
    source = job.get('source_data', {})
    original = None
    if isinstance(source, dict):
        original = (source.get('jobdetails') or {}).get('stellenangebotsBeschreibung')
    if original is None and isinstance(source, dict):
        original = source.get('description')
    description = source_text(original if original is not None else job.get('description'))
    body += tr('<h2>Полный текст вакансии</h2>')
    if description:
        body += f'<div class="text">{_lines(description)}</div>'
    else:
        body += _p(tr('Полный текст в полученных данных отсутствует. Требования проверить полностью нельзя.'))
    body += tr('<h2>Все доступные сведения источника</h2>')
    body += _p(tr(
        'Поля сохранены из того же чтения, на котором рассчитана оценка. Пустые поля не дополнены '
        'предположениями. Служебные ключи секретов исключены.',
    ), 'muted')
    body += _source_fields(_clean(source)) if source else _p(tr('Исходные поля не переданы.'))
    body += tr(
        '<h2>Проверяемость</h2>',
    ) + _p(tr(
        'Вход источника: ',
    ) + str(job.get('source_input_hash', '')), 'text')
    body += _p(tr('Вход оценки: ') + str(job.get('decision_input_hash', '')), 'text')
    return _page(title, body)


def overview_html(report: dict, evaluated_at: str) -> str:
    from app.jobcenter.search_provenance import coverage_html
    body = _p(tr('JOBSEEKER / ОБЗОР'), 'eyebrow') + tr('<h1>Вакансии для рассмотрения</h1>')
    body += _p(tr('Дата оценки: ') + _display_date(evaluated_at))
    body += _p(tr(
        'Оценено: {p0}. Документов по вакансиям: {p1}.',
        p0=report.get('metrics', {}).get('candidates_evaluated', 0),
        p1=len(report['results']),
    ))
    scope = report.get('scope', {})
    body += coverage_html(scope)
    body += _p(tr(
        'Источник: {p0}; охват: {p1}; регион: {p2}.',
        p0=scope.get('source', ''),
        p1=scope.get('vacancy_scope', ''),
        p2=scope.get('region') or tr('не задан / сохранённый файл'),
    ))
    body += _p(tr(
        'Балл — относительный показатель, не вероятность найма. Вакансии с выявленным конфликтом '
        'условий помещены после остальных. Текущая доступность не проверялась. Данные списка и '
        'деталей могут обновляться параллельно.',
    ), 'muted')
    rows = [(f'{rank:02d}', j['title'], j.get('employer') or '—', _display_location(j.get('location', '')),
             f"{j['score']:.2f}", tr(_STATUS_RU.get(j['status'], j['status'])))
            for rank, j in enumerate(report['results'], 1)]
    body += _table(rows, ('№', tr(
        'Вакансия',
    ), tr(
        'Работодатель',
    ), tr(
        'Место',
    ), tr(
        'Балл',
    ), tr(
        'Статус',
    ))).replace(
        '<table>', '<table class="overview">', 1)
    body += tr('<h2>Как читать документы</h2>')
    body += _p(tr(
        'Каждому номеру соответствует папка 01, 02 и далее. В ней «Оценка.pdf» и «Оценка.rtf» '
        'содержат сопоставление с профилем, а «Вакансия.pdf» — полное объявление, условия, оплату и'
        ' контакты. При включённом Anschreiben немецкое письмо добавляется в PDF/RTF. '
        '«Рекомендации.pdf» предназначен только кандидату и содержит замечания к заявке. Если нет '
        'организации, имени и фамилии кандидата или позиции, письмо не создаётся; причина указана в'
        ' рекомендациях. Переносы страниц в редакторах RTF могут отличаться от PDF.',
    ))
    body += tr('<h2>Источники справочных данных</h2>')
    for ref in report.get('references', []):
        body += _p(f"{ref.get('source', '')}: {ref.get('version', '')}")
        body += _p(tr(ref.get('attribution') or ('This service uses the ESCO classification of the European Commission.'
                                               if 'esco' in str(ref.get('source', '')).lower() else '')), 'muted')
    return _page(tr('Обзор вакансий'), body)


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def _private_directory(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        current.mkdir(mode=0o700, exist_ok=True)


def _existing(directory: Path, input_hash: str) -> bool:
    if not directory.exists():
        return False
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        valid = (not directory.is_symlink() and manifest['input_hash'] == input_hash
                 and manifest['files'] and all(
                     _sha(_manifest_file(directory, name)) == checksum
                     for name, checksum in manifest['files'].items()))
        if valid:
            return True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ValueError('Existing dossier folder was changed or is incomplete; '
                     'choose another --documents-dir to preserve edits')


def _manifest_file(directory: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name or relative.as_posix() != name:
        raise ValueError('Invalid manifest path')
    current = directory
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Manifest file must not be a symlink')
    return current


def _daily_state(directory: Path) -> tuple[str, str] | None:
    """Recognize a verified daily export or the previous single-folder layout."""
    if not directory.exists():
        return None
    manifest_path = directory / 'manifest.json'
    layout = 'daily'
    if not manifest_path.is_file():
        children = list(directory.iterdir())
        if len(children) != 1 or not children[0].is_dir() or not children[0].name.startswith('оценка-'):
            raise ValueError('Daily folder contains unrecognized files; choose another --documents-dir')
        manifest_path = children[0] / 'manifest.json'
        layout = 'legacy'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        _existing(manifest_path.parent, manifest['input_hash'])
        return layout, _sha(manifest_path)
    except (OSError, KeyError, TypeError):
        raise ValueError('Daily folder is incomplete; choose another --documents-dir') from None


def _publish_daily(temporary: Path, directory: Path, prior_state: tuple | None) -> None:
    """Keep prior completed exports in history; roll back a failed publication."""
    if _daily_state(directory) != prior_state:
        raise ValueError('Daily export changed during rendering; retry without overwriting it')
    archive = None
    if prior_state:
        history = directory.parent / '.history' / directory.name
        _private_directory(history)
        archive = Path(tempfile.mkdtemp(prefix='export-', dir=history))
        archive.rmdir()
        os.rename(directory, archive)
    try:
        os.rename(temporary, directory)
    except OSError:
        if archive is not None and not directory.exists():
            os.rename(archive, directory)
        raise


@report_language
def write_documents(report: dict, root: Path, *, renderer: DocumentRenderer | None = None,
                    now: datetime | None = None, application: dict | None = None,
                    text_only: bool = False) -> dict:
    """Atomically publish the ranked top; reuse a verified identical daily export."""
    if not 1 <= len(report.get('results', [])) <= MAX_RESULTS:
        raise ValueError(f'Document export requires 1..{MAX_RESULTS} ranked vacancies')
    if not text_only:
        renderer = renderer or DocumentRenderer()
    from app.matching.vacancy_text_documents import write_text_overview, write_text_position
    from app.matching.vacancy_readable import readable_vacancy_html
    from app.matching.cover_letters import (
        build_letter, letter_html, normalize_application_details, recommendations_html,
    )
    letter_settings = export_settings(report.get('configuration', {}))['cover_letter']
    stamp = (now or datetime.now(BERLIN)).astimezone(BERLIN)
    captured = _clean({'summary': _report_json(report),
                      'vacancies': [localize_result(item) for item in report['results']]})
    if letter_settings['enabled']:
        details = application if application is not None else report.get('application', {})
        captured['application'] = normalize_application_details(details)
    summary = captured['summary']
    # Wall time/cache hits/capture time change on every identical rerun.
    stable_summary = {k: v for k, v in summary.items() if k not in {'metrics', 'scope'}}
    scope = {k: v for k, v in summary['scope'].items()
             if k not in {'captured_at', 'all_vacancies_csv'}}
    readable_hash = _sha(Path(__file__).with_name('vacancy_readable.py'))
    letter_hash = _sha(Path(__file__).with_name('cover_letters.py')) if letter_settings['enabled'] else None
    renderer_identity = ({'formats': ['json', 'markdown'],
                          'text_exporter_sha256': _sha(Path(__file__).with_name('vacancy_text_documents.py'))}
                         if text_only else renderer.identity)
    coverage_hash = _sha(Path(__file__).parents[1] / 'jobcenter' / 'search_provenance.py')
    input_hash = digest([VERSION, EXPORTER_CODE_HASH, readable_hash, letter_hash, coverage_hash,
                         catalog_hash(), current_language(), CSS, renderer_identity,
                         stable_summary, scope, captured['vacancies'], letter_settings,
                         captured.get('application')])
    parent = localized_directory(root)
    directory = parent / stamp.date().isoformat()
    prior_state = _daily_state(directory)
    if prior_state and prior_state[0] == 'daily' and json.loads(
            (directory / 'manifest.json').read_text(encoding='utf-8'))['input_hash'] == input_hash:
        LOGGER.info('Dossiers reused: vacancies=%s conversions=0', len(report['results']))
        return {'directory': directory, 'manifest': directory / 'manifest.json', 'reused': True}
    _private_directory(parent)
    temporary = Path(tempfile.mkdtemp(prefix='.documents-', dir=parent))
    try:
        evaluated_at = stamp.isoformat(timespec='seconds')
        if text_only:
            write_text_overview(temporary, summary)
        else:
            renderer.render(overview_html({**summary, 'results': captured['vacancies']}, evaluated_at),
                            temporary / tr('00_Список'))
        letters = []
        for rank, job in enumerate(captured['vacancies'], 1):
            position = temporary / f'{rank:02d}'
            position.mkdir(mode=0o700)
            letter = None
            if not text_only:
                from app.jobcenter.search_provenance import coverage_html
                content = vacancy_html(job, rank, evaluated_at, semantic_enabled=bool(report.get('model')))
                content = content.replace('</body>', coverage_html(summary.get('scope', {})) + '</body>')
                renderer.render(content, position / tr('Оценка'))
                renderer.render(readable_vacancy_html(job, rank, evaluated_at), position / tr('Вакансия'),
                                formats=('pdf',))
            if letter_settings['enabled']:
                letter = build_letter(job, captured['application'], evaluated_at,
                                      max_skills=letter_settings['max_skills'])
                if not text_only:
                    if letter['status'] == 'ready':
                        renderer.render(letter_html(letter), position / 'Anschreiben')
                    renderer.render(recommendations_html(letter), position / tr(
                        'Рекомендации',
                    ), formats=('pdf',))
                letters.append({'rank': rank, **letter})
            if text_only:
                write_text_position(position, job, summary, rank, evaluated_at, letter)
            LOGGER.info('Dossiers rendered: %s/%s', rank, len(report['results']))
        files = {p.relative_to(temporary).as_posix(): _sha(p)
                 for p in sorted(temporary.rglob('*')) if p.is_file()}
        manifest = {'schema_version': 4, 'exporter_version': VERSION, 'input_hash': input_hash,
                    'output_language': current_language(), 'translation_hash': catalog_hash(),
                    'exporter_code_hash': EXPORTER_CODE_HASH,
                    'readable_template_hash': readable_hash,
                    'cover_letter_template_hash': letter_hash, 'cover_letters': letters,
                    'evaluated_at': evaluated_at, 'timezone': 'Europe/Berlin',
                    'renderers': renderer_identity, 'files': files, **captured}
        path = temporary / 'manifest.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        path.chmod(0o600)
        _publish_daily(temporary, directory, prior_state)
        if letters:
            LOGGER.info('Anschreiben: language=de ready=%s skipped=%s recommendations=%s llm_calls=0',
                        sum(item['status'] == 'ready' for item in letters),
                        sum(item['status'] == 'skipped' for item in letters), len(letters))
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {'directory': directory, 'manifest': directory / 'manifest.json', 'reused': False}
