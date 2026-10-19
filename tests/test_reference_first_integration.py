"""Reference-first CLI stages remain idempotent, scoped and opt-out compatible."""
import json

from tools import build_taxonomy_from_vacancies as taxonomy
from tools import build_synonyms_from_taxonomy as synonyms
from tools import reference_extraction, reference_prefilter
from test_reference_prefilter import index, response
from test_build_taxonomy_integration import _args, _vacancy


def test_taxonomy_stage_merges_local_and_llm_terms_and_reuses_duplicate_input(tmp_path, monkeypatch):
    args = _args(tmp_path, reference_first=True)
    # Validate real aggregate schema, including measured prefilter counters.
    from pathlib import Path
    args.aggregate_schema = Path('conf/normalization/schemas/taxonomy_domain_aggregate.schema.json')
    monkeypatch.setattr(taxonomy, 'parse_args', lambda: args)
    monkeypatch.setattr(taxonomy, 'load_app_config', lambda: {})
    monkeypatch.setattr(taxonomy, 'get_database', lambda *_: object())
    monkeypatch.setattr(taxonomy, '_iter_active_vacancies', lambda *_, **kwargs: [_vacancy('R1'), _vacancy('R2')])
    monkeypatch.setattr(reference_prefilter, 'load_index', lambda *_: index())
    calls = []
    def fake(**kwargs):
        calls.append(kwargs['vacancy'].canonical_text())
        return {'vacancy_domains': [{'domain': 'it_software_daten', 'confidence': .9}],
                'terms': [{'term': 'Monitoring', 'category': 'skills', 'facet': 'skill_requirement',
                           'domain_hint': 'it_software_daten', 'domain_confidence': .9,
                           'context_phrase': 'Kubernetes und Monitoring'}]}, response()
    monkeypatch.setattr(reference_extraction, 'request_extraction', fake)
    taxonomy.main()
    aggregate = json.loads(args.out_aggregate_json.read_text())
    assert aggregate['input']['processed_vacancies'] == 2
    assert aggregate['reference_prefilter']['reference_api_calls'] == 1
    assert aggregate['reference_prefilter']['reference_cache_hits'] == 1
    rows = [json.loads(line) for line in args.out_terms_jsonl.read_text().splitlines()]
    assert {row['term'] for row in rows} == {'Kubernetes', 'Monitoring'}
    assert any(row['origin'] == 'reference_prefilter' for row in rows)
    assert len(calls) == 1


def test_verified_synonym_stage_writes_reference_identity_without_model(tmp_path, monkeypatch):
    from test_build_synonyms_from_taxonomy import _args as synonym_args, FakeCollection
    from test_reference_prefilter import row
    args = synonym_args(tmp_path, mode='llm', reference_first=True, block_size=10,
                        cache_path=tmp_path/'cache.sqlite3', timeout_seconds=1, provider='test', model='test',
                        review_provider='test', review_model='test', escalation_provider='test', escalation_model='test')
    args.terms_jsonl.write_text('\n'.join(json.dumps(row(t, ref=f'R{i}'))
                                        for i, t in enumerate(('K8s', 'Kubernetes'))))
    state, audit = FakeCollection(), FakeCollection()
    db = {'normalization_synonyms_state': state, 'normalization_synonyms_audit': audit}
    monkeypatch.setattr(synonyms, 'parse_args', lambda: args)
    monkeypatch.setattr(synonyms, 'load_app_config', lambda: {})
    monkeypatch.setattr(synonyms, 'get_database', lambda *_: db)
    monkeypatch.setattr(synonyms, 'load_runtime_taxonomy', lambda: {})
    monkeypatch.setattr(reference_prefilter, 'load_index', lambda *_: index())
    from tools import synonyms_cluster
    monkeypatch.setattr(synonyms_cluster, 'cluster_block', lambda **kwargs: (_ for _ in ()).throw(AssertionError('No API')))
    synonyms.main()
    assert len(state.docs) == 1
    doc = state.docs[0]
    assert doc['source'] == 'reference_prefilter' and doc['processing_status'] == 'accepted'
    assert doc['dictionary_reference']['id'] == 'kubernetes'
    before = doc['candidate_hash']
    synonyms.main()
    assert len(state.docs) == 1 and state.docs[0]['candidate_hash'] == before
    state.docs[0].update(processing_status='rejected', decision_source='human')
    synonyms.main()
    assert state.docs[0]['processing_status'] == 'rejected'


def test_reference_switch_precedence(monkeypatch):
    from tools import dictionary_settings as settings
    monkeypatch.setattr(settings, 'load_dotenv', lambda: None)
    monkeypatch.setattr(settings, 'load_app_config', lambda: {'dictionary_pipeline': {'reference_first': True}})
    monkeypatch.setenv('DICTIONARY_REFERENCE_FIRST', 'false')
    assert settings.dictionary_settings()['reference_first'] is False
    monkeypatch.setenv('DICTIONARY_REFERENCE_FIRST', '')
    assert settings.dictionary_settings()['reference_first'] is True
    monkeypatch.setenv('DICTIONARY_REFERENCE_FIRST', 'invalid')
    import pytest
    with pytest.raises(ValueError, match='must be true or false'):
        settings.dictionary_settings()


def test_pipeline_passes_reference_switch_to_both_builders(tmp_path):
    from tools.run_dictionary_pipeline import build_commands
    from test_dictionary_pipeline import _args as pipeline_args
    for enabled in (True, False):
        commands = build_commands(pipeline_args(tmp_path, reference_first=enabled))
        flag = '--reference-first' if enabled else '--no-reference-first'
        assert flag in commands[0] and flag in commands[1]


def test_cli_overrides_config_reference_default_without_reading_env_file(monkeypatch):
    import sys
    from tools.dictionary_settings import DEFAULTS
    from tools import run_dictionary_pipeline
    for module in (taxonomy, synonyms, run_dictionary_pipeline):
        monkeypatch.setattr(module, 'dictionary_settings', lambda: {**DEFAULTS, 'reference_first': True})
        monkeypatch.setattr(sys, 'argv', ['tool', '--no-reference-first'])
        assert module.parse_args().reference_first is False
        monkeypatch.setattr(sys, 'argv', ['tool', '--reference-first'])
        assert module.parse_args().reference_first is True
