import json
from pathlib import Path

from app.normalization.loader import load_runtime_taxonomy
from app.normalization.runtime import NormalizationContext, resolve_canonical_term
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


def test_runtime_resolution_returns_structured_resolved_result():
    taxonomy = {
        "aliases": {"k8s": "Kubernetes"},
        "categories": {"skills": set(), "tools": set(), "stack": {"Kubernetes"}},
    }

    result = resolve_canonical_term(
        term="k8s",
        source_category="skills",
        taxonomy=taxonomy,
        context=NormalizationContext(
            domain="it_software_daten",
            domain_confidence=0.9,
            facet_hint="technology_stack",
            context_source="vacancy",
        ),
    )

    assert result.alias_key == "k8s"
    assert result.canonical == "Kubernetes"
    assert result.category == "stack"
    assert result.domain == "it_software_daten"
    assert result.facet == "technology_stack"
    assert result.resolution_status == "resolved"
    assert result.resolution_source == "flat_alias"


def test_runtime_resolution_keeps_unknown_term_as_raw_result():
    result = resolve_canonical_term(
        term="unknown skill",
        source_category="skills",
        taxonomy={"aliases": {}, "categories": {"skills": set(), "tools": set(), "stack": set()}},
        context={"domain": "unknown", "domain_confidence": 0.0, "context_source": "unknown"},
    )

    assert result.alias_key == "unknown skill"
    assert result.canonical == "unknown skill"
    assert result.category == "skills"
    assert result.domain == "unknown"
    assert result.resolution_status == "raw"
    assert result.resolution_source == "raw"


def test_runtime_resolution_keeps_ambiguous_alias_unresolved_without_safe_mapping():
    result = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy={
            "aliases": {},
            "ambiguous_aliases": ["rom"],
            "categories": {"skills": set(), "tools": set(), "stack": set()},
        },
        context={"domain": "unknown", "domain_confidence": 0.0, "context_source": "unknown"},
    )

    assert result.alias_key == "rom"
    assert result.canonical == "ROM"
    assert result.category == "skills"
    assert result.domain == "unknown"
    assert result.resolution_status == "unresolved"
    assert result.resolution_source == "ambiguous_without_context"


def test_runtime_resolution_prefers_domain_facet_match_for_ambiguous_alias():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {
            "rom": {
                "it_software_daten": "Read-Only Memory",
                "gesundheitswesen_pflege_medizin": "Range of Motion",
            }
        },
        "aliases_by_domain_facet": {
            "rom": {
                "it_software_daten": {"technology_stack": "Read-Only Memory"},
                "gesundheitswesen_pflege_medizin": {"skill_requirement": "Range of Motion"},
            }
        },
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory", "Range of Motion"}, "tools": set(), "stack": set()},
    }

    it_result = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=taxonomy,
        context=NormalizationContext(
            domain="it_software_daten",
            domain_confidence=0.92,
            facet_hint="technology_stack",
            context_source="vacancy",
        ),
    )
    med_result = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=taxonomy,
        context=NormalizationContext(
            domain="gesundheitswesen_pflege_medizin",
            domain_confidence=0.87,
            facet_hint="skill_requirement",
            context_source="vacancy",
        ),
    )

    assert it_result.canonical == "Read-Only Memory"
    assert it_result.resolution_source == "domain_facet"
    assert med_result.canonical == "Range of Motion"
    assert med_result.resolution_source == "domain_facet"


def test_runtime_resolution_falls_back_to_domain_match_without_facet():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {"rom": {"it_software_daten": "Read-Only Memory"}},
        "aliases_by_domain_facet": {"rom": {"it_software_daten": {"technology_stack": "Read-Only Memory"}}},
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory"}, "tools": set(), "stack": set()},
    }

    result = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=taxonomy,
        context=NormalizationContext(
            domain="it_software_daten",
            domain_confidence=0.83,
            facet_hint=None,
            context_source="profile",
        ),
    )

    assert result.canonical == "Read-Only Memory"
    assert result.resolution_status == "resolved"
    assert result.resolution_source == "domain"


def test_runtime_resolution_keeps_ambiguous_alias_unresolved_when_domain_context_misses():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {"rom": {"it_software_daten": "Read-Only Memory"}},
        "aliases_by_domain_facet": {"rom": {"it_software_daten": {"technology_stack": "Read-Only Memory"}}},
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory"}, "tools": set(), "stack": set()},
    }

    result = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=taxonomy,
        context=NormalizationContext(
            domain="gesundheitswesen_pflege_medizin",
            domain_confidence=0.8,
            facet_hint="skill_requirement",
            context_source="vacancy",
        ),
    )

    assert result.canonical == "ROM"
    assert result.resolution_status == "unresolved"
    assert result.resolution_source == "ambiguous_without_context"


