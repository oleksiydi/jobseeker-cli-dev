from tools import update_taxonomy_from_unknown_terms as updater


def _base_taxonomy() -> dict:
    return {
        "version": 1,
        "aliases": {},
        "categories": {"skills": [], "tools": [], "stack": []},
    }


def test_suggest_term_uses_seed_alias_and_category():
    taxonomy = _base_taxonomy()
    suggestion = updater.suggest_term(
        term="k8s",
        source_field="stack",
        context="worked with k8s clusters",
        taxonomy=taxonomy,
    )
    assert suggestion["canonical"] == "Kubernetes"
    assert suggestion["category"] == "stack"
    assert suggestion["confidence"] >= 0.9


def test_apply_suggestion_updates_alias_and_category_once():
    taxonomy = _base_taxonomy()
    suggestion = {
        "term_key": "jira",
        "canonical": "Jira",
        "category": "tools",
    }
    updater.apply_suggestion(taxonomy, suggestion)
    updater.apply_suggestion(taxonomy, suggestion)
    assert taxonomy["aliases"]["jira"] == "Jira"
    assert taxonomy["categories"]["tools"] == ["Jira"]


def test_parse_unknown_line_supports_json_and_plain():
    parsed_json = updater._parse_unknown_line('{"term":"Docker","source_field":"stack"}')
    assert parsed_json["term"] == "Docker"
    assert parsed_json["source_field"] == "stack"

    parsed_plain = updater._parse_unknown_line("Wireshark")
    assert parsed_plain["term"] == "Wireshark"
    assert parsed_plain["source_field"] == ""


def test_build_pending_record_contains_review_workflow_fields():
    suggestion = {
        "term": "DevOps",
        "term_key": "devops",
        "canonical": "DevOps",
        "category": "skills",
        "confidence": 0.78,
        "reasons": ["unknown_alias"],
        "source_field": "skills",
        "context": "",
    }
    pending = updater._build_pending_record(suggestion)
    assert pending["review_status"] == "auto_suggested"
    assert pending["review_code"] == 0
    assert pending["review_note"] == ""
    assert pending["reviewed_by"] == ""
    assert pending["reviewed_at"] == ""
