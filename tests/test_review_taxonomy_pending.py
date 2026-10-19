import json
from pathlib import Path

from tools import review_taxonomy_pending as reviewer


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_load_pending_adds_default_review_fields(tmp_path):
    pending = tmp_path / "pending.jsonl"
    _write_jsonl(
        pending,
        [
            {
                "term": "DevOps",
                "term_key": "devops",
                "canonical": "DevOps",
                "category": "skills",
                "confidence": 0.78,
            }
        ],
    )
    rows = reviewer._load_pending(pending)
    assert len(rows) == 1
    assert rows[0]["review_status"] == "auto_suggested"
    assert rows[0]["review_code"] == 0


def test_set_status_updates_matching_rows(tmp_path):
    pending = tmp_path / "pending.jsonl"
    _write_jsonl(
        pending,
        [
            {
                "term": "DevOps",
                "term_key": "devops",
                "canonical": "DevOps",
                "category": "skills",
                "review_status": "auto_suggested",
                "review_code": 0,
            }
        ],
    )
    rows = reviewer._load_pending(pending)
    updated = reviewer._set_status(
        rows,
        term_key="devops",
        category="",
        status="approved",
        by="tester",
        note="ok",
    )
    assert updated == 1
    assert rows[0]["review_status"] == "approved"
    assert rows[0]["review_code"] == 1
    assert rows[0]["reviewed_by"] == "tester"
    assert rows[0]["review_note"] == "ok"
    assert rows[0]["reviewed_at"] != ""


def test_apply_approved_updates_taxonomy(tmp_path):
    pending = [
        {
            "term": "DevOps",
            "term_key": "devops",
            "canonical": "DevOps",
            "category": "skills",
            "review_status": "approved",
            "review_code": 1,
        },
        {
            "term": "Foo",
            "term_key": "foo",
            "canonical": "Foo",
            "category": "tools",
            "review_status": "auto_suggested",
            "review_code": 0,
        },
    ]
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {"version": 1, "aliases": {}, "categories": {"skills": [], "tools": [], "stack": []}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    applied = reviewer._apply_approved(pending, taxonomy_path=taxonomy_path)
    assert applied == 1
    saved = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    assert saved["aliases"]["devops"] == "DevOps"
    assert "DevOps" in saved["categories"]["skills"]
    assert "foo" not in saved["aliases"]

