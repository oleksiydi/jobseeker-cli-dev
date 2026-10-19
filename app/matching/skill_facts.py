"""Exact professional source facts for experimental requirement retrieval."""
from __future__ import annotations

import re

from app.matching.intelligence_data import digest
from app.matching.project_episodes import fact, project_projection, validate_provenance
from app.matching.profile import normalize_profile

VERSION = 'skill-facts-v1'
_EXCLUDED_KEYS = re.compile(
    r'learn|wish|target|goal|avoid|unknown|negative|interest|lern|wunsch|ziel|'
    r'изуч|желан|цели|private|secret|password|token|api.?key|confidence|recall|note', re.I)
_NON_EXPERIENCE = re.compile(
    r'\b(?:keine?\s+(?:praktische\s+)?(?:erfahrung|kenntnisse)|'
    r'(?:no|without)\s+(?:practical\s+)?(?:experience|knowledge)|'
    r'lernen|lerne|изучаю|хочу|нет\s+опыта|не\s+умею|не\s+работал)\b|'
    r'^\s*(?:learning|learn|want\s+to\s+learn)\b', re.I)
_CREDENTIAL = re.compile(r'-----BEGIN .*PRIVATE KEY-----|'
                         r'\b(?:password|passwd|api[_-]?key|access[_-]?token)\s*[:=]|'
                         r'\b(?:mongodb(?:\+srv)?|https?)://[^\s/]+:[^\s/]+@', re.I)


def _blocked(value: dict) -> bool:
    return (bool(value.get('private') or value.get('do_not_mention'))
            or value.get('visibility') in {'private', 'excluded', 'do_not_mention'}
            or value.get('status') in {'unknown', 'learning', 'desired', 'denied', 'excluded'})


def build_facts(source: dict, supplement: dict | None = None, policy: dict | None = None) -> dict:
    """Keep actions, results and technology names distinct, with JSON Pointers.

    Only primary professional facts are indexed. Supporting constraints may come
    from the supplement, following the existing project projection policy.
    """
    projection = project_projection(source, supplement, policy, include_technologies_only=True)
    validate_provenance(projection, [s for s in (source, supplement) if s])
    facts = []
    excluded = 0

    def add(value: dict, kind: str, **context):
        nonlocal excluded
        text = value['value']
        if not isinstance(text, str):
            excluded += 1
            return
        negative_label = kind in {'declared_skill', 'technology'} and re.match(
            r'\s*(?:kein\w*|nicht|ohne|no|not|нет|не)\b', text, re.I)
        if (not text.strip() or _NON_EXPERIENCE.search(text)
                or negative_label or _CREDENTIAL.search(text)):
            excluded += 1
            return
        facts.append({'id': 'fact:' + digest([value['source'], value['pointer']])[:20],
                      'kind': kind, 'source_fact': value, **context})

    for episode in projection['episodes']:
        context = {key: episode[key] for key in ('label', 'company', 'timeframe') if episode.get(key)}
        context['episode_id'] = episode['id']
        for value in episode['actions']:
            add(value, 'action', **context)
        for result in episode['results']:
            add(result['fact'], 'project_result', result_scope='project', action_link=None, **context)
        for value in episode['technologies']:
            add(value, 'technology', **context)

    def skills(value, pointer):
        if isinstance(value, str):
            add(fact(source, pointer), 'declared_skill')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                skills(child, f'{pointer}/{index}')
        elif isinstance(value, dict) and not _blocked(value):
            if isinstance(value.get('label'), str):
                skills(value['label'], pointer + '/label')
            else:
                for key, child in value.items():
                    if not _EXCLUDED_KEYS.search(key):
                        escaped = key.replace('~', '~0').replace('/', '~1')
                        skills(child, pointer + '/' + escaped)

    raw = source['raw']
    # Global prose restrictions cover professional material until explicitly
    # resolved, preventing a hidden project from leaking through a skill list.
    restricted = raw.get('candidate', {}).get('ethics_constraints', {}).get('do_not_mention')
    if not restricted or policy is not None:
        for key in ('skills', 'TechnischeKenntnisse'):
            if key in raw:
                skills(raw[key], '/' + key)
        # Legacy and compact project fields use the same explicit privacy gates.
        def legacy(item: dict, pointer: str):
            if _blocked(item):
                return
            context = {}
            for output, keys in (('timeframe', ('period', 'Jahr', 'timeframe')),
                                 ('label', ('name', 'Firma', 'company'))):
                key = next((k for k in keys if isinstance(item.get(k), str)), None)
                if key:
                    context[output] = fact(source, pointer + '/' + key)
            for key, kind in (('Aufgaben', 'action'), ('responsibilities', 'action'),
                              ('skills', 'technology'), ('tech_stack', 'technology'),
                              ('deliverables', 'project_result')):
                values = item.get(key, [])
                details = ({**context, 'result_scope': 'project', 'action_link': None}
                           if kind == 'project_result' else context)
                if isinstance(values, str):
                    add(fact(source, pointer + '/' + key), kind, **details)
                elif isinstance(values, list):
                    for i, value in enumerate(values):
                        if isinstance(value, str):
                            add(fact(source, f'{pointer}/{key}/{i}'), kind, **details)

        for key in ('projects', 'Berufserfahrung'):
            for index, item in enumerate(raw.get(key, [])):
                if isinstance(item, dict):
                    legacy(item, f'/{key}/{index}')
    unique = {f['id']: f for f in facts}
    primary = normalize_profile(source['raw'])
    additional = normalize_profile(supplement['raw']) if supplement else {}
    result = {'version': VERSION, 'sources': projection['sources'], 'facts': list(unique.values()),
              'withheld_projects': projection['withheld'], 'excluded_facts': excluded,
              'constraints': projection['constraints'], 'languages': projection['languages'],
              'education': primary['education'] or additional.get('education', [])}
    # Absence of a license field is unknown, distinct from an explicit empty list.
    for origin in (source, supplement):
        if origin and 'licenses' in origin['raw']:
            result['licenses'] = origin['raw']['licenses']
            break
    result['input_hash'] = digest([VERSION, result])
    validate_provenance(result, [s for s in (source, supplement) if s])
    return result
