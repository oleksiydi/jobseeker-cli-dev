"""Graph provenance, conservative letter claims, and unchanged ranking/cache."""
import json
import re
import shutil
import subprocess

import pytest

from app.matching.cover_letters import build_letter, letter_html, recommendations_html
from app.matching.intelligence import IntelligenceMatcher
from app.matching.intelligence_report import _report_json
from app.matching.reference_relations import (
    ReferenceGraph, RelatedSkillFinder, load_reference_graph, relation_settings,
)
from app.matching.vacancy_documents import DocumentRenderer
from test_intelligence_data import FakeCollection, FakeDB
from test_job_intelligence import FakeEvidenceCache, _job, _profile, _settings


def node(identifier, label, *, source='esco', kind='skill', aliases=()):
    return dict(id=identifier, label=label, aliases=list(aliases), source=source,
                kind=kind, version='v1:snapshot')


def edge(start, end, *, source='esco', kind='hasOptionalSkill', version='v1:snapshot'):
    return {'from': start, 'to': end, 'type': kind, 'source': source, 'version': version}


def graph():
    # The direction mirrors a real ESCO skill relation; IDs are fixture IDs.
    return ReferenceGraph([node('model', '3D-Modellierung'), node('render', '3D-Bilder rendern')],
                          [edge('render', 'model')], snapshots=['snapshot'])


def profile(status='declared'):
    return _profile(skills=[{'label': '3D-Modellierung', 'status': status,
                             'evidence_quote': '3D-Modellierung', 'source_path': 'skills[0]'}])


def find(description, *, relations=None, candidate=None):
    finder = RelatedSkillFinder(relations or graph(), candidate or profile(), relation_settings({}))
    return finder.find({'description': description, 'matched_skills': []})


def test_declared_skill_has_job_quote_and_original_directed_relation():
    related, context = find('3D-Bilder rendern ist erforderlich.')
    assert not context and len(related) == 1
    item = related[0]
    assert item['label'] == '3D-Modellierung' and item['letter_eligible'] is True
    assert item['candidate_concept']['id'] == 'model' and item['target_concept']['id'] == 'render'
    assert item['profile_evidence'][0]['source_path'] == 'skills[0]'
    assert item['job_evidence'][0]['quote'] == '3D-Bilder rendern'
    assert item['relation_path'] == [{**edge('render', 'model'), 'traversal': 'reverse'}]


@pytest.mark.parametrize('description', [
    '3D-Bilder rendern ist nicht erforderlich.',
    'Wir bieten:\n3D-Bilder rendern',
])
def test_negated_or_benefit_mentions_are_never_related(description):
    assert find(description) == ([], [])


def test_unknown_context_is_review_only_but_task_is_eligible():
    related, context = find('Unsere Plattform: 3D-Bilder rendern.')
    assert not related and len(context) == 1
    related, context = find('Aufgaben:\n3D-Bilder rendern')
    assert len(related) == 1 and not context


@pytest.mark.parametrize('status', ['project_evidence', 'learning', 'unknown'])
def test_only_declared_candidate_skills_are_used(status):
    assert find('3D-Bilder rendern erforderlich.', candidate=profile(status)) == ([], [])


def test_ambiguous_candidate_or_target_and_unsafe_aliases_are_rejected():
    original = graph()
    nodes = list(original.nodes.values())
    for duplicate in (node('other', '3D-Modellierung'), node('other', '3D-Bilder rendern')):
        ambiguous = ReferenceGraph([*nodes, duplicate], original.edges)
        assert find('3D-Bilder rendern erforderlich.', relations=ambiguous) == ([], [])
    assert ReferenceGraph([node('go', 'Go')]).bind('Go') == []


@pytest.mark.parametrize('predicate,forward,eligible', [
    ('broaderSkill', True, True), ('broaderSkill', False, False),
    ('narrowerSkill', True, False), ('narrowerSkill', False, True),
    ('hasEssentialSkill', True, True), ('hasOptionalSkill', False, True),
])
def test_hierarchy_direction_never_promotes_broad_skill_to_specific_skill(predicate, forward, eligible):
    start, end = ('model', 'render') if forward else ('render', 'model')
    relations = ReferenceGraph(list(graph().nodes.values()), [edge(start, end, kind=predicate)])
    related, context = find('3D-Bilder rendern erforderlich.', relations=relations)
    assert bool(related) == eligible and bool(context) != eligible


def test_dkz_siblings_and_occupation_links_are_only_recommendations():
    nodes = [node('docker', 'Docker', source='dkz'), node('podman', 'Podman', source='dkz'),
             node('group', 'Software', source='dkz', kind='group'),
             node('role', 'Grafikdesigner', kind='occupation'), *graph().nodes.values()]
    links = [edge('docker', 'group', source='dkz', kind='parent_id'),
             edge('podman', 'group', source='dkz', kind='parent_id'),
             edge('role', 'model', kind='hasEssentialSkill')]
    relations = ReferenceGraph(nodes, links)
    candidate = profile()
    candidate['skills'].append(dict(label='Docker', status='declared', evidence_quote='Docker'))
    finder = RelatedSkillFinder(relations, candidate, relation_settings({}))
    related, context = finder.find({'description': 'Podman erforderlich.', 'role': 'Grafikdesigner'})
    assert not related and len(context) == 2
    assert {item['reason'] for item in context} == {'dkz_group', 'occupation_context'}
    assert len(next(x for x in context if x['reason'] == 'dkz_group')['relation_path']) == 2
    assert all(x['letter_eligible'] is False for x in context)


