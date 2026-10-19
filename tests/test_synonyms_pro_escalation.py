from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.client import LLMResponseError
from tools import adjudicate_synonyms_candidates as worker
from tools import publish_synonyms_auto as publisher
from tools.llm_artifact_cache import ArtifactCache


def _get_path(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    parent = document
    for part in parts[:-1]:
        parent = parent.setdefault(part, {})
    parent[parts[-1]] = value


class _Missing:
    pass


_MISSING = _Missing()


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(document, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
                return False
            continue
        value = _get_path(document, key)
        if isinstance(expected, dict):
            for operator, operand in expected.items():
                if operator == "$in" and (value is _MISSING or value not in operand):
                    return False
                if operator == "$nin" and value is not _MISSING and value in operand:
                    return False
                if operator == "$ne" and value is not _MISSING and value == operand:
                    return False
                if operator == "$lt" and (value is _MISSING or value is None or not value < operand):
                    return False
                if operator == "$gt" and (value is _MISSING or value is None or not value > operand):
                    return False
                if operator == "$exists" and ((value is not _MISSING) != bool(operand)):
                    return False
        elif value is _MISSING or value != expected:
            return False
    return True


class MiniCollection:
    """Small Mongo-shaped fake for nested worker selectors and guarded writes."""

    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = deepcopy(documents or [])
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.inserts: list[dict[str, Any]] = []

    def find_one_and_update(self, query, update, sort=None, return_document=None):
        matches = [document for document in self.documents if _matches(document, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(key=lambda item: _get_path(item, field) if _get_path(item, field) is not _MISSING else "",
                             reverse=direction < 0)
        if not matches:
            return None
        document = matches[0]
        for path, value in update.get("$set", {}).items():
            _set_path(document, path, deepcopy(value))
        return deepcopy(document)

    def update_one(self, query, update):
        self.updates.append((deepcopy(query), deepcopy(update)))
        for document in self.documents:
            if not _matches(document, query):
                continue
            for path, value in update.get("$set", {}).items():
                _set_path(document, path, deepcopy(value))
            for path, amount in update.get("$inc", {}).items():
                current = _get_path(document, path)
                _set_path(document, path, (0 if current is _MISSING else current) + amount)
            return SimpleNamespace(matched_count=1, modified_count=1, acknowledged=True)
        return SimpleNamespace(matched_count=0, modified_count=0, acknowledged=True)

    def find_one(self, query, projection=None):
        return next((deepcopy(document) for document in self.documents if _matches(document, query)), None)

    def insert_one(self, document):
        self.inserts.append(deepcopy(document))


def _candidate(candidate_id: str, **changes: Any) -> dict[str, Any]:
    alias, canonical = candidate_id.split("|")[:2]
    return {
        "_id": candidate_id,
        "alias": alias,
        "alias_key": alias,
        "canonical": canonical,
        "domain": "it",
        "category": "stack",
        "facet": "technology_stack",
        "skill_class": "not_applicable",
        "processing_status": "needs_adjudication",
        "adjudication_policy": {"mode": "machine", "stronger_model_escalation_enabled": True},
        "adjudication": {
            "primary": {"status": "done", "decision": "unresolved", "provider": "openrouter",
                        "model": "mistralai/mistral-nemo", "confidence": 0.7},
            "escalated": {"status": "pending", "decision": None, "provider": None, "model": None},
            "pro": {"status": "pending", "decision": None, "provider": None, "model": None},
        },
        "retry_count": 0,
        "pro_retry_count": 0,
        "lease_owner": None,
        "lease_token": None,
        "lease_expires_at": None,
    } | changes


def _args(stage: str, *, model: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        stage=stage,
        provider="openrouter",
        model=model or ("xiaomi/mimo-v2.6-flash" if stage == "flash" else "xiaomi/mimo-v2.6-pro"),
        base_url=None,
        api_key=None,
        timeout_seconds=30,
        llm_max_tokens=400,
        worker_id=f"{stage}-worker",
    )


def _decision(candidate: dict[str, Any], decision: str, confidence: float = 0.95) -> dict[str, Any]:
    return {worker._make_candidate_id(candidate): {
        "decision": decision,
        "confidence": confidence,
        "reason": f"{decision} decision",
    }}


def _run_stage(monkeypatch, document, stage: str, decision: str, confidence: float = 0.95):
    state = MiniCollection([document])
    audit = MiniCollection()
    block = worker._acquire_block(
        state, worker_id=f"{stage}-worker", lease_seconds=120,
        batch_size=1, scan_limit=1, stage=stage,
    )
    assert len(block) == 1
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **kwargs: _decision(kwargs["candidates"][0], decision, confidence))
    worker._process_block(_args(stage), block, state, audit, run_id=f"{stage}-run", cache=ArtifactCache(None))
    return state, audit


def test_flash_to_pro_preserves_nemo_flash_and_pro_decisions(monkeypatch):
    original = _candidate("k8s|Kubernetes|it|stack|technology_stack")
    state = MiniCollection([original])
    audit = MiniCollection()
    flash_block = worker._acquire_block(state, worker_id="flash-worker", lease_seconds=120,
                                        batch_size=1, scan_limit=1, stage="flash")
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **kwargs: _decision(kwargs["candidates"][0], "unresolved", 0.55))
    worker._process_block(_args("flash"), flash_block, state, audit, run_id="flash-run", cache=ArtifactCache(None))

    saved = state.documents[0]
    assert saved["processing_status"] == "needs_escalated_adjudication"
    assert saved["adjudication"]["primary"]["model"] == "mistralai/mistral-nemo"
    assert saved["adjudication"]["escalated"]["model"] == "xiaomi/mimo-v2.6-flash"
    assert saved["adjudication"]["escalated"]["decision"] == "unresolved"

    pro_block = worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                      batch_size=1, scan_limit=1, stage="pro")
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **kwargs: _decision(kwargs["candidates"][0], "accept"))
    worker._process_block(_args("pro"), pro_block, state, audit, run_id="pro-run", cache=ArtifactCache(None))
    saved = state.documents[0]
    assert saved["processing_status"] == "escalated_accepted"
    assert saved["adjudication"]["primary"]["model"] == "mistralai/mistral-nemo"
    assert saved["adjudication"]["escalated"]["model"] == "xiaomi/mimo-v2.6-flash"
    assert saved["adjudication"]["pro"]["model"] == "xiaomi/mimo-v2.6-pro"
    assert saved["adjudication"]["pro"]["decision"] == "accept"
    assert [event["event_type"] for event in audit.inserts] == ["llm_adjudicated", "llm_pro_adjudicated"]
    assert worker._acquire_block(state, worker_id="again", lease_seconds=120,
                                 batch_size=1, scan_limit=1, stage="pro") == []


