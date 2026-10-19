import json

import pytest

from tools.compare_local_models import load_and_validate, render_markdown, summarize


def _fixtures(tmp_path):
    cases = {
        'classification': [
            {'id': 'P1', 'split': 'development', 'control': {'relation': 'direct'},
             'requirement': {'text': 'x'}, 'facts': []},
            {'id': 'P2', 'split': 'new_holdout', 'control': {'relation': 'unknown'},
             'requirement': {'text': 'y'}, 'facts': []},
        ],
        'editorial': [{'id': 'E1', 'baseline': 'Template text', 'draft': 'Draft text',
                       'facts': [{'id': 'F1', 'value': 'A fact', 'source': 'profile.json',
                                  'pointer': '/experience/0'}]}],
    }
    cases_path = tmp_path / 'cases.json'
    cases_path.write_text(json.dumps(cases), encoding='utf-8')
    import hashlib
    case_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()

    def report(alias, predictions):
        records = []
        for case_id, task, relation in [('P1', 'classification', predictions[0]),
                                        ('P2', 'classification', predictions[1]),
                                        ('E1', 'editorial', None)]:
            records.append({'case_id': case_id, 'task': task, 'trial': 1,
                            'validated': relation is not None,
                            'answer': ({'relation': relation, 'boundary': 'boundary'} if relation
                                       else {'text': f'{alias} wording', 'used_fact_ids': ['F1']}),
                            'wall_seconds': 1.2,
                            'timings': {'predicted_per_second': 4.0}})
        return {'case_file_sha256': case_hash, 'identity': {'alias': alias,
                'manifest': {'model_file': {'size': 123}}}, 'records': records,
                'rules': {'P1': {'relation': 'direct'}, 'P2': {'relation': 'transferable'}},
                'metrics': {'server_rss_kib': 1024**2, 'tracked_total_rss_kib': 2 * 1024**2,
                            'tracked_total_swap_kib': 512}}

    a, b = report('alpha', ['direct', 'direct']), report('beta', ['transferable', None])
    paths = []
    for name, value in [('a', a), ('b', b)]:
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        paths.append(path)
    return cases_path, paths, a, b


def test_summary_covers_all_pairs_splits_rules_once_editorial_and_observation_limits(tmp_path):
    cases_path, paths, _, _ = _fixtures(tmp_path)
    cases, reports = load_and_validate(cases_path, paths)
    result = summarize(cases, reports)

    assert len(result['baseline']) == 2
    alpha = result['models'][0]
    assert alpha['classification']['agent_control_agreement'] == 1
    assert alpha['classification']['rules_agent_control_agreement'] == 1
    assert alpha['classification']['direct_against_non_direct_control'] == 1
    assert alpha['splits']['new_holdout']['pairs'] == 1
    assert alpha['memory']['server_gib'] == 1
    assert alpha['memory']['tracked_total_swap_gib'] == round(512 / 1024**2, 3)
    assert alpha['weights_file_size_bytes'] == 123
    assert result['editorial'][0]['models'][0]['text'] == 'alpha wording'
    assert 'not human ground truth' in result['annotation_origin']
    markdown = render_markdown(result)
    assert 'Semantic review required' in markdown
    assert 'Direct vs other control' in markdown
    assert 'not automatically a false factual claim' in markdown
    assert 'grouped preferences may be ambiguous' in markdown


@pytest.mark.parametrize('mutation, message', [
    (lambda run: run.update(case_file_sha256='wrong'), 'case_file_sha256'),
    (lambda run: run['records'].pop(), 'mismatched'),
    (lambda run: run.update(records=[]), 'no records'),
])
def test_rejects_hash_or_selected_set_or_empty_run(tmp_path, mutation, message):
    cases_path, paths, a, _ = _fixtures(tmp_path)
    mutation(a)
    paths[0].write_text(json.dumps(a), encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        load_and_validate(cases_path, paths)


def test_rejects_duplicate_model_identity_and_record_tuple(tmp_path):
    cases_path, paths, a, _ = _fixtures(tmp_path)
    paths[1].write_text(json.dumps(a), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate model identity'):
        load_and_validate(cases_path, paths)
    a['identity']['alias'] = 'other'
    a['records'].append(dict(a['records'][0]))
    paths[0].write_text(json.dumps(a), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate case/trial/task'):
        load_and_validate(cases_path, [paths[0]])


def test_decode_median_uses_first_trial_classification_records_only(tmp_path):
    cases_path, paths, report, _ = _fixtures(tmp_path)
    report['records'][0]['timings']['predicted_per_second'] = 2.0
    report['records'][1]['timings']['predicted_per_second'] = 8.0
    report['records'][2]['timings']['predicted_per_second'] = 1000.0
    second_trial = dict(report['records'][0], trial=2)
    second_trial['timings'] = {'predicted_per_second': 2000.0}
    report['records'].append(second_trial)
    paths[0].write_text(json.dumps(report), encoding='utf-8')

    cases, reports = load_and_validate(cases_path, [paths[0]])
    summary = summarize(cases, reports)

    assert summary['models'][0]['classification']['median_decode_tokens_per_second'] == 5.0


def test_rejects_disagreeing_rules_baselines_and_missing_first_trial(tmp_path):
    cases_path, paths, _, second = _fixtures(tmp_path)
    second['rules']['P2']['relation'] = 'unknown'
    paths[1].write_text(json.dumps(second), encoding='utf-8')
    with pytest.raises(ValueError, match='mismatched rules baseline'):
        load_and_validate(cases_path, paths)

    cases_path, paths, first, _ = _fixtures(tmp_path)
    first['records'][0]['trial'] = 2
    paths[0].write_text(json.dumps(first), encoding='utf-8')
    with pytest.raises(ValueError, match='missing trial 1'):
        load_and_validate(cases_path, [paths[0]])
