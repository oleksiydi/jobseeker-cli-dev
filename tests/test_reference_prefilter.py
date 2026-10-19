"""Reference-first filtering saves requests without treating related skills as synonyms."""
from types import SimpleNamespace

import pytest

from app.matching.models import MergedVacancyNormalized, MergedVacancyRaw
from tools.llm_artifact_cache import ArtifactCache
from tools.reference_prefilter import ReferenceIndex
from tools.reference_extraction import batches, extract
from tools.reference_synonyms import prepare
from tools.synonyms_cluster import build_proposals
from tools import reference_extraction, synonyms_cluster


def index(extra=()):
    return ReferenceIndex([
        {'id': 'python', 'label': 'Python', 'aliases': [], 'kind': 'skill', 'source': 'esco', 'version': 'v1'},
        {'id': 'kubernetes', 'label': 'Kubernetes', 'aliases': ['K8s'], 'kind': 'skill', 'source': 'esco', 'version': 'v1'},
        {'id': 'csharp', 'label': 'C#', 'aliases': [], 'kind': 'skill', 'source': 'esco', 'version': 'v1'},
        {'id': 'cpp', 'label': 'C++', 'aliases': [], 'kind': 'skill', 'source': 'esco', 'version': 'v1'},
        *extra], {'categories': {'stack': {'Python', 'Kubernetes', 'C++', 'C#'}}})


def vacancy(text, ref='synthetic'):
    return MergedVacancyNormalized.from_raw(MergedVacancyRaw.from_sources(
        {'referenznummer': ref, 'titel': 'Engineer'},
        {'referenznummer': ref, 'stellenangebotsBeschreibung': text}))


def args(tmp_path):
    return SimpleNamespace(provider='synthetic', model='test', base_url=None, api_key=None,
                           timeout_seconds=1,
                           max_terms_per_vacancy=30, max_input_chars=1500, llm_max_tokens=1000,
                           retry_on_length=1, overwrite_output=False, llm_calls_log=tmp_path/'calls.jsonl')


