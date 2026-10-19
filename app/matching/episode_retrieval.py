"""Rank project facts; similarity and shared terms never certify a requirement."""
from __future__ import annotations

import math
import re
from collections import Counter

from app.matching.intelligence_data import digest, text_key
from app.matching.local_evidence import EvidenceMatcher

RETRIEVAL_VERSION = 'episode-retrieval-v1'
STOPWORDS = set('und oder die der das den dem des ein eine einer einem einen von für mit in im am an '
                'auf aus als zu zur zum sie du ihr ihre dein deine wir ist sind werden sowie '
                'kenntnisse erfahrung guten gute sehr über the and of to with'.split())
TASK_PATTERNS = {
    'diagnosis': r'fehlersuche|fehleranalyse|ursachenanalyse|troubleshoot|root.cause|lokalisier|diagnos',
    'testing': r'testfäll|testfälle|testcase|teststrateg|testumgebung|testspezifikation|softwaretest|funktional.{0,10}test|test von|testläuf',
    'monitoring': r'monitoring|überwach|metriken|metrics|dashboards?',
    'requirements': r'anforderungsanalyse|requirements|user stories|softwareanforderung|anforderungsmanagement',
    'deployment': r'deploy|rollout|helm charts',
    'support': r'support|schulung|anwenderbetreuung|kundenbetreuung',
    'documentation': r'dokumentation|documentation|benutzerhandb',
    'learning': r'einarbeitung|einarbeiten|erlernen',
}


def words(text: str) -> set[str]:
    return {w for w in text_key(text).split() if len(w) >= 2 and w not in STOPWORDS}


def tasks(text: str) -> set[str]:
    return {name for name, pattern in TASK_PATTERNS.items() if re.search(pattern, text, re.I)}


def units_for(episodes: list[dict]) -> list[dict]:
    units = []
    for episode in episodes:
        for kind in ('actions', 'technologies'):
            units.extend({'episode_id': episode['id'], 'kind': kind, 'fact': f,
                          'text': f['value']} for f in episode[kind])
        # Results remain separate project facts, never attached to every action.
        units.extend({'episode_id': episode['id'], 'kind': 'project_result', 'fact': r['fact'],
                      'text': r['fact']['value']} for r in episode['results'])
    return units


