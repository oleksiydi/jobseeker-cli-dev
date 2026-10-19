from __future__ import annotations

from app.normalization.synonyms_contract import (
    validate_dictionary_payload,
    validate_pending_record,
)


def test_validate_pending_record_accepts_valid():
    allowed_domains = {"it_software_daten", "sonstiges"}
    record = {
        "run_id": "syn-1",
        "alias": "k8s",
        "canonical": "Kubernetes",
        "domain": "it_software_daten",
        "category": "stack",
        "facet": "technology_stack",
        "skill_class": "not_applicable",
        "confidence": 0.98,
        "source": "llm",
        "review_status": "auto_suggested",
        "created_at": "2026-03-06T12:00:00+00:00",
    }
    assert validate_pending_record(record, allowed_domains=allowed_domains) == []


def test_validate_pending_record_rejects_unknown_domain():
    allowed_domains = {"it_software_daten", "sonstiges"}
    record = {
        "run_id": "syn-1",
        "alias": "monitoring",
        "canonical": "Monitoring",
        "domain": "unknown_domain",
        "category": "skills",
        "facet": "skill_requirement",
        "skill_class": "professional",
        "confidence": 0.5,
        "source": "llm",
        "review_status": "auto_suggested",
        "created_at": "2026-03-06T12:00:00+00:00",
    }
    errors = validate_pending_record(record, allowed_domains=allowed_domains)
    assert any(err.startswith("invalid_domain=") for err in errors)


def test_validate_dictionary_payload_rejects_unknown_domain():
    allowed_domains = {"it_software_daten", "sonstiges"}
    payload = {
        "version": 1,
        "entries": [
            {
                "alias": "k8s",
                "canonical": "Kubernetes",
                "domain": "it_software_daten",
                "category": "stack",
                "facet": "technology_stack",
                "skill_class": "not_applicable",
                "domain_scope": "global_safe",
                "facet_scope": "specific",
            },
            {
                "alias": "xyz",
                "canonical": "XYZ",
                "domain": "bad_domain",
                "category": "skills",
                "facet": "other",
                "skill_class": "professional",
                "domain_scope": "specific",
                "facet_scope": "specific",
            },
        ],
    }
    errors = validate_dictionary_payload(payload, allowed_domains=allowed_domains)
    assert any("invalid_domain" in err for err in errors)


def test_validate_dictionary_payload_accepts_scope_fields():
    allowed_domains = {"it_software_daten", "sonstiges"}
    payload = {
        "version": 1,
        "entries": [
            {
                "alias": "k8s",
                "canonical": "Kubernetes",
                "domain": "it_software_daten",
                "category": "stack",
                "facet": "technology_stack",
                "skill_class": "not_applicable",
                "domain_scope": "global_safe",
                "facet_scope": "specific",
            }
        ],
    }
    assert validate_dictionary_payload(payload, allowed_domains=allowed_domains) == []


def test_validate_dictionary_payload_rejects_invalid_scope_fields():
    allowed_domains = {"it_software_daten", "sonstiges"}
    payload = {
        "version": 1,
        "entries": [
            {
                "alias": "k8s",
                "canonical": "Kubernetes",
                "domain": "it_software_daten",
                "category": "stack",
                "facet": "technology_stack",
                "skill_class": "not_applicable",
                "domain_scope": "wrong",
                "facet_scope": "wrong",
            }
        ],
    }
    errors = validate_dictionary_payload(payload, allowed_domains=allowed_domains)
    assert any("invalid_domain_scope" in err for err in errors)
    assert any("invalid_facet_scope" in err for err in errors)
