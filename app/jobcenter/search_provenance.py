"""Share collection coverage with downstream readers and human reports."""
from __future__ import annotations

import html
import json

from app.core.i18n import localize_message, tr


def usable_list_run(state: dict) -> bool:
    """A deliberately limited URL/window run can be usable without being complete."""
    return bool(state.get('last_run_usable', state.get('last_completed_full_scan', False)))


def attach_search_scope(scope: dict, state: dict) -> None:
    provenance = state.get('search_provenance')
    if not isinstance(provenance, dict):
        return
    scope['collection_coverage'] = provenance
    if provenance.get('search_id'):
        effective = provenance.get('effective_geography', [])
        places = [str(g.get('wo') or g.get('arbeitsort') or 'Deutschland') for g in effective]
        unique_places = list(dict.fromkeys(places))
        scope['region'] = '; '.join(unique_places[:10]) or None
        if len(unique_places) > 10:
            scope['region'] += tr(
                '; … (всего {p0} мест; полный список в collection_coverage)',
                p0=len(unique_places),
            )
        radii = {str(g.get('umkreis')) for g in effective}
        scope['radius_km'] = effective[0].get('umkreis') if effective and len(radii) == 1 else None


def coverage_rows(scope: dict) -> list[tuple[str, str]]:
    value = scope.get('collection_coverage') or {}
    if not value:
        return []
    rows = []
    if value.get('search_id'):
        effective = value['effective_geography']
        effective_text = json.dumps(effective[:10], ensure_ascii=False)
        if len(effective) > 10:
            effective_text += (tr(
                ' — первые 10 из {p0} запросов; полный список в JSON '
                'scope.collection_coverage.effective_geography.',
                p0=len(effective),
            ))
        rows.extend([
            (tr('Исходная ссылка поиска'), value['search_url']),
            (tr('ID поиска'), value['search_id']),
            (tr('Исходная география'), json.dumps(value['original_geography'], ensure_ascii=False)),
            (tr('Фактически обработанная география'), effective_text),
            (tr('География сужена'), tr('Да') if value['geography_narrowed'] else tr('Нет')),
            (tr('Фильтры'), tr('Переданы дословно; семантика неизвестных фильтров не проверяется.')),
        ])
    rows.append((tr('Полнота исходной выдачи'), tr('Не подтверждена / есть ограничения')
                 if not value.get('coverage_complete') else
                 tr('Выполнен проход выбранных запросов; точность границ зависит от API и PLZ-справочника')))
    rows.extend((tr('Предупреждение'), localize_message(item)['message'])
                for item in value.get('warnings', []))
    return rows


def coverage_html(scope: dict) -> str:
    rows = coverage_rows(scope)
    if not rows:
        return ''
    return tr('<h2>Поиск и ограничения сбора</h2>') + ''.join(
        '<p><strong>' + html.escape(label) + ':</strong> ' + html.escape(value) + '</p>'
        for label, value in rows)
