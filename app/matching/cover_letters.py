"""Deterministic German application letters with explicit, private contact data.

Only declared skills with vacancy citations become first-person statements.
Work-site addresses, inferred experience and profile name variants are never
promoted to application facts. Advice stays in a separate private document.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from app.core.i18n import (
    current_language, localize_message, message_record, report_language, tr,
)

TEMPLATE_VERSION = 'anschreiben-de-v4'
DEFAULT_DETAILS_FILE = 'conf/profiles/application.private.json'
CONTACT_FIELDS = ('full_name', 'street', 'postal_code', 'city', 'country', 'email', 'phone')
RECIPIENT_FIELDS = ('company', 'street', 'postal_code', 'city', 'country',
                    'contact_person', 'salutation', 'email', 'phone')
_MONTHS = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
           'August', 'September', 'Oktober', 'November', 'Dezember')
_ALIASES = {
    'full_name': ('full_name', 'vollstaendiger_name', 'Name'),
    'street': ('street', 'strasse', 'straße', 'Straße'),
    'postal_code': ('postal_code', 'plz', 'PLZ'), 'city': ('city', 'ort', 'Ort'),
    'country': ('country', 'land', 'Land'), 'email': ('email', 'e_mail', 'Email', 'E-Mail'),
    'phone': ('phone', 'telefon', 'Telefon'), 'company': ('company', 'firma', 'arbeitgeber'),
    'contact_person': ('contact_person', 'ansprechpartner', 'name'), 'salutation': ('salutation',),
}
LETTER_CSS = """
@page { size: A4; margin: 20mm 22mm; }
body { font-family: 'DejaVu Sans', sans-serif; color: #1c2d3c;
       font-size: 10.5pt; line-height: 1.4; }
