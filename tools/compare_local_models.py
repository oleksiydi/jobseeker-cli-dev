"""Compare completed offline model experiment reports without running models."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.matching.episode_report import private_json, private_write


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _case_tasks(cases: dict) -> dict[str, str]:
    return {case['id']: task for task in ('classification', 'editorial')
            for case in cases[task]}


def load_and_validate(cases_path: Path, run_paths: list[Path]) -> tuple[dict, list[dict]]:
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    expected_hash = sha256(cases_path)
    task_by_case = _case_tasks(cases)
    reports = []
    identities = set()
    selected_set = None
    for path in run_paths:
        report = json.loads(path.read_text(encoding='utf-8'))
        if report.get('case_file_sha256') != expected_hash:
            raise ValueError(f'{path}: report case_file_sha256 does not match cases file')
        identity = report.get('identity')
        if not isinstance(identity, dict):
            raise ValueError(f'{path}: missing model identity')
        identity_key = json.dumps(identity, sort_keys=True, ensure_ascii=False)
        if identity_key in identities:
            raise ValueError(f'{path}: duplicate model identity')
        identities.add(identity_key)
        records = report.get('records')
        if not isinstance(records, list) or not records:
            raise ValueError(f'{path}: run has no records')
        keys = []
        for row in records:
            case_id, task, trial = row.get('case_id'), row.get('task'), row.get('trial')
            if case_id not in task_by_case or task_by_case[case_id] != task:
                raise ValueError(f'{path}: record case/task does not match cases file')
            if not isinstance(trial, int) or trial < 1:
                raise ValueError(f'{path}: invalid trial')
            keys.append((case_id, task, trial))
        if len(keys) != len(set(keys)):
            raise ValueError(f'{path}: duplicate case/trial/task record')
        run_set = set(keys)
        selected_case_tasks = {(case_id, task) for case_id, task, _ in run_set}
        missing_first = selected_case_tasks - {(case_id, task) for case_id, task, trial in run_set
                                               if trial == 1}
        if missing_first:
            raise ValueError(f'{path}: selected cases are missing trial 1')
        if selected_set is None:
            selected_set = run_set
        elif run_set != selected_set:
            raise ValueError('Runs have mismatched selected case/trial/task sets')
        reports.append(report)
    if not reports:
        raise ValueError('At least one --run is required')
    selected_classification_ids = {case_id for case_id, task, _ in selected_set
                                   if task == 'classification'}
    baseline = None
    for path, report in zip(run_paths, reports):
        rules = report.get('rules', {})
        current = {case_id: rules.get(case_id, {}).get('relation')
                   for case_id in selected_classification_ids}
        if any(value is None for value in current.values()):
            raise ValueError(f'{path}: missing rules baseline for selected classification cases')
        if baseline is None:
            baseline = current
        elif current != baseline:
            raise ValueError('Runs have mismatched rules baseline for shared cases')
    return cases, reports


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _mib(kib: int | float | None) -> float | None:
    return round(kib / 1024, 3) if isinstance(kib, (int, float)) else None


def _memory(report: dict) -> dict:
    metrics = report.get('metrics', {})
    return {'server_gib': round(metrics['server_rss_kib'] / 1024**2, 3)
            if isinstance(metrics.get('server_rss_kib'), (int, float)) else None,
            'tracked_total_gib': round(metrics['tracked_total_rss_kib'] / 1024**2, 3)
            if isinstance(metrics.get('tracked_total_rss_kib'), (int, float)) else None,
            'tracked_total_swap_gib': round(metrics['tracked_total_swap_kib'] / 1024**2, 3)
            if isinstance(metrics.get('tracked_total_swap_kib'), (int, float)) else None,
            'ram_note': 'Tracked process RSS is not physical 8/16 GiB certification.'}


def summarize(cases: dict, reports: list[dict]) -> dict:
    task_by_id = _case_tasks(cases)
    classification = {case['id']: case for case in cases['classification']}
    rows = []
    models = []
    baseline_rows = []
    for report in reports:
        identity = report['identity']
        model_name = identity.get('alias') or identity.get('model') or 'unnamed model'
        records = {(r['case_id'], r['task'], r['trial']): r for r in report['records']}
        first = {case_id: records[(case_id, task, 1)] for case_id, task in task_by_id.items()
                 if (case_id, task, 1) in records}
        class_records = [first[cid] for cid in classification if cid in first]
        class_ids = [cid for cid in classification if cid in first]
        agreement = sum(r.get('validated') is True and r.get('answer', {}).get('relation') ==
                        classification[cid]['control']['relation']
                        for cid, r in ((cid, first[cid]) for cid in class_ids))
        rules_agreement = sum(report.get('rules', {}).get(cid, {}).get('relation') ==
                              classification[cid]['control']['relation'] for cid in class_ids)
        direct_against_non_direct_control = sum(r.get('validated') is True and
                                 r.get('answer', {}).get('relation') == 'direct' and
                                 classification[cid]['control']['relation'] != 'direct'
                                 for cid, r in ((cid, first[cid]) for cid in class_ids))
        rejected = sum(not r.get('validated', False) for r in report['records'])
        latencies = [r.get('timings', {}).get('predicted_per_second') for r in class_records
                     if r.get('validated') and r.get('timings', {}).get('predicted_per_second') is not None]
        latency = [r.get('wall_seconds') for r in class_records if r.get('wall_seconds') is not None]
        model = {'model': model_name, 'identity': identity,
                 'weights_file_size_bytes': identity.get('manifest', {}).get('model_file', {}).get('size'),
                 'classification': {'pairs': len(class_ids), 'total_pairs': len(classification),
                                    'agent_control_agreement': agreement,
                                    'rules_agent_control_agreement': rules_agreement,
                                    'direct_against_non_direct_control': direct_against_non_direct_control,
                                    'rejected_records': rejected,
                                    'median_pair_latency_seconds': _median(latency),
                                    'median_decode_tokens_per_second': _median(latencies)},
                 'splits': {}, 'memory': _memory(report)}
        split_names = sorted({case.get('split', 'unspecified') for case in cases['classification']})
        for split in split_names:
            ids = [cid for cid, case in classification.items() if case.get('split', 'unspecified') == split and cid in first]
            model['splits'][split] = {
                'pairs': len(ids),
                'agent_control_agreement': sum(first[cid].get('validated') is True and
                    first[cid].get('answer', {}).get('relation') == classification[cid]['control']['relation']
                    for cid in ids),
                'rules_agent_control_agreement': sum(report.get('rules', {}).get(cid, {}).get('relation') ==
                    classification[cid]['control']['relation'] for cid in ids)}
        models.append(model)
        for case_id in class_ids:
            answer = first[case_id].get('answer') or {}
            rows.append({'case_id': case_id, 'split': classification[case_id].get('split', 'unspecified'),
                         'control': classification[case_id]['control'].get('relation'),
                         'rules': report.get('rules', {}).get(case_id, {}).get('relation'),
                         'model': model_name, 'prediction': answer.get('relation', 'rejected'),
                         'validated': bool(first[case_id].get('validated')),
                         'boundary': answer.get('boundary')})
        if not baseline_rows:
            baseline_rows = [{'case_id': cid,
                              'control': classification[cid]['control'].get('relation'),
                              'rules': report.get('rules', {}).get(cid, {}).get('relation')}
                             for cid in classification]
    return {'schema_version': 1, 'annotation_origin': 'agent annotations and rules; not human ground truth',
            'accuracy_claim': 'No automatic accuracy or language-quality claim; semantic review required.',
            'memory_limit': 'Tracked process RSS and swap do not certify physical 8/16 GiB RAM limits.',
            'baseline': baseline_rows, 'models': models, 'per_case_predictions': rows,
            'editorial': _editorial(cases, reports)}


def _editorial(cases: dict, reports: list[dict]) -> list[dict]:
    output = []
    for case in cases['editorial']:
        row = {'case_id': case['id'], 'template': case.get('baseline'), 'draft': case.get('draft'),
               'source_facts': case.get('facts', []), 'semantic_review_required': True, 'models': []}
        for report in reports:
            record = next((r for r in report['records'] if r['case_id'] == case['id'] and
                           r['task'] == 'editorial' and r['trial'] == 1), None)
            answer = (record or {}).get('answer') or {}
            row['models'].append({'model': report['identity'].get('alias', 'unnamed model'),
                                  'validated': bool((record or {}).get('validated')),
                                  'text': answer.get('text'),
                                  'used_fact_ids': answer.get('used_fact_ids', [])})
        output.append(row)
    return output


def render_markdown(data: dict) -> str:
    lines = ['# Local model comparison', '',
             '**Annotation agent/not human ground truth. Semantic review required.** '
             'A direct prediction against a non-direct agent label is disagreement with the agent labels, '
             'not automatically a false factual claim; grouped preferences may be ambiguous. '
             'No automatic accuracy or German language quality claim is made.', '',
             '**Memory:** process RSS/swap are observations and do not certify physical 8/16 GiB RAM.', '',
             '## Classification summary', '',
             '| Model | Agreement with agent controls | Rules agreement | Direct vs other control | Rejected | Median pair s | Decode tok/s | Server GiB | Tracked total GiB | Swap GiB | Weights bytes |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for model in data['models']:
        c, mem = model['classification'], model['memory']
        lines.append(f"| {model['model']} | {c['agent_control_agreement']}/{c['pairs']} | "
                     f"{c['rules_agent_control_agreement']}/{c['pairs']} | {c['direct_against_non_direct_control']} | "
                     f"{c['rejected_records']} | {c['median_pair_latency_seconds']} | "
                     f"{c['median_decode_tokens_per_second']} | {mem['server_gib']} | "
                     f"{mem['tracked_total_gib']} | {mem['tracked_total_swap_gib']} | {model['weights_file_size_bytes']} |")
    lines += ['', '### Split results', '', '| Model | Split | Agent control agreement | Rules agreement |', '| --- | --- | ---: | ---: |']
    for model in data['models']:
        for split, values in model['splits'].items():
            lines.append(f"| {model['model']} | {split} | {values['agent_control_agreement']}/{values['pairs']} | "
                         f"{values['rules_agent_control_agreement']}/{values['pairs']} |")
    lines += ['', '### Shared rules baseline', '', '| Case | Agent control | Rules |', '| --- | --- | --- |']
    lines += [f"| {row['case_id']} | {row['control']} | {row['rules']} |" for row in data['baseline']]
    lines += ['', '### Per-case model predictions', '',
              '| Model | Case | Split | Control | Rules | Prediction | Validated | Boundary |',
              '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in data['per_case_predictions']:
        lines.append(f"| {row['model']} | {row['case_id']} | {row['split']} | {row['control']} | "
                     f"{row['rules']} | {row['prediction']} | {row['validated']} | "
                     f"{_md(row.get('boundary'))} |")
    lines += ['', '## Editorial outputs', '',
              'Templates, drafts, source facts and model texts are shown for semantic review; this report does not score language quality.', '']
    for case in data['editorial']:
        lines += [f"### {case['case_id']}", '']
        if case.get('draft'):
            lines += ['Draft:', '', case['draft'], '']
        if case.get('template'):
            lines += ['Template:', '', case['template'], '']
        lines += ['Source facts:', '']
        lines.extend(f"- {fact.get('id')}: {fact.get('value')} ({fact.get('source')}#{fact.get('pointer')})"
                     for fact in case['source_facts'])
        for model in case['models']:
            lines += ['', f"{model['model']} (validated={model['validated']}):", '',
                      model.get('text') or '[rejected or missing]', '']
    return '\n'.join(lines) + '\n'


def _md(value) -> str:
    return str(value or '').replace('|', '\\|').replace('\n', ' ')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-file', required=True, type=Path)
    parser.add_argument('--run', required=True, action='append', type=Path,
                        help='Completed report JSON; repeat for each model')
    parser.add_argument('--output', required=True, type=Path, help='Output path prefix')
    args = parser.parse_args(argv)
    try:
        cases_path = args.cases_file.resolve()
        run_paths = [p.resolve() for p in args.run]
        outputs = {args.output.with_suffix(s).resolve() for s in ('.json', '.md')}
        if cases_path in outputs or outputs.intersection(run_paths):
            raise ValueError('Output must not overwrite an input file')
        cases, reports = load_and_validate(cases_path, run_paths)
        data = summarize(cases, reports)
        private_json(args.output.with_suffix('.json'), data)
        private_write(args.output.with_suffix('.md'), render_markdown(data))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
