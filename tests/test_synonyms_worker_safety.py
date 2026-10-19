from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from tools import adjudicate_synonyms_candidates as mod
from tools import dictionary_llm


class StatefulCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.inserts: list[dict[str, Any]] = []

    @staticmethod
    def _matches_lease_query(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        if doc.get("processing_status") not in query["processing_status"]["$in"]:
            return False
        if doc.get("adjudication_policy", {}).get("mode") in query["adjudication_policy.mode"]["$nin"]:
            return False
        retry = doc.get("retry_count")
        if retry is not None and retry >= 3:
            return False
        expires_at = doc.get("lease_expires_at")
        if expires_at is not None and expires_at >= query["$or"][1]["lease_expires_at"]["$lt"]:
            return False
        return all(doc.get(key) == value for key, value in query.items() if key in {"domain", "category", "facet", "skill_class"})

    def find_one_and_update(self, query, update, sort=None, return_document=None):
        for doc in self.docs:
            if not self._matches_lease_query(doc, query):
                continue
            doc.update(update["$set"])
            return deepcopy(doc)
        return None

    def update_one(self, key_filter, update):
        self.updates.append((key_filter, update))
        for doc in self.docs:
            if not self._matches_update_filter(doc, key_filter):
                continue
            doc.update(update.get("$set", {}))
            for key, amount in update.get("$inc", {}).items():
                doc[key] = int(doc.get(key, 0)) + int(amount)
            return SimpleNamespace(matched_count=1, modified_count=1, acknowledged=True)
        return SimpleNamespace(matched_count=0, modified_count=0, acknowledged=True)

    @staticmethod
    def _matches_update_filter(doc, key_filter):
        for key, expected in key_filter.items():
            value = doc.get(key)
            if isinstance(expected, dict) and "$gt" in expected:
                if value is None or value <= expected["$gt"]:
                    return False
            elif value != expected:
                return False
        return True

    def find_one(self, query):
        for doc in self.docs:
            if any(doc.get(key) != value for key, value in query.items()
                   if key not in {"canonical", "processing_status"}):
                continue
            if doc.get("canonical") == query["canonical"]["$ne"]:
                continue
            if doc.get("processing_status") in query["processing_status"]["$in"]:
                return doc
        return None

    def insert_one(self, document):
        self.inserts.append(document)

    def count_documents(self, query):
        status_filter = query.get("processing_status")
        statuses = status_filter.get("$in", []) if isinstance(status_filter, dict) else [status_filter]
        return sum(doc.get("processing_status") in statuses for doc in self.docs)


def _candidate(candidate_id: str = "k8s|Kubernetes|it|stack|technology_stack") -> dict[str, Any]:
    return {
        "_id": "candidate-1",
        "alias_key": "k8s",
        "canonical": "Kubernetes",
        "domain": "it",
        "category": "stack",
        "facet": "technology_stack",
        "skill_class": "not_applicable",
        "processing_status": "needs_adjudication",
        "lease_owner": None,
        "lease_expires_at": None,
        "retry_count": 0,
    }


def test_expired_lease_is_recoverable_and_retry_limit_is_enforced() -> None:
    old_lease = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    state = StatefulCollection(
        [
            _candidate() | {"_id": "expired", "processing_status": "in_adjudication", "lease_owner": "dead", "lease_expires_at": old_lease},
            _candidate() | {"_id": "active", "processing_status": "in_adjudication", "lease_owner": "alive", "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
            _candidate() | {"_id": "exhausted", "retry_count": 3},
            _candidate() | {"_id": "human", "adjudication_policy": {"mode": "human"}},
        ]
    )

    block = mod._acquire_block(state, worker_id="worker", lease_seconds=60, batch_size=3, scan_limit=10)

    assert [doc["_id"] for doc in block] == ["expired"]
    assert block[0]["lease_owner"] == "worker"


def test_error_retries_stop_after_finite_attempts() -> None:
    doc = _candidate() | {"processing_status": "in_adjudication", "lease_owner": "worker", "lease_token": "token-1",
                          "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    state = StatefulCollection([doc])

    for retry_count, expected_status in ((0, "needs_adjudication"), (1, "needs_adjudication"), (2, "unresolved")):
        doc.update({"retry_count": retry_count, "processing_status": "in_adjudication", "lease_owner": "worker", "lease_token": "token-1",
                    "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})
        mod._mark_unresolved_due_to_error(
            state, worker_id="worker", document=doc, message="temporary failure"
        )
        assert doc["processing_status"] == expected_status
        assert doc["retry_count"] == retry_count + 1
        assert doc["lease_owner"] is None


@pytest.mark.parametrize(
    "decision_ids",
    [[], ["unknown|target|it|stack|technology_stack"]],
)
def test_batch_rejects_incomplete_or_extraneous_candidate_ids(monkeypatch, decision_ids) -> None:
    payload = {
        "decisions": [
            {
                "candidate_id": candidate_id,
                "decision": "accept",
                "confidence": 0.95,
                "reason": "clear match",
            }
            for candidate_id in decision_ids
        ]
    }
    monkeypatch.setattr(
        dictionary_llm,
        "chat_completion",
        lambda _request: SimpleNamespace(finish_reason="stop", content=json.dumps(payload)),
    )

    with pytest.raises(ValueError, match="exactly one decision per candidate"):
        mod.adjudicate_batch(
            provider="openrouter",
            model="xiaomi/mimo-v2.6-flash",
            base_url=None,
            api_key=None,
            timeout_seconds=30,
            llm_max_tokens=200,
            candidates=[_candidate()],
        )


def test_conflicting_accepted_target_is_left_unresolved() -> None:
    current = _candidate() | {
        "processing_status": "in_adjudication",
        "lease_owner": "worker",
        "lease_token": "token-1",
        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    conflict = _candidate() | {"_id": "other", "canonical": "Container platform", "processing_status": "published"}
    state = StatefulCollection([current, conflict])
    audit = StatefulCollection()

    mod._update_with_decision(
        state,
        audit,
        run_id="run-1",
        worker_id="worker",
        document=current,
        decision_payload={"decision": "accept", "confidence": 0.95, "reason": "candidate conflict"},
    )

    assert current["processing_status"] == "unresolved"
    assert audit.inserts[0]["payload"]["decision"] == "unresolved"


def test_stale_worker_cannot_commit_or_write_audit() -> None:
    live = _candidate() | {
        "processing_status": "in_adjudication",
        "lease_owner": "new-worker",
        "lease_token": "new-token",
        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    stale_snapshot = dict(live)
    stale_snapshot.update(
        {
            "lease_owner": "old-worker",
            "lease_token": "old-token",
            "lease_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        }
    )
    state = StatefulCollection([live])
    audit = StatefulCollection()

    mod._update_with_decision(
        state,
        audit,
        run_id="stale-run",
        worker_id="old-worker",
        document=stale_snapshot,
        decision_payload={"decision": "accept", "confidence": 0.95, "reason": "stale result"},
    )

    assert state.docs[0]["processing_status"] == "in_adjudication"
    assert state.docs[0]["lease_owner"] == "new-worker"
    assert state.updates[-1][0]["lease_token"] == "old-token"
    assert audit.inserts == []


def test_dry_run_counts_candidates_without_leasing_or_writes(monkeypatch) -> None:
    state = StatefulCollection([_candidate()])
    audit = StatefulCollection()
    args = SimpleNamespace(
        batch_size=4,
        max_batches=1,
        scan_limit=8,
        timeout_seconds=30,
        lease_seconds=120,
        provider="openrouter",
        model="xiaomi/mimo-v2.6-flash",
        run_id="dry-run",
        worker_id="worker",
        dry_run=True,
    )
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "load_app_config", lambda: {})
    monkeypatch.setattr(mod, "get_database", lambda _cfg: {"normalization_synonyms_state": state,
                                                            "normalization_synonyms_audit": audit})
    monkeypatch.setattr(mod, "ensure_synonyms_state_indexes", lambda *_args: None)
    monkeypatch.setattr(mod, "_acquire_block", lambda *_args, **_kwargs: pytest.fail("dry run must not lease"))
    monkeypatch.setattr(mod, "adjudicate_batch", lambda **_kwargs: pytest.fail("dry run must not call LLM"))

    mod.main()

    assert state.updates == []
    assert audit.inserts == []
    assert state.docs[0]["processing_status"] == "needs_adjudication"
