"""Contract checks for privacy-filtered, provenance-backed project episodes."""

import json

import pytest

from app.matching.project_episodes import (
    fact,
    project_projection,
    read_source,
    validate_provenance,
)


def write_source(tmp_path, name, raw):
    path = tmp_path / name
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return read_source(path)


def profile_with_project(**project_fields):
    project = {
        "name": "Synthetic release platform",
        "company": "Example Systems",
        "role_title": "Platform engineer",
        "timeframe": "2021–2023",
        "responsibilities": ["Automated synthetic release checks"],
        "deliverables": ["Reduced synthetic deployment delays"],
        **project_fields,
    }
    return {"experience_projects": [project]}


def test_project_facts_keep_exact_source_references_and_reject_value_tampering(tmp_path):
    source = write_source(tmp_path, "profile.json", profile_with_project())
    projection = project_projection(source)
    episode = projection["episodes"][0]

    assert episode["actions"][0] == {
        "source": source["path"],
        "source_sha256": source["sha256"],
        "pointer": "/experience_projects/0/responsibilities/0",
        "value": "Automated synthetic release checks",
    }
    validate_provenance(projection, [source])

    episode["actions"][0]["value"] = "Tampered source claim"
    with pytest.raises(ValueError, match="Source quote differs"):
        validate_provenance(projection, [source])


def test_child_inherits_parent_period_and_company_but_keeps_project_scope(tmp_path):
    raw = profile_with_project(subprojects=[{
        "name": "Synthetic migration",
        "responsibilities": ["Migrated synthetic service"],
        "deliverables": ["Completed synthetic migration"],
    }])
    source = write_source(tmp_path, "profile.json", raw)
    projection = project_projection(source)
    parent, child = projection["episodes"]

    assert parent["company"]["value"] == "Example Systems"
    assert child["company"]["value"] == "Example Systems"
    assert child["timeframe"]["value"] == "2021–2023"
    result = child["results"][0]
    assert result["scope"] == "project"
    assert result["action_link"] is None


def test_project_results_are_not_emitted_as_action_units(tmp_path):
    source = write_source(tmp_path, "profile.json", profile_with_project())
    episode = project_projection(source)["episodes"][0]

    from app.matching.episode_retrieval import units_for

    units = units_for([episode])
    action_units = [unit for unit in units if unit["kind"] == "actions"]
    result_units = [unit for unit in units if unit["kind"] == "project_result"]
    assert [unit["text"] for unit in action_units] == ["Automated synthetic release checks"]
    assert [unit["text"] for unit in result_units] == ["Reduced synthetic deployment delays"]
    assert all(unit["fact"].get("action_link") is None for unit in result_units)


def test_parent_and_child_exclusions_propagate_without_leaking_text(tmp_path):
    raw = {"experience_projects": [
        {
            "name": "Hidden synthetic parent",
            "company": "Private synthetic employer",
            "responsibilities": ["Hidden parent action phrase"],
            "subprojects": [{
                "name": "Hidden synthetic child",
                "responsibilities": ["Hidden child action phrase"],
                "private": True,
            }],
            "do_not_mention": True,
        },
        {
            "name": "Visible synthetic parent",
            "responsibilities": ["Visible parent action"],
            "subprojects": [{
                "name": "Excluded synthetic child",
                "responsibilities": ["Excluded child action phrase"],
                "visibility": "excluded",
            }],
        },
    ]}
    source = write_source(tmp_path, "profile.json", raw)
    projection = project_projection(source)
    serialized = json.dumps(projection, ensure_ascii=False)

    assert [episode["label"]["value"] for episode in projection["episodes"]] == [
        "Visible synthetic parent",
    ]
    assert len(projection["withheld"]) == 3
    for private_text in (
        "Hidden synthetic parent", "Private synthetic employer",
        "Hidden parent action phrase", "Hidden child action phrase",
        "Excluded synthetic child", "Excluded child action phrase",
    ):
        assert private_text not in serialized


def test_global_prose_restriction_quarantines_all_episodes_before_embedding(tmp_path):
    raw = profile_with_project()
    raw["candidate"] = {"ethics_constraints": {
        "do_not_mention": ["Synthetic confidential project details"]
    }}
    source = write_source(tmp_path, "profile.json", raw)

    projection = project_projection(source)
    assert projection["episodes"] == []
    assert projection["withheld"][0]["reason"] == "unresolved_usage_restriction"
    assert "Automated synthetic release checks" not in json.dumps(projection)


def test_policy_must_be_bound_to_current_source_hash_and_user_resolution(tmp_path):
    source = write_source(tmp_path, "profile.json", profile_with_project())
    stale_policy = {
        "profile_sha256": "stale-hash",
        "resolution": "listed_projects_permitted",
        "basis": "user_statement",
    }
    with pytest.raises(ValueError, match="exact source hash"):
        project_projection(source, policy=stale_policy)

    raw = profile_with_project()
    raw["candidate"] = {"ethics_constraints": {"do_not_mention": ["synthetic client"]}}
    restricted_source = write_source(tmp_path, "restricted.json", raw)
    user_policy = {
        "profile_sha256": restricted_source["sha256"],
        "resolution": "listed_projects_permitted",
        "basis": "user_statement",
    }
    projection = project_projection(restricted_source, policy=user_policy)
    assert len(projection["episodes"]) == 1
    assert projection["usage_policy"] == user_policy


def test_supplement_adds_education_and_constraints_without_project_or_story_evidence(tmp_path):
    primary = write_source(tmp_path, "primary.json", {
        "experience_projects": [{
            "name": "Primary synthetic project",
            "responsibilities": ["Primary synthetic responsibility"],
        }],
        "constraints": {"locations": ["Primary location"]},
        "interview_story_bank": {"best_stories": ["Primary private interview story"]},
    })
    supplement = write_source(tmp_path, "supplement.json", {
        "Ausbildung": [{"Abschluss": "Synthetic technical diploma"}],
        "zusätzliche_informationen": {
            "körperliche_verfassung": "Keine schwere körperliche Arbeit"
        },
        "experience_projects": [{
            "name": "Supplement synthetic project",
            "responsibilities": ["Supplement synthetic responsibility"],
        }],
        "interview_story_bank": {"best_stories": ["Supplement private story"]},
    })

    projection = project_projection(primary, supplement=supplement)
    serialized = json.dumps(projection, ensure_ascii=False)
    assert len(projection["episodes"]) == 1
    assert projection["episodes"][0]["label"]["value"] == "Primary synthetic project"
    assert any(fact_row["source"] == supplement["path"]
               and fact_row["pointer"] == "/Ausbildung"
               for fact_row in projection["supporting_facts"])
    assert projection["constraints"]["heavy_physical_work"] is False
    assert projection["constraints"]["locations"] == ["Primary location"]
    for excluded in (
        "Supplement synthetic project", "Supplement synthetic responsibility",
        "Primary private interview story", "Supplement private story",
    ):
        assert excluded not in serialized


def test_story_bank_and_letter_text_are_not_project_or_application_evidence(tmp_path):
    source = write_source(tmp_path, "profile.json", {
        **profile_with_project(),
        "interview_story_bank": {"best_stories": ["Synthetic STAR story only"]},
        "application_letter": "Synthetic letter paragraph only",
    })
    projection = project_projection(source)
    serialized = json.dumps(projection, ensure_ascii=False)

    assert projection["unlinked_stories"] == 1
    assert "Synthetic STAR story only" not in serialized
    assert "Synthetic letter paragraph only" not in serialized
    assert all("story" not in key.casefold() for episode in projection["episodes"] for key in episode)
