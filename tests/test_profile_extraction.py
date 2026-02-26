from tools import extract_profile_from_text


def test_normalize_runtime_profile_basic():
    raw = {
        "key": " default ",
        "target_role": " DevOps Engineer ",
        "summary": "Mail me at x@example.com for details.",
        "skills": ["Python", "python", " Docker "],
        "tools": "Jenkins",
        "stack": ["Kubernetes", "kubernetes"],
        "experience": "10 years",
        "seniority": "SR",
        "industries": ["IT", "it"],
        "locations": ["Regensburg", "Regensburg"],
        "languages": ["Deutsch", "Deutsch", "English"],
    }
    profile = extract_profile_from_text.normalize_runtime_profile(raw, profile_key="default")
    assert profile["key"] == "default"
    assert profile["summary"] == "Mail me at for details."
    assert profile["skills"] == ["Python", "Docker"]
    assert profile["tools"] == ["Jenkins"]
    assert profile["stack"] == ["Kubernetes"]
    assert profile["seniority"] == "senior"
    assert profile["industries"] == ["IT"]
    assert profile["languages"] == ["Deutsch", "English"]


def test_validate_runtime_profile_rejects_missing_field():
    profile = {
        "key": "default",
        "target_role": "DevOps Engineer",
        "summary": "Summary",
        "skills": [],
        "tools": [],
        "stack": [],
        "experience": "",
        "seniority": "mid",
        "industries": [],
        "locations": [],
    }
    try:
        extract_profile_from_text.validate_runtime_profile(profile)
    except ValueError as exc:
        assert "Missing required fields" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_parse_json_from_fenced_output():
    content = """```json
{"key":"default","target_role":"DevOps","summary":"","skills":[],"tools":[],"stack":[],"experience":"","seniority":"mid","industries":[],"locations":[],"languages":[]}
```"""
    parsed = extract_profile_from_text._parse_json_from_llm_output(content)
    assert parsed["target_role"] == "DevOps"


def test_validate_profile_content_rejects_empty_meaningful_fields():
    profile = {
        "key": "default",
        "target_role": "",
        "summary": "",
        "skills": [],
        "tools": [],
        "stack": [],
        "experience": "",
        "seniority": "mid",
        "industries": [],
        "locations": [],
        "languages": [],
    }
    try:
        extract_profile_from_text.validate_profile_content(profile)
    except ValueError as exc:
        assert "target_role is empty" in str(exc)
    else:
        raise AssertionError("Expected quality gate ValueError")


def test_validate_profile_content_accepts_reasonable_profile():
    profile = {
        "key": "default",
        "target_role": "DevOps Engineer",
        "summary": "Experienced engineer in CI/CD and cloud operations.",
        "skills": ["Python", "SQL"],
        "tools": ["Jenkins"],
        "stack": ["Kubernetes"],
        "experience": "10 years",
        "seniority": "senior",
        "industries": ["IT"],
        "locations": ["Regensburg"],
        "languages": ["Deutsch", "English"],
    }
    extract_profile_from_text.validate_profile_content(profile)


def test_normalize_runtime_profile_applies_taxonomy_aliases_and_categories():
    raw = {
        "key": "default",
        "target_role": "DevOps Engineer",
        "summary": "Summary",
        "skills": ["k8s", "Jira"],
        "tools": [],
        "stack": [],
        "experience": "10 years",
        "seniority": "senior",
        "industries": [],
        "locations": ["Regensburg"],
        "languages": ["Deutsch"],
    }
    taxonomy = {
        "aliases": {"k8s": "Kubernetes"},
        "categories": {
            "skills": {"CI/CD"},
            "tools": {"Jira"},
            "stack": {"Kubernetes"},
        },
    }
    profile = extract_profile_from_text.normalize_runtime_profile(
        raw,
        profile_key="default",
        taxonomy=taxonomy,
    )
    assert profile["stack"] == ["Kubernetes"]
    assert profile["tools"] == ["Jira"]
    assert profile["skills"] == []


def test_normalize_runtime_profile_collects_unknown_terms():
    raw = {
        "key": "default",
        "target_role": "Engineer",
        "summary": "Summary",
        "skills": ["RareTechX"],
        "tools": [],
        "stack": [],
        "experience": "",
        "seniority": "mid",
        "industries": [],
        "locations": [],
        "languages": ["Deutsch"],
    }
    taxonomy = {"aliases": {}, "categories": {"skills": set(), "tools": set(), "stack": set()}}
    unknown: list[dict[str, str]] = []
    profile = extract_profile_from_text.normalize_runtime_profile(
        raw,
        profile_key="default",
        taxonomy=taxonomy,
        unknown_terms=unknown,
    )
    assert profile["skills"] == ["RareTechX"]
    assert len(unknown) == 1
    assert unknown[0]["term"] == "RareTechX"
    assert unknown[0]["source_field"] == "skills"


def test_append_unknown_terms_writes_deduped_jsonl(tmp_path):
    out = tmp_path / "unknown_terms.jsonl"
    terms = [
        {"term": "RareTechX", "source_field": "skills", "context": ""},
        {"term": "raretechx", "source_field": "skills", "context": ""},
        {"term": "Jira", "source_field": "tools", "context": ""},
    ]
    written = extract_profile_from_text._append_unknown_terms(out, terms)
    assert written == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
