"""Behavioral checks for requirement extraction and project episode retrieval."""

import numpy as np
import pytest

from app.matching.episode_retrieval import (
    EpisodeRetriever,
    extract_requirements,
    validate_requirements,
)
from app.matching.local_evidence import EvidenceMatcher


def make_fact(value, pointer):
    return {"source": "synthetic-profile.json", "source_sha256": "synthetic-hash",
            "pointer": pointer, "value": value}


def make_episode(episode_id, actions=(), technologies=(), results=()):
    return {
        "id": episode_id,
        "actions": [make_fact(value, f"/projects/{episode_id}/actions/{i}")
                    for i, value in enumerate(actions)],
        "technologies": [make_fact(value, f"/projects/{episode_id}/tech/{i}")
                         for i, value in enumerate(technologies)],
        "results": [{"fact": make_fact(value, f"/projects/{episode_id}/results/{i}"),
                     "scope": "project", "action_link": None}
                    for i, value in enumerate(results)],
        "timeframe": make_fact("2022–2024", f"/projects/{episode_id}/timeframe"),
        "confidence": make_fact("medium", f"/projects/{episode_id}/confidence"),
    }


def requirement(quote, modality="unknown"):
    return {"quote": quote, "text_span": [0, len(quote)], "source": "description",
            "modality": modality, "context_role": "requirement"}


class FixedVectors:
    def __init__(self, scores):
        self.scores = np.asarray(scores, dtype=float)

    def similarities(self, queries, documents):
        assert self.scores.shape == (len(queries), len(documents))
        return self.scores


def test_requirement_quote_offsets_are_validated_against_description():
    job = {"ref": "synthetic-job", "description": "Create reliable test plans."}
    row = {"quote": "reliable test", "text_span": [7, 20], "source": "description"}
    assert validate_requirements(job, [row])[0]["quote"] == "reliable test"

    invalid_span = {**row, "text_span": [8, 21]}
    with pytest.raises(ValueError, match="quote/span"):
        validate_requirements(job, [invalid_span])
    with pytest.raises(ValueError, match="source must be description"):
        validate_requirements(job, [{**row, "source": "profile"}])


def test_negated_requirement_and_benefit_never_become_direct_evidence():
    job = {"ref": "synthetic-job", "description": ""}
    matcher = EvidenceMatcher([{"id": "ci", "label": "CI/CD", "kind": "skill"}])
    job["description"] = (
        "## Benefits\n- CI/CD-Kenntnisse sind von Vorteil.\n"
        "## Deine Aufgaben\n- CI/CD-Pipelines betreuen.\n"
        "## Dein Profil\n- CI/CD ist nicht erforderlich.\n"
    )

    extracted = extract_requirements(job, matcher)
    assert not any("Benefits" in item["quote"] or "von Vorteil" in item["quote"]
                   for item in extracted)
    negated = next(item for item in extracted if "nicht erforderlich" in item["quote"])
    assert negated["modality"] == "negated"

    episodes = [make_episode("episode-a", actions=[negated["quote"]])]
    evidence = EpisodeRetriever(episodes, []).compare([negated])[0]["evidence"]
    assert evidence
    assert all(item["relation"] != "direct" for item in evidence)
    assert all(item["requirement_use"] == "not_required_or_benefit" for item in evidence)


def test_shared_technology_alone_never_proves_a_direct_action():
    retriever = EpisodeRetriever(
        [make_episode("episode-python", technologies=["Python"])], [],
    )
    result = retriever.compare([requirement("Use Python for service development")])[0]

    evidence = result["evidence"][0]
    assert evidence["confirmed_part"]["value"] == "Python"
    assert evidence["confirmed_scope"] == "historical_project_fact_only"
    assert evidence["relation"] == "unknown"


