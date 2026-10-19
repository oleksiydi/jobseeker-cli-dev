"""German template facts, recipient addressing, completeness, and real output."""
import json
import re
import shutil
import subprocess

import pytest

from app.matching.cover_letters import (
    application_context, build_letter, candidate_contact, letter_html, load_application_details, recommendations_html,
)
from app.matching.vacancy_documents import DocumentRenderer, export_settings


def contact():
    return {'full_name': 'Alex Beispiel', 'street': 'Beispielweg 12', 'postal_code': '93047',
            'city': 'Regensburg', 'country': 'Deutschland', 'email': 'alex@example.org',
            'phone': '+49 941 1234567'}


def job():
    return {'ref': 'ref-1', 'title': 'DevOps Engineer (m/w/d)', 'employer': 'Beispiel GmbH',
            'source_data': {'stellenangebote': {'referenznummer': 'ref-1',
                            'arbeitsort': {'strasse': 'WORKSITE_ONLY', 'plz': '12345', 'ort': 'Bonn'}},
                            'jobdetails': {'stellenlokationen': [{'adresse': {
                                'strasse': 'OTHER_WORKSITE', 'plz': '55555', 'ort': 'Köln'}}]}},
            'matched_skills': [
                {'label': 'Docker', 'profile_evidence': [{'status': 'declared', 'source_path': 'skills[0]',
                    'evidence_quote': 'Docker'}], 'job_evidence': [{'quote': 'Docker', 'modality': 'unknown'}]},
                {'label': 'Kubernetes', 'profile_evidence': [{'status': 'declared', 'source_path': 'skills[1]',
                    'evidence_quote': 'Kubernetes'}], 'job_evidence': [{'quote': 'Kubernetes', 'modality': 'required'}]},
            ], 'constraints': [], 'missing_required': []}


def context():
    return {'candidate': contact(), 'recipients': {'ref-1': {'company': 'Beispiel GmbH',
            'street': 'Personalstraße 7', 'postal_code': '10115', 'city': 'Berlin',
            'contact_person': 'Müller', 'salutation': 'female'}}}


def test_complete_german_letter_uses_explicit_addresses_and_uninflected_skill_slots():
    letter = build_letter(job(), context(), '2026-10-18T10:00:00+02:00')
    rendered = letter_html(letter)
    assert letter['status'] == 'ready' and not letter['critical_missing']
    for text in ('lang="de"', 'Alex Beispiel', 'Beispiel GmbH', 'Personalstraße 7', '10115 Berlin',
                 'alex@example.org', '+49 941 1234567', 'Sehr geehrte Frau Müller,',
                 '18. Oktober 2026', 'DevOps Engineer (m/w/d)', 'ref-1', 'Docker', 'Kubernetes',
                 'Mit freundlichen Grüßen'):
        assert text in rendered
    assert not re.search('[А-Яа-яЁё]', rendered)
    assert 'ENTWURF' not in rendered
    assert 'WORKSITE_ONLY' not in rendered and 'OTHER_WORKSITE' not in rendered
    assert 'Experte' not in rendered and 'langjährig' not in rendered


def test_company_and_full_name_are_enough_and_empty_fields_are_hidden():
    letter = build_letter(job(), {'candidate': {'full_name': 'Alex Beispiel'}}, '2026-10-18')
    assert letter['status'] == 'ready' and not letter['critical_missing']
    rendered = letter_html(letter)
    assert 'Beispiel GmbH' in rendered and 'Alex Beispiel' in rendered
    assert 'Sehr geehrte Damen und Herren,' in rendered
    assert 'ENTWURF' not in rendered and 'ergänzen' not in rendered
    assert '[' not in rendered and 'WORKSITE_ONLY' not in rendered and 'OTHER_WORKSITE' not in rendered
    assert 'class="date">18. Oktober 2026' in rendered
    advice = recommendations_html(letter)
    assert 'реквизиты организации' in advice and 'кандидата' in advice
    assert 'не нужно прикладывать работодателю' in advice


