from tools import extract_profile_from_text
from tools.build_taxonomy_from_vacancies import ExtractedTerm, _apply_runtime_taxonomy_to_row


def test_profile_and_vacancy_use_same_taxonomy_mapping_for_same_term():
    taxonomy = {
        "aliases": {"k8s": "Kubernetes"},
        "categories": {
            "skills": set(),
            "tools": set(),
            "stack": {"Kubernetes"},
        },
    }

    profile_raw = {
        "key": "default",
        "target_role": "DevOps Engineer",
        "summary": "Summary",
        "skills": ["k8s"],
        "tools": [],
        "stack": [],
        "experience": "5 years",
        "seniority": "senior",
        "industries": [],
        "locations": [],
        "languages": ["Deutsch"],
    }
    profile = extract_profile_from_text.normalize_runtime_profile(
        profile_raw,
        profile_key="default",
        taxonomy=taxonomy,
    )

    vacancy_row = ExtractedTerm(
        referenznummer="ref-1",
        term="k8s",
        term_key="k8s",
        canonical="k8s",
        category="skills",
        facet="technology_stack",
        skill_class="professional",
        domain_hint="it_software_daten",
        domain_confidence=0.9,
        context_phrase="k8s cluster Betrieb",
        vacancy_domain_top="it_software_daten",
        vacancy_domain_confidence=0.9,
    )
    mapped_vacancy_row = _apply_runtime_taxonomy_to_row(vacancy_row, taxonomy)

    assert profile["stack"] == ["Kubernetes"]
    assert mapped_vacancy_row.canonical == "Kubernetes"
    assert mapped_vacancy_row.category == "stack"
