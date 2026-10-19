"""Experimental matching preserves sources and never certifies requirements."""
import json
from copy import deepcopy

import numpy as np
import pytest

from app.matching.intelligence_data import digest, normalize_job
from app.matching.skill_facts import build_facts
from app.matching.skill_matching import (
    DEFAULTS, SkillMatcher, SkillRanking, requirements_for, settings_for,
)
from app.matching.local_evidence import EvidenceMatcher


def source(raw):
    return {'raw': raw, 'path': 'synthetic.json', 'sha256': digest(raw)}


def corpus(*values):
    return build_facts(source({'skills': list(values)}))


class FixedVectors:
    identity = {'model': 'synthetic'}

    def __init__(self, scores):
        self.scores = scores
        self.calls = 0

    def similarities(self, queries):
        self.calls += 1
        return np.asarray(self.scores)


def job(text):
    return normalize_job({'ref': 'synthetic', 'title': 'Engineer', 'description': text})


def test_fact_corpus_keeps_exact_sources_and_excludes_wishes_and_private_data():
    raw = {'skills': {'cloud': ['Concourse', 'Lerne Azure', 'kein Terraform'],
                      'learning_goals': ['HIDDEN_WISH'],
                      'notes': ['HIDDEN_NOTE'],
                      'structured': [{'label': 'HIDDEN_SKILL', 'private': True}]},
           'application_contact': {'email': 'HIDDEN_CONTACT'},
           'experience_projects': [
               {'name': 'Visible project', 'timeframe': '2020–2022',
                'responsibilities': ['Backlog ohne Datenverlust verarbeitet.'],
                'deliverables': ['Stable service'], 'tech_stack': ['Java']},
               {'private': True, 'responsibilities': ['HIDDEN_ACTION'], 'tech_stack': ['HIDDEN_STACK']}],
           'target_roles': ['HIDDEN_TARGET'],
           'interview_story_bank': {'best_stories': [{'text': 'HIDDEN_STORY'}]}}
    original = deepcopy(raw)
    result = build_facts(source(raw))
    serialized = json.dumps(result)
    assert 'HIDDEN_' not in serialized
    assert all(value not in serialized for value in ('Lerne Azure', 'kein Terraform'))
    action = next(f for f in result['facts'] if f['kind'] == 'action')
    assert action['source_fact']['value'] == 'Backlog ohne Datenverlust verarbeitet.'
    assert action['source_fact']['pointer'] == '/experience_projects/0/responsibilities/0'
    assert action['timeframe']['value'] == '2020–2022'
    result_fact = next(f for f in result['facts'] if f['kind'] == 'project_result')
    assert result_fact['result_scope'] == 'project' and result_fact['action_link'] is None
    assert raw == original


def test_restriction_quarantines_professional_facts_until_source_bound_resolution():
    s = source({'skills': ['HIDDEN_SKILL'], 'candidate': {'ethics_constraints': {'do_not_mention': ['private']}},
                'experience_projects': [{'responsibilities': ['HIDDEN_ACTION']}]})
    assert build_facts(s)['facts'] == []
    with pytest.raises(ValueError, match='Pass --usage-policy'):
        SkillMatcher(build_facts(s), DEFAULTS, None)
    with pytest.raises(ValueError, match='exact source hash'):
        build_facts(s, policy={'profile_sha256': 'old'})
    permitted = build_facts(s, policy={'profile_sha256': s['sha256'],
                                     'basis': 'user_statement', 'resolution': 'listed_projects_permitted'})
    assert len(permitted['facts']) == 2


def test_stack_only_legacy_and_compact_profiles_are_supported_without_supplement_skills():
    s = source({'experience_projects': [{'tech_stack': ['Kafka'], 'timeframe': '2021'}],
                'Berufserfahrung': [{'Aufgaben': ['Testpläne erstellt'], 'Jahr': '2020', 'skills': ['Python']}],
                'projects': [{'responsibilities': ['Prepared release notes']}],
                'TechnischeKenntnisse': {'platform': ['Linux']}})
    supplement = {**source({'skills': ['HIDDEN_SUPPLEMENT'], 'languages': {'de': 'B2'}}), 'path': 'supplement'}
    result = build_facts(s, supplement)
    assert len(result['facts']) == 5
    assert 'HIDDEN_SUPPLEMENT' not in json.dumps(result)
    assert result['languages']['de']['cefr'] == 'B2'


def test_unknown_terms_and_prose_are_extracted_with_exact_spans_and_benefits_excluded():
    j = job('Dein Profil\nErfahrung mit UnbekanntX erforderlich. Kenntnisse in SystemY.\n'
            'Deine Aufgaben\nDu betreust Testsysteme.\nBenefits\n- UnbekanntX-Schulungen werden angeboten.')
    rows = requirements_for(j, EvidenceMatcher([]))
    assert len(rows) == 3
    assert not any('angeboten' in r['quote'] for r in rows)
    for r in rows:
        a, b = r['text_span']
        assert j['description'][a:b] == r['quote']


