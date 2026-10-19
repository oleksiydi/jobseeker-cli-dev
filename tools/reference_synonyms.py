"""Reuse authoritative equivalent names and shortlist anchors for novel terms."""
from __future__ import annotations

import re
from collections import defaultdict

from tools.taxonomy_transform import normalize_term_key, skill_class_for


def prepare(rows, index, *, anchor_limit=20):
    """Return unresolved observations + bounded anchors, and verified alias pairs.

    Never use broader/narrower relations. Only one exact reference concept is
    eligible, within the existing domain/category/facet/skill_class partition.
    """
    scopes = defaultdict(lambda: defaultdict(list))
    for row in rows:
        scope = (row.get('domain_hint', 'sonstiges'), row.get('category', 'skills'), row.get('facet', 'other'),
                 skill_class_for(category=row.get('category', 'skills'), facet=row.get('facet', 'other')))
        scopes[scope][normalize_term_key(str(row.get('term') or ''))].append(row)
    residual, proposals = [], []
    known_count = 0
    for scope, terms in scopes.items():
        known, unknown = {}, {}
        groups = defaultdict(list)
        for term, observations in terms.items():
            record = index.resolve(term)
            if (record is None or scope[0] == 'sonstiges'
                    or record['category'] is not None and record['category'] != scope[1]):
                unknown[term] = observations
                continue
            known[term] = observations
            known_count += 1
            groups[record['id']].append((term, observations, record))
        fields = dict(zip(('domain', 'category', 'facet', 'skill_class'), scope))
        for members in groups.values():
            for alias, observations, record in members:
                if alias == normalize_term_key(record['canonical']):
                    continue
                proposals.append({**fields, 'alias': alias, 'canonical': record['canonical'],
                                  'confidence': 1.0, 'source_refs_count': len({r['referenznummer'] for r in observations}),
                                  'sample_phrases': tuple(sorted({r.get('context_phrase', '') for r in observations})[:3]),
                                  'requires_review': False, 'reviewed_by_strong': False,
                                  'provider': 'local_reference', 'model': index.version,
                                  'reason': 'Exact equivalent name of one reference concept',
                                  'origin': 'reference_prefilter', 'reference_id': record['id']})
        if not unknown:
            continue
        # Include every unresolved observation. Rank known anchors against new
        # surfaces locally; do not send the whole reference dictionary.
        query_words = set(re.findall(r'\w+', ' '.join(unknown)))
        anchors = sorted(known, key=lambda term: (
            -len(query_words & set(re.findall(r'\w+', term))),
            -len({r['referenznummer'] for r in known[term]}), term))[:anchor_limit]
        for term, observations in unknown.items():
            residual.extend(observations)
        for term in anchors:
            residual.extend({**row, 'reference_anchor': True} for row in known[term])
    metrics = {'reference_known_terms': known_count, 'reference_reused_pairs': len(proposals),
               'reference_original_rows': len(rows), 'reference_residual_rows': len(residual)}
    return residual, proposals, metrics


def augment(block, index, block_size):
    terms = list(block['terms'])
    seen = {t['term_key'] for t in terms}
    for item in list(terms):
        if item.get('reference_anchor'):
            continue
        for record in index.candidates(item['canonical'], block['block']['category']):
            key = normalize_term_key(record['canonical'])
            if key in seen or len(terms) >= block_size:
                continue
            seen.add(key)
            terms.append({'term_key': key, 'canonical': record['canonical'],
                          'source_refs_count': 0, 'sample_phrases': [],
                          'skill_class': block['block']['skill_class'], 'reference_only': True,
                          'reference_id': record['id'], 'reference_version': index.version})
    return {**block, 'terms': terms, 'reference_version': index.version}
