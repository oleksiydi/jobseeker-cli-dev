"""Private ru/de reports for experimental requirement matching."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path

from app.core.i18n import current_language, localized_prefix, report_language, tr
from app.matching.episode_report import private_write
from app.matching.intelligence_report import _csv_cell, _md
from app.matching.output_localization import localize_scope

NOTICE = ('Экспериментальный поиск: смысловая близость помогает найти факты для проверки. '
          'Баллы не являются вероятностью, подтверждением требования или оценкой уровня навыка. '
          'Основной рейтинг и Anschreiben не изменены.')
BOUNDARY = ('Проверьте конкретную технологию, объём выполненной работы, стаж и актуальность. '
            'Результат проекта не приписывается каждому действию; название технологии не описывает выполненную задачу.')
EXTRACTION_NOTICE = ('Автоматический разбор может пропустить требования. Проверьте полное описание вакансии; '
                     'этот отчёт не устанавливает полноту соответствия.')
STATUSES = {
    'no_candidate': 'Подходящий факт не найден в профиле; это не доказывает отсутствие опыта.',
    'excluded': 'Отрицание или нерелевантное требование: исключено из рейтинга.',
    'input_limit': 'Требование превышает лимит обработки; соответствие неизвестно.',
    'mixed_modality': 'Смешанная обязательность: требуется ручная проверка.',
    'candidates_for_review': 'Найдены кандидаты для ручной проверки.',
    'structured_check': 'Образование, язык или допуск проверяются отдельно; смысловая близость не подтверждает квалификацию.',
}
KINDS = {'action': 'Выполненное действие', 'declared_skill': 'Заявленный навык',
         'technology': 'Технология проекта', 'project_result': 'Результат проекта'}
RELATIONS = {'task_overlap': 'Есть общая группа задач', 'term_overlap': 'Есть общие термины',
             'semantic_candidate': 'Предполагаемая смысловая связь'}


@report_language
def project_report(report: dict) -> dict:
    # Build only presentation metadata; exact source facts remain untouched.
    result = {**report, 'output_language': current_language(), 'notice': tr(NOTICE),
              'extraction_notice': tr(EXTRACTION_NOTICE),
              'scope': localize_scope(report['scope'])}
    result['results'] = []
    for job in report['results']:
        rows = []
        for row in job['requirements']:
            candidates = [{**c, 'kind_label': tr(KINDS[c['kind']]),
                           'relation_label': tr(RELATIONS[c['relation']]), 'boundary': tr(BOUNDARY)}
                          for c in row['candidates']]
            rows.append({**row, 'message': tr(STATUSES[row['status']]), 'candidates': candidates})
        result['results'].append({**job, 'requirements': rows})
    return result


def markdown(report: dict) -> str:
    from app.jobcenter.search_provenance import coverage_rows

    lines = [tr('# Экспериментальное сопоставление требований и опыта'), '', report['notice'], '',
             report['extraction_notice'], '',
             tr('Порог cosine: {threshold}; это некалиброванный фильтр кандидатов.',
                threshold=report['parameters']['min_similarity']), '',
             tr('Проверено вакансий: {count}. В отчёте: {shown}.',
                count=report['metrics']['vacancies'], shown=len(report['results'])), '']
    for label, value in coverage_rows(report['scope']):
        lines += [f'**{_md(label)}:** {_md(value)}', '']
    lines += [tr('| Место | Вакансия | Сигнал близости | Требования с кандидатами |'),
              '| --- | --- | --- | --- |']
    for rank, job in enumerate(report['results'], 1):
        signal = job['semantic_rank_signal']
        lines.append(f"| {rank} | {_md(job['title'])} | {signal if signal is not None else '—'} | "
                     f"{job['candidate_requirements']}/{job['requirements_considered']} |")
    for rank, job in enumerate(report['results'], 1):
        lines += ['', f"## {rank}. {_md(job['title'])} — {_md(job['ref'])}", '']
        if job.get('url'):
            lines += [f"{_md(job['url'])}", '']
        if not job['requirements_total']:
            lines += [tr('Требования не выделены автоматически; проверьте описание вакансии вручную.'), '']
        if not job['extraction_complete']:
            lines += [tr('Достигнут лимит требований; общий сигнал для вакансии не рассчитывается.'), '']
        for constraint in job.get('constraints', []):
            lines += [tr('Структурная проверка: {kind} — {status}; {quote}',
                         kind=constraint['kind'], status=constraint['status'], quote=_md(constraint['quote'])), '']
        for row in job['requirements']:
            req = row['requirement']
            lines += [f"### {_md(req['quote'])}", '',
                      f"`description[{req['text_span'][0]}:{req['text_span'][1]}]`; {req['modality']}",
                      '', row['message'], '']
            for candidate in row['candidates']:
                source = candidate['source_fact']
                lines += [f"- **{candidate['kind_label']} / {candidate['relation_label']}**; "
                          f"cosine={candidate['cosine']}; {', '.join(candidate['retrieval_basis'])}",
                          f"  > {_md(source['value'])}",
                          f"  `{_md(source['source'])}#{_md(source['pointer'])}`", '']
                for key in ('label', 'company', 'timeframe'):
                    if candidate.get(key):
                        lines += [f"  {_md(candidate[key]['value'])}", '']
                lines += ['  ' + candidate['boundary'], '']
                if candidate['unmatched_requirement_terms']:
                    lines += ['  ' + tr(
                        'Термины требования без буквального совпадения в этом факте: {terms}.',
                        terms=', '.join(_md(t) for t in candidate['unmatched_requirement_terms'])), '']
    return '\n'.join(lines) + '\n'


class RankingCsv:
    """Use the existing streaming atomic CSV context with explicit new columns."""

    columns = ('ref', 'title', 'semantic_rank_signal', 'requirements_total', 'requirements_considered',
               'requirements_processed', 'candidate_requirements', 'extraction_complete',
               'requires_review', 'assessment_origin', 'input_hash')

    @classmethod
    def row(cls, job):
        return [_csv_cell(job.get(key)) for key in cls.columns]


@report_language
def write_skill_reports(report: dict, prefix: Path, *, renderer=None) -> dict:
    prefix = localized_prefix(prefix)
    report = project_report(report)
    # Appending suffixes, rather than with_suffix, preserves prefixes with dots.
    paths = {key: prefix.parent / (prefix.name + suffix) for key, suffix in (
        ('skills_json', '.skills.json'), ('skills_markdown', '.skills.md'), ('skills_csv', '.skills.csv'))}
    text = markdown(report)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(RankingCsv.columns)
    writer.writerows(RankingCsv.row(job) for job in report['results'])
    prefix.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.skills-', dir=prefix.parent) as temporary:
        staging = Path(temporary)
        contents = {'skills_json': json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                    'skills_markdown': text, 'skills_csv': stream.getvalue()}
        for key, content in contents.items():
            private_write(staging / paths[key].name, content)
        if renderer is not None:
            from app.matching.vacancy_documents import _e, _lines, _page
            body = []
            for paragraph in text.split('\n\n'):
                if paragraph.startswith('#') and '\n' not in paragraph:
                    title = paragraph.lstrip('# ')
                    level = min(3, len(paragraph) - len(paragraph.lstrip('#')))
                    body.append(f'<h{level}>' + _e(title) + f'</h{level}>')
                else:
                    body.append('<p class="text">' + _lines(paragraph) + '</p>')
            content = _page(tr('Экспериментальное сопоставление требований и опыта'), ''.join(body))
            document = staging / (prefix.name + '.skills.document')
            renderer.render(content, document)
            paths.update(skills_pdf=prefix.parent / document.with_suffix('.pdf').name,
                         skills_rtf=prefix.parent / document.with_suffix('.rtf').name)
        # A conversion failure leaves all prior complete report files intact.
        for path in paths.values():
            os.replace(staging / path.name, path)
    return paths