@pytest.mark.parametrize('missing', ['full_name', 'last_name', 'company', 'title'])
def test_missing_critical_identity_skips_letter_with_actionable_recommendations(missing):
    vacancy, data = job(), context()
    expected = {'full_name': 'candidate.full_name', 'last_name': 'candidate.full_name',
                'company': 'recipient.company', 'title': 'position.title'}[missing]
    if missing == 'full_name':
        data['candidate'].pop('full_name')
    elif missing == 'last_name':
        data['candidate']['full_name'] = 'Alex'
    elif missing == 'company':
        vacancy['employer'] = ''
        data['recipients']['ref-1'].pop('company')
    else:
        vacancy['title'] = ''
    letter = build_letter(vacancy, data, '2026-10-18')
    assert letter['status'] == 'skipped' and expected in letter['critical_missing']
    with pytest.raises(ValueError, match='requires'):
        letter_html(letter)
    assert 'Письмо не создано' in recommendations_html(letter)


def test_historical_ambiguous_negated_and_unmatched_facts_never_become_current_skill_claims():
    vacancy = job()
    vacancy['matched_skills'][0]['profile_evidence'][0]['status'] = 'project_evidence'
    vacancy['matched_skills'][1]['job_evidence'][0]['needs_review'] = True
    vacancy['matched_skills'].append({'label': 'Terraform',
        'profile_evidence': [{'status': 'declared', 'evidence_quote': 'Terraform'}],
        'job_evidence': [{'quote': 'Terraform nicht erforderlich', 'modality': 'negated'}]})
    letter = build_letter(vacancy, context(), '2026-10-18')
    assert not letter['skills'] and letter['status'] == 'ready'
    text = letter_html(letter)
    assert 'Docker' not in text and 'Kubernetes' not in text and 'Terraform' not in text
    assert '[' not in text and 'ENTWURF' not in text
    assert any(r['code'] == 'skills.no_supported_matches' for r in letter['recommendations'])


def test_scoring_gaps_and_constraints_require_review_without_disclosing_private_reasons():
    vacancy = job()
    vacancy['constraints'] = [{'status': 'conflict', 'quote': 'PRIVATE_HEALTH_NOTE'}]
    vacancy['missing_required'] = [{'label': 'Unverified degree'}]
    letter = build_letter(vacancy, context(), '2026-10-18')
    assert letter['status'] == 'ready'
    rendered = letter_html(letter)
    advice = recommendations_html(letter)
    assert 'PRIVATE_HEALTH_NOTE' not in rendered and 'PRIVATE_HEALTH_NOTE' not in advice
    assert 'ENTWURF' not in rendered and 'Unverified degree' not in rendered
    assert {'requirements.conflict', 'requirements.unconfirmed'} <= {r['code'] for r in letter['recommendations']}
    assert 'конфликт условий' in advice and 'обязательные требования' in advice


@pytest.mark.parametrize('salutation,expected', [('male', 'Sehr geehrter Herr Müller,'),
                                               ('neutral', 'Guten Tag Müller,')])
def test_salutation_uses_explicit_preference_not_a_name_gender_guess(salutation, expected):
    data = context()
    data['recipients']['ref-1']['salutation'] = salutation
    assert build_letter(job(), data, '2026-10-18')['greeting'] == expected