def validate_requirements(job: dict, rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        source = row.get('source', 'description')
        if source != 'description':
            raise ValueError('Requirement source must be description')
        start, end = row['text_span']
        if not (0 <= start < end <= len(job['description'])) or job['description'][start:end] != row['quote']:
            raise ValueError('Requirement quote/span does not match vacancy description')
        if row.get('modality', 'unknown') not in {'unknown', 'required', 'preferred', 'negated', 'irrelevant'}:
            raise ValueError('Unknown requirement modality')
        result.append({**row, 'id': 'requirement:' + digest([job['ref'], source, start, end])[:16],
                       'job_ref': job['ref'], 'description_sha256': digest(job['description'])})
    return result


def extract_requirements(job: dict, matcher: EvidenceMatcher) -> list[dict]:
    """Keep whole list items, including unmapped tasks and exact source offsets."""
    text = job['description']
    mentions = matcher.extract(text)
    rows, section, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        header = EvidenceMatcher._header_role(line)
        clean = re.sub(r'[#*:]', '', line).strip().casefold()
        if re.fullmatch(r'(?:dein|ihr|euer) profil', clean):
            header = 'requirement'
        elif re.fullmatch(r'(?:(?:deine|ihre|eure) )?aufgaben', clean):
            header = 'task'
        if header:
            section = header
            offset += len(line)
            continue
        bullet = re.match(r'\s*(?:[-*•●▪◦]|\d+[.)])\s+', line)
        local = [m for m in mentions if offset <= m['text_span'][0] < offset + len(line)]
        eligible = section in {'requirement', 'task'} and bool(bullet)
        eligible |= any(m['modality'] in {'required', 'preferred', 'negated'} for m in local)
        if eligible and section != 'benefit':
            left = offset + (bullet.end() if bullet else len(line) - len(line.lstrip()))
            right = offset + len(line.rstrip())
            if right > left:
                quote = text[left:right]
                # Whole-item status is kept mixed if clauses disagree.
                modalities = {m['modality'] for m in local if m['modality'] != 'unknown'}
                modality = next(iter(modalities)) if len(modalities) == 1 else 'unknown'
                if not modalities:
                    modality, _ = EvidenceMatcher._modality(quote, 0, len(quote))
                    if modality == 'unknown' and section == 'requirement':
                        modality = 'required'
                rows.append({'quote': quote, 'text_span': [left, right], 'source': 'description',
                             'modality': modality, 'context_role': section,
                             'extraction': 'deterministic_list_item', 'mixed_modality': len(modalities) > 1})
        offset += len(line)
    return validate_requirements(job, rows)


class EpisodeRetriever:
    """Return independent lexical/MiniLM lists and their union for inspection."""

    def __init__(self, episodes: list[dict], terms: list[dict], vectors=None):
        self.episodes = {e['id']: e for e in episodes}
        self.units = units_for(episodes)
        self.matcher = EvidenceMatcher([t for t in terms if t.get('kind') == 'skill'])
        self.vectors = vectors
        self.unit_words = [words(u['text']) for u in self.units]
        self.unit_concepts = [self.concepts(u['text']) | {'task:' + t for t in tasks(u['text'])}
                              for u in self.units]
        counts = Counter(w for ws in self.unit_words for w in ws)
        self.idf = {w: math.log(1 + (len(self.units) + 1) / (count + 1)) for w, count in counts.items()}

    def concepts(self, text: str) -> set[str]:
        return {m['concept_id'] for m in self.matcher.extract(text)
                if not m['needs_review'] and m['modality'] not in {'negated', 'irrelevant'}}

    def compare(self, requirements: list[dict], top_k: int = 3) -> list[dict]:
        similarities = (self.vectors.similarities([r['quote'] for r in requirements],
                                                [u['text'] for u in self.units])
                        if self.vectors else None)
        rows = []
        for index, requirement in enumerate(requirements):
            query = requirement['quote']
            qw = words(query)
            qc = self.concepts(query) | {'task:' + t for t in tasks(query)}
            denominator = sum(self.idf.get(w, math.log(2 + len(self.units))) for w in qw) or 1
            lexical = []
            for uw, uc in zip(self.unit_words, self.unit_concepts, strict=True):
                lexical.append(.8 * sum(self.idf[w] for w in qw & uw) / denominator
                               + .2 * len(qc & uc) / max(1, len(qc)))
            baseline = self._rank(lexical, top_k, positive_only=True)
            semantic = self._rank(similarities[index], top_k) if similarities is not None else []
            union = list(dict.fromkeys(c['episode_id'] for c in baseline + semantic))
            rows.append({'requirement': requirement, 'lexical': baseline, 'minilm': semantic,
                         'union_episode_ids': union,
                         'minilm_added': [c['episode_id'] for c in semantic
                                          if c['episode_id'] not in {b['episode_id'] for b in baseline}],
                         'evidence': [self._evidence(requirement, eid, baseline, semantic) for eid in union]})
        return rows

    def _rank(self, scores, top_k: int, positive_only: bool = False) -> list[dict]:
        best = {}
        for i, (unit, score) in enumerate(zip(self.units, scores, strict=True)):
            eid = unit['episode_id']
            if positive_only and score <= 0:
                continue
            if eid not in best or score > best[eid]['score']:
                best[eid] = {'episode_id': eid, 'score': float(score), 'unit_index': i,
                             'matched_fact': unit['fact'], 'fact_kind': unit['kind']}
        return sorted(best.values(), key=lambda c: (-c['score'], c['episode_id']))[:top_k]

    def _evidence(self, requirement: dict, eid: str, lexical: list, semantic: list) -> dict:
        episode = self.episodes[eid]
        lexical_hit = next((c for c in lexical if c['episode_id'] == eid), None)
        semantic_hit = next((c for c in semantic if c['episode_id'] == eid), None)
        hit = lexical_hit or semantic_hit
        matched = hit['matched_fact']
        common_tasks = tasks(requirement['quote']) & tasks(matched['value'])
        exact_action = (hit['fact_kind'] == 'actions'
                        and text_key(requirement['quote']) == text_key(matched['value']))
        excluded = requirement.get('modality') in {'negated', 'irrelevant'}
        episode_concepts = set().union(*(self.unit_concepts[i] for i, unit in enumerate(self.units)
                                        if unit['episode_id'] == eid))
        unconfirmed = sorted({m['quote'] for m in self.matcher.extract(requirement['quote'])
                              if m['concept_id'] not in episode_concepts})
        relation = 'unknown'
        if not excluded and exact_action:
            relation = 'direct'
        elif not excluded and common_tasks and hit['fact_kind'] == 'actions':
            relation = 'transferable'
        return {'episode_id': eid, 'relation': relation,
                'assessment_origin': 'deterministic_rule_not_human_expert',
                'requires_review': not exact_action or excluded,
                'confirmed_part': matched,
                'confirmed_scope': 'historical_project_fact_only',
                'task_overlap': sorted(common_tasks),
                'unconfirmed_requirement_terms': unconfirmed,
                'retrieval_basis': [name for name, value in [('lexical', lexical_hit), ('minilm', semantic_hit)] if value],
                'unknown': ([f"В эпизоде не найдены термины требования: {', '.join(unconfirmed)}."]
                            if unconfirmed else []) + ([] if exact_action and not excluded else
                            ['Полное требование не подтверждено одним совпадением.']) + [
                            'Текущий уровень, глубина владения и актуальность опыта неизвестны.',
                            'Новая технология/отрасль, квалификация и результат переноса не следуют из сходства.'],
                'requirement_use': 'not_required_or_benefit' if excluded else 'needs_fact_review',
                'outcomes': episode['results'], 'outcome_scope': 'project; no automatic action/result link',
                'period': episode.get('timeframe'), 'recall': episode.get('confidence')}
