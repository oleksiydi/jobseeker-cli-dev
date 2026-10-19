"""Evidence-based ranking and adjacent-role explanations, without generative calls."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.matching.constraints import evaluate_constraints
from app.matching.intelligence_data import DerivedCache, digest, text_key
from app.matching.local_evidence import EvidenceMatcher, MODEL_VERSION
from app.matching.search import lexical_overlap_score

ENGINE_VERSION = 'job-intelligence-v1'


def _literal_terms(profile: dict) -> list[dict]:
    terms = {}
    for fact in profile.get('skills', []):
        label = re.sub(r'\s*\([^)]*\)\s*$', '', fact['label']).strip()
        if (not label or len(label.split()) > 4 or len(label) < 2 or len(label) > 90
                or re.search(r'\b(?:und|mit|arbeiten|administrieren|betreuen|bearbeiten|unterstützen|bedienen|organisieren)\b', label, re.I)):
            continue
        pieces = [label]
        if re.fullmatch(r'[\w+#.]+/[\w+#.]+', label):
            pieces.extend(label.split('/'))
        for piece in pieces:
            if len(piece) < 2:
                continue
            key = 'literal:' + text_key(piece)
            terms[key] = dict(id=key, label=piece, aliases=[], source='profile',
                              version=profile['profile_hash'], kind='skill')
    return list(terms.values())


def _role_score(profile: dict, job: dict, query: str) -> float:
    roles = profile.get('target_roles') or profile.get('roles', [])
    if query:
        roles = [query, *roles]
    return max((lexical_overlap_score(text_key(role), text_key(value))
                for role in roles for value in (job['title'], job['role'])), default=0.0)


class IntelligenceMatcher:
    def __init__(self, profile: dict, terms: list[dict], cache: DerivedCache, settings: dict, *, relations=None):
        self.profile, self.cache, self.settings = profile, cache, settings
        self.term_map = {t['id']: t for t in terms}
        self.occupations = defaultdict(list)
        for term in terms:
            if term['kind'] == 'occupation':
                for label in [term['label'], *term.get('aliases', [])]:
                    self.occupations[text_key(label)].append({k: term[k] for k in ('id', 'label', 'source', 'version')})
        matching_terms = [t for t in terms if t['kind'] == 'skill']
        self.matcher = EvidenceMatcher(matching_terms)
        literals = {t['id']: t for t in _literal_terms(profile)}
        # A literal overlap can be explained without deciding that two external
        # classifications mean exactly the same thing. Keep ambiguous short
        # words out of this path; the source fact and original quote remain.
        unsafe_literals = {'go', 'spring', 'r', 'c', 'ad', 'office', 'server', 'support'}
        literals = {key: term for key, term in literals.items() if text_key(term['label']) not in unsafe_literals}
        for fact in profile.get('skills', []):
            for mention in self.matcher.extract(fact['label'], source='profile'):
                surface = mention['quote']
                if (text_key(surface) in unsafe_literals or len(surface) < 3
                        or mention['modality'] in {'negated', 'irrelevant'}):
                    continue
                key = 'literal:' + text_key(surface)
                literals[key] = dict(id=key, label=surface, aliases=[], source='profile',
                                     version=profile['profile_hash'], kind='skill')
        self.matcher.add_terms(list(literals.values()))
        matching_terms.extend(literals.values())
        self.term_map.update({t['id']: t for t in matching_terms})
        self.matcher_key = digest([ENGINE_VERSION, MODEL_VERSION, matching_terms])
        self.facts = defaultdict(list)
        for fact in profile.get('skills', []):
            # A learning wish or negative statement is not a declared competency.
            if re.search(r'\b(?:kein\w*|nicht|ohne|learning|lerne|изучаю|нет)\b', fact['label'], re.I):
                continue
            for evidence in self.matcher.extract(fact['label'], source='profile'):
                if not evidence['needs_review'] and evidence['modality'] not in {'negated', 'irrelevant'}:
                    self.facts[evidence['concept_id']].append({**fact, 'text_span': evidence['text_span']})
        self.counters = {'jobs_with_recognized_skills': 0, 'skill_mentions': 0,
                         'unknown_modality': 0, 'ambiguous_mentions': 0}
        self.relations = relations
        self.related_finder = None
        if relations is not None:
            from app.matching.reference_relations import RelatedSkillFinder, relation_settings
            self.related_finder = RelatedSkillFinder(relations, profile, relation_settings(settings))
            self.counters.update(jobs_with_related_skills=0, related_skills=0, relation_context_suggestions=0)

    def _evidence(self, job: dict) -> list[dict]:
        key = digest(['job-evidence', self.matcher_key, job['description']])
        evidence = self.cache.get(key)
        if evidence is None:
            evidence = self.matcher.extract(job['description'])
            self.cache.put(key, evidence)
        return evidence

    def rank(self, jobs: list[dict], semantic: dict[str, float] | None = None, query: str = '',
             *, model_identity: dict | None = None) -> list[dict]:
        results = []
        for job in jobs:
            evidence = self._evidence(job)
            grouped = defaultdict(list)
            unknowns = []
            for found in evidence:
                if found['modality'] in {'negated', 'irrelevant'}:
                    continue
                if found['needs_review']:
                    unknowns.append({'quote': found.get('context_quote') or found['quote'],
                                     'reason': found['reason'], 'candidate_id': found['concept_id']})
                    continue
                grouped[found['concept_id']].append(found)
            matched, missing, unconfirmed = [], [], []
            for concept_id, mentions in grouped.items():
                item = {'label': self.term_map[concept_id]['label'], 'concept_id': concept_id,
                        'job_evidence': mentions, 'profile_evidence': self.facts.get(concept_id, [])}
                if concept_id in self.facts:
                    matched.append(item)
                elif any(m['modality'] == 'required' for m in mentions):
                    missing.append(item)
                else:
                    unconfirmed.append(item)
            # Expose requirements outside the known vocabulary rather than
            # interpreting an empty extraction as absence of requirements.
            for clause in re.finditer(r'[^\n;.!?]+', job['description']):
                if not re.search(r'\b(?:zwingend|erforderlich|vorausgesetzt)\b', clause.group(), re.I):
                    continue
                modality, _ = self.matcher._modality(job['description'], *clause.span())
                if modality == 'required' and not any(clause.start() <= e['text_span'][0] < clause.end() for e in evidence):
                    unknowns.append({'quote': clause.group().strip(), 'reason': 'Explicit requirement outside recognized vocabulary',
                                     'source': 'description', 'text_span': list(clause.span())})
            if not job['description']:
                unknowns.append({'quote': '', 'reason': 'Details are not available yet; requirements cannot be checked'})
            unknowns = list({(u['quote'], u['reason']): u for u in unknowns}.values())
            constraints = evaluate_constraints(job, self.profile)
            modality_unknown = any(e['modality'] == 'unknown' for e in evidence)
            status = ('conflict' if any(c['status'] == 'conflict' for c in constraints) else
                      'needs_review' if unknowns or modality_unknown or any(c['status'] == 'unknown' for c in constraints) else 'no_known_conflict')
            role = _role_score(self.profile, job, query)
            similarity = max(0.0, min(1.0, (semantic or {}).get(job['ref'], 0.0)))
            skill_score = min(1.0, len({text_key(m['label']) for m in matched}) / self.settings['skill_evidence_target'])
            weights = self.settings['weights']
            components = {'role': role, 'skills': skill_score, 'semantic': similarity}
            active_weight = sum(value for k, value in weights.items() if semantic is not None or k != 'semantic')
            score = sum(weights[k] * value for k, value in components.items()) / active_weight
            penalty = min(self.settings['max_required_gap_penalty'], len(missing) * self.settings['required_gap_penalty'])
            explicit_required = [e for es in grouped.values() for e in es if e['modality'] == 'required']
            required_ids = {e['concept_id'] for e in explicit_required}
            components['required_coverage'] = len(required_ids & self.facts.keys()) / len(required_ids) if required_ids else None
            components['gap_penalty'] = penalty
            role_type = ('direct' if role >= self.settings['direct_role_threshold'] else
                         'adjacent_candidate' if matched else 'weak')
            result = {**job, 'score': round(100 * max(0.0, score - penalty), 2),
                      'status': status, 'role_type': role_type, 'scores': components,
                      'matched_skills': matched, 'missing_required': missing, 'unconfirmed_skills': unconfirmed,
                      'constraints': constraints, 'unknowns': unknowns,
                      'requirement_modality_unknown': modality_unknown,
                      'occupation_refs': self.occupations.get(text_key(job['role']), []),
                      'evidence': evidence,
                      'decision_input_hash': digest([ENGINE_VERSION, self.matcher_key, job['source_input_hash'],
                                                     self.profile['profile_hash'],
                                                     {k: v for k, v in self.settings.items()
                                                      if k not in {'output_language', 'experimental_skill_matching'}},
                                                     similarity, query,
                                                     model_identity])}
            if self.related_finder is not None:
                result['related_skills'], result['relation_context_skills'] = self.related_finder.find(result)
                result['reference_relations_status'] = self.relations.identity['status']
                result['decision_input_hash'] = digest([result['decision_input_hash'], self.relations.identity])
                self.counters['jobs_with_related_skills'] += bool(result['related_skills'])
                self.counters['related_skills'] += len(result['related_skills'])
                self.counters['relation_context_suggestions'] += len(result['relation_context_skills'])
            self.counters['jobs_with_recognized_skills'] += bool(grouped)
            self.counters['skill_mentions'] += len(evidence)
            self.counters['unknown_modality'] += sum(e['modality'] == 'unknown' for e in evidence)
            self.counters['ambiguous_mentions'] += sum(e['needs_review'] and e['modality'] != 'negated' for e in evidence)
            results.append(result)
        results.sort(key=lambda r: (r['status'] == 'conflict', -r['score'], r['ref']))
        return results


def profession_ranking(results: list[dict], limit: int = 10) -> list[dict]:
    """Group the ranked observations, without inventing unobserved occupations."""
    roles = {}
    for job in results:
        if job['status'] == 'conflict' or job['role_type'] == 'weak':
            continue
        role = job['role'] or job['title']
        key = text_key(role)
        if key not in roles:
            roles[key] = {'role': role, 'score': job['score'], 'best_ref': job['ref'],
                          'url': job['url'], 'status': job['status'], 'vacancies_observed': 0,
                          'role_type': job['role_type'],
                          'transferable_skills': list(dict.fromkeys(s['label'] for s in job['matched_skills']))}
        roles[key]['vacancies_observed'] += 1
        if job['role_type'] == 'direct':
            roles[key]['role_type'] = 'direct'
    return list(roles.values())[:limit]


def adjacent_roles(results: list[dict], limit: int = 5) -> list[dict]:
    """Observed roles with transferable evidence; never a promise of eligibility."""
    roles = {}
    direct_roles = {text_key(j['role'] or j['title']) for j in results
                    if j['role_type'] == 'direct' and j['status'] != 'conflict'}
    for job in results:
        role = job['role'] or job['title']
        role_key = text_key(role)
        if (job['status'] == 'conflict' or job['role_type'] != 'adjacent_candidate'
                or not job['matched_skills'] or role_key in roles or role_key in direct_roles):
            continue
        roles[role_key] = {'role': role, 'ref': job['ref'], 'url': job['url'],
                       'transferable_skills': list(dict.fromkeys(s['label'] for s in job['matched_skills'])),
                       'evidence': [{'label': s['label'], 'profile_evidence': s['profile_evidence'],
                                     'job_evidence': s['job_evidence']} for s in job['matched_skills']],
                       'missing_required': [s['label'] for s in job['missing_required']],
                       'unknown_requirements': list(dict.fromkeys(
                           [c['quote'] for c in job['constraints'] if c['status'] == 'unknown']
                           + [u['quote'] for u in job['unknowns'] if u['quote']])),
                       'reason': 'В наблюдаемой вакансии есть совпадения с указанными навыками. '
                                 'Переход в профессию, квалификацию и текущую готовность нужно проверить.'}
        if len(roles) >= limit:
            break
    return list(roles.values())


class RankingAccumulator:
    """Keep global top jobs and best evidence per observed role across batches."""

    def __init__(self, limit: int, *, retain_documents: bool = False):
        self.limit = limit
        self.retain_documents = retain_documents
        self.results = []
        self.roles = {}
        self.count = 0

    @staticmethod
    def _key(job):
        return job['status'] == 'conflict', -job['score'], job['ref']

    def add(self, ranked: list[dict]) -> None:
        self.count += len(ranked)
        excluded = {'evidence'} if self.retain_documents else {'description', 'evidence', 'source_data'}
        compact = [{k: v for k, v in job.items() if k not in excluded}
                   for job in ranked]
        self.results = sorted([*self.results, *compact], key=self._key)[:self.limit]
        for job in compact:
            job = {k: v for k, v in job.items() if k not in {'description', 'source_data'}}
            if job['status'] == 'conflict' or job['role_type'] == 'weak':
                continue
            key = text_key(job['role'] or job['title'])
            group = self.roles.setdefault(key, {'best': job, 'count': 0, 'direct': False})
            group['count'] += 1
            group['direct'] |= job['role_type'] == 'direct'
            if self._key(job) < self._key(group['best']):
                group['best'] = job

    def _best_roles(self):
        rows = [{**g['best'], 'role_type': 'direct' if g['direct'] else 'adjacent_candidate'}
                for g in self.roles.values()]
        return sorted(rows, key=self._key)

    def professions(self) -> list[dict]:
        rows = profession_ranking(self._best_roles())
        for row in rows:
            row['vacancies_observed'] = self.roles[text_key(row['role'])]['count']
        return rows

    def adjacent(self) -> list[dict]:
        return adjacent_roles(self._best_roles())
