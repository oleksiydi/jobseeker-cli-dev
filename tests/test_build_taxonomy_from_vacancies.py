from tools.build_taxonomy_from_vacancies import (
    ExtractedTerm,
    _build_aggregate,
    _load_domains,
    _normalize_domain,
    _sanitize_term_record,
)


def test_sanitize_term_record_accepts_valid_item():
    row = _sanitize_term_record(
        raw={
            "term": " Monitoring ",
            "category": "skills",
            "facet": "skill_requirement",
            "domain_hint": "healthcare",
            "domain_confidence": 0.9,
            "context_phrase": "patient monitoring",
        },
        referenznummer="123",
        vacancy_domain_top="healthcare",
        vacancy_domain_confidence=0.8,
    )
    assert row is not None
    assert row.term == "Monitoring"
    assert row.term_key == "monitoring"
    assert row.category == "skills"
    assert row.facet == "skill_requirement"
    assert row.domain_hint == "gesundheitswesen_pflege_medizin"


def test_build_aggregate_counts_domain_votes():
    rows = [
        ExtractedTerm(
            referenznummer="A",
            term="Monitoring",
            term_key="monitoring",
            canonical="Monitoring",
            category="skills",
            facet="skill_requirement",
            domain_hint="healthcare",
            domain_confidence=0.9,
            context_phrase="patient monitoring",
            vacancy_domain_top="healthcare",
            vacancy_domain_confidence=0.9,
        ),
        ExtractedTerm(
            referenznummer="B",
            term="Monitoring",
            term_key="monitoring",
            canonical="Monitoring",
            category="skills",
            facet="skill_requirement",
            domain_hint="it",
            domain_confidence=0.3,
            context_phrase="system monitoring",
            vacancy_domain_top="it",
            vacancy_domain_confidence=0.8,
        ),
    ]
    agg = _build_aggregate(
        extracted_rows=rows,
        provider="openai",
        model="gpt-4o-mini",
        scanned_vacancies=2,
        processed_vacancies=2,
        failed_vacancies=0,
    )
    assert agg["input"]["term_rows"] == 2
    terms = {item["term_key"]: item for item in agg["terms"]}
    monitoring = terms["monitoring"]
    assert monitoring["primary_category"] == "skills"
    assert monitoring["primary_facet"] == "skill_requirement"
    assert monitoring["domain_votes"]["healthcare"]["count"] == 1
    assert monitoring["domain_votes"]["it"]["count"] == 1


def test_normalize_domain_alias_and_underscore():
    assert _normalize_domain("public sector") == "verwaltung_oeffentlicher_dienst"
    assert _normalize_domain("sales marketing") == "vertrieb_marketing_kommunikation"
    assert _normalize_domain("other") == "sonstiges"


def test_sanitize_term_record_corrects_category_from_facet():
    row = _sanitize_term_record(
        raw={
            "term": "Kubernetes",
            "category": "skills",
            "facet": "technology_stack",
            "domain_hint": "it",
            "domain_confidence": 0.95,
            "context_phrase": "Kubernetes cluster operations",
        },
        referenznummer="x",
        vacancy_domain_top="it",
        vacancy_domain_confidence=0.9,
    )
    assert row is not None
    assert row.category == "stack"


def test_load_domains_from_file(tmp_path):
    p = tmp_path / "domains.json"
    p.write_text(
        '{"domains":["IT","public sector","sonstiges","IT"]}',
        encoding="utf-8",
    )
    domains = _load_domains(p)
    assert domains == ["it_software_daten", "verwaltung_oeffentlicher_dienst", "sonstiges"]
