"""Typed ESCO/DKZ neighbours for application explanations, never score or skill closure."""
from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from pathlib import Path

from app.matching.intelligence_data import digest, text_key
from app.matching.local_evidence import EvidenceMatcher
from app.normalization.reference_data import ReferenceStore

GRAPH_VERSION = 'reference-relations-v1'
GRAPH_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOGGER = logging.getLogger(__name__)
_DATASETS = ('skill', 'occupation', 'DKZ_Kompetenzen_gueltig.xml', 'DKZ_Taetigkeiten_gueltig.xml')
_PREDICATES = ('broaderSkill', 'narrowerSkill', 'hasOptionalSkill', 'hasEssentialSkill')
_NEGATIVE = re.compile(r'\b(?:kein\w*|nicht|ohne|learning|lerne|изучаю|нет)\b', re.I)
_UNSAFE = {'go', 'spring', 'r', 'c', 'ad', 'office', 'server', 'support'}
_LABELS = {
    'hasOptionalSkill': 'Связанный дополнительный навык ESCO',
    'hasEssentialSkill': 'Связь базового навыка ESCO',
    'broaderSkill': 'Более широкая область ESCO',
    'narrowerSkill': 'Более узкая область ESCO',
    'parent_id': 'Группа компетенций DKZ',
}


def relation_settings(settings: dict, override: str | None = None) -> dict:
    """Validate the feature before database reads; ENV overrides JSON/code defaults."""
    raw = settings.get('reference_relations', {})
    if not isinstance(raw, dict):
        raise ValueError('reference_relations must be an object')
    result = {'enabled': True, 'max_related_skills': 3, 'max_context_suggestions': 3, **raw}
    if override is not None:
        value = override.strip().lower()
        if value not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValueError('JOB_INTELLIGENCE_RELATIONS_ENABLED must be true or false')
        result['enabled'] = value in {'true', '1', 'yes'}
    if type(result['enabled']) is not bool:
        raise ValueError('reference_relations.enabled must be boolean')
    for name in ('max_related_skills', 'max_context_suggestions'):
        minimum = 1 if name == 'max_related_skills' else 0
        if type(result[name]) is not int or not minimum <= result[name] <= 3:
            raise ValueError(f'reference_relations.{name} must be in {minimum}..3')
    return result


def _identifier(source: str, kind: str, source_id: str) -> str:
    return source_id if source == 'esco' else f'dkz:{kind}:{source_id}'


class ReferenceGraph:
    """Immutable, source-scoped graph. No cross-source equivalence is inferred."""

    def __init__(self, nodes=(), edges=(), *, snapshots=(), status='ready'):
        self.nodes = {n['id']: n for n in nodes}
        self.edges = []
        self.neighbours = defaultdict(list)
        self.groups = defaultdict(set)
        seen = set()
        dangling = 0
        for edge in sorted(edges, key=lambda e: (e['from'], e['to'], e['type'])):
            key = (edge['from'], edge['to'], edge['type'])
            if key in seen or edge['from'] == edge['to']:
                continue
            seen.add(key)
            if edge['from'] not in self.nodes or edge['to'] not in self.nodes:
                dangling += 1
                continue
            self.edges.append(edge)
            self.neighbours[edge['from']].append((edge['to'], edge, 'forward'))
            self.neighbours[edge['to']].append((edge['from'], edge, 'reverse'))
            if edge['type'] == 'parent_id':
                self.groups[edge['to']].add(edge['from'])
        self.identity = {
            'version': GRAPH_VERSION, 'code_hash': GRAPH_CODE_HASH,
            'status': status, 'snapshots': sorted(snapshots),
            'nodes': len(self.nodes), 'edges': len(self.edges), 'dangling_edges_skipped': dangling,
            'hash': digest([GRAPH_VERSION, sorted(self.nodes.values(), key=lambda n: n['id']), self.edges]),
        }
        self.names = defaultdict(lambda: defaultdict(set))
        for node in self.nodes.values():
            for label in [node['label'], *node.get('aliases', [])]:
                key = text_key(label)
                if key and key not in _UNSAFE:
                    self.names[key][(node['source'], node['kind'])].add(node['id'])

    def bind(self, label: str, kind='skill') -> list[str]:
        """Only unambiguous exact labels per source; keep ESCO/DKZ identities separate."""
        groups = self.names.get(text_key(label), {})
        return sorted(next(iter(ids)) for (_, node_kind), ids in groups.items()
                      if node_kind == kind and len(ids) == 1)

    def paths(self, candidate_id: str):
        """Direct typed links; DKZ siblings are explicitly context-only paths."""
        for target, edge, traversal in self.neighbours.get(candidate_id, []):
            node = self.nodes[target]
            path = [{**edge, 'traversal': traversal}]
            if node['kind'] == 'skill':
                eligible = (edge['source'] == 'esco' and (
                    edge['type'] in {'hasOptionalSkill', 'hasEssentialSkill'}
                    or (edge['type'] == 'broaderSkill' and traversal == 'forward')
                    or (edge['type'] == 'narrowerSkill' and traversal == 'reverse')))
                yield target, path, eligible
            elif node['kind'] == 'occupation':
                yield target, path, False
            elif node['kind'] == 'group':
                for sibling in sorted(self.groups.get(target, set())):
                    if sibling == candidate_id or self.nodes[sibling]['kind'] != 'skill':
                        continue
                    sibling_edge = next(e for t, e, _ in self.neighbours[sibling]
                                        if t == target and e['type'] == 'parent_id')
                    yield sibling, [*path, {**sibling_edge, 'traversal': 'reverse'}], False