def test_explicit_application_contact_wins_and_unrelated_profile_data_is_excluded(tmp_path):
    raw = {'application_contact': contact(), 'candidate': {'name_variants': ['OTHER_NAME']},
           'raw_cv': 'PRIVATE_CV', 'OPENAI_API_KEY': 'EXAMPLE_SECRET'}
    data = {'candidate': {'phone': '+49 123 9876543'}, 'recipients': {}}
    result = application_context(candidate_contact(raw), data)
    assert result['candidate']['phone'] == '+49 123 9876543'
    assert result['candidate']['full_name'] == 'Alex Beispiel'
    assert 'PRIVATE_CV' not in json.dumps(result) and 'EXAMPLE_SECRET' not in json.dumps(result)
    assert not candidate_contact({'candidate': {'name_variants': ['AMBIGUOUS_NAME']}})
    path = tmp_path / 'missing.json'
    assert load_application_details(path) == {'candidate': {}, 'recipients': {}}
    with pytest.raises(ValueError, match='not found'):
        load_application_details(path, required=True)


def test_explicit_bewerbungsadresse_is_used_and_markup_stays_inert():
    vacancy = job()
    vacancy['source_data']['jobdetails']['bewerbungsadresse'] = {
        'strasse': 'Personalstraße 7', 'plz': '10115', 'ort': 'Berlin'}
    data = {'candidate': contact()}
    vacancy['title'] = 'DevOps & Engineer'
    letter = build_letter(vacancy, data, '2026-10-18')
    assert letter['recipient']['street'] == 'Personalstraße 7'
    assert letter['status'] == 'ready'
    assert 'DevOps &amp; Engineer' in letter_html(letter)


def test_generated_internal_reference_is_not_presented_as_employers_reference():
    vacancy = job()
    vacancy['source_data']['stellenangebote'].pop('referenznummer')
    letter = build_letter(vacancy, context(), '2026-10-18')
    assert not letter['reference_number']
    assert 'Referenznummer' not in letter_html(letter)


@pytest.mark.parametrize('field,value', [('email', 'not-an-email'), ('phone', '123'),
                                         ('street', '[Straße ergänzen]')])
def test_invalid_optional_contacts_are_omitted_and_only_reported_in_recommendations(field, value):
    data = context()
    data['candidate'][field] = value
    letter = build_letter(job(), data, '2026-10-18')
    assert letter['status'] == 'ready' and field not in letter['sender']
    assert value not in letter_html(letter)
    assert any(r['code'] == 'contacts.invalid' for r in letter['recommendations'])


def test_placeholder_name_is_critical():
    data = context()
    data['candidate']['full_name'] = '[Name ergänzen]'
    letter = build_letter(job(), data, '2026-10-18')
    assert letter['status'] == 'skipped' and 'candidate.full_name' in letter['critical_missing']


@pytest.mark.parametrize('options', [{'enabled': 'true'}, {'max_skills': 0}, {'max_skills': True},
                                    {'details_file': ''}])
def test_invalid_letter_config_is_rejected(options):
    with pytest.raises(ValueError):
        export_settings({'document_export': {'cover_letter': options}})


def test_german_letter_roundtrips_real_pdf_and_rtf_on_one_page(tmp_path):
    pytest.importorskip('weasyprint')
    if not all(shutil.which(p) for p in ('pandoc', 'pdftotext', 'pdfinfo')):
        pytest.skip('Document verification programs not installed')
    letter = build_letter(job(), context(), '2026-10-18')
    prefix = tmp_path / 'Anschreiben'
    DocumentRenderer().render(letter_html(letter), prefix)
    for command in (['pdftotext', str(prefix.with_suffix('.pdf')), '-'],
                    ['pandoc', '-f', 'rtf', '-t', 'plain', str(prefix.with_suffix('.rtf'))]):
        text = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout
        assert 'Sehr geehrte Frau Müller,' in text
        assert 'Alex Beispiel' in text and 'Personalstraße 7' in text
        assert 'Docker' in text and 'Kubernetes' in text and 'Mit freundlichen Grüßen' in text
        assert not re.search('[А-Яа-яЁё]', text)
    info = subprocess.run(['pdfinfo', str(prefix.with_suffix('.pdf'))],
                          capture_output=True, text=True, check=True, timeout=30).stdout
    assert re.search(r'Pages:\s+1\b', info)
