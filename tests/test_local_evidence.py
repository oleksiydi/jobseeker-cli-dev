import pytest

from app.matching.local_evidence import EvidenceMatcher


def _term(concept_id, label, **overrides):
    return {
        "id": concept_id,
        "label": label,
        "source": "ESCO-DE",
        "version": "1.2",
        "kind": "skill",
        **overrides,
    }


def test_extract_matches_java_without_matching_javascript_substring():
    matcher = EvidenceMatcher([_term("skill-java", "Java")])
    text = "JAVA und JavaScript werden genutzt."

    matches = matcher.extract(text)

    assert len(matches) == 1
    assert matches[0]["concept_id"] == "skill-java"
    assert matches[0]["quote"] == "JAVA"
    start = text.index("JAVA")
    assert matches[0]["text_span"] == [start, start + len("JAVA")]


def test_extract_preserves_original_offsets_and_umlauts():
    matcher = EvidenceMatcher([_term("skill-pflege", "Krankenpflege", aliases=["Pflegefachkraft"])])
    text = "Für die Tätigkeit ist Erfahrung in KRANKENPFLEGE erforderlich."

    match = matcher.extract(text, source="job_description")[0]

    start = text.index("KRANKENPFLEGE")
    assert match["text_span"] == [start, start + len("KRANKENPFLEGE")]
    assert text[slice(*match["text_span"])] == match["quote"] == "KRANKENPFLEGE"
    assert match["source"] == "job_description"
    assert match["taxonomy_source"] == "ESCO-DE"
    assert match["taxonomy_version"] == "1.2"


def test_extract_assigns_negated_preferred_and_required_modalities():
    matcher = EvidenceMatcher(
        [
            _term("skill-java", "Java"),
            _term("skill-sql", "SQL"),
            _term("skill-python", "Python"),
        ]
    )
    text = "Java ist nicht erforderlich; SQL ist von Vorteil; Python ist zwingend erforderlich."

    matches = {match["concept_id"]: match for match in matcher.extract(text)}

    assert matches["skill-java"]["modality"] == "negated"
    assert matches["skill-sql"]["modality"] == "preferred"
    assert matches["skill-python"]["modality"] == "required"


@pytest.mark.parametrize(
    "text",
    [
        "Java ist nicht erforderlich.",
        "Keine Kenntnisse in Java erforderlich.",
        "Ohne Kenntnisse in Java.",
    ],
)
def test_extract_recognizes_common_negation_forms(text):
    matcher = EvidenceMatcher([_term("skill-java", "Java")])

    match = matcher.extract(text)[0]

    assert match["modality"] == "negated"


def test_extract_recognizes_required_profile_language_and_german_diacritics():
    matcher = EvidenceMatcher([_term("skill-team", "Teamfähigkeit", aliases=["Kommunikationsfähigkeit"])])

    required = matcher.extract("Ihr Profil: Teamfähigkeit.")
    with_you = matcher.extract("Sie bringen mit: Kommunikationsfähigkeit.")

    assert required[0]["quote"] == "Teamfähigkeit"
    assert required[0]["modality"] == "required"
    assert with_you[0]["quote"] == "Kommunikationsfähigkeit"
    assert with_you[0]["modality"] == "required"


def test_extract_does_not_spread_modality_across_sentence_or_list_item():
    matcher = EvidenceMatcher([_term("skill-java", "Java"), _term("skill-python", "Python")])
    text = "Java.\n• Python ist erforderlich."

    matches = {match["concept_id"]: match for match in matcher.extract(text)}

    assert matches["skill-java"]["modality"] == "unknown"
    assert matches["skill-python"]["modality"] == "required"


def test_extract_marks_same_span_concepts_ambiguous_and_prefers_maximal_phrase():
    matcher = EvidenceMatcher(
        [
            _term("skill-java", "Java"),
            _term("occupation-java", "Java", kind="occupation"),
            _term("skill-java-programming", "Java Programmierung"),
        ]
    )

    ambiguous = matcher.extract("Java gesucht.")
    maximal = matcher.extract("Java Programmierung ist erforderlich.")

    assert {match["concept_id"] for match in ambiguous} == {"skill-java", "occupation-java"}
    assert all(match["modality"] == "unknown" and match["needs_review"] for match in ambiguous)
    assert len(maximal) == 1
    assert maximal[0]["concept_id"] == "skill-java-programming"
    assert maximal[0]["quote"] == "Java Programmierung"