def load_reference_graph(db, terms: list[dict], references: list[dict]) -> ReferenceGraph:
    """Read existing active snapshots once; do not change originals or write caches."""
    manifests = {m['source']: m for m in references if m.get('source') in {'esco', 'dkz'}}
    snapshots = [s for m in manifests.values() for s in m.get('snapshots', [])]
    if not snapshots:
        return ReferenceGraph(status='unavailable')
    canonical = {t['id']: t for t in terms}
    projection = {'source': 1, 'source_id': 1, 'snapshot_id': 1, 'dataset': 1, 'labels': 1,
                  'raw.tag': 1, 'raw.parent_id': 1, 'raw.attributes.zustand': 1, 'raw._links': 1}
    rows = ReferenceStore(db).entries.find(
        {'source': {'$in': sorted(manifests)}, 'snapshot_id': {'$in': snapshots},
         'dataset': {'$in': list(_DATASETS)}}, projection).batch_size(500)
    nodes, edges, parents = [], [], []
    for row in rows:
        source, source_id = row['source'], row['source_id']
        # Do not accept a snapshot activated only for the other source.
        manifest = manifests.get(source, {})
        if row['snapshot_id'] not in manifest.get('snapshots', []):
            continue
        raw = row.get('raw') or {}
        kind = ('occupation' if row['dataset'] in {'occupation', 'DKZ_Taetigkeiten_gueltig.xml'}
                else 'group' if raw.get('tag') == 'kompetenzgruppe' else 'skill')
        if kind == 'occupation' and source == 'dkz' and raw.get('attributes', {}).get('zustand') != 'E':
            continue
        labels = row.get('labels') or []
        if not labels:
            continue
        concept_id = _identifier(source, kind, source_id)
        term = canonical.get(concept_id, {})
        aliases = list(term.get('aliases', []))
        # Explicit product descriptors in DKZ headings; only graph binding changes.
        short = re.sub(r'^(?:Virtualisierungssoftware|Open-Source-Plattform)\s+', '', labels[0])
        if short != labels[0]:
            aliases.append(short)
        version = f'{manifest.get("version", "unknown")}:{row["snapshot_id"]}'
        nodes.append({'id': concept_id, 'label': labels[0], 'aliases': sorted(set(aliases)),
                      'kind': kind, 'source': source, 'version': version})
        if source == 'esco':
            for predicate in _PREDICATES:
                links = raw.get('_links', {}).get(predicate, [])
                if isinstance(links, dict):
                    links = [links]
                for link in links if isinstance(links, list) else []:
                    if isinstance(link, dict) and isinstance(link.get('uri'), str):
                        edges.append({'from': concept_id, 'to': link['uri'], 'type': predicate,
                                      'source': source, 'version': version})
        elif raw.get('parent_id'):
            parents.append((concept_id, raw['parent_id'], version))
    dkz_ids = {n['id'].rsplit(':', 1)[-1]: n['id'] for n in nodes if n['source'] == 'dkz'}
    for concept_id, parent, version in parents:
        edges.append({'from': concept_id, 'to': dkz_ids.get(parent, f'dkz:group:{parent}'),
                      'type': 'parent_id', 'source': 'dkz', 'version': version})
    graph = ReferenceGraph(nodes, edges, snapshots=snapshots,
                           status='ready' if nodes else 'unavailable')
    LOGGER.info('Reference graph: nodes=%s edges=%s dangling=%s',
                len(graph.nodes), len(graph.edges), graph.identity['dangling_edges_skipped'])
    return graph


