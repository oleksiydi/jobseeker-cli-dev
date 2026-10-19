"""Private, source-linked reports for reviewing episode retrieval before writing."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.core.i18n import localized_prefix, report_language, tr
from app.matching.output_localization import localize_episode_report


def private_json(path: Path, value) -> None:
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def private_write(path: Path, text: str) -> None:
    """Publish one artifact atomically, with owner-only file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _escape(value) -> str:
    return str(value or '').replace('|', '\\|').replace('\n', ' ')


def _citation(fact: dict | None) -> str:
    if not fact:
        return tr('не указан')
    return f"{_escape(fact['value'])} (`{_escape(fact['source'])}#{_escape(fact['pointer'])}`)"


@report_language
def render_report(report: dict) -> str:
    report = localize_episode_report(report)
    episodes = {e['id']: e for e in report['projection']['episodes']}
    lines = [tr('# Требования и проектный опыт'), '',
             tr(
                 'Подбор кандидатов для проверки перед Anschreiben. Факты сообщены кандидатом; это '
                 'не независимая проверка опыта и не экспертная оценка человека.',
             ), '',
             tr(
                 'MiniLM показывает смысловую близость. Она не подтверждает требование, '
                 'квалификацию, текущий уровень или успешное освоение новой технологии.',
             ), '',
             tr(
                 'Эпизодов: {p0}; исключено: {p1}; несвязанных интервью-историй: {p2} (не '
                 'индексируются).',
                 p0=len(episodes),
                 p1=len(report['projection']['withheld']),
                 p2=report['projection']['unlinked_stories'],
             ), '',
             tr(
                 'Лексический top и MiniLM top показаны независимо. Объединение сохраняет оба '
                 'списка; баллы — внутренние сигналы поиска, не вероятность соответствия.',
             ), '']
    for vacancy in report['vacancies']:
        job = vacancy['job']
        lines += [f"## {job['title']} — {job['ref']}", '',
                  tr(
                      'Источник: {p0}; группа: {p1}. Текущая доступность вакансии не проверялась.',
                      p0=job['url'] or tr('локальный снимок'),
                      p1=vacancy.get('category', 'selected'),
                  ), '']
        conflicts = [c for c in vacancy['constraints'] if c.get('status') == 'conflict']
        if conflicts:
            lines += [tr(
                'Есть конфликт условий; подробности в приватном JSON. Подбор опыта его не отменяет.',
            ), '']
        for row in vacancy['comparison']:
            req = row['requirement']
            lines += [f"### {req['quote']}", '',
                      f"`{req['id']}`; {req.get('modality', 'unknown')}; "
                      f"description[{req['text_span'][0]}:{req['text_span'][1]}].", '']
            for method, label in [('lexical', tr('Слова + словарь')), ('minilm', 'MiniLM')]:
                candidates = []
                for c in row[method]:
                    episode = episodes[c['episode_id']]
                    title = episode['label']['value'] if episode.get('label') else c['episode_id']
                    candidates.append(f"{title} [{c['episode_id']}; {c['score']:.3f}]")
                lines.append(f"{label}: " + (' → '.join(candidates) if candidates else tr(
                    'кандидатов нет',
                )) + '\n')
            lines += [tr('| Подходящий опыт | Подтверждённая часть | Неизвестное / граница |'),
                      '| --- | --- | --- |']
            for evidence in row['evidence']:
                episode = episodes[evidence['episode_id']]
                label = episode.get('label', {}).get('value', episode['id'])
                matched = _citation(evidence['confirmed_part'])
                boundary = ' '.join(evidence['unknown'])
                if evidence['relation'] == 'transferable':
                    boundary = tr('Смежная задача по правилу; перенос требует проверки. ') + boundary
                elif evidence['relation'] == 'unknown':
                    boundary = tr('Связь не подтверждена. ') + boundary
                if evidence['requirement_use'] == 'not_required_or_benefit':
                    boundary = tr(
                        'Отрицание/льгота: не использовать как обязательное требование. ',
                    ) + boundary
                lines.append(f"| {_escape(label)}; {evidence['relation']}; {_citation(evidence['period'])} "
                             f"| {matched} | {_escape(boundary)} |")
            if not row['evidence']:
                lines.append(tr(
                    '| — | Подтверждения не найдены | Отсутствие записи не доказывает отсутствие '
                    'навыка |',
                ))
            lines.append('')
        if not vacancy['comparison']:
            lines += [tr(
                'Требования не выделены; описание/разметку нужно проверить. Это не отсутствие '
                'требований.',
            ), '']
    lines += [tr('## Исходные эпизоды для проверки'), '',
              tr(
                  'Результаты ниже относятся к проекту в целом. Связь с конкретным действием '
                  'автоматически не устанавливается.',
              ), '']
    for episode in episodes.values():
        lines += [f"### {episode.get('label', {}).get('value', episode['id'])} — {episode['id']}", '',
                  tr('Период: ') + _citation(episode.get('timeframe')), '']
        lines += [tr('Проект/работодатель: ') + _citation(episode.get('company')),
                  tr('Роль по источнику: ') + _citation(episode.get('role_title')), '']
        if episode.get('context'):
            lines += [tr('Контекст: ') + _citation(episode['context']), '']
        lines += [tr('Действия:'), ''] + ['- ' + _citation(f) for f in episode['actions']] + ['']
        lines += [tr('Известные результаты проекта:'), '']
        lines += (['- ' + _citation(r['fact']) for r in episode['results']]
                  or [tr('- Результат в источнике не указан.')])
        lines += ['', tr('Технологии проекта (не доказательство конкретной обязанности): ') +
                  '; '.join(_citation(f) for f in episode['technologies']), '']
        if episode.get('confidence'):
            lines += [tr('Оговорки источника: ') + _citation(episode['confidence']), '']
    lines += [tr('## Воспроизводимость'), '',
              tr('Версия: `{p0}`; вход: `{p1}`.', p0=report['engine_version'], p1=report['input_hash']), '',
              tr('Метрики: `{p0}`.', p0=json.dumps(report['metrics'], ensure_ascii=False)), '',
              tr(
                  'Разметка и выводы правил — агентские/автоматические, не human ground truth. '
                  'Recall и точность без независимой человеческой разметки здесь не заявляются.',
              ), '']
    return '\n'.join(lines)


@report_language
def write_episode_reports(report: dict, prefix: Path) -> dict:
    report = localize_episode_report(report)
    prefix = localized_prefix(prefix)
    paths = {key: prefix.with_suffix(suffix) for key, suffix in
             [('json', '.json'), ('markdown', '.md'), ('projection', '.episodes.json')]}
    private_json(paths['json'], report)
    private_json(paths['projection'], report['projection'])
    private_write(paths['markdown'], render_report(report))
    return paths
