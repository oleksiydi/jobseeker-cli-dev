"""Translate owned display fields on a copy, preserving source material verbatim."""
from __future__ import annotations

from copy import deepcopy

from app.core.i18n import current_language, localize_message, translate_system_text


def localize_result(result: dict) -> dict:
    if current_language() == 'ru':
        return result
    value = deepcopy(result)
    for key in ('reason', 'relation_explanation'):
        if key in value:
            value[key] = translate_system_text(value[key])
    for field in ('matched_skills', 'missing_required', 'unconfirmed_skills',
                  'related_skills', 'relation_context_skills', 'unknowns', 'evidence'):
        for item in value.get(field, []):
            for key in ('reason', 'relation_explanation'):
                if key in item:
                    item[key] = translate_system_text(item[key])
            for evidence in item.get('job_evidence', []):
                if 'reason' in evidence:
                    evidence['reason'] = translate_system_text(evidence['reason'])
    return value


def localize_scope(scope: dict) -> dict:
    value = deepcopy(scope)
    coverage = value.get('collection_coverage')
    if coverage:
        coverage['warnings'] = [localize_message(item) for item in coverage.get('warnings', [])]
        if coverage.get('search_id'):
            from app.jobcenter.search_provenance import attach_search_scope
            attach_search_scope(value, {'search_provenance': coverage})
    if current_language() == 'de':
        for key in ('note', 'selection'):
            if key in value:
                value[key] = translate_system_text(value[key])
    return value


def localize_report(report: dict) -> dict:
    value = deepcopy(report)
    value['output_language'] = current_language()
    value['results'] = [localize_result(item) for item in value.get('results', [])]
    value['adjacent_roles'] = [localize_result(item) for item in value.get('adjacent_roles', [])]
    value['scope'] = localize_scope(value.get('scope', {}))
    if current_language() == 'de':
        for reference in value.get('references', []):
            if reference.get('attribution'):
                reference['attribution'] = translate_system_text(reference['attribution'])
    return value


def localize_episode_report(report: dict) -> dict:
    value = deepcopy(report)
    value['output_language'] = current_language()
    if current_language() == 'de':
        for vacancy in value.get('vacancies', []):
            for comparison in vacancy.get('comparison', []):
                requirement = comparison.get('requirement', {})
                if 'reason' in requirement:
                    requirement['reason'] = translate_system_text(requirement['reason'])
                for evidence in comparison.get('evidence', []):
                    evidence['unknown'] = [translate_system_text(s) for s in evidence.get('unknown', [])]
    return value