def test_extract_returns_unknown_without_an_explicit_modality_cue():
    matcher = EvidenceMatcher([_term("skill-sap", "SAP")])

    match = matcher.extract("Erfahrung mit SAP.")[0]

    assert match["modality"] == "unknown"
    assert match["needs_review"] is False
    assert match["matcher"] == "spacy.PhraseMatcher"
    assert match["model_version"] == "spacy-phrase-matcher-v2"


def test_extract_matches_intra_token_skills_across_hyphens_with_original_spans():
    matcher = EvidenceMatcher([_term("docker", "Docker"), _term("support", "IT-Support"), _term("java", "Java")])
    text = "Docker‑Kenntnisse, IT‑Support und JavaScript."

    matches = matcher.extract(text)

    assert [match["quote"] for match in matches] == ["Docker", "IT‑Support"]
    for match in matches:
        assert text[slice(*match["text_span"])] == match["quote"]


def test_profile_heading_scopes_list_until_a_new_benefits_section():
    matcher = EvidenceMatcher([_term("java", "Java"), _term("python", "Python")])
    text = "Ihr Profil:\n• Java\n• Python\nWir bieten:\n• Java"

    matches = matcher.extract(text)

    assert [match["modality"] for match in matches] == ["required", "required", "irrelevant"]
    assert matches[-1]["context_role"] == "benefit"


def test_tasks_section_is_not_treated_as_benefit_or_requirement():
    matcher = EvidenceMatcher([_term("docker", "Docker")])
    match = matcher.extract("Aufgaben:\n• Docker‑Administration")[0]

    assert match["modality"] == "unknown"
    assert match["context_role"] == "task"


def test_clause_negation_overrides_requirement_heading():
    matcher = EvidenceMatcher([_term("java", "Java")])

    match = matcher.extract("Ihr Profil:\n• Java ist nicht erforderlich.")[0]

    assert match["modality"] == "negated"
    assert match["needs_review"] is True


def test_nearest_local_modality_cue_wins_within_a_clause():
    matcher = EvidenceMatcher([_term("java", "Java"), _term("docker", "Docker")])

    matches = {
        match["concept_id"]: match
        for match in matcher.extract("Ihr Profil: Java nicht erforderlich, Docker zwingend.")
    }

    assert matches["java"]["modality"] == "negated"
    assert matches["docker"]["modality"] == "required"


def test_markdown_section_headings_scope_prose_until_the_next_heading():
    matcher = EvidenceMatcher([_term("docker", "Docker")])
    text = (
        "**Aufgaben:**\nWir setzen Docker ein.\n\n"
        "**Qualifikation:**\nDocker-Erfahrung wird vorausgesetzt.\n\n"
        "**Mitarbeitervorteile:**\nDocker-Konferenzbudget."
    )

    matches = matcher.extract(text)

    assert [match["context_role"] for match in matches] == ["task", "requirement", "benefit"]
    assert [match["modality"] for match in matches] == ["unknown", "required", "irrelevant"]


def test_profile_heading_aliases_and_generic_heading_scope():
    matcher = EvidenceMatcher([_term("docker", "Docker")])
    text = "**Das bringen Sie mit:**\nDocker\n\n**Tätigkeit:**\nDocker"

    matches = matcher.extract(text)

    assert [match["context_role"] for match in matches] == ["requirement", None]
    assert [match["modality"] for match in matches] == ["required", "unknown"]


def test_add_terms_adds_patterns_without_losing_existing_terms():
    matcher = EvidenceMatcher([_term("java", "Java")])
    matcher.add_terms([_term("docker", "Docker", source="profile")])
    matcher.add_terms([_term("sql", "SQL", source="profile")])

    matches = matcher.extract("Java, Docker und SQL")

    assert [match["concept_id"] for match in matches] == ["java", "docker", "sql"]
    assert [match["taxonomy_source"] for match in matches] == ["ESCO-DE", "profile", "profile"]