@pytest.mark.parametrize("origin", ["legacy_flash", "strong_clustering"])
def test_pro_acquires_legacy_flash_and_strong_clustering_unresolved(origin):
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack", processing_status="unresolved")
    doc["adjudication"]["escalated"] = {
        "status": "done", "decision": "unresolved", "confidence": 0.7,
        "provider": "openrouter", "model": "xiaomi/mimo-v2.6-flash",
        "origin": origin,
    }
    state = MiniCollection([doc])
    block = worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                  batch_size=1, scan_limit=1, stage="pro")
    assert [item["_id"] for item in block] == [doc["_id"]]
    assert block[0]["processing_status"] == "escalated_in_progress"


def test_pro_unresolved_is_terminal_and_excluded_from_reacquire(monkeypatch):
    doc = _candidate("tf|Terraform|it|stack|technology_stack", processing_status="needs_escalated_adjudication")
    doc["adjudication"]["escalated"] = {"status": "done", "decision": "unresolved",
                                         "confidence": 0.6, "model": "xiaomi/mimo-v2.6-flash"}
    state, _audit = _run_stage(monkeypatch, doc, "pro", "unresolved", 0.4)
    saved = state.documents[0]
    assert saved["processing_status"] == "unresolved"
    assert saved["adjudication"]["pro"]["status"] == "done"
    assert worker._acquire_block(state, worker_id="again", lease_seconds=120,
                                 batch_size=1, scan_limit=1, stage="pro") == []


