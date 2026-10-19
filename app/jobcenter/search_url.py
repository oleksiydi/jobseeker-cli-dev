"""Read a public Jobsuche URL without interpreting its business filters."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


RESULT_WINDOW_LIMIT = 10_000
_PRIVATE_KEY = re.compile(
    r'key|token|secret|passw|pwd|authorization|credential|code|state', re.I)
_TECHNICAL = {'page', 'size', 'facetten', 'aktualisiertVor', 'as'}
_UI_ONLY = {'id', 'ansicht', 'suchespeichern', 'vormerken', 'stellemelden',
            'notiz', 'bewerbungsstatus'}


@dataclass(frozen=True)
class SearchURL:
    original: str
    canonical: str
    search_id: str
    params: dict
    ignored: tuple[str, ...]


def configured_search_url(config: dict) -> str | None:
    """ENV overrides JSON; empty ENV uses JSON, null/empty JSON disables URL mode."""
    value = os.getenv('JOBCENTER_SEARCH_URL', '').strip() or config.get('search_url')
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('list_api.search_url must be a string or null')
    return value.strip() or None


def result_window_limit(config: dict) -> int:
    value = config.get('result_window_limit', RESULT_WINDOW_LIMIT)
    if type(value) is not int or value < 1:
        raise ValueError('list_api.result_window_limit must be a positive integer')
    return value


def parse_search_url(value: str, defaults: dict | None = None) -> SearchURL:
    """Keep opaque filter values and repetitions; only pagination is owned by us.

    Canonicalization sorts parameter names, preserving the order of repeated
    values. Semicolon lists are deliberately not reordered or interpreted.
    Errors never quote the supplied URL, which could contain pasted secrets.
    """
    original = value
    try:
        url = urlsplit(value)
        valid = (url.scheme == 'https' and url.hostname in {
            'www.arbeitsagentur.de', 'arbeitsagentur.de'}
            and not url.username and not url.password and url.port in {None, 443}
            and url.path.rstrip('/') in {'/jobsuche', '/jobsuche/suche'}
            and not url.fragment and not any(ord(c) < 32 for c in value))
        if not valid or re.search(r'%(?![0-9a-fA-F]{2})', url.query):
            raise ValueError
        pairs = parse_qsl(url.query, keep_blank_values=True, encoding='utf-8',
                          errors='strict', max_num_fields=200)
    except (ValueError, UnicodeError):
        raise ValueError('search_url must be a public HTTPS Jobsuche search URL without credentials or fragment') from None
    if any(_PRIVATE_KEY.search(key) for key, _ in pairs):
        raise ValueError('search_url contains a forbidden private/authentication parameter')
    if not pairs:
        pairs = [('suchbereich', 'jobs')]
    ignored = tuple(sorted({k for k, _ in pairs if k in _TECHNICAL | _UI_ONLY}))
    filters = [(k, v) for k, v in pairs if k not in _TECHNICAL | _UI_ONLY]
    if not any(k == 'suchbereich' for k, _ in filters):
        filters.append(('suchbereich', 'jobs'))
    canonical = urlunsplit(('https', 'www.arbeitsagentur.de', '/jobsuche/suche',
                            urlencode(sorted(filters, key=lambda pair: pair[0])), ''))
    params = {k: v for k, v in (defaults or {}).items() if k in {'size', 'facetten'}}
    params.setdefault('size', '200')
    params['facetten'] = 'false'
    for key, value in filters:
        if key in params:
            previous = params[key]
            params[key] = [*previous, value] if isinstance(previous, list) else [previous, value]
        else:
            params[key] = value
    for key in ('wo', 'umkreis', 'koordinaten', 'suchbereich'):
        if isinstance(params.get(key), list):
            raise ValueError('search_url repeats a scalar geography/search parameter')
    if params.get('umkreis') not in {None, ''}:
        try:
            radius = int(params['umkreis'])
        except (ValueError, TypeError):
            raise ValueError('search_url umkreis must be a nonnegative integer') from None
        if radius < 0:
            raise ValueError('search_url umkreis must be a nonnegative integer')
    return SearchURL(original, canonical, hashlib.sha256(canonical.encode()).hexdigest(),
                     params, ignored)


def advertised_total(data: dict) -> int | None:
    """v6 maxErgebnisse; legacy support for saved responses/tests."""
    for key in ('maxErgebnisse', 'maxErgebnis', 'anzahlStellen', 'total'):
        value = data.get(key)
        if type(value) is int and value >= 0:
            return value
    return None


def geography(params: dict) -> dict:
    return {k: params[k] for k in ('wo', 'arbeitsort', 'umkreis', 'koordinaten') if k in params}
