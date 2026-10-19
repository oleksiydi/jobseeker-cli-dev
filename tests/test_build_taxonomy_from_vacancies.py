from tools.build_taxonomy_from_vacancies import (
    ExtractedTerm,
    _apply_runtime_taxonomy_to_row,
    _apply_preset_limits,
    _build_aggregate,
    _effective_dry_run,
    _load_domains,
    _normalize_domain,
    _sanitize_term_record,
)
from tools.taxonomy_extract import build_system_prompt, build_user_prompt


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
    assert row.skill_class == "professional"
    assert row.domain_hint == "gesundheitswesen_pflege_medizin"


def test_sanitize_term_record_rejects_translation_only_candidate():
    row = _sanitize_term_record(
        raw={
            "term": "team orientation",
            "category": "skills",
            "facet": "interpersonal_competency",
            "domain_hint": "other",
            "domain_confidence": 0.7,
            "context_phrase": "Teamfähigkeit und Kommunikation",
        },
        referenznummer="translated",
        vacancy_domain_top="other",
        vacancy_domain_confidence=0.5,
    )
    assert row is None


def test_sanitize_term_record_accepts_source_near_german_compound():
    row = _sanitize_term_record(
        raw={
            "term": "Schweißen",
            "category": "skills",
            "facet": "task_responsibility",
            "domain_hint": "industry",
            "domain_confidence": 0.9,
            "context_phrase": "Durchführung von Schweißarbeiten",
        },
        referenznummer="compound",
        vacancy_domain_top="industry",
        vacancy_domain_confidence=0.8,
    )
    assert row is not None
    assert row.term == "Schweißen"


def test_sanitize_term_record_rejects_abstract_only_candidate():
    row = _sanitize_term_record(
        raw={
            "term": "operational excellence",
            "category": "skills",
            "facet": "interpersonal_competency",
            "domain_hint": "finance",
            "domain_confidence": 0.8,
            "context_phrase": "strukturierte sowie prozess- und lösungsorientierte Arbeitsweise",
        },
        referenznummer="abstract",
        vacancy_domain_top="finance",
        vacancy_domain_confidence=0.8,
    )
    assert row is None


def test_build_aggregate_counts_domain_votes():
    rows = [
        ExtractedTerm(
            referenznummer="A",
            term="Monitoring",
            term_key="monitoring",
            canonical="Monitoring",
            category="skills",
            facet="skill_requirement",
            skill_class="professional",
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
            skill_class="professional",
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
    assert row.skill_class == "not_applicable"


def test_load_domains_from_file(tmp_path):
    p = tmp_path / "domains.json"
    p.write_text(
        '{"domains":["IT","public sector","sonstiges","IT"]}',
        encoding="utf-8",
    )
    domains = _load_domains(p)
    assert domains == ["it_software_daten", "verwaltung_oeffentlicher_dienst", "sonstiges"]


def test_sanitize_term_record_sets_non_skill_conditions_for_benefits():
    row = _sanitize_term_record(
        raw={
            "term": "Urlaubs- und Weihnachtsgeld",
            "category": "skills",
            "facet": "benefit",
            "domain_hint": "industry",
            "domain_confidence": 0.8,
            "context_phrase": "Zusatzleistungen: Urlaubs- und Weihnachtsgeld",
        },
        referenznummer="z",
        vacancy_domain_top="industry",
        vacancy_domain_confidence=0.9,
    )
    assert row is not None
    assert row.category == "skills"
    assert row.skill_class == "non_skill_conditions"


def test_sanitize_term_record_sets_interpersonal_class_for_interpersonal_facet():
    row = _sanitize_term_record(
        raw={
            "term": "Teamfähigkeit",
            "category": "skills",
            "facet": "interpersonal_competency",
            "domain_hint": "other",
            "domain_confidence": 0.6,
            "context_phrase": "Teamfähigkeit und Kommunikation",
        },
        referenznummer="s",
        vacancy_domain_top="other",
        vacancy_domain_confidence=0.4,
    )
    assert row is not None
    assert row.category == "skills"
    assert row.skill_class == "interpersonal"


def test_effective_dry_run_respects_allow_learning_flag():
    assert _effective_dry_run(dry_run=False, allow_learning=False) is True
    assert _effective_dry_run(dry_run=False, allow_learning=True) is False
    assert _effective_dry_run(dry_run=True, allow_learning=True) is True


def test_apply_runtime_taxonomy_to_row_remaps_category_and_canonical():
    row = ExtractedTerm(
        referenznummer="ref-1",
        term="k8s",
        term_key="k8s",
        canonical="k8s",
        category="skills",
        facet="technology_stack",
        skill_class="professional",
        domain_hint="it_software_daten",
        domain_confidence=0.9,
        context_phrase="Erfahrung mit k8s",
        vacancy_domain_top="it_software_daten",
        vacancy_domain_confidence=0.9,
    )
    taxonomy = {
        "aliases": {"k8s": "Kubernetes"},
        "categories": {
            "skills": set(),
            "tools": set(),
            "stack": {"Kubernetes"},
        },
    }
    mapped = _apply_runtime_taxonomy_to_row(row, taxonomy)
    assert mapped.canonical == "Kubernetes"
    assert mapped.term_key == "kubernetes"
    assert mapped.category == "stack"
    assert mapped.skill_class == "not_applicable"


def test_apply_preset_limits_cpu_safe():
    max_terms, max_input = _apply_preset_limits(preset="cpu_safe", max_terms_per_vacancy=30, max_input_chars=6000)
    assert max_terms == 10
    assert max_input == 3000


def test_apply_preset_limits_quality_pass():
    max_terms, max_input = _apply_preset_limits(preset="quality_pass", max_terms_per_vacancy=30, max_input_chars=6000)
    assert max_terms == 15
    assert max_input == 3000


def test_extraction_prompt_documents_extractive_first_policy():
    prompt = build_system_prompt(["it_software_daten", "sonstiges"], max_terms_per_vacancy=10)
    assert "extractive-first" in prompt
    assert "Do not translate German source phrases into English-only concepts" in prompt
    assert "Do not replace source text with abstract concepts" in prompt


def test_user_prompt_requires_source_near_terms():
    class Vacancy:
        def canonical_text(self) -> str:
            return "Teamfähigkeit und Kommunikation"

    prompt = build_user_prompt(Vacancy(), max_input_chars=100)
    assert "term must be copied from, or be very close to" in prompt
    assert "Do not output translated-only or abstract-only candidates" in prompt