p { margin: 0 0 3.5mm; }
.sender { text-align: right; font-size: 9pt; margin-bottom: 6mm; }
.recipient { margin-bottom: 5mm; }
.date { text-align: right; margin-bottom: 5mm; }
.subject { font-weight: bold; font-size: 12pt; margin-bottom: 1.5mm; }
.reference { font-size: 9pt; margin-bottom: 6mm; }
.signature { margin-top: 6mm; }
li { margin-bottom: 1mm; }
ul { margin-top: 0; margin-bottom: 4mm; }
a { color: inherit; text-decoration: none; }
"""


def _text(value) -> str:
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _pick_contact(raw: dict, fields: tuple) -> dict:
    result = {}
    for field in fields:
        value = next((_text(raw.get(key)) for key in _ALIASES[field] if _text(raw.get(key))), '')
        if value:
            result[field] = value
    return result


def candidate_contact(raw: dict) -> dict:
    """Read explicit application/contact blocks, not CV prose or name variants."""
    candidate = raw.get('candidate') if isinstance(raw.get('candidate'), dict) else {}
    application = raw.get('application') if isinstance(raw.get('application'), dict) else {}
    blocks = [raw.get('application_contact'), application.get('candidate'),
              candidate.get('contact'), raw.get('contact'), raw.get('PersoenlicheDaten')]
    result = {}
    for block in blocks:
        if isinstance(block, dict):
            for field, value in _pick_contact(block, CONTACT_FIELDS).items():
                result.setdefault(field, value)
    if _text(candidate.get('full_name')):
        result.setdefault('full_name', _text(candidate['full_name']))
    return result


def load_application_details(path: Path, *, required: bool = False) -> dict:
    """Load a private override file; missing optional defaults leave contacts empty."""
    if not path.is_file():
        if required:
            raise ValueError('Application details file was not found; check --application-file')
        return {'candidate': {}, 'recipients': {}}
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ValueError('Cannot read application details JSON') from None
    return normalize_application_details(raw)


def normalize_application_details(raw: dict) -> dict:
    """Whitelist printable contacts and per-vacancy recipients; reject bad shapes."""
    if not isinstance(raw, dict) or not isinstance(raw.get('candidate', {}), dict):
        raise ValueError('Application details must contain a candidate object')
    recipients = raw.get('recipients', {})
    if not isinstance(recipients, dict):
        raise ValueError('Application recipients must be an object keyed by vacancy reference')
    clean = {}
    for ref, recipient in recipients.items():
        if not isinstance(recipient, dict):
            raise ValueError('Each application recipient must be an object')
        clean[str(ref)] = _pick_contact(recipient, RECIPIENT_FIELDS)
        if clean[str(ref)].get('salutation', 'neutral') not in {'neutral', 'female', 'male'}:
            raise ValueError('Recipient salutation must be neutral, female, or male')
    return {'candidate': _pick_contact(raw.get('candidate', {}), CONTACT_FIELDS), 'recipients': clean}


def application_context(profile_contact: dict, details: dict) -> dict:
    details = normalize_application_details(details)
    details['candidate'] = {**_pick_contact(profile_contact, CONTACT_FIELDS), **details['candidate']}
    return details


def _recipient(job: dict, overrides: dict) -> dict:
    result = {'company': _text(job['employer'])} if _text(job.get('employer')) else {}
    source = job.get('source_data') or {}
    for section in (source.get('jobdetails') or {}, source.get('stellenangebote') or {}, source):
        for key in ('bewerbungskontakt', 'kontakt', 'kontaktdaten'):
            contact = section.get(key)
            if not isinstance(contact, dict):
                continue
            values = _pick_contact(contact, RECIPIENT_FIELDS)
            address = contact.get('adresse') or contact.get('address')
            if isinstance(address, dict):
                values.update(_pick_contact(address, RECIPIENT_FIELDS))
            for field, value in values.items():
                result.setdefault(field, value)
        address = section.get('bewerbungsadresse')
        if isinstance(address, dict):
            for field, value in _pick_contact(address, RECIPIENT_FIELDS).items():
                result.setdefault(field, value)
    # Stellenlokationen/Arbeitsort are deliberately not recipient addresses.
    return {**result, **_pick_contact(overrides.get(job['ref'], {}), RECIPIENT_FIELDS)}


def _selected_skills(job: dict, maximum: int) -> list[dict]:
    result, seen = [], set()
    for skill in job.get('matched_skills', []):
        label = _text(skill.get('label'))
        if (not 2 <= len(label) <= 80 or len(label.split()) > 6
                or re.search(r'[А-Яа-яЁё\[\]{}<>]', label) or label.casefold() in seen):
            continue
        profile = [fact for fact in skill.get('profile_evidence', [])
                   if fact.get('status') == 'declared' and fact.get('evidence_quote')]
        vacancy = [fact for fact in skill.get('job_evidence', [])
                   if fact.get('quote') and not fact.get('needs_review')
                   and fact.get('modality') not in {'negated', 'irrelevant'}]
        if not profile or not vacancy:
            continue
        seen.add(label.casefold())
        result.append({'label': label,
                       'profile_evidence': [{k: f[k] for k in ('source_path', 'evidence_quote', 'status') if k in f}
                                            for f in profile],
                       'job_evidence': [{k: f[k] for k in ('quote', 'source', 'text_span') if k in f}
                                        for f in vacancy]})
        if len(result) >= maximum:
            break
    return result


def _usable(value: str) -> bool:
    return bool(value) and not re.search(
        r'[<>{}]|\[[^\]]*(?:ergänzen|eintragen|ausfüllen|name|firma|unternehmen|position|заполн)[^\]]*\]',
        value, re.I)


def _selected_related_skills(job: dict, maximum: int, direct: list[dict]) -> list[dict]:
    """Only declared facts backed by an explicit, direction-appropriate ESCO edge."""
    direct_labels = {skill['label'].casefold() for skill in direct}
    approved = []
    for item in job.get('related_skills', []):
        path = item.get('relation_path', [])
        candidate = item.get('candidate_concept', {})
        target = item.get('target_concept', {})
        if (item.get('letter_eligible') is not True or len(path) != 1
                or candidate.get('kind') != 'skill' or target.get('kind') != 'skill'
                or candidate.get('source') != 'esco' or target.get('source') != 'esco'
                or item.get('label', '').casefold() in direct_labels):
            continue
        edge = path[0]
        endpoints = ((candidate.get('id'), target.get('id')) if edge.get('traversal') == 'forward'
                     else (target.get('id'), candidate.get('id')))
        if (not all(endpoints) or (edge.get('from'), edge.get('to')) != endpoints
                or edge.get('traversal') not in {'forward', 'reverse'}):
            continue
        evidence = [fact for fact in item.get('job_evidence', [])
                    if fact.get('modality') in {'required', 'preferred'}
                    or fact.get('context_role') == 'task']
        if not evidence:
            continue
        allowed = (edge.get('type') in {'hasOptionalSkill', 'hasEssentialSkill'}
                   or (edge.get('type') == 'broaderSkill' and edge.get('traversal') == 'forward')
                   or (edge.get('type') == 'narrowerSkill' and edge.get('traversal') == 'reverse'))
        if edge.get('source') == 'esco' and allowed:
            approved.append({**item, 'job_evidence': evidence})
    selected = _selected_skills({'matched_skills': approved}, min(maximum, 3))
    for skill in selected:
        original = next(item for item in approved if item['label'] == skill['label'])
        skill.update({key: original[key] for key in (
            'candidate_concept', 'target_concept', 'relation_path', 'relation_explanation') if key in original})
    return selected


def _printable_contact(raw: dict, fields: tuple) -> tuple[dict, list[str]]:
    values = _pick_contact(raw, fields)
    invalid = [key for key, value in values.items() if not _usable(value)]
    if values.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        invalid.append('email')
    if values.get('phone') and not 6 <= len(re.sub(r'\D', '', values['phone'])) <= 15:
        invalid.append('phone')
    return {key: value for key, value in values.items() if key not in invalid}, invalid


def build_letter(job: dict, context: dict, letter_date: str, *, max_skills: int = 3) -> dict:
    """Prepare a clean German letter; only identity, company and position are critical."""
    sender, bad_sender = _printable_contact(context.get('candidate', {}), CONTACT_FIELDS)
    recipient, bad_recipient = _printable_contact(
        _recipient(job, context.get('recipients', {})), RECIPIENT_FIELDS)
    title = _text(job.get('title'))
    if not _usable(title):
        title = ''
    skills = _selected_skills(job, max_skills)
    related_skills = _selected_related_skills(job, max_skills, skills)
    critical_missing, recommendations = [], []

    def advise(code, message):
        recommendations.append({'code': code, **message_record(message)})

    name_parts = sender.get('full_name', '').split()
    if len(name_parts) < 2 or not all(any(c.isalpha() for c in part) for part in name_parts):
        critical_missing.append('candidate.full_name')
        advise('candidate.full_name', tr('Укажите имя и фамилию кандидата в поле full_name.'))
    if not recipient.get('company'):
        critical_missing.append('recipient.company')
        advise('recipient.company', tr('Укажите название организации для этой позиции.'))
    if not title:
        critical_missing.append('position.title')
        advise('position.title', tr('Укажите название позиции в исходных данных вакансии.'))

    sender_labels = {'street': tr('улица и дом'), 'postal_code': tr('индекс'), 'city': tr('город'),
                     'country': tr('страна'), 'email': 'email', 'phone': tr('телефон')}
    recipient_labels = {'street': tr('улица и дом'), 'postal_code': tr('индекс'), 'city': tr('город'),
                        'country': tr('страна'), 'contact_person': tr('контактное лицо'),
                        'email': 'email', 'phone': tr('телефон')}
    missing_sender = [label for key, label in sender_labels.items() if not sender.get(key)]
    if missing_sender:
        advise('candidate.optional_contacts', tr(
            'При наличии дополните реквизиты кандидата: {p0}. Их отсутствие не мешает созданию '
            'письма.',
            p0=', '.join(missing_sender),
        ))
    missing_recipient = [label for key, label in recipient_labels.items() if not recipient.get(key)]
    if missing_recipient:
        advise('recipient.optional_contacts', tr(
            'При наличии дополните реквизиты организации: {p0}. Используйте адрес для подачи '
            'заявки; адрес места работы может отличаться. В письме показаны только известные '
            'данные.',
            p0=', '.join(missing_recipient),
        ))
    if bad_sender or bad_recipient:
        advise('contacts.invalid', tr(
            'Проверьте формат реквизитов: некорректные значения или заполнители исключены из '
            'письма. Поля кандидата: {p0}; организации: {p1}.',
            p0=', '.join(bad_sender) or tr('нет'),
            p1=', '.join(bad_recipient) or tr('нет'),
        ))
    if not skills and not related_skills:
        advise('skills.no_supported_matches', tr(
            'Подтверждённых совпадений заявленных навыков не найдено. Письмо содержит общее '
            'обращение без утверждений о навыках; при необходимости дополните профессиональный '
            'профиль.',
        ))
    for item in related_skills:
        advise('skills.related_in_letter', tr(
            'В письмо добавлен заявленный навык «{p0}». Основание: связь ESCO с «{p1}», упомянутым '
            'в вакансии. Это не подтверждение владения требуемым навыком.',
            p0=item['label'],
            p1=item['target_concept']['label'],
        ))
    for item in job.get('relation_context_skills', []):
        advise('skills.relation_context', tr(
            'Возможная связь для проверки: «{p0}» → «{p1}». {p2}. В письмо автоматически не '
            'добавлена.',
            p0=item['label'],
            p1=item['target_concept']['label'],
            p2=item['relation_explanation'],
        ))
    if job.get('reference_relations_status') == 'unavailable':
        advise('skills.relations_unavailable', tr(
            'Связи справочников недоступны; письмо составлено по обычным подтверждённым '
            'совпадениям.',
        ))
    if any(item.get('status') == 'conflict' for item in job.get('constraints', [])):
        advise('requirements.conflict', tr(
            'В оценке обнаружен конфликт условий вакансии с профилем. Перед отправкой проверьте '
            'раздел ограничений в документе оценки.',
        ))
    if job.get('missing_required'):
        advise('requirements.unconfirmed', tr(
            'Есть обязательные требования без подтверждения в профиле. Проверьте их в документе '
            'оценки: письмо не подтверждает их выполнение.',
        ))

    person = recipient.get('contact_person')
    prefix = {'female': 'Sehr geehrte Frau', 'male': 'Sehr geehrter Herr',
              'neutral': 'Guten Tag'}.get(recipient.get('salutation'), 'Guten Tag')
    greeting = f'{prefix} {person},' if person else 'Sehr geehrte Damen und Herren,'
    parsed_date = datetime.fromisoformat(letter_date.replace('Z', '+00:00'))
    source = job.get('source_data') or {}
    ref = next((_text(section.get(key))
                for section in (source.get('jobdetails') or {}, source.get('stellenangebote') or {}, source)
                for key in ('referenznummer', 'ref', 'refnr') if _text(section.get(key))), '')
    if not _usable(ref):
        ref = ''
    return {'template_version': TEMPLATE_VERSION, 'language': 'de',
            'output_language': current_language(), 'ref': job['ref'],
            'title': title, 'reference_number': ref, 'sender': sender, 'recipient': recipient,
            'date': f'{parsed_date.day}. {_MONTHS[parsed_date.month - 1]} {parsed_date.year}',
            'greeting': greeting, 'skills': skills, 'related_skills': related_skills,
            'critical_missing': critical_missing,
            'recommendations': recommendations, 'status': 'skipped' if critical_missing else 'ready'}


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _address(data: dict, *, sender: bool) -> str:
    name_key = 'full_name' if sender else 'company'
    lines = [data.get(name_key, '')]
    if not sender:
        lines.append(data.get('contact_person', ''))
    lines += [data.get('street', ''),
              ' '.join(data[key] for key in ('postal_code', 'city') if data.get(key)),
              data.get('country', ''),
              ' · '.join(data[key] for key in ('email', 'phone') if data.get(key))]
    return '<br>'.join(_e(line) for line in lines if line)


def letter_html(letter: dict) -> str:
    """Render only German fixed prose and escaped facts, ready for PDF and RTF."""
    if letter['status'] != 'ready':
        raise ValueError('Anschreiben requires candidate first and last name, company and position')
    body = ''
    body += '<p class="sender">' + _address(letter['sender'], sender=True) + '</p>'
    body += '<p class="recipient">' + _address(letter['recipient'], sender=False) + '</p>'
    city = letter['sender'].get('city')
    date = f"{city}, {letter['date']}" if city else letter['date']
    body += '<p class="date">' + _e(date) + '</p>'
    body += '<p class="subject"><strong>Bewerbung – ' + _e(letter['title']) + '</strong></p>'
    if letter['reference_number']:
        body += '<p class="reference">Referenznummer: ' + _e(letter['reference_number']) + '</p>'
    body += '<p>' + _e(letter['greeting']) + '</p>'
    body += ('<p>mit Interesse habe ich Ihre Stellenausschreibung gelesen. '
             'Hiermit bewerbe ich mich um die ausgeschriebene Position.</p>')
    if letter['skills']:
        body += '<p>Zu meinen Kenntnissen gehören folgende in Ihrer Ausschreibung angesprochene Themen:</p><ul>'
        body += ''.join('<li>' + _e(skill['label']) + '</li>' for skill in letter['skills']) + '</ul>'
        body += '<p>Diese Kenntnisse möchte ich bei den von Ihnen beschriebenen Aufgaben einbringen.</p>'
    if letter.get('related_skills'):
        labels = [_e(skill['label']) for skill in letter['related_skills']]
        if len(labels) <= 2:
            areas = ('im Bereich ' + labels[0] if len(labels) == 1
                     else 'in den Bereichen ' + ' und '.join(labels))
            body += ('<p>Auch meine Kenntnisse ' + areas
                     + ' könnten für die Aufgaben in Ihrem Team hilfreich sein.</p>')
        else:
            body += ('<p>Ergänzend bringe ich Kenntnisse in folgenden Bereichen mit, die bei passenden '
                     'Aufgaben in Ihrem Team ebenfalls hilfreich sein können:</p><ul>')
            body += ''.join('<li>' + label + '</li>' for label in labels) + '</ul>'
    body += ('<p>Gern erläutere ich Ihnen in einem persönlichen Gespräch meinen beruflichen Hintergrund '
             'und die Anknüpfungspunkte zu Ihrer Position. Ich freue mich über Ihre Rückmeldung.</p>'
             '<p>Mit freundlichen Grüßen</p><p class="signature">')
    body += _e(letter['sender']['full_name']) + '</p>'
    return ('<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Anschreiben</title>'
            '<style>' + LETTER_CSS + '</style></head><body>' + body + '</body></html>')


@report_language
def recommendations_html(letter: dict) -> str:
    """Private advice for the candidate, separate from the employer-facing letter."""
    body = tr('<h1>Рекомендации по заявке</h1>')
    body += tr('<p><strong>Для кандидата. Этот документ не нужно прикладывать работодателю.</strong></p>')
    body += tr('<p>Организация: ') + _e(letter['recipient'].get('company') or tr('не указана')) + '<br>'
    body += tr('Позиция: ') + _e(letter['title'] or tr('не указана')) + '</p>'
    if letter['status'] == 'skipped':
        body += tr(
            '<p><strong>Письмо не создано: заполните критические данные ниже и повторите '
            'экспорт.</strong></p>',
        )
    else:
        body += tr('<p>Письмо создано. Необязательные реквизиты и замечания не включены в Anschreiben.</p>')
    if letter['recommendations']:
        body += '<ul>' + ''.join('<li>' + _e(localize_message(item)['message']) + '</li>'
                               for item in letter['recommendations']) + '</ul>'
    else:
        body += tr('<p>Дополнительных замечаний по доступным данным нет.</p>')
    body += (tr(
        '<p>Перед отправкой прочитайте письмо, проверьте актуальность вакансии и способ подачи '
        'заявки. Создание письма не означает подтверждение всех требований работодателя.</p>',
    ))
    return (tr(
        '<!doctype html><html lang="ru"><head><meta '
        'charset="utf-8"><title>Рекомендации</title><style>',
    ) + LETTER_CSS + '</style></head><body>' + body + '</body></html>')
