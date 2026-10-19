"""Experimental requirement-to-fact retrieval, separate from confirmed matching."""
from __future__ import annotations

import math
import re

import numpy as np

from app.matching.episode_retrieval import extract_requirements, tasks, validate_requirements, words
from app.matching.intelligence_data import digest
from app.matching.local_evidence import EvidenceMatcher

VERSION = 'experimental-skill-matching-v1'
DEFAULTS = {'enabled': False, 'top_k': 3, 'min_similarity': 0.5,
            'max_requirements': 100, 'max_profile_facts': 2000, 'max_fact_chars': 12000,
            'max_requirement_chars': 4000}
_STRUCTURED = re.compile(r'\b(?:deutsch|englisch)\w*\b|\b(?:führerschein|fahrerlaubnis|'
                         r'staplerschein|studium|ausbildung|hochschulabschluss|zertifikat|'
                         r'zertifizierung|cefr|driver.s license|degree|диплом|водительские права)\b', re.I)


def settings_for(settings: dict, *, enabled: bool | None = None, env: str | None = None) -> dict:
    raw = settings.get('experimental_skill_matching', {})
    if not isinstance(raw, dict):
        raise ValueError('experimental_skill_matching must be an object')
    value = {**DEFAULTS, **raw}
    if env and env.strip():
        if env.strip().lower() not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValueError('JOB_INTELLIGENCE_SKILL_MATCHING must be true or false')
        value['enabled'] = env.strip().lower() in {'true', '1', 'yes'}
    if enabled is not None:
        value['enabled'] = enabled
    if type(value['enabled']) is not bool:
        raise ValueError('experimental_skill_matching.enabled must be boolean')
    for key, upper in (('top_k', 10), ('max_requirements', 1000), ('max_profile_facts', 10000),
                       ('max_fact_chars', 100000), ('max_requirement_chars', 20000)):
        if type(value[key]) is not int or not 1 <= value[key] <= upper:
            raise ValueError(f'experimental_skill_matching.{key} must be in 1..{upper}')
    threshold = value['min_similarity']
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('experimental_skill_matching.min_similarity must be in [0,1]')
    return value


def requirements_for(job: dict, matcher: EvidenceMatcher) -> list[dict]:
    """Extend existing exact list-item extraction to prose in explicit sections."""
    rows = extract_requirements(job, matcher)
    occupied = [r['text_span'] for r in rows]
    excluded_spans = []
    section, offset = None, 0
    for line in job['description'].splitlines(keepends=True):
        heading = matcher._header_role(line)
        cleaned = re.sub(r'[#*:]', '', line).strip().casefold()
        if re.fullmatch(r'(?:dein|ihr|euer) profil', cleaned):
            heading = 'requirement'
        elif re.fullmatch(r'(?:(?:deine|ihre|eure) )?aufgaben', cleaned):
            heading = 'task'
        if heading:
            section = heading
        elif re.fullmatch(r'\s*(?:kontakt|bewerbung|ansprechpartner|datenschutz|contact|apply)\s*:?[\s]*',
                          cleaned, re.I):
            section = 'contact'
        elif section not in {'benefit', 'contact'}:
            for match in re.finditer(r'[^.!?\n]+(?:[.!?]|$)', line):
                left = offset + match.start() + len(match[0]) - len(match[0].lstrip(' \t-•●▪◦'))
                right = offset + match.end() - len(match[0]) + len(match[0].rstrip())
                quote = job['description'][left:right]
                cue = re.search(r'\b(?:erforderlich|vorausgesetzt|wünschenswert|idealerweise|'
                                r'erfahrung|kenntnisse|experience|required|must|знания|опыт)\b', quote, re.I)
                overlap = any(left < end and start < right for start, end in occupied)
                if quote and not overlap and (section in {'requirement', 'task'} or cue):
                    modality, _ = matcher._modality(quote, 0, len(quote))
                    if modality == 'unknown' and section == 'requirement':
                        modality = 'required'
                    rows.append({'quote': quote, 'text_span': [left, right], 'source': 'description',
                                 'modality': modality, 'context_role': section or 'unknown',
                                 'extraction': 'experimental_prose_rule', 'mixed_modality': False})
        if section == 'contact':
            excluded_spans.append((offset, offset + len(line)))
        offset += len(line)
    rows = [r for r in rows if not any(r['text_span'][0] < end and start < r['text_span'][1]
                                      for start, end in excluded_spans)]
    return sorted(validate_requirements(job, rows), key=lambda r: r['text_span'])