def test_no_transitive_closure_and_source_identities_are_not_merged():
    nodes = [*graph().nodes.values(), node('far', 'Fotografie'),
             node('dkz-model', '3D-Modellierung', source='dkz')]
    relations = ReferenceGraph(nodes, [*graph().edges, edge('far', 'render')])
    assert relations.bind('3D-Modellierung') == ['dkz-model', 'model']
    assert find('Fotografie erforderlich.', relations=relations) == ([], [])


def test_already_directly_matched_skill_is_not_duplicated_as_related():
    finder = RelatedSkillFinder(graph(), profile(), relation_settings({}))
    assert finder.find({'description': '3D-Bilder rendern erforderlich.',
                        'matched_skills': [{'label': '3D-Modellierung'}]}) == ([], [])


def test_graph_does_not_change_scores_gaps_or_evidence_and_repeat_reuses_cache():
    relations, candidate, settings, cache = graph(), profile(), _settings(), FakeEvidenceCache()
    job = _job('1', '3D-Bilder rendern erforderlich.')
    terms = list(relations.nodes.values())
    baseline = IntelligenceMatcher(candidate, terms, cache, settings).rank([job])[0]
    writes = len(cache.writes)
    matcher = IntelligenceMatcher(candidate, terms, cache, settings, relations=relations)
    enriched = matcher.rank([job])[0]
    for field in ('score', 'scores', 'status', 'matched_skills', 'missing_required', 'evidence', 'constraints'):
        assert baseline[field] == enriched[field]
    assert enriched['missing_required'] and enriched['related_skills']
    assert len(cache.writes) == writes
    assert enriched == matcher.rank([job])[0]
    changed = ReferenceGraph(terms, [edge('render', 'model', version='v2')])
    updated = IntelligenceMatcher(candidate, terms, cache, settings, relations=changed).rank([job])[0]
    assert updated['decision_input_hash'] != enriched['decision_input_hash']
    assert updated['score'] == enriched['score'] and len(cache.writes) == writes


def test_loader_reads_only_active_source_snapshots_and_preserves_parent_links():
    rows = [
        dict(source='esco', source_id='render', snapshot_id='e1', dataset='skill',
             labels=['3D-Bilder rendern'], raw={'_links': {'hasOptionalSkill': {'uri': 'model'}}}),
        dict(source='esco', source_id='model', snapshot_id='e1', dataset='skill',
             labels=['3D-Modellierung'], raw={}),
        dict(source='esco', source_id='inactive', snapshot_id='d1', dataset='skill',
             labels=['Must not load'], raw={}),
        dict(source='dkz', source_id='1', snapshot_id='d1', dataset='DKZ_Kompetenzen_gueltig.xml',
             labels=['Virtualisierungssoftware Docker'], raw={'tag': 'kompetenz', 'parent_id': '9'}),
        dict(source='dkz', source_id='9', snapshot_id='d1', dataset='DKZ_Kompetenzen_gueltig.xml',
             labels=['Betriebssysteme'], raw={'tag': 'kompetenzgruppe'}),
        dict(source='dkz', source_id='7', snapshot_id='d1', dataset='DKZ_Taetigkeiten_gueltig.xml',
             labels=['Inactive occupation'], raw={'attributes': {'zustand': 'A'}}),
    ]
    collection = FakeCollection(rows)
    db = FakeDB({'reference_entries': collection})
    references = [dict(source='esco', version='v1', snapshots=['e1']),
                  dict(source='dkz', version='v2', snapshots=['d1'])]
    relations = load_reference_graph(db, [], references)
    assert len(relations.nodes) == 4 and len(relations.edges) == 2
    assert relations.bind('Docker') == ['dkz:skill:1']
    assert relations.nodes['dkz:group:9']['kind'] == 'group'
    assert relations.edges[0]['version'] in {'v1:e1', 'v2:d1'}
    assert rows == collection.rows and not collection.writes
    assert len(collection.reads) == 1
    assert load_reference_graph(object(), [], []).identity['status'] == 'unavailable'


def test_graph_identity_is_stable_and_dangling_and_self_edges_are_ignored():
    nodes = list(graph().nodes.values())
    links = [*graph().edges, edge('missing', 'model'), edge('model', 'model'), *graph().edges]
    relations = ReferenceGraph(nodes, links)
    other = ReferenceGraph(list(reversed(nodes)), list(reversed(links)))
    assert relations.identity == other.identity
    assert len(relations.edges) == 1 and relations.identity['dangling_edges_skipped'] == 1


@pytest.mark.parametrize('block', [None, {'enabled': 'yes'}, {'max_related_skills': 4},
                                  {'max_context_suggestions': -1}, {'max_related_skills': True}])
