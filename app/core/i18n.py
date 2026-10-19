"""Local output translations; source facts, search identity and scoring stay intact."""
from __future__ import annotations

import hashlib
import json
import os
import re
import string
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from pathlib import Path

_LANGUAGE = ContextVar('jobseeker_output_language', default='ru')
CATALOG_PATH = Path(__file__).with_name('translations_de.json')


def validate_language(value: str) -> str:
    if not isinstance(value, str) or value not in {'ru', 'de'}:
        raise ValueError('output_language must be ru or de')
    return value


def resolve_language(config: dict | None = None, *, cli: str | None = None,
                     fallback: dict | None = None) -> str:
    """CLI > nonempty ENV > selected JSON > app JSON > ru; null means inherit."""
    value = cli or os.getenv('JOBSEEKER_OUTPUT_LANGUAGE', '').strip()
    if not value:
        value = (config or {}).get('output_language')
    if value is None:
        value = (fallback or {}).get('output_language', 'ru')
    return validate_language(value)


def current_language() -> str:
    return _LANGUAGE.get()


@contextmanager
def language_scope(language: str):
    token = _LANGUAGE.set(validate_language(language))
    try:
        yield
    finally:
        _LANGUAGE.reset(token)


def set_language(language: str):
    return _LANGUAGE.set(validate_language(language))


def reset_language(token) -> None:
    if token is not None:
        _LANGUAGE.reset(token)


@lru_cache(maxsize=1)
def catalog() -> dict[str, str]:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def catalog_hash() -> str:
    return hashlib.sha256(CATALOG_PATH.read_bytes() + Path(__file__).read_bytes()).hexdigest()


def message_id(template: str) -> str:
    return 'text.' + hashlib.sha256(template.encode()).hexdigest()[:24]


class RenderedMessage(str):
    """A printable string that can also retain language-independent parameters."""

    def __new__(cls, text, template, params):
        value = super().__new__(cls, text)
        value.template = template
        value.params = params
        return value

    def __getnewargs__(self):
        return str(self), self.template, self.params


def tr(template: str, **params) -> str:
    translated = catalog().get(template, template) if current_language() == 'de' else template
    text = translated.format(**params) if params else translated
    return RenderedMessage(text, template, params)


def translated(mapping: dict) -> dict:
    return {key: tr(value) for key, value in mapping.items()}


def message_record(value: str) -> dict:
    if isinstance(value, RenderedMessage):
        return {'message_id': message_id(value.template), 'params': value.params,
                'message': str(value)}
    return {'message': str(value)}


@lru_cache(maxsize=1)
def _legacy_templates():
    """Match only owned system-message templates, never source quotes/documents."""
    compiled = []
    formatter = string.Formatter()
    for template in catalog():
        parts = list(formatter.parse(template))
        if not any(field is not None for _, field, _, _ in parts):
            continue
        pattern = ''
        fields = []
        for literal, field, spec, _ in parts:
            pattern += re.escape(literal)
            if field is not None:
                pattern += '(.*?)'
                fields.append((field, spec))
        compiled.append((template, re.compile('^' + pattern + '$', re.S), fields))
    return compiled


def translate_system_text(value: str) -> str:
    """Translate an explicitly selected system field, with old-message support."""
    if not isinstance(value, str) or current_language() == 'ru':
        return value
    if value in catalog():
        return tr(value)
    for template, pattern, fields in _legacy_templates():
        found = pattern.fullmatch(value)
        if found:
            params = {}
            for (field, spec), captured in zip(fields, found.groups(), strict=True):
                try:
                    if spec.endswith('d'):
                        captured = int(captured)
                    elif spec.endswith('f'):
                        captured = float(captured)
                except ValueError:
                    break
                params[field] = captured
            else:
                return tr(template, **params)
    return value


@lru_cache(maxsize=1)
def _message_templates() -> dict[str, str]:
    return {message_id(template): template for template in catalog()}


def localize_message(record: dict) -> dict:
    result = dict(record)
    template = _message_templates().get(record.get('message_id'))
    if template is not None:
        result['message'] = tr(template, **record.get('params', {}))
    else:
        result['message'] = translate_system_text(record.get('message', ''))
    return result


def report_language(function):
    """Select the report's resolved language without leaking it to other calls."""
    @wraps(function)
    def wrapped(report, *args, **kwargs):
        language = (report.get('output_language')
                    or report.get('configuration', {}).get('output_language')
                    or current_language())
        with language_scope(language):
            return function(report, *args, **kwargs)
    return wrapped


def localized_prefix(prefix: Path) -> Path:
    prefix = Path(prefix)
    return prefix.parent / 'de' / prefix.name if current_language() == 'de' else prefix


def localized_directory(root: Path) -> Path:
    return Path(root) / 'de' if current_language() == 'de' else Path(root)