def test_queued_pro_candidate_with_completed_pro_decision_is_not_reacquired():
    doc = _candidate("tf|Terraform|it|stack|technology_stack",
                     processing_status="needs_escalated_adjudication")
    doc["adjudication"]["pro"] = {"status": "done", "decision": "accept",
                                   "confidence": 0.95, "model": "xiaomi/mimo-v2.6-pro"}
    state = MiniCollection([doc])
    assert worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                 batch_size=1, scan_limit=1, stage="pro") == []


@pytest.mark.parametrize("message", ["OpenRouter upstream error code 400", "OPENROUTER_API_KEY is required"])
def test_provider_value_error_does_not_queue_or_call_pro(monkeypatch, message):
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack")
    state = MiniCollection([doc])
    audit = MiniCollection()
    block = worker._acquire_block(state, worker_id="flash-worker", lease_seconds=120,
                                  batch_size=1, scan_limit=1, stage="flash")
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **_kwargs: (_ for _ in ()).throw(ValueError(message)))
    with pytest.raises(SystemExit):
        worker._process_block(_args("flash"), block, state, audit,
                              run_id="flash-api-error", cache=ArtifactCache(None))
    saved = state.documents[0]
    assert saved["processing_status"] == "needs_adjudication"
    assert saved["adjudication"]["escalated"]["status"] == "pending"
    assert saved["adjudication"]["pro"]["status"] == "pending"
    assert saved["retry_count"] == 1
    assert saved["pro_retry_count"] == 0


def test_invalid_json_flash_response_queues_pro_and_invalid_payload_is_not_cached(tmp_path, monkeypatch):
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack")
    state = MiniCollection([doc])
    audit = MiniCollection()
    cache = ArtifactCache(tmp_path / "invalid-flash.sqlite")
    requests = []

    def completion(request):
        requests.append(request)
        if request.model.endswith("flash") and len([item for item in requests if item.model.endswith("flash")]) == 1:
            return SimpleNamespace(finish_reason="stop", content="not valid json")
        user_rows = json.loads(request.messages[1]["content"].split("\n", 1)[1])
        payload = {"decisions": [{"candidate_id": row["candidate_id"], "decision": "accept",
                                  "confidence": 0.95, "reason": "clear mapping"} for row in user_rows]}
        return SimpleNamespace(finish_reason="stop", content=json.dumps(payload))

    monkeypatch.setattr("tools.dictionary_llm.chat_completion", completion)
    flash_block = worker._acquire_block(state, worker_id="flash-worker", lease_seconds=120,
                                        batch_size=1, scan_limit=1, stage="flash")
    worker._process_block(_args("flash"), flash_block, state, audit, run_id="invalid-flash", cache=cache)
    assert state.documents[0]["processing_status"] == "needs_escalated_adjudication"
    assert state.documents[0]["adjudication"]["escalated"]["status"] == "invalid"

    # The same Flash request must reach the API again because the invalid payload was not cached.
    worker.adjudicate_batch(
        provider="openrouter", model="xiaomi/mimo-v2.6-flash", base_url=None, api_key=None,
        timeout_seconds=30, llm_max_tokens=400, candidates=[doc], cache=cache,
    )
    assert len([request for request in requests if request.model.endswith("flash")]) == 2

    pro_block = worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                      batch_size=1, scan_limit=1, stage="pro")
    worker._process_block(_args("pro"), pro_block, state, audit, run_id="pro-after-invalid-flash", cache=cache)
    assert state.documents[0]["processing_status"] == "escalated_accepted"
    assert any(request.model.endswith("pro") for request in requests)
    cache.close()


