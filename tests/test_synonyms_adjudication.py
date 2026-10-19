from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from tools.adjudicate_synonyms_candidates import (
    _acquire_block,
    _make_candidate_id,
    _parse_decisions,
    _update_with_decision,
)


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []
        self.updates: list[dict[str, Any]] = []
        self.inserts: list[dict[str, Any]] = []

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        sort: list[tuple[str, int]] | None = None,
        return_document: bool | None = None,
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if doc.get("processing_status") not in query["processing_status"]["$in"]:
                continue
            if doc.get("lease_owner") is not None:
                continue
            if any(field in query and doc.get(field) != query[field]
                   for field in ("domain", "category", "facet", "skill_class")):
                continue
            doc.update(update["$set"])
            return deepcopy(doc)
        return None

    def update_one(self, key_filter: dict[str, Any], update: dict[str, Any]) -> None:
        self.updates.append({"filter": key_filter, "update": update})
        for doc in self.docs:
            if key_filter.get("_id") is not None and doc.get("_id") != key_filter.get("_id"):
                continue
            if key_filter.get("lease_owner") is not None and doc.get("lease_owner") != key_filter.get("lease_owner"):
                continue
            if key_filter.get("processing_status") is not None and doc.get("processing_status") != key_filter.get("processing_status"):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    doc[k] = int(doc.get(k, 0)) + int(v)
            break

    def insert_one(self, document: dict[str, Any]) -> None:
        self.inserts.append(document)


def test_parse_decisions_rejects_invalid_batch() -> None:
    with pytest.raises(ValidationError):
        _parse_decisions('{"decisions":[{"candidate_id":"a","decision":"unknown","confidence":0.9,"reason":"ok"}]}')
    with pytest.raises(ValueError, match="Duplicate"):
        _parse_decisions('{"decisions":[{"candidate_id":"a","decision":"accept","confidence":0.9,"reason":"ok"},'
                         '{"candidate_id":"a","decision":"reject","confidence":0.9,"reason":"ok"}]}')


def test_acquire_block_leases_only_selected_scope() -> None:
    docs = [
        {"_id": "1", "processing_status": "needs_adjudication", "domain": "it", "category": "stack", "facet": "technology_stack", "skill_class": "not_applicable"},
        {"_id": "2", "processing_status": "needs_adjudication", "domain": "finance", "category": "skills", "facet": "skill_requirement", "skill_class": "professional"},
        {"_id": "3", "processing_status": "needs_adjudication", "domain": "it", "category": "stack", "facet": "technology_stack", "skill_class": "not_applicable"},
    ]
    state = FakeCollection(docs=docs)
    block = _acquire_block(
        state,
        worker_id="w1",
        lease_seconds=60,
        batch_size=2,
        scan_limit=3,
    )
    assert len(block) == 2
    assert all(doc["domain"] == "it" for doc in block)
    released = [call for call in state.updates if call["update"]["$set"].get("processing_status") == "needs_adjudication"]
    assert len(released) == 0
    assert docs[1]["processing_status"] == "needs_adjudication"


def test_update_with_decision_updates_state_and_audit() -> None:
    doc = {
        "_id": "d1",
        "alias_key": "k8s",
        "canonical": "Kubernetes",
        "domain": "it_software_daten",
        "category": "stack",
        "facet": "technology_stack",
        "processing_status": "in_adjudication",
        "lease_owner": "w1",
    }
    state = FakeCollection(docs=[doc])
    audit = FakeCollection()
    _update_with_decision(
        state,
        audit,
        run_id="r1",
        worker_id="w1",
        document=doc,
        decision_payload={"decision": "accept", "confidence": 0.88, "reason": "clear mapping"},
    )
    assert state.docs[0]["processing_status"] == "adjudicated_accepted"
    assert state.docs[0]["decision_source"] == "llm_adjudication"
    assert state.docs[0]["lease_owner"] is None
    assert len(audit.inserts) == 1
    assert audit.inserts[0]["event_type"] == "llm_adjudicated"
    assert audit.inserts[0]["payload"]["decision"] == "accept"


def test_make_candidate_id_is_stable() -> None:
    doc = {"alias_key": "k8s", "canonical": "Kubernetes", "domain": "it", "category": "stack", "facet": "technology_stack"}
    assert _make_candidate_id(doc) == "k8s|Kubernetes|it|stack|technology_stack"


def test_low_confidence_strong_decision_remains_unresolved():
    doc = {"_id": "d1", "processing_status": "in_adjudication", "lease_owner": "w1"}
    state = FakeCollection([doc])
    _update_with_decision(state, FakeCollection(), run_id="r1", worker_id="w1", document=doc,
        decision_payload={"decision": "accept", "confidence": 0.5, "reason": "uncertain"},
        provider="openrouter", model="xiaomi/mimo-v2.6-flash")
    assert doc["processing_status"] == "unresolved"
    assert doc["adjudication.escalated"]["model"] == "xiaomi/mimo-v2.6-flash"
