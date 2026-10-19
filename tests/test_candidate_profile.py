from app.matching.profile import normalize_profile, profile_search_text
from app.search_jobs import _serialize_profile_for_search


def test_short_profile_preserves_phrases_and_explicit_constraints() -> None:
    profile = {
        "key": "demo",
        "roles": ["Support Specialist"],
        "target_roles": ["Junior Data Analyst"],
        "skills": ["I can help customers with complicated technical questions"],
        "languages": {"de": "fließend", "en": "good"},
        "constraints": {
            "heavy_physical_work": False,
            "on_call": False,
            "shift_work": True,
            "locations": ["Berlin"],
            "radius_km": 20,
        },
    }

    normalized = normalize_profile(profile, "conf/local-demo/profiles.json")

    assert normalized["roles"] == ["Support Specialist"]
    assert normalized["target_roles"] == ["Junior Data Analyst"]
    assert normalized["skills"][0]["label"] == profile["skills"][0]
    assert normalized["skills"][0]["status"] == "declared"
    assert normalized["constraints"] == profile["constraints"]
    assert normalized["languages"]["de"]["cefr"] is None
    assert normalized["languages"]["en"]["cefr"] is None


def test_legacy_german_profile_supplies_past_roles_skills_and_education() -> None:
    profile = {
        "Berufsprofil": {"Beschreibung": "Software support"},
        "Berufserfahrung": [
            {"Position": "IT Support", "Jahr": "2022", "Aufgaben": ["Helpdesk"]}
        ],
        "Ausbildung": [{"Titel": "Informatik", "Jahr": "2020"}],
        "Sprachkenntnisse": {"Deutsch": "fließend"},
        "TechnischeKenntnisse": {"Werkzeuge": ["Linux", "Ticket-Systeme"]},
    }

    normalized = normalize_profile(profile, "legacy")

    assert normalized["roles"] == ["IT Support"]
    assert normalized["target_roles"] == []
    assert [item["label"] for item in normalized["skills"]] == ["Linux", "Ticket-Systeme"]
    assert normalized["languages"]["de"] == {
        "description": "fließend",
        "cefr": None,
        "source_path": "Sprachkenntnisse.Deutsch",
    }
    assert normalized["education"] == [{"Titel": "Informatik", "Jahr": "2020"}]


def test_extended_profile_uses_project_stack_without_private_search_fields() -> None:
    profile = {
        "key": "candidate",
        "candidate": {
            "name_variants": ["Private Name"],
            "current_location": "Private City",
            "work_authorization": "private authorization detail",
            "languages": {"de": "German language description"},
            "positioning_core": {"best_fit_roles": ["Platform Engineer"]},
            "work_style": {"risks": ["private health detail"]},
            "ethics_constraints": {"do_not_mention": ["private exclusion"]},
        },
        "skills": {"domains": ["Data platforms"], "tech_stack": {"cloud": ["Kubernetes"]}},
        "experience_projects": [
            {
                "role_title": "Data Engineer",
                "timeframe": "2021-2023",
                "tech_stack": ["Kafka", "Python"],
                "confidence": {"overall": "high", "recall_gaps": ["not a skill level"]},
            }
        ],
        "interview_story_bank": {"best_stories": [{"title": "private story"}]},
    }

    normalized = normalize_profile(profile, "extended")
    text = profile_search_text(normalized)

    assert normalized["roles"] == ["Data Engineer"]
    assert normalized["target_roles"] == ["Platform Engineer"]
    assert normalized["projects"][0] == {
        "role": "Data Engineer",
        "period": "2021-2023",
        "skills": ["Kafka", "Python"],
        "source_path": "experience_projects[0]",
    }
    assert all(item["status"] in {"declared", "project_evidence"} for item in normalized["skills"])
    assert "Kubernetes" in text and "Kafka" in text
    for private_value in (
        "Private Name",
        "Private City",
        "private authorization detail",
        "private health detail",
        "private exclusion",
        "private story",
        "not a skill level",
    ):
        assert private_value not in text


def test_hash_ignores_mongo_metadata_but_changes_with_profile_content() -> None:
    profile = {"key": "demo", "roles": ["Support"], "skills": ["Linux"]}
    original = normalize_profile(profile)
    loaded = normalize_profile(
        {**profile, "_id": "mongo-id", "created_at": "old", "updated_at": "new", "profile_hash": "stored"}
    )
    changed = normalize_profile({**profile, "skills": ["Linux", "Networking"]})

    assert original["profile_hash"] == loaded["profile_hash"]
    assert original["profile_hash"] != changed["profile_hash"]


def test_application_contacts_do_not_change_professional_hash_or_search_text():
    profile = {'key': 'demo', 'roles': ['Support'], 'skills': ['Linux']}
    original = normalize_profile(profile)
    personal = normalize_profile({**profile, 'application_contact': {
        'full_name': 'PRIVATE_SENDER', 'email': 'private@example.org', 'city': 'PRIVATE_CITY'}})
    assert personal['profile_hash'] == original['profile_hash']
    assert profile_search_text(personal) == profile_search_text(original)


def test_explicit_cefr_licenses_nested_project_evidence_and_preferences() -> None:
    profile = {
        "key": "nested",
        "licenses": ["Führerschein Klasse B"],
        "candidate": {
            "languages": {"de": "Deutsch B2", "en": "fließend"},
            "positioning_core": {"roles_to_avoid": ["Sales"]},
            "work_style": {"preferred_environment": ["ruhiges Team"]},
        },
        "experience_projects": [
            {
                "role_title": "Data Engineer",
                "timeframe": "2013–2021",
                "tech_stack": ["Python"],
                "subprojects": [
                    {"name": "Streaming platform", "tech_stack": ["Kafka", "Spark"]},
                    {"name": "Cloud migration", "tech_stack": ["Kubernetes"]},
                ],
            }
        ],
    }

    normalized = normalize_profile(profile, "extended")
    skills_by_label = {skill["label"]: skill for skill in normalized["skills"]}

    assert normalized["languages"]["de"]["cefr"] == "B2"
    assert normalized["languages"]["en"]["cefr"] is None
    assert normalized["licenses"] == ["Führerschein Klasse B"]
    assert normalized["preferences"] == {
        "roles_to_avoid": ["Sales"],
        "preferred_environment": ["ruhiges Team"],
    }
    assert normalized["projects"][1] == {
        "role": "Data Engineer",
        "period": "2013–2021",
        "skills": ["Kafka", "Spark"],
        "source_path": "experience_projects[0].subprojects[0]",
    }
    assert skills_by_label["Kafka"]["source_path"] == "experience_projects[0].subprojects[0].tech_stack"
    assert skills_by_label["Kafka"]["last_used"] == "2013–2021"
    assert skills_by_label["Python"]["last_used"] == "2013–2021"
    search_text = profile_search_text(normalized)
    assert "Sales" not in search_text
    assert "ruhiges Team" not in search_text


def test_search_serializer_keeps_known_fields_and_ignores_unknown_fields() -> None:
    text = _serialize_profile_for_search(
        {
            "key": "default",
            "skills": ["python", "spark"],
            "languages": ["de", "en"],
            "ignored_block": {"secret": "value"},
        }
    )

    assert "skills: python spark" in text
    assert "languages: de en" in text
    assert "ignored_block" not in text
