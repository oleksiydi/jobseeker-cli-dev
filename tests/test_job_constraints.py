from app.matching.constraints import evaluate_constraints


def _profile(**overrides):
    profile = {
        "languages": {"de": {"description": "fließend", "cefr": None}},
        "constraints": {"on_call": False, "shift_work": False, "heavy_physical_work": False},
        "licenses": [],
    }
    profile.update(overrides)
    return profile


def test_unknown_cefr_is_not_a_conflict_and_carries_source_evidence():
    text = "Deutschkenntnisse auf Niveau B2 sind zwingend erforderlich."
    result = evaluate_constraints({"id": "job-1", "description": text}, _profile())[0]

    assert result["kind"] == "language"
    assert result["required"] is True
    assert result["status"] == "unknown"
    assert result["source"] == "description"
    assert text[slice(*result["text_span"])] == "Deutschkenntnisse auf Niveau B2"
    assert "B2" in result["quote"]


def test_explicit_shift_requirement_conflicts_but_remote_clause_does_not_leak():
    description = "Schichtarbeit ist zwingend erforderlich.\nRemote-Arbeit ist möglich."
    results = evaluate_constraints({"description": description}, _profile())

    assert len(results) == 1
    assert results[0]["kind"] == "shift_work"
    assert results[0]["status"] == "conflict"
    assert results[0]["source"] == "description"
    assert "Remote" not in results[0]["quote"]


def test_negated_or_optional_constraint_is_not_reported_as_hard():
    job = {"description": "Bereitschaftsdienst ist nicht erforderlich; Nachtschicht ist optional."}

    assert evaluate_constraints(job, _profile()) == []


def test_nearest_hard_cue_is_assigned_to_each_work_condition():
    results = evaluate_constraints(
        {"description": "Bereitschaftsdienst nicht erforderlich, Schichtarbeit zwingend erforderlich."},
        _profile(),
    )

    assert [(result["kind"], result["status"]) for result in results] == [("shift_work", "conflict")]


def test_language_negation_and_profile_header_list_are_respected():
    negated = evaluate_constraints(
        {"description": "Keine Deutschkenntnisse erforderlich."}, _profile()
    )
    listed = evaluate_constraints(
        {"description": "Ihr Profil:\n• Englisch B2\n• Führerschein Klasse B"}, _profile()
    )

    assert negated == []
    assert [result["kind"] for result in listed] == ["language", "license:B"]


def test_absent_license_claim_does_not_become_a_conflict():
    profile = _profile()
    profile.pop("licenses")
    result = evaluate_constraints(
        {"description": "Führerschein Klasse B zwingend erforderlich."},
        profile,
    )[0]

    assert result["kind"] == "license:B"
    assert result["status"] == "unknown"


def test_explicitly_empty_license_list_conflicts_and_generic_degree_stays_unknown():
    results = evaluate_constraints(
        {"description": "Staplerschein erforderlich. Studium zwingend vorausgesetzt."},
        _profile(description="Studium der Informatik"),
    )

    assert [result["status"] for result in results] == ["conflict", "unknown"]


def test_pipe_delimited_location_uses_the_city_component():
    results = evaluate_constraints(
        {"location": "DE|Bayern|Regensburg|93047"},
        _profile(constraints={"locations": ["Regensburg"], "radius_km": None}),
    )

    assert len(results) == 1
    assert results[0]["kind"] == "location"
    assert results[0]["status"] == "met"
    assert results[0]["quote"] == "Regensburg"


def test_exact_language_level_and_license_evidence_can_be_met():
    profile = _profile(
        languages={"de": {"description": "Deutsch B2", "cefr": "B2"}},
        licenses=["B"],
    )
    results = evaluate_constraints(
        {"description": "Deutschkenntnisse B1 erforderlich. Führerschein B erforderlich."}, profile
    )

    assert [result["status"] for result in results] == ["met", "met"]


def test_unquantified_fluent_language_requirement_is_not_satisfied_by_any_cefr():
    profile = _profile(languages={"de": {"description": "Deutsch A1", "cefr": "A1"}})
    result = evaluate_constraints(
        {"description": "Fließende Deutschkenntnisse zwingend erforderlich."}, profile
    )[0]
    assert result["status"] == "unknown"


def test_different_language_levels_in_one_clause_keep_exact_source_quotes():
    description = 'Deutsch auf Niveau C1 und Englisch B1 zwingend erforderlich.'
    profile = _profile(languages={'de': {'cefr': 'C1'}, 'en': {'cefr': 'B1'}})
    results = evaluate_constraints({'description': description}, profile)
    assert [r['status'] for r in results] == ['met', 'met']
    assert [r['quote'] for r in results] == ['Deutsch auf Niveau C1', 'Englisch B1']
    assert all(description[slice(*r['text_span'])] == r['quote'] for r in results)
