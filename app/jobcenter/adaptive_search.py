"""Plan URL searches lazily, using the first API page before storing vacancies."""
from __future__ import annotations

import re
from collections import deque

from app.core.i18n import message_record, tr
from app.jobcenter.list_query_expansion import PhysicalListQuery
from app.jobcenter.plz import (
    is_country_wide_bundesland, resolve_bundesland_to_plz, resolve_city_to_plz,
)
from app.jobcenter.search_url import SearchURL, advertised_total, geography


class AdaptiveSearch:
    """Keep logical identity stable when geography is narrowed or sharded."""

    def __init__(self, search: SearchURL, config: dict, window: int):
        self.search = search
        self.window = window
        self.area = config.get('search_area') or {}
        for name, default in (('max_plz_per_run', 200), ('max_pages_per_physical_query', 50)):
            value = self.area.get(name, default)
            if type(value) is not int or value < 1:
                raise ValueError(f'search_area.{name} must be a positive integer')
        self.pending = deque()
        self.stages = {}
        self.warnings = []
        self.outcomes = []
        self.narrowed = False
        self.coverage_complete = True
        self._counter = 0
        self._scheduled_plz = set()
        self._append(search.params, 'original')
        if search.params.get('umkreis') in {'0', 0}:
            self.warn('geographic_boundary',
                      tr('umkreis=0 не гарантирует административные границы города или PLZ.'))
        if 'homeoffice' in search.params:
            self.warn('homeoffice_passthrough',
                      tr(
                          'Homeoffice передан дословно. Сайт переключает nv_true перед '
                          'API-запросом; результат может отличаться от браузера. Значения фильтра '
                          'не преобразованы.',
                      ))
        if search.ignored:
            self.warn('browser_controls_ignored',
                      tr(
                          'Пагинация и действия интерфейса из URL не используются: {p0}.',
                          p0=', '.join(search.ignored),
                      ))

    def warn(self, code: str, message: str, shard_id: str | None = None):
        item = {'code': code, **message_record(message)}
        if shard_id:
            item['shard_id'] = shard_id
        if item not in self.warnings:
            self.warnings.append(item)

    def preview(self) -> dict:
        """No HTTP/geocoder/database access; describe the initial probe only."""
        return {'mode': 'url', 'search_id': self.search.search_id,
                'search_url': self.search.original, 'canonical_search_url': self.search.canonical,
                'params': self.search.params, 'result_window_limit': self.window,
                'warnings': self.warnings, 'dry_run': True,
                'note': tr('Adaptive geography is resolved after the first API response.')}

    def _append(self, params: dict, stage: str, plz: str | None = None):
        self._counter += 1
        query = PhysicalListQuery(f'url-{self._counter:04d}', plz, params)
        self.stages[query.shard_id] = stage
        self.pending.append(query)

    def _places(self, query: PhysicalListQuery) -> list[str]:
        if query.params.get('wo'):
            return [str(query.params['wo'])]
        values = query.params.get('arbeitsort', [])
        if not isinstance(values, list):
            values = [values]
        return list(dict.fromkeys(p.strip() for v in values for p in str(v).split(';') if p.strip()))

    @staticmethod
    def _at_place(params: dict, place: str, *, radius: int = 0) -> dict:
        # Retain the work-location facet: it may further restrict wo, and dropping
        # it would broaden a combined URL. Coordinates belong to the replaced wo.
        return {**{k: v for k, v in params.items() if k not in {
            'wo', 'umkreis', 'koordinaten'}}, 'wo': place, 'umkreis': radius}

    def redirect(self, query: PhysicalListQuery, data: dict) -> bool:
        """Return True when this probe is replaced, before its rows are persisted."""
        total = advertised_total(data)
        if total is None or total < self.window:
            return False
        self.warn('result_window_reached',
                  tr(
                      'Достигнут предел API {p0} (найдено {p1}); полнота прямой выдачи не '
                      'гарантирована.',
                      p0=self.window,
                      p1=total,
                  ),
                  query.shard_id)
        if self.stages[query.shard_id] == 'plz':
            return False  # A terminal shard is collected up to the window, with a warning.
        places = self._places(query)
        if len(places) > 1:
            for place in places:
                radius = int(query.params.get('umkreis') or 0)
                self._append(self._at_place(query.params, place, radius=radius), 'place')
            self._replaced(query, total, 'split_locations')
            self.warn('geographic_boundary',
                      tr(
                          'Города и PLZ — разные территории; API-запросы по месту, включая '
                          'umkreis=0, не подтверждают точное совпадение административных границ.',
                      ))
            return True
        place = places[0] if places else 'Deutschland'
        output = data.get('woOutput')
        output = output if isinstance(output, dict) else {}
        mode = str(output.get('suchmodus', ''))
        regional = mode in {'BUNDESLANDSUCHE', 'LANDSUCHE'} or is_country_wide_bundesland(place)
        raw_radius = query.params.get('umkreis')
        radius = int(raw_radius) if raw_radius not in {None, ''} else None
        if not query.params.get('wo') or (radius is None and mode == 'ORTSUCHE'):
            radius = 0
        # For wo without umkreis the API may apply a default radius. An overflowing
        # city query is therefore explicitly retried with zero before PLZ expansion.
        if not regional and radius != 0:
            self.narrowed = True
            self.coverage_complete = False
            self.warn('radius_removed',
                      tr(
                          'Из-за предела API поиск «{p0}» ограничен самим местом (umkreis=0). '
                          'Окружение исключено. Попробуйте более узкий поиск по PLZ с радиусом.',
                          p0=place,
                      ), query.shard_id)
            self.warn('geographic_boundary',
                      tr('umkreis=0 не гарантирует административные границы города или PLZ.'))
            self._append(self._at_place(query.params, place), 'zero_radius')
            self._replaced(query, total, 'radius_removed')
            return True
        if re.fullmatch(r'\d{5}', place):
            return False
        try:
            codes = (resolve_bundesland_to_plz(place) if regional else resolve_city_to_plz(place))
        except Exception:
            # Geocoder failures may contain network URLs; never log their text.
            codes = []
        maximum = int(self.area.get('max_plz_per_run', 200))
        new_codes = [code for code in codes if code not in self._scheduled_plz]
        if not codes or len(self._scheduled_plz | set(codes)) > maximum:
            self.warn('sharding_unavailable',
                      tr(
                          'PLZ-разбиение для «{p0}» недоступно или превышает max_plz_per_run={p1}; '
                          'будет сохранена ограниченная выдача.',
                          p0=place,
                          p1=maximum,
                      ), query.shard_id)
            return False
        for code in new_codes:
            self._scheduled_plz.add(code)
            self._append(self._at_place(query.params, code), 'plz', code)
        self.warn('geographic_boundary',
                  tr(
                      'Использовано PLZ-разбиение по географическому справочнику. PLZ и '
                      'административные границы различаются; umkreis=0 не гарантирует точного '
                      'покрытия исходной территории.',
                  ))
        self._replaced(query, total, 'plz_sharding')
        return True

    def _replaced(self, query, total, reason):
        self.outcomes.append({'shard_id': query.shard_id, 'params': query.params,
                              'total': total, 'status': 'replaced', 'reason': reason})

    def record(self, query, *, total, received, status, attempted=True):
        self.outcomes.append({'shard_id': query.shard_id, 'params': query.params,
                              'total': total, 'received': received, 'status': status,
                              'attempted': attempted})
        if status != 'complete':
            self.coverage_complete = False
            self.warn('incomplete_query',
                      tr(
                          'Выдача этого запроса ограничена или не завершена; полнота не обеспечена.',
                      ), query.shard_id)

    def provenance(self, execution_complete: bool) -> dict:
        return {
            'search_url': self.search.original, 'canonical_search_url': self.search.canonical,
            'search_id': self.search.search_id, 'filter_mode': 'passthrough',
            'result_window_limit': self.window,
            'original_geography': geography(self.search.params) or {'wo': 'Deutschland'},
            'effective_geography': [geography(o['params']) or {'wo': 'Deutschland'}
                                    for o in self.outcomes if o['status'] != 'replaced'
                                    and o.get('attempted', True)],
            'geography_narrowed': self.narrowed,
            'coverage_complete': self.coverage_complete and execution_complete,
            'execution_complete': execution_complete,
            'pending_query_count': len(self.pending),
            'warnings': self.warnings, 'queries': self.outcomes,
        }
