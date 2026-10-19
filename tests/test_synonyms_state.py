from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from app.normalization.synonyms_state import (
    SynonymStateCandidate,
    build_synonym_state_document,
    build_synonyms_audit_event,
    ensure_synonyms_state_indexes,
    upsert_synonym_state,
    write_synonyms_audit_event,
)


class FakeCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.updates: list[dict[str, Any]] = []
        self.inserts: list[dict[str, Any]] = []

    def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> None:
        self.indexes.append((keys, kwargs))

    def update_one(self, key_filter: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> dict[str, Any]:
        self.updates.append({"filter": key_filter, "update": update, "upsert": upsert})
        return {"acknowledged": True}

    def insert_one(self, document: dict[str, Any]) -> dict[str, Any]:
        self.inserts.append(document)
        return {"acknowledged": True}


class StatefulFakeCollection(FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.documents: list[dict[str, Any]] = []

    def find_one(self, key_filter: dict[str, Any]) -> dict[str, Any] | None:
        for document in self.documents:
            if all(document.get(key) == value for key, value in key_filter.items()):
                return deepcopy(document)
        return None

    def update_one(self, key_filter: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> Any:
        super().update_one(key_filter, update, upsert)
        existing = next((doc for doc in self.documents if self._matches(doc, key_filter)), None)
        if existing is not None:
            for key, value in update.get("$set", {}).items():
                existing[key] = deepcopy(value)
            for key in update.get("$unset", {}):
                existing.pop(key, None)
            return SimpleNamespace(acknowledged=True, matched_count=1, modified_count=1, upserted_id=None)
        if upsert and not any(
            all(doc.get(key) == value for key, value in key_filter.items())
            for doc in self.documents
        ):
            inserted = dict(update.get("$setOnInsert", {}))
            self.documents.append(inserted)
            self.inserts.append(inserted)
            return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0, upserted_id="inserted")
        return SimpleNamespace(acknowledged=True, matched_count=0, modified_count=0, upserted_id=None)

    @staticmethod
    def _matches(document: dict[str, Any], key_filter: dict[str, Any]) -> bool:
        for key, expected in key_filter.items():
            if isinstance(expected, dict) and "$exists" in expected:
                if (key in document) != expected["$exists"]:
                    return False
            elif document.get(key) != expected:
                return False
        return True


def _sample_candidate() -> SynonymStateCandidate:
    return SynonymStateCandidate(
        alias="k8s",
        canonical="Kubernetes",
        domain="it_software_daten",
        category="stack",
        facet="technology_stack",
        skill_class="not_applicable",
        confidence=0.9,
        source="rule",
        run_id="run-1",
        source_refs_count=3,
        sample_phrases=("Erfahrung mit k8s",),
    )


def test_ensure_synonyms_state_indexes_creates_required_indexes() -> None:
    state_collection = FakeCollection()
    audit_collection = FakeCollection()

    ensure_synonyms_state_indexes(state_collection, audit_collection)

    assert any(index[1].get("name") == "uniq_alias_canonical_domain_category_facet" for index in state_collection.indexes)
    assert any(index[1].get("name") == "status_updated_at" for index in state_collection.indexes)
    assert any(index[1].get("name") == "status_lease_expires_at" for index in state_collection.indexes)
    assert any(index[1].get("name") == "state_id_created_at" for index in audit_collection.indexes)


def test_build_synonym_state_document_uses_contract_defaults() -> None:
    document = build_synonym_state_document(_sample_candidate())

    assert document["alias"] == "k8s"
    assert document["alias_key"] == "k8s"
    assert document["canonical"] == "Kubernetes"
    assert document["processing_status"] == "new"
    assert document["adjudication_policy"]["mode"] == "machine"
    assert document["adjudication"]["primary"]["status"] == "pending"


def test_upsert_synonym_state_uses_natural_key_filter() -> None:
    collection = StatefulFakeCollection()
    document = build_synonym_state_document(_sample_candidate())

    upsert_synonym_state(collection, document)

    assert len(collection.updates) == 1
    update_call = collection.updates[0]
    assert update_call["upsert"] is True
    assert update_call["filter"] == {
        "alias_key": "k8s",
        "canonical": "Kubernetes",
        "domain": "it_software_daten",
        "category": "stack",
        "facet": "technology_stack",
    }
    assert update_call["update"]["$setOnInsert"]["alias"] == document["alias"]
    assert len(update_call["update"]["$setOnInsert"]["candidate_hash"]) == 64
    assert set(update_call["update"]) == {"$setOnInsert"}


def test_upsert_synonym_state_replay_preserves_adjudication_and_timestamps() -> None:
    collection = StatefulFakeCollection()
    document = build_synonym_state_document(_sample_candidate())
    document["created_at"] = "2026-10-01T12:00:00Z"
    document["updated_at"] = "2026-10-02T12:00:00Z"
    created_at = document["created_at"]
    updated_at = document["updated_at"]

    first_result = upsert_synonym_state(collection, document)
    assert first_result.upserted_id == "inserted"
    stored = collection.documents[0]
    stored.update(
        {
            "processing_status": "published",
            "decision_source": "human_adjudication",
            "lease_owner": "adjudicator-1",
            "lease_expires_at": "2026-10-14T12:00:00Z",
            "adjudication": {"primary": {"status": "done", "decision": "accept"}},
        }
    )
    saved_state = dict(stored)

    replay = build_synonym_state_document(replace(_sample_candidate(), run_id="run-2"))
    replay["created_at"] = "2026-10-20T12:00:00Z"
    replay["updated_at"] = "2026-10-20T12:00:00Z"
    result = upsert_synonym_state(collection, replay)

    assert len(collection.documents) == 1
    assert len(collection.inserts) == 1
    assert len(collection.updates) == 1
    assert result.matched_count == 1
    assert result.modified_count == 0
    assert result.upserted_id is None
    assert result.acknowledged is True
    assert collection.documents[0] == saved_state
    assert collection.documents[0]["created_at"] == created_at
    assert collection.documents[0]["updated_at"] == updated_at
    assert collection.documents[0]["processing_status"] == "published"
    assert collection.documents[0]["adjudication"]["primary"]["status"] == "done"


def test_changed_evidence_resets_proposed_state_with_compare_and_set() -> None:
    collection = StatefulFakeCollection()
    original = build_synonym_state_document(_sample_candidate())
    upsert_synonym_state(collection, original)
    stored = collection.documents[0]
    stored.update({"processing_status": "published", "updated_at": "2026-10-03T12:00:00Z"})
    created_at = stored["created_at"]
    old_hash = stored["candidate_hash"]

    changed = build_synonym_state_document(
        replace(
            _sample_candidate(),
            run_id="run-2",
            source_refs_count=8,
            sample_phrases=("Kubernetes Cluster betreiben", "Erfahrung mit k8s"),
            confidence=0.96,
        )
    )
    changed["processing_status"] = "needs_adjudication"
    changed["decision_source"] = "llm_cluster"
    changed["adjudication"]["primary"] = {
        "status": "done",
        "decision": "unresolved",
        "confidence": 0.87,
        "provider": "openrouter",
        "model": "mistralai/mistral-nemo",
    }

    result = upsert_synonym_state(collection, changed)
    update_call = collection.updates[-1]

    assert result.matched_count == 1
    assert update_call["upsert"] is False
    assert update_call["filter"]["candidate_hash"] == old_hash
    assert collection.documents[0]["candidate_hash"] != old_hash
    assert collection.documents[0]["processing_status"] == "needs_adjudication"
    assert collection.documents[0]["source_refs_count"] == 8
    assert collection.documents[0]["sample_phrases"] == ["Kubernetes Cluster betreiben", "Erfahrung mit k8s"]
    assert collection.documents[0]["adjudication"]["primary"]["model"] == "mistralai/mistral-nemo"
    assert collection.documents[0]["created_at"] == created_at
    assert collection.documents[0]["updated_at"] != "2026-10-03T12:00:00Z"


def test_changed_proposal_model_resets_candidate() -> None:
    collection = StatefulFakeCollection()
    document = build_synonym_state_document(_sample_candidate())
    document["processing_status"] = "accepted"
    document["adjudication"]["primary"] = {
        "status": "done",
        "decision": "accept",
        "confidence": 0.95,
        "provider": "openrouter",
        "model": "mistralai/mistral-nemo",
    }
    upsert_synonym_state(collection, document)
    stored_hash = collection.documents[0]["candidate_hash"]

    new_proposal = deepcopy(document)
    new_proposal["adjudication"]["primary"]["model"] = "different/model"
    result = upsert_synonym_state(collection, new_proposal)

    assert result.matched_count == 1
    assert collection.documents[0]["adjudication"]["primary"]["model"] == "different/model"
    assert collection.documents[0]["candidate_hash"] != stored_hash


def test_legacy_candidate_hash_is_derived_before_reset() -> None:
    collection = StatefulFakeCollection()
    legacy = build_synonym_state_document(_sample_candidate())
    legacy.pop("candidate_hash", None)
    legacy["created_at"] = "2026-10-01T12:00:00Z"
    collection.documents.append(legacy)

    same_input = build_synonym_state_document(replace(_sample_candidate(), run_id="new-run"))
    result = upsert_synonym_state(collection, same_input)

    assert result.modified_count == 0
    assert collection.updates == []
    assert collection.documents[0].get("candidate_hash") is None

    changed = build_synonym_state_document(replace(_sample_candidate(), source_refs_count=9))
    changed_result = upsert_synonym_state(collection, changed)
    assert changed_result.matched_count == 1
    assert collection.updates[-1]["filter"]["candidate_hash"] == {"$exists": False}
    assert collection.documents[0]["candidate_hash"]


def test_write_synonyms_audit_event_inserts_document() -> None:
    collection = FakeCollection()
    event = build_synonyms_audit_event(
        state_id="state-1",
        event_type="status_changed",
        run_id="run-1",
        payload={"from": "new", "to": "accepted"},
    )

    write_synonyms_audit_event(collection, event)

    assert len(collection.inserts) == 1
    assert collection.inserts[0]["event_type"] == "status_changed"
