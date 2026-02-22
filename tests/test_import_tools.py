import json
from datetime import datetime, timezone

from tools import backfill_is_active_flag, import_analysis_prompts, import_profiles


def test_profile_normalize_adds_default_key_and_hash():
    now = datetime(2026, 2, 8, 10, 0, tzinfo=timezone.utc)
    doc = {"name": "John", "skills": ["python"]}
    normalized = import_profiles.normalize_profile_doc(
        doc,
        default_key="default",
        upsert_key="key",
        strict_validate=False,
        created_by="test",
        now=now,
    )
    assert normalized["key"] == "default"
    assert normalized["created_by"] == "test"
    assert normalized["updated_at"] == now
    assert normalized["profile_hash"]


def test_profile_normalize_strict_rejects_empty_business_payload():
    now = datetime(2026, 2, 8, 10, 0, tzinfo=timezone.utc)
    doc = {"key": "default"}
    try:
        import_profiles.normalize_profile_doc(
            doc,
            default_key="default",
            upsert_key="key",
            strict_validate=True,
            created_by="test",
            now=now,
        )
    except ValueError as exc:
        assert "enthält keine Business-Felder" in str(exc)
    else:
        raise AssertionError("Expected ValueError for strict empty business payload")


def test_prompt_normalize_sets_hash_and_version():
    now = datetime(2026, 2, 8, 10, 0, tzinfo=timezone.utc)
    doc = {
        "key": "default",
        "version": "1",
        "system_prompt": "S",
        "analysis_task": "T",
    }
    normalized = import_analysis_prompts.normalize_prompt_doc(doc, now=now, created_by="test")
    assert normalized["version"] == 1
    assert normalized["prompt_hash"]
    assert normalized["created_by"] == "test"
    assert normalized["updated_at"] == now


def test_prompt_load_docs_accepts_object(tmp_path):
    file_path = tmp_path / "prompt.json"
    file_path.write_text(
        json.dumps(
            {
                "key": "default",
                "version": 1,
                "system_prompt": "S",
                "analysis_task": "T",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    docs = import_analysis_prompts.load_docs(file_path)
    assert isinstance(docs, list)
    assert len(docs) == 1
    assert docs[0]["key"] == "default"


def test_backfill_is_active_legacy_filter_targets_missing_flag():
    assert backfill_is_active_flag._legacy_active_filter() == {"is_active": {"$exists": False}}