def response(finish='stop'):
    return SimpleNamespace(finish_reason=finish, provider='synthetic', model='test',
                           usage={'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}, elapsed_ms=1)


def test_known_clause_removed_but_unknown_mixed_and_negated_context_remain():
    original = vacancy('Ihr Profil\nPython erforderlich.\nPython und NeuWerkzeug erforderlich.\n'
                       'Kubernetes ist nicht erforderlich.\nC++ und C# Kenntnisse.')
    plan = index().plan(original)
    assert 'Python erforderlich.' not in plan.residual
    assert 'Python und NeuWerkzeug erforderlich.' in plan.residual
    assert 'Kubernetes ist nicht erforderlich.' in plan.residual
    assert 'C#' in plan.residual  # Short names are deliberately not auto-resolved.
    assert plan.removed_segments == 1
    assert any(r['term'] == 'Python' for r in plan.local_terms)
    for observation in plan.observations:
        a, b = observation['text_span']
        assert original.description[a:b] == observation['term']


def test_same_text_in_different_sections_is_not_deduplicated():
    plan = index().plan(vacancy('Ihr Profil\nPython und NeuWerkzeug\nBenefits\nPython und NeuWerkzeug'))
    assert plan.residual.count('Python und NeuWerkzeug') == 2


def test_ambiguous_homograph_never_resolved_locally():
    references = index([{'id': 'python-other', 'label': 'Python', 'kind': 'skill', 'source': 'dkz'}])
    assert references.resolve('Python') is None
    assert 'Python erforderlich' in references.plan(vacancy('Python erforderlich')).residual


def test_domain_and_product_fields_are_not_lost_when_description_is_known():
    plan = index().plan(vacancy('Python erforderlich.'))
    assert 'TITLE: Engineer' in plan.residual
    assert plan.local_terms and plan.residual


def test_dictionary_fingerprint_is_stable_and_invalidates_changed_snapshot():
    assert index().version == index().version
    assert index([{'id': 'novel', 'label': 'New concept', 'kind': 'skill'}]).version != index().version
    first = ReferenceIndex(runtime={'categories': {'stack': {'Python'}}, 'updated_at': 'old'})
    second = ReferenceIndex(runtime={'categories': {'stack': {'Python'}}, 'updated_at': 'new'})
    assert first.version == second.version


def test_long_residuals_are_not_truncated():
    text = 'TITLE: Engineer\nDESCRIPTION:\n' + ('Neue Tätigkeit X' * 300) + '\nTAIL_UNIQUE'
    chunks = list(batches(text, 500))
    assert len(chunks) > 1 and all(len(chunk) <= 500 for chunk in chunks)
    assert 'TAIL_UNIQUE' in chunks[-1]
    # Every character participates; overlapping windows retain word boundaries.
    original = text.partition('DESCRIPTION:\n')[2].splitlines()[0]
    for start in range(0, len(original)-20, 20):
        assert any(original[start:start+20] in chunk for chunk in chunks)


def test_residual_extraction_deduplicates_cache_and_keeps_local_provenance(tmp_path, monkeypatch):
    calls = []
    def fake(**kwargs):
        text = kwargs['vacancy'].canonical_text()
        calls.append(text)
        return {'vacancy_domains': [{'domain': 'it_software_daten', 'confidence': .9}],
                'terms': [{'term': 'NeuWerkzeug', 'category': 'tools', 'facet': 'tool_or_platform',
                           'domain_hint': 'it_software_daten', 'domain_confidence': .9,
                           'context_phrase': 'NeuWerkzeug erforderlich.'}]}, response()
    monkeypatch.setattr(reference_extraction, 'request_extraction', fake)
    cache = ArtifactCache(tmp_path/'cache.sqlite3')
    text = 'Ihr Profil\n' + 'Python erforderlich.\n' * 100 + 'NeuWerkzeug erforderlich.'
    config = args(tmp_path)
    domains = ['it_software_daten', 'sonstiges']
    rows, metrics = extract(vacancy(text), index(), None, config, domains, {}, cache, 'test')
    assert {r.term for r, _ in rows} == {'Python', 'NeuWerkzeug'}
    assert next(meta for row, meta in rows if row.term == 'Python')['origin'] == 'reference_prefilter'
    assert metrics['reference_removed_segments'] == 100
    assert metrics['reference_prompt_chars'] < metrics['reference_baseline_prompt_chars']
    assert metrics['reference_usage_prompt_tokens'] == 100
    _, again = extract(vacancy(text, 'another-ref'), index(), None, config, domains, {}, cache, 'test')
    assert len(calls) == 1 and again['reference_cache_hits'] == 1
    assert again['reference_prompt_chars'] == 0
    cache.close()


def test_incomplete_responses_are_not_published_or_cached_and_usage_is_accounted(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_extraction, 'request_extraction',
                        lambda **kwargs: ({'terms': [{'term': 'incomplete'}]}, response('length')))
    cache = ArtifactCache(tmp_path/'cache.sqlite3')
    totals = {}
    with pytest.raises(ValueError, match='Incomplete'):
        extract(vacancy('NeuWerkzeug'), index(), None, args(tmp_path), ['sonstiges'], {}, cache, 'test', totals=totals)
    assert totals['reference_api_calls'] == 2
    assert totals['reference_usage_prompt_tokens'] == 200
    assert cache.connection.execute('select count(*) from responses').fetchone()[0] == 0
    cache.close()


def test_invalid_json_accounts_usage_and_sends_the_compact_prompt(tmp_path, monkeypatch):
    from tools import taxonomy_extract
    calls = []

    def fake(request):
        calls.append(request)
        result = response()
        result.content = 'Invalid JSON'
        return result

    monkeypatch.setattr(taxonomy_extract, 'chat_completion', fake)
    cache = ArtifactCache(tmp_path/'invalid.sqlite3')
    totals = {}
    with pytest.raises(ValueError, match='not valid JSON'):
        extract(vacancy('NeuWerkzeug'), index(), None, args(tmp_path),
                ['sonstiges'], {}, cache, 'test', totals=totals)
    assert totals['reference_api_calls'] == 1
    assert totals['reference_usage_prompt_tokens'] == 100
    assert calls[0].messages[0]['content'] == taxonomy_extract.build_reference_system_prompt(
        ['sonstiges'], args(tmp_path).max_terms_per_vacancy)
    assert cache.connection.execute('select count(*) from responses').fetchone()[0] == 0
    cache.close()


def row(term, ref='synthetic', **kw):
    return {'term': term, 'referenznummer': ref, 'category': 'stack', 'facet': 'technology_stack',
            'domain_hint': 'it_software_daten', 'context_phrase': term, **kw}


def test_verified_alias_is_reused_without_llm_and_different_products_are_not_merged(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail('All terms are verified; no LLM request is needed')
    monkeypatch.setattr(synonyms_cluster, 'cluster_block', forbidden)
    metrics = {}
    proposals = build_proposals([row('K8s'), row('Kubernetes'), row('Python')], provider='test', model='test',
                                cache=ArtifactCache(None), block_size=10, timeout_seconds=1,
                                reference_index=index(), metrics=metrics)
    assert [(p['alias'], p['canonical']) for p in proposals] == [('k8s', 'Kubernetes')]
    assert proposals[0]['origin'] == 'reference_prefilter'
    assert metrics['llm_blocks'] == 0


def test_unknown_term_uses_small_external_reference_shortlist_and_requires_review(monkeypatch):
    seen = []
    def complete(**kwargs):
        import json
        block = json.loads(kwargs['messages'][1]['content'])
        seen.append(block)
        return {'clusters': [{**block['block'], 'canonical_term_key': 'kubernetes',
                              'aliases': ['kubernetes werkzeug'], 'confidence': .99,
                              'requires_review': False, 'reason': 'Possible equivalent'}]}
    monkeypatch.setattr(synonyms_cluster, 'validated_completion', complete)
    proposals = build_proposals([row('Kubernetes Werkzeug', ref=f'R{i}') for i in range(3)],
                                provider='test', model='test', cache=ArtifactCache(None),
                                block_size=10, timeout_seconds=1, reference_index=index())
    assert len(seen) == 1 and len(seen[0]['terms']) <= 10
    assert proposals[0]['requires_review'] and proposals[0]['reference_id'] == 'kubernetes'


def test_unknown_scopes_never_auto_accept_reference_aliases():
    _, proposals, _ = prepare([row('K8s', domain_hint='sonstiges')], index())
    assert proposals == []


def test_dictionary_completion_counts_real_usage_and_cache_hits(tmp_path, monkeypatch):
    import json
    from tools import dictionary_llm
    from tools.llm_artifact_cache import ArtifactCache
    payload = {'ok': True}
    fake = SimpleNamespace(content=json.dumps(payload), finish_reason='stop',
                           usage={'prompt_tokens': 23, 'completion_tokens': 4, 'total_tokens': 27})
    monkeypatch.setattr(dictionary_llm, 'chat_completion', lambda *_: fake)
    cache = ArtifactCache(tmp_path/'metrics.sqlite3')
    metrics = {}
    params = {'provider': 'openrouter', 'model': 'vendor/test',
              'messages': [{'role': 'user', 'content': 'Synthetic'}],
              'schema': {'type': 'object'}, 'cache': cache, 'metrics': metrics}
    assert dictionary_llm.validated_completion(**params) == payload
    assert dictionary_llm.validated_completion(**params) == payload
    assert metrics == {'api_calls': 1, 'prompt_tokens': 23, 'completion_tokens': 4,
                       'total_tokens': 27, 'cache_hits': 1}
    cache.close()
