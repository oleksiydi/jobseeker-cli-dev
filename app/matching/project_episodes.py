"""Lossless source references for a separate, privacy-filtered project view.

Only whitelisted professional project fields become retrieval input. Source
documents are never rewritten; JSON Pointers address original values exactly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.matching.intelligence_data import digest
from app.matching.profile import normalize_profile

PROJECTION_VERSION = 'project-episodes-v1'


def read_source(path: Path) -> dict:
    """Read a complete source, retaining a byte hash for provenance/invalidation."""
    data = path.read_bytes()
    raw = json.loads(data.decode('utf-8-sig'))
    if not isinstance(raw, dict):
        raise ValueError('Profile source must be a JSON object')
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'raw': raw}


def resolve_pointer(raw: dict, pointer: str):
    value = raw
    if not pointer:
        return value
    if not pointer.startswith('/'):
        raise ValueError('Expected a JSON Pointer')
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def fact(source: dict, pointer: str) -> dict:
    return {'source': source['path'], 'source_sha256': source['sha256'],
            'pointer': pointer, 'value': resolve_pointer(source['raw'], pointer)}


def _facts(source: dict, pointer: str, value) -> list[dict]:
    if isinstance(value, str):
        return [fact(source, pointer)] if value.strip() else []
    if isinstance(value, list):
        return [fact(source, f'{pointer}/{i}') for i, item in enumerate(value)
                if isinstance(item, str) and item.strip()]
    return []


def project_projection(source: dict, supplement: dict | None = None,
                       policy: dict | None = None, *, include_technologies_only: bool = False) -> dict:
    """Keep coherent project episodes; outcomes retain project-level scope.

    Prose restrictions cannot safely be resolved by string similarity. Without
    a source-bound explicit resolution projects are withheld before embedding.
    """
    raw = source['raw']
    restriction = raw.get('candidate', {}).get('ethics_constraints', {}).get('do_not_mention', [])
    permitted = not restriction
    if policy is not None:
        if (policy.get('profile_sha256') != source['sha256']
                or policy.get('resolution') != 'listed_projects_permitted'
                or policy.get('basis') != 'user_statement'):
            raise ValueError('Usage policy must resolve restrictions for this exact source hash')
        permitted = True
    episodes, withheld = [], []

    def visit(item: dict, pointer: str, inherited: dict, blocked: bool = False):
        current = dict(inherited)
        for key in ('company', 'role_title', 'timeframe', 'context', 'confidence'):
            if key in item:
                current[key] = fact(source, pointer + '/' + key)
        blocked = blocked or bool(item.get('do_not_mention')) or bool(item.get('private'))
        blocked = blocked or item.get('visibility') in {'private', 'excluded', 'do_not_mention'}
        children = item.get('subprojects', [])
        actions = _facts(source, pointer + '/responsibilities', item.get('responsibilities'))
        technologies = _facts(source, pointer + '/tech_stack', item.get('tech_stack'))
        results = _facts(source, pointer + '/deliverables', item.get('deliverables'))
        results += _facts(source, pointer + '/key_achievement', item.get('key_achievement'))
        if actions or results or (include_technologies_only and technologies):
            episode_id = 'episode:' + digest([source['path'], pointer])[:20]
            if blocked or not permitted:
                # No restricted text goes into the projection, cache or vectors.
                withheld.append({'id': episode_id, 'pointer': pointer,
                                 'reason': 'explicit_exclusion' if blocked else 'unresolved_usage_restriction'})
            else:
                label = fact(source, pointer + '/name') if item.get('name') else current.get('company')
                episode = {'id': episode_id, 'pointer': pointer, 'label': label,
                           **current, 'actions': actions,
                           'results': [{'fact': value, 'scope': 'project',
                                        'action_link': None} for value in results],
                           'technologies': technologies,
                           'source_kind': 'candidate_profile', 'current_proficiency': 'unknown',
                           'usage': 'permitted_by_source_and_policy'}
                episode['input_hash'] = digest([PROJECTION_VERSION, episode])
                episodes.append(episode)
        for index, child in enumerate(children):
            if isinstance(child, dict):
                visit(child, f'{pointer}/subprojects/{index}', current, blocked)

    for index, item in enumerate(raw.get('experience_projects', [])):
        if isinstance(item, dict):
            visit(item, f'/experience_projects/{index}', {})
    primary = normalize_profile(raw)
    additional = normalize_profile(supplement['raw']) if supplement else {}
    profile = {**primary,
               'languages': {**additional.get('languages', {}), **primary['languages']},
               'constraints': {**additional.get('constraints', {}), **primary['constraints']}}
    supporting = []
    for origin in (source, supplement):
        if origin:
            for key in ('Ausbildung', 'education', 'Sprachkenntnisse'):
                if key in origin['raw']:
                    supporting.append(fact(origin, '/' + key))
    if raw.get('candidate', {}).get('languages'):
        supporting.append(fact(source, '/candidate/languages'))
    # A normalized restriction is useful for matching but its private source text
    # must not become an application fact or an embedding input.
    if supplement and 'körperliche_verfassung' in supplement['raw'].get('zusätzliche_informationen', {}):
        supporting.append({'source': supplement['path'], 'source_sha256': supplement['sha256'],
                           'pointer': '/zusätzliche_informationen/körperliche_verfassung',
                           'purpose': 'constraint_only',
                           'normalized': additional['constraints']})
    projection = {'schema_version': 1, 'version': PROJECTION_VERSION,
                  'sources': [{k: s[k] for k in ('path', 'sha256')}
                              for s in (source, supplement) if s],
                  'usage_policy': policy, 'episodes': episodes, 'withheld': withheld,
                  'supporting_facts': supporting,
                  'unlinked_stories': len(raw.get('interview_story_bank', {}).get('best_stories', [])),
                  'story_policy': 'not indexed without an explicit project link; not independent evidence',
                  'supplement_policy': 'education/languages/constraints only; primary fields win',
                  'constraints': profile['constraints'], 'languages': profile['languages']}
    projection['input_hash'] = digest(projection)
    return projection


def validate_provenance(projection: dict, sources: list[dict]) -> None:
    """Reject changed quotes, unresolved pointers and stale source hashes."""
    origins = {s['path']: s for s in sources}

    def check(value):
        if isinstance(value, dict):
            if {'source', 'source_sha256', 'pointer'} <= value.keys():
                source = origins[value['source']]
                if source['sha256'] != value['source_sha256']:
                    raise ValueError('Stale source hash')
                actual = resolve_pointer(source['raw'], value['pointer'])
                if 'value' in value and actual != value['value']:
                    raise ValueError('Source quote differs from original field')
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(projection)
