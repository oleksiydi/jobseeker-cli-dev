"""Readable experiment observations, explicitly separate from human judgments."""
from __future__ import annotations

import re
from statistics import median

from app.matching.episode_report import private_write


def word_count(text: str) -> int:
    return len(text.split())


def summarize(result: dict, cases: dict) -> dict:
    first = {r['case_id']: r for r in result['records'] if r['trial'] == 1}
    pairs = [c for c in cases['classification'] if c['id'] in first]
    valid = [r for r in first.values() if r['validated']]
    summary = {'annotation_origin': 'agent_controls_not_human_ground_truth',
               'classification_pairs': len(pairs),
               'rules_agreement': sum(result['rules'][c['id']]['relation'] == c['control']['relation'] for c in pairs),
               'model_agreement': sum(first[c['id']]['validated'] and
                                      first[c['id']]['answer']['relation'] == c['control']['relation'] for c in pairs),
               'model_direct_against_non_direct_control': sum(
                   first[c['id']]['validated'] and first[c['id']]['answer']['relation'] == 'direct'
                   and c['control']['relation'] != 'direct' for c in pairs),
               'rules_wall_seconds': round(sum(v['wall_seconds'] for v in result['rules'].values()), 3),
               'validation_rejections': sum(not r['validated'] for r in first.values()),
               'first_trial_median_wall_seconds': round(median(r['wall_seconds'] for r in first.values()), 3),
               'thinking_content_observed': any(r['reasoning_content_present'] for r in result['records'])}
    for split in sorted({'development', 'holdout'} | {c['split'] for c in pairs}):
        subset = [c for c in pairs if c['split'] == split]
        summary[split] = {'count': len(subset), 'rules_agreement': sum(
            result['rules'][c['id']]['relation'] == c['control']['relation'] for c in subset),
            'model_agreement': sum(first[c['id']]['validated'] and
                                   first[c['id']]['answer']['relation'] == c['control']['relation'] for c in subset)}
    for task in ('classification', 'editorial'):
        records = [r for r in valid if r['task'] == task]
        if records:
            rates = [r['timings']['predicted_per_second'] for r in records
                     if r['timings'].get('predicted_per_second') is not None]
            summary[task] = {'median_wall_seconds': round(median(r['wall_seconds'] for r in records), 3),
                             'decode_tokens_per_second_median': round(median(rates), 3) if rates else None,
                             'prompt_tokens': [r['usage'].get('prompt_tokens') for r in records],
                             'completion_tokens': [r['usage'].get('completion_tokens') for r in records]}
    summary['repeats'] = [{'case_id': r['case_id'], 'identical_answer':
                           r['answer'] == first[r['case_id']]['answer']
                           if r['validated'] and first[r['case_id']]['validated'] else None,
                           'wall_seconds': r['wall_seconds']} for r in result['records'] if r['trial'] == 2]
    return summary


def write_summary_markdown(result: dict, cases: dict, path) -> None:
    first = {r['case_id']: r for r in result['records'] if r['trial'] == 1}
    model = result['identity']['alias']
    lines = [f'# {model}: наблюдения локального эксперимента', '',
             'Контрольные оценки и разбор — агентские, не human expert ground truth. '
             'Совпадение с ними не является доказанной точностью на реальных заявлениях.', '',
             '| Пара | Раздел | Агентский контроль | Правила | Модель | Граница по ответу модели |',
             '| --- | --- | --- | --- | --- | --- |']
    for case in cases['classification']:
        if case['id'] not in first:
            continue
        record = first[case['id']]
        answer = record['answer'] or {}
        boundary = answer.get('boundary', 'Ответ отклонён проверкой формы/завершения.').replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {case['id']} | {case['split']} | {case['control']['relation']} | "
                     f"{result['rules'][case['id']]['relation']} | {answer.get('relation', 'rejected')} | {boundary} |")
    lines += ['', '## Немецкая редактура', '',
              'Форма/ID проверяются кодом; фактическая точность и естественность требуют отдельного просмотра.', '']
    for case in cases['editorial']:
        if case['id'] not in first:
            continue
        answer = first[case['id']]['answer'] or {}
        text = answer.get('text', '[Ответ отклонён]')
        lines += [f"### {case['id']}", '']
        if 'draft' in case:
            lines += ['Исходный черновик для минимальной языковой правки:', '', case['draft'], '']
        lines += ['Шаблон для сравнения (не передавался модели):', '', case['baseline'], '',
                  f"{model} ({word_count(text)} слов; целевой диапазон {case['word_range']}):", '', text, '',
                  'Разрешённые исходные факты:', '']
        lines += [f"- {f['id']}: {f['value']} (`{f['source']}#{f['pointer']}`)" for f in case['facts']]
        numeric_context = str(case['context']) + ' '.join(str(f['value']) for f in case['facts'])
        extra = sorted(set(re.findall(r'\d+(?:[.,]\d+)?', text)) -
                       set(re.findall(r'\d+(?:[.,]\d+)?', numeric_context)))
        if extra:
            lines += ['', 'Числа без буквального основания во входе: ' + ', '.join(extra)]
        lines.append('')
    lines += ['## Исходные ссылки пар', '']
    for case in cases['classification']:
        if case['id'] in first:
            lines += [f"### {case['id']}: {case['requirement']['text']}", '',
                      'Контрольная граница: ' + case['control']['scope'], '']
            lines += [f"- {f['id']}: {f['value']} (`{f['source']}#{f['pointer']}`)" for f in case['facts']]
            lines.append('')
    private_write(path, '\n'.join(lines) + '\n')