def test_contact_details_are_not_mistaken_for_requirements():
    rows = requirements_for(job('Dein Profil\n- Linux\nKontakt\n- Python Fragen: private@example.test'),
                            EvidenceMatcher([]))
    assert len(rows) == 1 and rows[0]['quote'] == 'Linux'


def test_degrees_language_and_licenses_do_not_receive_embedding_scores():
    vectors = FixedVectors([])
    matcher = SkillMatcher(corpus('Python'), DEFAULTS, vectors)
    result = matcher.compare(job('Dein Profil\n- Deutsch C1 erforderlich.\n- Führerschein B erforderlich.'))
    assert vectors.calls == 0
    assert all(r['status'] == 'structured_check' for r in result['requirements'])
    assert result['requirements_considered'] == 0 and result['semantic_rank_signal'] is None


def test_fact_credentials_are_quarantined_without_recording_their_values():
    result = corpus('Python', 'api_key=EXAMPLE_NOT_A_REAL_KEY')
    assert len(result['facts']) == 1
    assert 'EXAMPLE_NOT_A_REAL_KEY' not in json.dumps(result)


def test_machine_learning_is_a_skill_while_learning_wish_is_not():
    result = corpus('Machine Learning', 'Learning Python')
    assert [f['source_fact']['value'] for f in result['facts']] == ['Machine Learning']


def test_high_cosine_only_marks_a_semantic_candidate_and_preserves_gap_terms():
    matcher = SkillMatcher(corpus('Kundenkonten verwaltet'), DEFAULTS, FixedVectors([[.9999]]))
    result = matcher.compare(job('Dein Profil\n- PostgreSQL-Cluster betreiben'))
    candidate = result['requirements'][0]['candidates'][0]
    assert candidate['relation'] == 'semantic_candidate'
    assert candidate['requirement_met'] is None and candidate['requires_review']
    assert 'postgresql' in candidate['unmatched_requirement_terms']
    assert result['requires_review']
    assert 'score' not in result


def test_low_similarity_abstains_and_does_not_claim_lack_of_experience():
    matcher = SkillMatcher(corpus('Linux'), DEFAULTS, FixedVectors([[.1]]))
    result = matcher.compare(job('Dein Profil\n- Buchhaltung'))
    row = result['requirements'][0]
    assert row['status'] == 'no_candidate' and row['candidates'] == []
    assert result['semantic_rank_signal'] == 0


def test_negation_mixed_items_and_oversized_requirements_are_never_embedded():
    vectors = FixedVectors([])
    matcher = SkillMatcher(corpus('Python'), {**DEFAULTS, 'max_requirement_chars': 10}, vectors)
    result = matcher.compare(job('Dein Profil\n- Python ist nicht erforderlich.\n- Sehr lange Anforderung'))
    assert vectors.calls == 0
    assert [r['status'] for r in result['requirements']] == ['excluded', 'input_limit']
    assert result['semantic_rank_signal'] == 0


def test_partial_extraction_never_produces_aggregate_signal():
    matcher = SkillMatcher(corpus('Linux'), {**DEFAULTS, 'max_requirements': 1}, FixedVectors([[.8]]))
    result = matcher.compare(job('Dein Profil\n- Linux\n- Python'))
    assert result['requirements_total'] == 2 and result['requirements_processed'] == 1
    assert not result['extraction_complete'] and result['semantic_rank_signal'] is None


def test_invalid_similarity_matrix_is_rejected():
    matcher = SkillMatcher(corpus('Linux'), DEFAULTS, FixedVectors([[float('nan')]]))
    with pytest.raises(ValueError, match='similarity matrix'):
        matcher.compare(job('Dein Profil\n- Linux'))


@pytest.mark.parametrize('values', [{'enabled': 'yes'}, {'top_k': 0}, {'min_similarity': float('nan')},
                                    {'min_similarity': True}, {'max_profile_facts': -1}])
def test_invalid_config(values):
    with pytest.raises(ValueError):
        settings_for({'experimental_skill_matching': values})


def test_switch_precedence():
    settings = {'experimental_skill_matching': {'enabled': True}}
    assert not settings_for(settings, env='false')['enabled']
    assert settings_for(settings, env='false', enabled=True)['enabled']
    assert settings_for(settings, env='')['enabled']
    with pytest.raises(ValueError):
        settings_for({}, env='invalid')


def test_main_matcher_results_and_hashes_ignore_experimental_configuration():
    from test_job_intelligence import _matcher, _settings, _job
    baseline, _ = _matcher(settings=_settings())
    changed, _ = _matcher(settings={**_settings(), 'experimental_skill_matching': DEFAULTS})
    vacancy = _job('synthetic', 'Linux erforderlich.')
    assert baseline.rank([vacancy]) == changed.rank([vacancy])


def test_ranking_is_bounded_stable_and_keeps_unscored_at_end():
    ranking = SkillRanking(2)
    for ref, score in [('z', None), ('c', .5), ('b', .8), ('a', .8)]:
        ranking.add({'ref': ref, 'semantic_rank_signal': score, 'requirements_total': 1, 'requirements': []})
    assert ranking.count == 4
    assert [r['ref'] for r in ranking.results] == ['a', 'b']
