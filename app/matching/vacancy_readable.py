"""Human-readable vacancy sheets from the captured BA source, without ranking facts."""
from __future__ import annotations

import json
import re

from app.core.i18n import tr, translated

from app.matching.vacancy_documents import (
    _clean, _display_date, _display_location, _display_schedule, _e, _lines,
    _p, _page, _safe_url, _table, source_text,
)

_SERVICE = {
    '_id', 'created_at', 'updated_at', 'last_checked_at', 'meta_hash', 'details_hash',
    'last_seen_remote_updated_at', 'last_seen_remote_updated_at_dt', 'is_active',
    'arbeitgeberKundennummerHash', 'kundennummerHash',
}
_DESCRIPTION = {'stellenangebotsBeschreibung', 'description'}
_LABELS = {
    'titel': 'Название вакансии', 'stellenangebotsTitel': 'Название вакансии', 'title': 'Название вакансии',
    'referenznummer': 'Номер вакансии', 'refnr': 'Номер вакансии', 'ref': 'Номер вакансии',
    'arbeitgeber': 'Работодатель', 'firma': 'Работодатель', 'employer': 'Работодатель',
    'beruf': 'Профессия', 'hauptberuf': 'Профессия', 'role': 'Профессия',
    'alleBerufe': 'Все подходящие профессии', 'weitereBerufe': 'Другие профессии',
    'alternativBeruf1': 'Альтернативная профессия', 'alternativBeruf2': 'Альтернативная профессия',
    'stellenangebotsart': 'Тип предложения', 'chiffrenummer': 'Шифр вакансии',
    'arbeitsort': 'Место работы', 'arbeitsorte': 'Места работы', 'stellenlokationen': 'Места работы',
    'location': 'Место работы', 'adresse': 'Адрес', 'land': 'Страна', 'region': 'Регион',
    'ort': 'Город', 'plz': 'Почтовый индекс', 'strasse': 'Улица', 'hausnummer': 'Дом',
    'breite': 'Широта', 'laenge': 'Долгота', 'entfernung': 'Расстояние по данным источника',
    'arbeitszeitVollzeit': 'Полная занятость', 'arbeitszeitTeilzeitVormittag': 'Неполный день: утро',
    'arbeitszeitTeilzeitNachmittag': 'Неполный день: вторая половина дня',
    'arbeitszeitTeilzeitAbend': 'Неполный день: вечер', 'arbeitszeitTeilzeitFlexibel': 'Неполный день: гибко',
    'arbeitszeitSchichtNachtWochenende': 'Смены, ночная работа или выходные',
    'istGeringfuegigeBeschaeftigung': 'Minijob', 'vertragsdauer': 'Длительность договора',
    'befristetBis': 'Договор до', 'befristungInMonaten': 'Срок договора в месяцах',
    'eintrittsdatum': 'Начало работы', 'eintrittszeitraum': 'Период начала работы',
    'von': 'С', 'bis': 'По', 'homeofficemoeglich': 'Возможна работа из дома',
    'homeofficeprozent': 'Работа из дома, %', 'homeofficetyp': 'Формат работы из дома',
    'quereinstiegGeeignet': 'Подходит для смены профессии',
    'istArbeitnehmerUeberlassung': 'Работа через предоставление персонала',
    'istPrivateArbeitsvermittlung': 'Частное агентство по трудоустройству',
    'istBehinderungGefordert': 'В источнике указано требование инвалидности',
    'istBetreut': 'Предложение сопровождается службой занятости',
    'verguetungsangabe': 'Сведения об оплате', 'verguetung': 'Оплата', 'salary': 'Оплата',
    'festgehalt': 'Фиксированная оплата', 'gehaltsspanneVon': 'Оплата: нижняя граница',
    'gehaltsspanneBis': 'Оплата: верхняя граница', 'artDerVerguetung': 'Вид оплаты',
    'kontakt': 'Контакты', 'kontaktdaten': 'Контакты', 'ansprechpartner': 'Контактное лицо',
    'email': 'Электронная почта', 'telefon': 'Телефон', 'name': 'Имя / название',
    'vorname': 'Имя', 'nachname': 'Фамилия', 'bewerbung': 'Подача заявки',
    'bewerbungsarten': 'Способы подачи заявки', 'externeURL': 'Внешняя ссылка',
    'externeUrl': 'Внешняя ссылка', 'url': 'Ссылка',
    'allianzpartnerName': 'Партнёр публикации', 'allianzpartnerUrl': 'Сайт партнёра',
    'aenderungsdatum': 'Дата изменения', 'modifikationsTimestamp': 'Дата изменения списка',
    'datumErsteVeroeffentlichung': 'Впервые опубликовано',
    'aktuelleVeroeffentlichungsdatum': 'Текущая дата публикации',
    'veroeffentlichungszeitraum': 'Период публикации',
}
_ENUMS = {
    'UNBEFRISTET': 'Бессрочный договор', 'BEFRISTET': 'Срочный договор',
    'ARBEIT': 'Работа', 'AUSBILDUNG': 'Обучение профессии', 'PRAKTIKUM': 'Практика',
    'DEUTSCHLAND': 'Deutschland', 'BAYERN': 'Bayern',
    'KEINE_ANGABEN': 'Не указано', 'KEINE_ANGABE': 'Не указано',
    'JAHRESGEHALT': 'Годовая оплата', 'FESTGEHALT': 'Фиксированная оплата',
    'GEHALTSSPANNE': 'Диапазон оплаты', 'NACH_VEREINBARUNG': 'По договорённости',
    'ANGABE_IN_PROZENT': 'Доля работы из дома указана в процентах',
}