def test_legacy_unresolved_without_completed_flash_decision_is_not_acquired():
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack", processing_status="unresolved")
    doc["adjudication"]["escalated"] = {
        "status": "pending", "decision": None, "confidence": None,
        "provider": None, "model": "xiaomi/mimo-v2.6-flash",
    }
    state = MiniCollection([doc])
    assert worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                 batch_size=1, scan_limit=1, stage="pro") == []


def test_invalid_pro_response_has_finite_retry_budget(monkeypatch):
    doc = _candidate("tf|Terraform|it|stack|technology_stack",
                     processing_status="needs_escalated_adjudication")
    doc["adjudication"]["escalated"] = {"status": "done", "decision": "unresolved",
                                         "confidence": 0.6, "model": "xiaomi/mimo-v2.6-flash"}
    state = MiniCollection([doc])
    audit = MiniCollection()
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **_kwargs: (_ for _ in ()).throw(
        LLMResponseError("invalid Pro response")))

    for expected_retry in (1, 2, 3):
        block = worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                      batch_size=1, scan_limit=1, stage="pro")
        assert len(block) == 1
        with pytest.raises(SystemExit, match="Pro returned an invalid response"):
            worker._process_block(_args("pro"), block, state, audit,
                                  run_id=f"invalid-pro-{expected_retry}", cache=ArtifactCache(None))
        assert state.documents[0]["pro_retry_count"] == expected_retry
    assert state.documents[0]["processing_status"] == "unresolved"
    assert worker._acquire_block(state, worker_id="again", lease_seconds=120,
                                 batch_size=1, scan_limit=1, stage="pro") == []


def test_terminal_pro_ambiguous_low_confidence_candidate_is_not_published(monkeypatch):
    doc = _candidate("tf|Terraform|it|stack|technology_stack",
                     processing_status="needs_escalated_adjudication")
    doc["adjudication"]["escalated"] = {"status": "done", "decision": "unresolved",
                                         "confidence": 0.6, "model": "xiaomi/mimo-v2.6-flash"}
    state, _audit = _run_stage(monkeypatch, doc, "pro", "unresolved", 0.45)
    saved = state.documents[0]
    assert saved["processing_status"] == "unresolved"
    assert saved["adjudication"]["pro"]["decision"] == "unresolved"
    monkeypatch.setattr("sys.argv", ["publish_synonyms_auto"])
    publish_statuses = publisher.parse_args().include_status.split(",")
    assert "unresolved" not in publish_statuses
    queried = [saved] if saved["processing_status"] in publish_statuses else []
    assert publisher._iter_publishable(queried) == []


def test_pro_excludes_human_disabled_and_escalation_disabled_candidates():
    docs = [
        _candidate("a|A|it|stack|technology_stack", processing_status="needs_escalated_adjudication",
                   adjudication_policy={"mode": "human", "stronger_model_escalation_enabled": True}),
        _candidate("b|B|it|stack|technology_stack", processing_status="needs_escalated_adjudication",
                   adjudication_policy={"mode": "disabled", "stronger_model_escalation_enabled": True}),
        _candidate("c|C|it|stack|technology_stack", processing_status="needs_escalated_adjudication",
                   adjudication_policy={"mode": "hybrid", "stronger_model_escalation_enabled": False}),
        _candidate("d|D|it|stack|technology_stack", processing_status="needs_escalated_adjudication"),
    ]
    state = MiniCollection(docs)
    block = worker._acquire_block(state, worker_id="pro-worker", lease_seconds=120,
                                  batch_size=4, scan_limit=4, stage="pro")
    assert [doc["_id"] for doc in block] == ["d|D|it|stack|technology_stack"]