def test_profile_and_vacancy_use_same_runtime_synonyms_entries(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        json.dumps({"version": 1, "aliases": {}, "categories": {"skills": [], "tools": [], "stack": []}}) + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        json.dumps({"version": 1, "aliases": {}, "categories": {"skills": [], "tools": [], "stack": []}}) + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text(json.dumps({"version": 1, "entries": [], "aliases": {}}) + "\n", encoding="utf-8")
    synonyms_auto.write_text(
        json.dumps(
            {
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
        )
        + "\n",
        encoding="utf-8",
    )

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )

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
        taxonomy=runtime_taxonomy,
    )

    vacancy_row = ExtractedTerm(
        referenznummer="ref-2",
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
    mapped_vacancy_row = _apply_runtime_taxonomy_to_row(vacancy_row, runtime_taxonomy)

    assert profile["stack"] == ["Kubernetes"]
    assert mapped_vacancy_row.canonical == "Kubernetes"
    assert mapped_vacancy_row.category == "stack"


def test_vacancy_runtime_resolution_uses_domain_context_for_ambiguous_alias():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {
            "rom": {
                "it_software_daten": "Read-Only Memory",
                "gesundheitswesen_pflege_medizin": "Range of Motion",
            }
        },
        "aliases_by_domain_facet": {
            "rom": {
                "it_software_daten": {"technology_stack": "Read-Only Memory"},
                "gesundheitswesen_pflege_medizin": {"skill_requirement": "Range of Motion"},
            }
        },
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory", "Range of Motion"}, "tools": set(), "stack": set()},
    }

    vacancy_row = ExtractedTerm(
        referenznummer="ref-rom",
        term="ROM",
        term_key="rom",
        canonical="ROM",
        category="skills",
        facet="technology_stack",
        skill_class="professional",
        domain_hint="it_software_daten",
        domain_confidence=0.9,
        context_phrase="ROM chip diagnostics",
        vacancy_domain_top="it_software_daten",
        vacancy_domain_confidence=0.91,
    )

    mapped_vacancy_row = _apply_runtime_taxonomy_to_row(vacancy_row, taxonomy)
    assert mapped_vacancy_row.canonical == "Read-Only Memory"
    assert mapped_vacancy_row.category == "skills"


def test_vacancy_runtime_resolution_keeps_ambiguous_alias_when_domain_confidence_is_low():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {"rom": {"it_software_daten": "Read-Only Memory"}},
        "aliases_by_domain_facet": {"rom": {"it_software_daten": {"technology_stack": "Read-Only Memory"}}},
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory"}, "tools": set(), "stack": set()},
    }

    vacancy_row = ExtractedTerm(
        referenznummer="ref-rom-low",
        term="ROM",
        term_key="rom",
        canonical="ROM",
        category="skills",
        facet="technology_stack",
        skill_class="professional",
        domain_hint="it_software_daten",
        domain_confidence=0.4,
        context_phrase="ROM chip diagnostics",
        vacancy_domain_top="it_software_daten",
        vacancy_domain_confidence=0.4,
    )

    mapped_vacancy_row = _apply_runtime_taxonomy_to_row(vacancy_row, taxonomy)
    assert mapped_vacancy_row.canonical == "ROM"
    assert mapped_vacancy_row.category == "skills"


def test_profile_runtime_resolution_uses_explicit_target_domain_for_ambiguous_alias():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {
            "rom": {
                "it_software_daten": "Read-Only Memory",
                "gesundheitswesen_pflege_medizin": "Range of Motion",
            }
        },
        "aliases_by_domain_facet": {},
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory", "Range of Motion"}, "tools": set(), "stack": set()},
    }

    profile_raw = {
        "key": "default",
        "target_role": "Firmware Engineer",
        "target_domain": "it_software_daten",
        "target_domain_confidence": 0.84,
        "summary": "Summary",
        "skills": ["ROM"],
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

    assert profile["skills"] == ["Read-Only Memory"]


def test_profile_runtime_resolution_keeps_ambiguous_alias_without_explicit_domain():
    taxonomy = {
        "aliases": {},
        "aliases_global_safe": {},
        "aliases_by_domain": {"rom": {"it_software_daten": "Read-Only Memory"}},
        "aliases_by_domain_facet": {},
        "ambiguous_aliases": ["rom"],
        "categories": {"skills": {"Read-Only Memory"}, "tools": set(), "stack": set()},
    }

    profile_raw = {
        "key": "default",
        "target_role": "Engineer",
        "summary": "Summary",
        "skills": ["ROM"],
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

    assert profile["skills"] == ["ROM"]