def _label(key: str) -> str:
    # Unknown business fields stay visible, with readable word boundaries.
    return translated(_LABELS).get(key) or re.sub(r'(?<=[a-zäöüß])(?=[A-ZÄÖÜ])', ' ', key).replace('_', ' ').capitalize()


def _value(value) -> str:
    if value is None or value == '':
        return tr('Не указано')
    if isinstance(value, bool):
        return tr('Да') if value else tr('Нет')
    text = str(value)
    if re.fullmatch(r'\d{4}-\d\d-\d\d', text):
        return _display_date(text).split(',')[0]
    if re.match(r'^\d{4}-\d\d-\d\d(?:T| |$)', text):
        return _display_date(text)
    return translated(_ENUMS).get(text, source_text(text))


def _field(label: str, value) -> str:
    if isinstance(value, dict):
        children = ''.join(_field(_label(str(k)), v) for k, v in value.items()
                           if k not in _SERVICE and not str(k).endswith('_dt'))
        return f'<h3>{_e(label)}</h3>{children}' if children else ''
    if isinstance(value, list):
        if not value:
            return ''
        if all(not isinstance(v, (dict, list)) for v in value):
            return _p(label + ': ' + '; '.join(_value(v) for v in value))
        return f'<h3>{_e(label)}</h3>' + ''.join(
            _field(tr('Вариант {p0}', p0=i), v) for i, v in enumerate(value, 1))
    url = _safe_url(value)
    text = f'<a href="{_e(url)}">{_e(url)}</a>' if url else _lines(_value(value))
    return f'<p><strong>{_e(label)}:</strong> {text}</p>'


def _section(key: str) -> str:
    if key.startswith(('arbeitszeit', 'homeoffice', 'ist', 'befrist')) or key in {
        'vertragsdauer', 'eintrittsdatum', 'eintrittszeitraum', 'quereinstiegGeeignet', 'schedule',
    }:
        return tr('Условия работы')
    if key in {'verguetungsangabe', 'verguetung', 'salary', 'festgehalt',
               'gehaltsspanneVon', 'gehaltsspanneBis', 'artDerVerguetung'}:
        return tr('Оплата')
    if key in {'arbeitsort', 'arbeitsorte', 'stellenlokationen', 'location', 'entfernung', 'weitere_orte'}:
        return tr('Места работы')
    if key in {'kontakt', 'kontaktdaten', 'ansprechpartner', 'bewerbung', 'bewerbungsarten',
               'email', 'telefon', 'externeURL', 'externeUrl', 'url', 'allianzpartnerName', 'allianzpartnerUrl'}:
        return tr('Контакты и подача заявки')
    if key in {'aenderungsdatum', 'modifikationsTimestamp', 'datumErsteVeroeffentlichung',
               'aktuelleVeroeffentlichungsdatum', 'veroeffentlichungszeitraum'}:
        return tr('Публикация и даты')
    return tr('Другие сведения о вакансии')


def _description(job: dict) -> str:
    source = job.get('source_data', {})
    details = source.get('jobdetails') or {}
    return source_text(details.get('stellenangebotsBeschreibung',
                                  source.get('description', job.get('description', ''))))