def test_high_minilm_score_does_not_certify_a_requirement():
    episodes = [make_episode("episode-support", actions=["Supported Linux user accounts"])]
    retriever = EpisodeRetriever(episodes, [], vectors=FixedVectors([[0.9999]]))
    result = retriever.compare([requirement("Administer PostgreSQL production clusters")])[0]

    assert result["minilm"][0]["score"] == pytest.approx(0.9999)
    assert result["evidence"][0]["retrieval_basis"] == ["minilm"]
    assert result["evidence"][0]["relation"] != "direct"
    assert result["evidence"][0]["requires_review"] is True


def test_union_keeps_lexical_candidates_when_minilm_ranks_another_episode_first():
    episodes = [
        make_episode("episode-lexical", actions=["Deploy CI/CD pipelines"]),
        make_episode("episode-semantic", actions=["Maintain customer billing workflow"]),
    ]
    retriever = EpisodeRetriever(
        episodes, [], vectors=FixedVectors([[0.05, 0.99]]),
    )
    result = retriever.compare([requirement("Deploy CI/CD pipelines")], top_k=1)[0]

    assert [hit["episode_id"] for hit in result["lexical"]] == ["episode-lexical"]
    assert [hit["episode_id"] for hit in result["minilm"]] == ["episode-semantic"]
    assert result["union_episode_ids"] == ["episode-lexical", "episode-semantic"]
    assert result["minilm_added"] == ["episode-semantic"]


def test_retrieval_results_keep_project_scope_and_do_not_link_outcomes_to_actions():
    episode = make_episode(
        "episode-a", actions=["Automated nightly deployment checks"],
        results=["Reduced synthetic release failures"],
    )
    result = EpisodeRetriever([episode], []).compare(
        [requirement("Automated nightly deployment checks")]
    )[0]

    assert result["evidence"][0]["relation"] == "direct"
    assert result["evidence"][0]["outcome_scope"] == "project; no automatic action/result link"
    assert result["evidence"][0]["outcomes"] == episode["results"]
    assert result["evidence"][0]["outcomes"][0]["action_link"] is None
    assert all(hit["fact_kind"] != "actions" or "Reduced synthetic release failures" not in hit["matched_fact"]["value"]
               for hit in result["lexical"] + result["minilm"])


def test_equal_similarity_ties_use_stable_episode_id_order():
    episodes = [
        make_episode("episode-z", actions=["Unrelated synthetic action"]),
        make_episode("episode-a", actions=["Another unrelated synthetic action"]),
    ]
    result = EpisodeRetriever(
        episodes, [], vectors=FixedVectors([[0.8, 0.8]]),
    ).compare([requirement("Novel requirement phrase")], top_k=2)[0]

    assert [hit["episode_id"] for hit in result["minilm"]] == ["episode-a", "episode-z"]


def test_task_and_profile_headings_extract_whole_list_items_with_context():
    description = (
        "## Deine Aufgaben\n"
        "- Analyze release failures and document findings.\n"
        "## Dein Profil\n"
        "- Experience with synthetic test strategy.\n"
    )
    rows = extract_requirements(
        {"ref": "synthetic-job", "description": description}, EvidenceMatcher([]),
    )

    assert [row["quote"] for row in rows] == [
        "Analyze release failures and document findings.",
        "Experience with synthetic test strategy.",
    ]
    assert [row["context_role"] for row in rows] == ["task", "requirement"]
    assert rows[0]["modality"] == "unknown"
    assert rows[1]["modality"] == "required"
    for row in rows:
        start, end = row["text_span"]
        assert description[start:end] == row["quote"]


def test_generic_support_history_does_not_qualify_nursing_domain_requirement():
    retriever = EpisodeRetriever(
        [make_episode("episode-support", actions=["Supported users with account access"] )],
        [], vectors=FixedVectors([[0.97]]),
    )
    result = retriever.compare([
        requirement("Provide professional nursing care to elderly patients")
    ])[0]

    assert result["minilm"][0]["score"] == pytest.approx(0.97)
    assert result["evidence"][0]["relation"] == "unknown"
    assert result["evidence"][0]["requires_review"] is True