def test_invalid_settings_rejected(block):
    with pytest.raises(ValueError):
        relation_settings({'reference_relations': block})


def test_environment_override_and_disabled_fallback(monkeypatch):
    from app.job_intelligence import load_settings, main
    from pathlib import Path
    monkeypatch.setenv('JOB_INTELLIGENCE_RELATIONS_ENABLED', 'false')
    assert load_settings(Path('conf/job-intelligence.json'))['reference_relations']['enabled'] is False
    monkeypatch.setenv('JOB_INTELLIGENCE_RELATIONS_ENABLED', 'maybe')
    monkeypatch.setattr('app.job_intelligence.load_dotenv', lambda *_: None)
    monkeypatch.setattr('app.job_intelligence.get_database', lambda *_: pytest.fail('DB accessed'))
    assert main(['--no-embeddings']) == 1
    finder = RelatedSkillFinder(ReferenceGraph(status='unavailable'), profile(), relation_settings({}))
    assert finder.find({'description': '3D-Bilder rendern erforderlich.'}) == ([], [])


def related_vacancy():
    related, context = find('3D-Bilder rendern erforderlich.')
    return dict(ref='example', title='Grafikdesigner (m/w/d)', employer='Beispiel GmbH',
                matched_skills=[], related_skills=related, relation_context_skills=context)


def test_german_letter_claims_only_declared_related_skill_and_separates_advice():
    vacancy = related_vacancy()
    letter = build_letter(vacancy, {'candidate': {'full_name': 'Alex Beispiel'}}, '2026-10-18')
    text = letter_html(letter)
    assert '3D-Modellierung' in text and '3D-Bilder rendern' not in text
    assert 'Auch meine Kenntnisse im Bereich 3D-Modellierung könnten' in text
    assert 'ESCO' not in text and 'ENTWURF' not in text and not re.search('[А-Яа-яЁё]', text)
    assert letter['status'] == 'ready' and not letter['skills']
    advice = recommendations_html(letter)
    assert '3D-Bilder rendern' in advice and 'Это не подтверждение' in advice
    vacancy['related_skills'][0]['profile_evidence'][0]['status'] = 'project_evidence'
    assert not build_letter(vacancy, {}, '2026-10-18')['related_skills']


def test_broad_dkz_or_wrong_direction_is_not_printed_even_if_marked_eligible():
    for kind, source, traversal in [('parent_id', 'dkz', 'forward'),
                                     ('narrowerSkill', 'esco', 'forward')]:
        vacancy = related_vacancy()
        vacancy['related_skills'][0]['relation_path'][0].update(type=kind, source=source, traversal=traversal)
        assert not build_letter(vacancy, {}, '2026-10-18')['related_skills']


@pytest.mark.parametrize('change', ['endpoints', 'source', 'unknown_context'])
def test_letter_rechecks_evidence_and_graph_endpoints(change):
    vacancy = related_vacancy()
    item = vacancy['related_skills'][0]
    if change == 'endpoints':
        item['relation_path'][0]['from'] = 'unrelated-concept'
    elif change == 'source':
        item['target_concept']['source'] = 'dkz'
    else:
        item['job_evidence'][0]['modality'] = 'unknown'
    assert not build_letter(vacancy, {}, '2026-10-18')['related_skills']


def test_report_retains_relation_provenance_but_removes_private_profile_fields():
    vacancy = related_vacancy()
    vacancy['related_skills'][0]['profile_evidence'][0].update(raw_profile='PRIVATE', secret='SECRET')
    row = _report_json({'results': [vacancy]})['results'][0]
    assert row['related_skills'][0]['relation_path']
    assert row['related_skills'][0]['profile_evidence'][0]['source_path'] == 'skills[0]'
    assert 'PRIVATE' not in json.dumps(row) and 'SECRET' not in json.dumps(row)


def test_related_skill_roundtrips_pdf_and_styled_rtf(tmp_path):
    pytest.importorskip('weasyprint')
    if not all(shutil.which(p) for p in ('pandoc', 'pdftotext', 'pdfinfo')):
        pytest.skip('Document verification programs not installed')
    letter = build_letter(related_vacancy(), {'candidate': {'full_name': 'Alex Beispiel'}}, '2026-10-18')
    prefix = tmp_path / 'Anschreiben'
    DocumentRenderer().render(letter_html(letter), prefix)
    for command in (['pdftotext', str(prefix.with_suffix('.pdf')), '-'],
                    ['pandoc', '-f', 'rtf', '-t', 'plain', str(prefix.with_suffix('.rtf'))]):
        text = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout
        assert '3D-Modellierung' in text and 'hilfreich sein' in text
        assert '3D-Bilder rendern' not in text and not re.search('[А-Яа-яЁё]', text)
    assert r'\stylesheet' in prefix.with_suffix('.rtf').read_text()
    info = subprocess.run(['pdfinfo', str(prefix.with_suffix('.pdf'))],
                          capture_output=True, text=True, check=True, timeout=30).stdout
    assert re.search(r'Pages:\s+1\b', info)