def _inline(text: str) -> str:
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', _e(text))


def _description_html(text: str) -> str:
    """Format existing headings/lists; never summarize or invent vacancy text."""
    output, paragraph, items = [], [], []
    list_tag = ''

    def flush():
        if paragraph:
            output.append('<p>' + '<br>'.join(_inline(line) for line in paragraph) + '</p>')
            paragraph.clear()
        if items:
            output.append(f'<{list_tag}>' + ''.join(
                f'<li value="{number}">{_inline(text)}</li>' if number is not None
                else f'<li>{_inline(text)}</li>' for number, text in items) + f'</{list_tag}>')
            items.clear()

    for line in text.splitlines():
        heading = re.match(r'^\s*#{1,6}\s+(.+)$', line)
        bullet = re.match(r'^\s*(?:[-*•]\s+|\d+[.)]\s+)(.+)$', line)
        if not line.strip() or heading:
            flush()
            if heading:
                output.append('<h3>' + _inline(heading[1]) + '</h3>')
        elif bullet:
            tag = 'ol' if re.match(r'^\s*\d', line) else 'ul'
            if paragraph or items and tag != list_tag:
                flush()
            list_tag = tag
            number = re.match(r'^\s*(\d+)', line)
            items.append((int(number[1]) if number else None, bullet[1]))
        else:
            if items:
                flush()
            paragraph.append(line)
    flush()
    return ''.join(output)


def readable_vacancy_html(job: dict, rank: int, evaluated_at: str) -> str:
    """Show complete business information once, with full source retained in manifest."""
    body = _p(tr('ВАКАНСИЯ {p0:02d} / ПОДБОР ОТ {p1}', p0=rank, p1=_display_date(evaluated_at)), 'eyebrow')
    body += '<h1>' + _e(job['title']) + '</h1>'
    body += _table([
        (tr('Работодатель'), job.get('employer') or tr('Не указано')),
        (tr('Место работы'), _display_location(job.get('location', ''))),
        (tr('Занятость'), _display_schedule(job.get('schedule', ''))),
        (tr('Профессия'), job.get('role') or tr('Не указано')), (tr('Номер вакансии'), job['ref']),
    ])
    url = _safe_url(job.get('url'))
    if url:
        body += tr('<p><a href="{p0}">Открыть вакансию в источнике</a></p>', p0=_e(url))
    groups = {name: [] for name in (tr('Условия работы'), tr('Оплата'), tr('Места работы'),
                                   tr('Контакты и подача заявки'), tr('Публикация и даты'),
                                   tr('Другие сведения о вакансии'))}
    source = _clean(job.get('source_data', {}))
    sources = ([(tr('Список вакансий'), source.get('stellenangebote') or {}),
                (tr('Подробные сведения'), source.get('jobdetails') or {})]
               if 'stellenangebote' in source or 'jobdetails' in source else [(tr('Источник'), source)])
    seen = {(translated(_LABELS)[key], json.dumps(job[key], ensure_ascii=False))
            for key in ('title', 'ref', 'employer', 'role') if job.get(key)}
    for origin, fields in sources:
        for key, value in fields.items():
            if key in _SERVICE or key in _DESCRIPTION or key.endswith('_dt') or value is None or value == '':
                continue
            label = _label(key)
            identity = (label, json.dumps(value, ensure_ascii=False, sort_keys=True))
            if identity in seen:
                continue
            same_label = any(previous[0] == label for previous in seen)
            seen.add(identity)
            content = _field(label + (f' ({origin.lower()})' if same_label else ''), value)
            if content:
                groups[_section(key)].append(content)
    for name in (tr('Условия работы'), tr('Оплата'), tr('Места работы'), tr('Контакты и подача заявки')):
        body += f'<h2>{name}</h2>' + (''.join(groups[name]) or _p(tr('Не указано в полученных данных.')))
    description = _description(job)
    body += tr('<h2>Полный текст вакансии</h2>')
    body += _description_html(description) if description else _p(tr(
        'Полный текст в полученных данных отсутствует.',
    ))
    for name in (tr('Другие сведения о вакансии'), tr('Публикация и даты')):
        if groups[name]:
            body += f'<h2>{name}</h2>' + ''.join(groups[name])
    return _page(job['title'], body).replace(tr('JOBSEEKER / ЛОКАЛЬНАЯ ОЦЕНКА'), tr('JOBSEEKER / ВАКАНСИЯ'))