class RelatedSkillFinder:
    """Resolve declared facts first, then match only their nearby concepts in vacancies."""

    def __init__(self, graph: ReferenceGraph, profile: dict, settings: dict):
        self.graph, self.settings = graph, settings
        self.targets = defaultdict(list)
        for fact in profile.get('skills', []):
            label = fact.get('label', '')
            if (fact.get('status') != 'declared' or not fact.get('evidence_quote')
                    or _NEGATIVE.search(label)):
                continue
            for concept_id in graph.bind(label):
                for target, path, eligible in graph.paths(concept_id):
                    self.targets[target].append((concept_id, fact, path, eligible))
        # Group nodes are never phrase-matched; broad DKZ siblings stay advice only.
        target_nodes = [graph.nodes[i] for i in sorted(self.targets) if graph.nodes[i]['kind'] == 'skill']
        self.matcher = EvidenceMatcher(target_nodes) if target_nodes else None

    def find(self, job: dict) -> tuple[list[dict], list[dict]]:
        evidence = self.matcher.extract(job.get('description', '')) if self.matcher else []
        role_ids = self.graph.bind(job.get('role', ''), 'occupation')
        evidence += [{'concept_id': i, 'quote': job['role'], 'source': 'occupation',
                      'modality': 'context', 'needs_review': False} for i in role_ids if i in self.targets]
        direct_labels = {text_key(x['label']) for x in job.get('matched_skills', [])}
        related, contextual = {}, {}
        for mention in evidence:
            if mention.get('needs_review') or mention['modality'] in {'negated', 'irrelevant'}:
                continue
            target = mention['concept_id']
            # The neighbourhood matcher is small; ambiguity must still be checked
            # against the complete source, including concepts outside this subgraph.
            kind = self.graph.nodes[target]['kind']
            if target not in self.graph.bind(mention['quote'], kind):
                continue
            for concept_id, fact, path, eligible in self.targets[target]:
                if target == concept_id or text_key(fact['label']) in direct_labels:
                    continue
                # Unknown mention context is visible for review, never added to a letter.
                eligible = eligible and (mention['modality'] in {'required', 'preferred'}
                                         or mention.get('context_role') == 'task')
                reason = ('occupation_context' if mention['source'] == 'occupation'
                          else 'dkz_group' if path[0]['source'] == 'dkz' else path[0]['type'])
                item = {'label': fact['label'], 'candidate_concept': self.graph.nodes[concept_id],
                        'target_concept': self.graph.nodes[target], 'profile_evidence': [fact],
                        'job_evidence': [mention], 'relation_path': path,
                        'reason': reason, 'letter_eligible': bool(eligible),
                        'relation_explanation': _LABELS.get(path[0]['type'], 'Связь справочника')}
                bucket = related if eligible else contextual
                key = text_key(fact['label'])
                bucket.setdefault(key, item)
        for key in related:
            contextual.pop(key, None)
        order = lambda x: (text_key(x['label']), x['target_concept']['id'],
                           digest(x['relation_path']))
        return (sorted(related.values(), key=order)[:self.settings['max_related_skills']],
                sorted(contextual.values(), key=order)[:self.settings['max_context_suggestions']])
