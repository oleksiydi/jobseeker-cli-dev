from __future__ import annotations

from typing import Any

from tools.classify_synonyms_candidates import (
    classify_and_update,
    classify_candidate,
)


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def __iter__(self):
        return iter(self.docs)

    def limit(self, n: int) -> "FakeCursor":
        return FakeCursor(self.docs[:n])


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []
        self.updates: list[dict[str, Any]] = []
        self.inserts: list[dict[str, Any]] = []
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []

    def find(self, _selector: dict[str, Any]) -> FakeCursor:
        return FakeCursor(self.docs)

    def update_one(self, key_filter: dict[str, Any], update: dict[str, Any]) -> None:
        self.updates.append({"filter": key_filter, "update": update})

    def insert_one(self, document: dict[str, Any]) -> None:
        self.inserts.append(document)

    def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> None:
        self.indexes.append((keys, kwargs))


def test_classify_candidate_accepts_high_confidence_supported():
    status, reasons = classify_candidate(
        {
            "alias": "kubernetes",
            "confidence": 0.92,
            "source_refs_count": 3,
            "category": "stack",
            "facet": "technology_stack",
        },
        min_accept_confidence=0.85,
        min_accept_support=2,
        max_reject_confidence=0.45,
        min_reject_support=1,
        min_alias_len=3,
    )
    assert status == "accepted"
    assert reasons == ["high_confidence_supported"]


def test_classify_candidate_rejects_short_alias():
    status, reasons = classify_candidate(
        {
            "alias": "ai",
            "confidence": 0.9,
            "source_refs_count": 3,
            "category": "skills",
            "facet": "skill_requirement",
        },
        min_accept_confidence=0.85,
        min_accept_support=2,
        max_reject_confidence=0.45,
        min_reject_support=1,
        min_alias_len=3,
    )
    assert status == "rejected"
    assert "alias_too_short" in reasons


def test_classify_candidate_routes_to_needs_adjudication():
    status, reasons = classify_candidate(
        {
            "alias": "monitoring",
            "confidence": 0.6,
            "source_refs_count": 1,
            "category": "skills",
            "facet": "skill_requirement",
        },
        min_accept_confidence=0.85,
        min_accept_support=2,
        max_reject_confidence=0.45,
        min_reject_support=1,
        min_alias_len=3,
    )
    assert status == "needs_adjudication"
    assert reasons == ["insufficient_confidence_or_support"]


def test_classify_and_update_writes_state_and_audit():
    state_collection = FakeCollection(
        docs=[
            {
                "_id": "1",
                "alias": "kubernetes",
                "confidence": 0.92,
                "source_refs_count": 3,
                "category": "stack",
                "facet": "technology_stack",
            },
            {
                "_id": "2",
                "alias": "xy",
                "confidence": 0.3,
                "source_refs_count": 1,
                "category": "skills",
                "facet": "skill_requirement",
            },
            {
                "_id": "3",
                "alias": "monitoring",
                "confidence": 0.62,
                "source_refs_count": 1,
                "category": "skills",
                "facet": "skill_requirement",
            },
        ]
    )
    audit_collection = FakeCollection()
    stats = classify_and_update(
        state_collection=state_collection,
        audit_collection=audit_collection,
        run_id="gate-run",
        min_accept_confidence=0.85,
        min_accept_support=2,
        max_reject_confidence=0.45,
        min_reject_support=1,
        min_alias_len=3,
        limit=0,
        dry_run=False,
    )
    assert stats == {"total": 3, "accepted": 1, "rejected": 1, "needs_adjudication": 1}
    assert len(state_collection.updates) == 3
    assert len(audit_collection.inserts) == 3


def test_classify_and_update_dry_run_keeps_collections_untouched():
    state_collection = FakeCollection(
        docs=[
            {
                "_id": "1",
                "alias": "monitoring",
                "confidence": 0.62,
                "source_refs_count": 1,
                "category": "skills",
                "facet": "skill_requirement",
            }
        ]
    )
    audit_collection = FakeCollection()
    stats = classify_and_update(
        state_collection=state_collection,
        audit_collection=audit_collection,
        run_id="gate-run",
        min_accept_confidence=0.85,
        min_accept_support=2,
        max_reject_confidence=0.45,
        min_reject_support=1,
        min_alias_len=3,
        limit=0,
        dry_run=True,
    )
    assert stats == {"total": 1, "accepted": 0, "rejected": 0, "needs_adjudication": 1}
    assert state_collection.updates == []
    assert audit_collection.inserts == []