def test_pro_lease_blocks_active_reclaims_expired_and_rejects_stale_commit():
    active = _candidate("active|Active|it|stack|technology_stack", processing_status="escalated_in_progress",
                        lease_owner="alive", lease_token="live",
                        lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    expired = _candidate("expired|Expired|it|stack|technology_stack", processing_status="needs_escalated_adjudication",
                         lease_owner="dead", lease_token="old",
                         lease_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    state = MiniCollection([active, expired])
    audit = MiniCollection()
    block = worker._acquire_block(state, worker_id="new-worker", lease_seconds=120,
                                  batch_size=2, scan_limit=2, stage="pro")
    assert [doc["_id"] for doc in block] == [expired["_id"]]
    stale = deepcopy(block[0])
    stale.update({"lease_owner": "stale-worker", "lease_token": "old-token"})
    result = worker._update_with_decision(
        state, audit, run_id="stale", worker_id="stale-worker", document=stale,
        decision_payload={"decision": "accept", "confidence": 0.99, "reason": "stale"},
        provider="openrouter", model="xiaomi/mimo-v2.6-pro", stage="pro",
    )
    assert result is None
    assert state.documents[1]["processing_status"] == "escalated_in_progress"
    assert state.documents[1]["lease_owner"] == "new-worker"
    assert audit.inserts == []


def test_flash_and_pro_retries_are_independent_and_stage_guarded():
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack",
                     processing_status="escalated_in_progress", lease_owner="worker",
                     lease_token="token", lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat())
    state = MiniCollection([doc])
    worker._mark_unresolved_due_to_error(state, worker_id="worker", document=doc,
                                         message="pro transient", stage="pro")
    saved = state.documents[0]
    assert saved["pro_retry_count"] == 1
    assert saved["retry_count"] == 0
    assert saved["processing_status"] == "needs_escalated_adjudication"

    # A stale/expired lease cannot consume either stage's retry budget.
    saved.update({"processing_status": "in_adjudication", "lease_owner": "old", "lease_token": "old-token",
                  "lease_expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()})
    worker._mark_unresolved_due_to_error(state, worker_id="old", document=saved,
                                         message="expired flash transient", stage="flash")
    assert saved["retry_count"] == 0


@pytest.mark.parametrize("error_name", ["HTTP401Error", "HTTP402Error"])
def test_flash_http_auth_or_payment_error_never_invokes_pro(monkeypatch, error_name):
    doc = _candidate("k8s|Kubernetes|it|stack|technology_stack")
    state = MiniCollection([doc])
    audit = MiniCollection()
    block = worker._acquire_block(state, worker_id="flash-worker", lease_seconds=120,
                                  batch_size=1, scan_limit=1, stage="flash")
    class APIError(Exception):
        pass
    APIError.__name__ = error_name
    monkeypatch.setattr(worker, "adjudicate_batch", lambda **_kwargs: (_ for _ in ()).throw(APIError()))
    pro_calls = []

    def call_pro(**_kwargs):
        pro_calls.append(True)

    with pytest.raises(SystemExit):
        worker._process_block(_args("flash"), block, state, audit,
                              run_id="flash-error", cache=ArtifactCache(None))
    assert pro_calls == []
    assert state.documents[0]["processing_status"] == "needs_adjudication"
    assert state.documents[0]["retry_count"] == 1
    assert state.documents[0]["pro_retry_count"] == 0
    assert state.documents[0]["adjudication"]["pro"]["status"] == "pending"


def test_adjudication_cache_hits_same_request_and_technical_reordering(tmp_path, monkeypatch):
    calls = []

    def completion(request):
        calls.append(request)
        user = json.loads(request.messages[1]["content"].split("\n", 1)[1])
        decisions = [{"candidate_id": row["candidate_id"], "decision": "accept",
                      "confidence": 0.95, "reason": "same evidence"} for row in user]
        return SimpleNamespace(finish_reason="stop", content=json.dumps({"decisions": decisions}))

    monkeypatch.setattr("tools.dictionary_llm.chat_completion", completion)
    cache = ArtifactCache(tmp_path / "pro-cache.sqlite")
    candidates = [
        _candidate("k8s|Kubernetes|it|stack|technology_stack"),
        _candidate("tf|Terraform|it|stack|technology_stack"),
    ]
    common = {"provider": "openrouter", "model": "xiaomi/mimo-v2.6-pro", "base_url": "https://llm.example",
              "api_key": "secret", "timeout_seconds": 30, "llm_max_tokens": 400}
    try:
        first = worker.adjudicate_batch(**common, candidates=candidates, cache=cache)
        second = worker.adjudicate_batch(**common, candidates=candidates, cache=cache)
        technical_variant = {**common, "timeout_seconds": 90, "api_key": "rotated-secret"}
        reordered = worker.adjudicate_batch(**technical_variant, candidates=list(reversed(candidates)), cache=cache)
    finally:
        cache.close()
    assert first == second == reordered
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["evidence", "model", "base_url"])
def test_adjudication_cache_misses_when_decision_inputs_change(tmp_path, monkeypatch, change):
    calls = []

    def completion(request):
        calls.append(request)
        user = json.loads(request.messages[1]["content"].split("\n", 1)[1])
        decisions = [{"candidate_id": row["candidate_id"], "decision": "accept",
                      "confidence": 0.95, "reason": "same evidence"} for row in user]
        return SimpleNamespace(finish_reason="stop", content=json.dumps({"decisions": decisions}))

    monkeypatch.setattr("tools.dictionary_llm.chat_completion", completion)
    cache = ArtifactCache(tmp_path / f"{change}.sqlite")
    candidate = _candidate("k8s|Kubernetes|it|stack|technology_stack")
    original = {"provider": "openrouter", "model": "xiaomi/mimo-v2.6-pro", "base_url": "https://llm.example",
                "api_key": None, "timeout_seconds": 30, "llm_max_tokens": 400}
    changed = dict(original)
    changed_candidates = [candidate]
    if change == "evidence":
        changed_candidates = [candidate | {"sample_phrases": ["new supporting phrase"]}]
    elif change == "model":
        changed["model"] = "xiaomi/mimo-v2.6-flash"
    else:
        changed["base_url"] = "https://other-llm.example"
    try:
        worker.adjudicate_batch(**original, candidates=[candidate], cache=cache)
        worker.adjudicate_batch(**changed, candidates=changed_candidates, cache=cache)
    finally:
        cache.close()
    assert len(calls) == 2


def test_changed_candidate_input_replay_resets_proposal_state():
    from app.normalization.synonyms_state import SynonymStateCandidate, build_synonym_state_document, upsert_synonym_state

    class UpsertCollection(MiniCollection):
        def upsert_synonym(self, document):
            existing = next((item for item in self.documents if item["alias_key"] == document["alias_key"]), None)
            if existing is None:
                self.documents.append(deepcopy(document))
            elif existing.get("candidate_hash") != document.get("candidate_hash"):
                existing.update(deepcopy(document))

        def update_one(self, query, update, upsert=False):
            if "$setOnInsert" in update:
                self.upsert_synonym(update["$setOnInsert"])
                return SimpleNamespace(matched_count=1, modified_count=0, upserted_id=None, acknowledged=True)
            return super().update_one(query, update)

    collection = UpsertCollection()
    first = build_synonym_state_document(SynonymStateCandidate(
        alias="k8s", canonical="Kubernetes", domain="it", category="stack", facet="technology_stack",
        skill_class="not_applicable", confidence=0.8, source="llm_cluster", run_id="run-1",
        source_refs_count=2, sample_phrases=("Kubernetes experience",),
    ))
    first["processing_status"] = "escalated_accepted"
    first["adjudication"]["pro"] = {"status": "done", "decision": "accept", "model": "xiaomi/mimo-v2.6-pro"}
    upsert_synonym_state(collection, first)

    changed = build_synonym_state_document(SynonymStateCandidate(
        alias="k8s", canonical="Kubernetes", domain="it", category="stack", facet="technology_stack",
        skill_class="not_applicable", confidence=0.8, source="llm_cluster", run_id="run-2",
        source_refs_count=3, sample_phrases=("Kubernetes experience", "running a cluster"),
    ))
    changed["processing_status"] = "needs_adjudication"
    upsert_synonym_state(collection, changed)
    assert collection.documents[0]["processing_status"] == "needs_adjudication"
    assert collection.documents[0]["adjudication"]["pro"]["status"] == "pending"
