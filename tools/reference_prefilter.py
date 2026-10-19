"""Conservative local reference recognition before taxonomy/synonym LLM calls.

Only complete known clauses can disappear from an extraction prompt. Unknown
words, ambiguous names and missing classification retain their original context.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.matching.intelligence_data import digest
from app.matching.local_evidence import EvidenceMatcher
from tools.taxonomy_transform import normalize_term_key, normalize_ws

VERSION = 'reference-prefilter-v1'
UNSAFE = {'go', 'r', 'c', 'ad', 'office', 'server', 'support', 'spring'}
GLUE = set(('und oder sowie mit in im am an auf aus von für der die das den dem des ein eine '
            'einer einem einen zu zur zum als auch sehr gute guten guter fundierte fundierten '
            'kenntnisse erfahrung erfahrungen erforderlich zwingend vorausgesetzt notwendig '
            'wünschenswert idealerweise nicht keine kein ohne von vorteil sie du ihr ihre '
            'bringen mit haben verfügen erwartet werden ist sind').split())


def stable(value):
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(stable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [stable(item) for item in value]
    return value


@dataclass
class ExtractionPlan:
    local_terms: list[dict]
    residual: str
    observations: list[dict]
    input_chars: int
    retained_chars: int
    removed_segments: int


class ReferenceIndex:
    """Keep concept identity, category evidence, ambiguity and dictionary hash."""

    def __init__(self, terms=(), runtime=None):
        runtime = runtime or {}
        runtime_version = digest(stable({key: runtime.get(key, {})
                                        for key in ('aliases', 'categories', 'ambiguous_aliases')}))
        self.records, self.aliases = {}, defaultdict(set)
        self.ambiguous = {normalize_term_key(v) for v in runtime.get('ambiguous_aliases', [])}
        categories = defaultdict(set)
        display = {}
        for category, labels in runtime.get('categories', {}).items():
            for label in labels:
                categories[normalize_term_key(label)].add(category)
                display[normalize_term_key(label)] = label

        def add_alias(alias, key):
            alias = normalize_term_key(alias)
            if len(alias) >= 3 and alias not in UNSAFE:
                self.aliases[alias].add(key)

        for term in terms:
            if term.get('kind') != 'skill':
                continue
            label = term['label']
            category = categories.get(normalize_term_key(label), set())
            if not category:
                if re.match(r'^(?:Programmiersprache|Betriebssystem|Datenbanksoftware)\s+', label):
                    category = {'stack'}
                elif re.match(r'^(?:Anwenden|Durchführen|Erstellen|Planen|Betreuen|Überwachen)\b', label, re.I):
                    category = {'skills'}
            key = str(term['id'])
            self.records[key] = {'canonical': label, 'category': next(iter(category)) if len(category) == 1 else None,
                                 'source': term.get('source'), 'version': term.get('version'), 'id': key}
            for alias in [label, *term.get('aliases', [])]:
                add_alias(alias, key)

        # A verified local category can classify a unique reference short name.
        for label, values in categories.items():
            if len(values) != 1:
                continue
            ids = self.aliases.get(label, set())
            if len(ids) == 1:
                self.records[next(iter(ids))]['category'] = next(iter(values))
            elif not ids:
                key = 'runtime:' + label
                self.records[key] = {'canonical': display[label], 'category': next(iter(values)),
                                     'source': 'runtime', 'version': runtime_version, 'id': key}
                add_alias(label, key)
        for alias, canonical in runtime.get('aliases', {}).items():
            ids = self.aliases.get(normalize_term_key(canonical), set())
            if len(ids) == 1:
                add_alias(alias, next(iter(ids)))
        self.version = digest([VERSION, self.records, {k: sorted(v) for k, v in self.aliases.items()},
                               sorted(self.ambiguous)])
        matcher_terms = []
        for alias, ids in self.aliases.items():
            for key in ids:
                matcher_terms.append({'id': key, 'label': alias, 'source': 'reference_prefilter'})
        self.matcher = EvidenceMatcher(matcher_terms)
        self.word_index = defaultdict(set)
        for key, record in self.records.items():
            for word in re.findall(r'\w{3,}', record['canonical'].casefold()):
                self.word_index[word].add(key)

    def resolve(self, text):
        key = normalize_term_key(text)
        ids = self.aliases.get(key, set())
        if key in self.ambiguous or len(ids) != 1:
            return None
        return self.records[next(iter(ids))]

    def candidates(self, text, category, limit=3):
        """Bounded lexical reference candidates are suggestions, not aliases."""
        words = set(re.findall(r'\w{3,}', text.casefold()))
        keys = set().union(*(self.word_index.get(word, set()) for word in words))
        scored = []
        for key in keys:
            record = self.records[key]
            if record['category'] not in {None, category}:
                continue
            overlap = len(words & set(re.findall(r'\w{3,}', record['canonical'].casefold())))
            scored.append((-overlap, record['canonical'], key))
        return [self.records[key] for _, _, key in sorted(scored)[:limit]]

    def plan(self, vacancy) -> ExtractionPlan:
        original = vacancy.description
        mentions = self.matcher.extract(original)
        local, observations, residual = [], [], []
        removed, section, offset = 0, None, 0
        for line in original.splitlines(keepends=True):
            heading = EvidenceMatcher._header_role(line)
            if heading:
                section = heading
            # Split sentences without touching package names such as Node.js.
            for piece in re.finditer(r'.+?(?:(?<=[.!?;])\s+(?=\S)|$)', line.rstrip('\r\n')):
                start, end = offset + piece.start(), offset + piece.end()
                text = original[start:end]
                hits = []
                for mention in mentions:
                    a, b = mention['text_span']
                    if not start <= a < b <= end:
                        continue
                    record = self.resolve(mention['quote'])
                    if not record or mention['needs_review']:
                        continue
                    observation = {'term': mention['quote'], 'canonical': record['canonical'],
                                   'reference_id': record['id'], 'source': record['source'],
                                   'version': record['version'], 'text_span': [a, b],
                                   'modality': mention['modality'], 'context_role': section or 'unknown',
                                   'context_phrase': normalize_ws(text)}
                    observations.append(observation)
                    if record['category'] is None or len(text) > 200:
                        continue
                    hits.append((a - start, b - start))
                    category = record['category']
                    facet = {'stack': 'technology_stack', 'tools': 'tool_or_platform',
                             'skills': 'benefit' if section == 'benefit' else
                             'task_responsibility' if section == 'task' else 'skill_requirement'}[category]
                    local.append({'term': mention['quote'], 'category': category, 'facet': facet,
                                  'domain_hint': 'sonstiges', 'domain_confidence': 0.0,
                                  'context_phrase': normalize_ws(text)})
                remainder = text
                for a, b in sorted(set(hits), reverse=True):
                    remainder = remainder[:a] + ' ' * (b - a) + remainder[b:]
                tokens = re.findall(r'[^\W_]+', remainder.casefold())
                fully_known = bool(hits) and all(t in GLUE for t in tokens)
                for observation in observations:
                    if start <= observation['text_span'][0] < end:
                        observation['omitted_from_prompt'] = fully_known
                if fully_known:
                    removed += 1
                else:
                    residual.append((section, text))
            offset += len(line)
        # Keep all non-description fields; deduplicate only identical fragments.
        unique = [text for _, text in dict.fromkeys(item for item in residual if item[1].strip())]
        prefix = vacancy.canonical_text().split('DESCRIPTION:\n', 1)[0]
        # Titles, occupations and conditions can themselves contain new terms.
        # Retain them even when every description clause was recognized.
        text = prefix + 'DESCRIPTION:\n' + '\n'.join(unique)
        return ExtractionPlan(local, text, observations, len(vacancy.canonical_text()), len(text), removed)


def load_index(db, runtime=None):
    """Read active snapshots once; no API calls and no dictionary writes."""
    from app.matching.intelligence_data import load_reference_terms
    terms, _ = load_reference_terms(db)
    return ReferenceIndex(terms, runtime)