class SkillMatcher:
    """Return auditable candidates; no result asserts that a requirement is met."""

    def __init__(self, corpus: dict, settings: dict, vectors, terms=()):
        self.corpus, self.settings, self.vectors = corpus, settings, vectors
        self.facts = corpus['facts']
        if not self.facts:
            if any(item.get('reason') == 'unresolved_usage_restriction'
                   for item in corpus.get('withheld_projects', [])):
                raise ValueError(
                    'Professional facts withheld by profile usage restrictions. '
                    'Pass --usage-policy with an existing user-authorized resolution '
                    'bound to this profile hash; do not remove restrictions to bypass this check.')
            raise ValueError('No permitted professional facts available for experimental matching')
        if len(self.facts) > settings['max_profile_facts']:
            raise ValueError('Professional facts exceed experimental max_profile_facts')
        if any(len(f['source_fact']['value']) > settings['max_fact_chars'] for f in self.facts):
            raise ValueError('Professional fact exceeds experimental max_fact_chars')
        self.matcher = EvidenceMatcher([t for t in terms if t.get('kind') == 'skill'])
        self.word_sets = [words(f['source_fact']['value']) for f in self.facts]
        self.concepts = [self._concepts(f['source_fact']['value']) for f in self.facts]

    def _concepts(self, text):
        return {m['concept_id'] for m in self.matcher.extract(text)
                if not m['needs_review'] and m['modality'] not in {'negated', 'irrelevant'}}

    def compare(self, job: dict) -> dict:
        extracted = requirements_for(job, self.matcher)
        selected = extracted[:self.settings['max_requirements']]
        eligible = [r for r in selected if r['modality'] not in {'negated', 'irrelevant'}
                    and not r.get('mixed_modality')
                    and not _STRUCTURED.search(r['quote'])
                    and len(r['quote']) <= self.settings['max_requirement_chars']]
        scores = self.vectors.similarities([r['quote'] for r in eligible]) if eligible else []
        scores = np.asarray(scores, dtype=float)
        if eligible and (scores.shape != (len(eligible), len(self.facts)) or not np.isfinite(scores).all()
                         or np.any(np.abs(scores) > 1.00001)):
            raise ValueError('Invalid experimental similarity matrix')
        lookup = {r['id']: np.clip(row, -1, 1) for r, row in zip(eligible, scores, strict=True)}
        rows = [self._row(r, lookup.get(r['id'])) for r in selected]
        considered = [r for r in rows if r['status'] not in {'excluded', 'structured_check'}]
        # Every considered item remains in the denominator, including no-match
        # and oversized items. Truncated extraction gets no aggregate score.
        signal = (sum(r['rank_signal'] for r in considered) / len(considered)
                  if considered and len(selected) == len(extracted) else None)
        result = {'ref': job['ref'], 'title': job['title'], 'role': job.get('role', ''),
                  'location': job.get('location', ''), 'url': job.get('url', ''),
                  'source_input_hash': job['source_input_hash'], 'requires_review': True,
                  'assessment_origin': 'experimental_retrieval_not_verified',
                  'semantic_rank_signal': round(signal, 6) if signal is not None else None,
                  'extraction_complete': len(selected) == len(extracted),
                  'requirements_total': len(extracted), 'requirements_processed': len(rows),
                  'requirements_considered': len(considered),
                  'candidate_requirements': sum(bool(r['candidates']) for r in rows),
                  'requirements': rows}
        result['input_hash'] = digest([VERSION, self.corpus['input_hash'], self.settings,
                                       self.vectors.identity, result])
        return result

    def _row(self, requirement, scores):
        row = {'requirement': requirement, 'requires_review': True,
               'status': 'no_candidate', 'rank_signal': 0.0, 'candidates': []}
        if requirement['modality'] in {'negated', 'irrelevant'}:
            row['status'] = 'excluded'
            return row
        if _STRUCTURED.search(requirement['quote']):
            row['status'] = 'structured_check'
            return row
        if scores is None:
            row['status'] = 'mixed_modality' if requirement.get('mixed_modality') else 'input_limit'
            return row
        text = requirement['quote']
        qw, qc = words(text), self._concepts(text)
        lexical = [(len(qw & ws) / max(1, len(qw)) + bool(qc & cs))
                   for ws, cs in zip(self.word_sets, self.concepts, strict=True)]
        semantic_ids = sorted(range(len(self.facts)), key=lambda i: (-scores[i], self.facts[i]['id']))
        lexical_ids = sorted((i for i, score in enumerate(lexical) if score > 0),
                             key=lambda i: (-lexical[i], self.facts[i]['id']))
        # Independent retrieval channels avoid adding incomparable raw scores.
        ids = list(dict.fromkeys(semantic_ids[:self.settings['top_k']] + lexical_ids[:self.settings['top_k']]))
        for index in ids:
            if scores[index] < self.settings['min_similarity'] and lexical[index] <= 0:
                continue
            value = self.facts[index]
            fact_text = value['source_fact']['value']
            basis = []
            if index in semantic_ids[:self.settings['top_k']] and scores[index] >= self.settings['min_similarity']:
                basis.append('embedding')
            if index in lexical_ids[:self.settings['top_k']]:
                basis.append('lexical')
            overlap = sorted(qw & self.word_sets[index])
            row['candidates'].append({**value, 'cosine': round(float(scores[index]), 6),
                                      'retrieval_basis': basis, 'overlap_terms': overlap,
                                      'shared_tasks': sorted(tasks(text) & tasks(fact_text)),
                                      'unmatched_requirement_terms': sorted(qw - self.word_sets[index]),
                                      'requires_review': True, 'requirement_met': None,
                                      'relation': 'task_overlap' if value['kind'] == 'action' and tasks(text) & tasks(fact_text)
                                      else 'term_overlap' if overlap or qc & self.concepts[index]
                                      else 'semantic_candidate',
                                      'current_proficiency': 'unknown'})
        if row['candidates']:
            row['status'] = 'candidates_for_review'
            row['rank_signal'] = max(
                (max(0.0, c['cosine']) for c in row['candidates'] if 'embedding' in c['retrieval_basis']),
                default=0.0)
        return row


class SkillRanking:
    """Bounded independent top; never reuse the main matcher's score/status."""

    def __init__(self, limit: int):
        self.limit, self.count, self.results = limit, 0, []
        self.requirements = 0
        self.no_candidate = 0

    def add(self, result: dict):
        self.count += 1
        self.requirements += result['requirements_total']
        self.no_candidate += sum(r['status'] == 'no_candidate' for r in result['requirements'])
        self.results.append(result)
        self.results.sort(key=lambda r: (r['semantic_rank_signal'] is None,
                                        -(r['semantic_rank_signal'] or 0), r['ref']))
        del self.results[self.limit:]
