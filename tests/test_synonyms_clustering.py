import json
from types import SimpleNamespace

import pytest
from jsonschema.exceptions import ValidationError

from tools import dictionary_llm, synonyms_cluster
from tools.llm_artifact_cache import ArtifactCache
from tools.synonyms_cluster import build_blocks, build_proposals, cluster_block
from app.llm.client import LLMResponseError


def _row(term, ref, domain="healthcare", **extra):
    return {
        "term": term,
        "referenznummer": ref,
        "domain_hint": domain,
        "category": "skills",
        "facet": "skill_requirement",
        "skill_class": "professional",
        "context_phrase": f"Experience with {term}",
        **extra,
    }


def test_build_blocks_counts_distinct_vacancies_and_keeps_scopes_separate():
    rows = [
        _row("Pflegeplanung", "H-1"),
        _row("Pflegeplanung", "H-1", context_phrase="duplicate extraction row"),
        _row("Pflegeplanung", "H-2"),
        _row("Pflegeplanung", "L-1", domain="logistics"),
        _row("Tourenplanung", "L-2", domain="logistics"),
        _row("Einzelbegriff", "H-3"),
        _row("Nur ein Begriff", "E-1", domain="education"),
    ]

    blocks = build_blocks(rows, block_size=10)

    assert len(blocks) == 2
    by_domain = {block["block"]["domain"]: block for block in blocks}
    assert set(by_domain) == {"healthcare", "logistics"}
    healthcare_terms = {term["term_key"]: term for term in by_domain["healthcare"]["terms"]}
    logistics_terms = {term["term_key"]: term for term in by_domain["logistics"]["terms"]}
    assert "einzelbegriff" in healthcare_terms
    assert healthcare_terms["pflegeplanung"]["source_refs_count"] == 2
    assert logistics_terms["pflegeplanung"]["source_refs_count"] == 1


def test_build_blocks_repeats_popular_anchor_and_respects_batch_size():
    rows = []
    for index in range(9):
        ref_count = 5 if index == 0 else 1
        rows.extend(_row(f"Term {index:02d}", f"R-{index}-{ref}") for ref in range(ref_count))

    blocks = build_blocks(rows, block_size=4)

    assert len(blocks) == 3
    term_batches = [[term["term_key"] for term in block["terms"]] for block in blocks]
    assert all(2 <= len(batch) <= 4 for batch in term_batches)
    assert all("term 00" in batch for batch in term_batches)
    assert {key for batch in term_batches for key in batch} == {
        f"term {index:02d}" for index in range(9)
    }


def _block(terms):
    return {
        "block": {
            "domain": "healthcare",
            "category": "skills",
            "facet": "skill_requirement",
            "skill_class": "professional",
        },
        "terms": terms,
    }


def _cluster_payload(block, *, canonical, alias, confidence=0.95):
    return {
        "clusters": [
            {
                "canonical_term_key": canonical,
                "aliases": [alias],
                "confidence": confidence,
                "reason": "Same meaning in supplied examples",
                **block["block"],
                "requires_review": False,
            }
        ]
    }


@pytest.mark.parametrize(
    ("canonical", "alias", "scope_change"),
    [("invented", "pflege", False), ("pflegeplanung", "pflege", True)],
)
def test_cluster_block_rejects_invented_terms_and_changed_scope(
    monkeypatch, tmp_path, canonical, alias, scope_change
):
    block = _block(
        [
            {"term_key": "pflegeplanung", "canonical": "Pflegeplanung", "source_refs_count": 2, "sample_phrases": []},
            {"term_key": "pflege", "canonical": "Pflege", "source_refs_count": 2, "sample_phrases": []},
        ]
    )
    payload = _cluster_payload(block, canonical=canonical, alias=alias)
    if scope_change:
        payload["clusters"][0]["domain"] = "logistics"
    monkeypatch.setattr(synonyms_cluster, "validated_completion", lambda **_kwargs: payload)

    with pytest.raises(ValueError, match="synonym scope|synonym terms"):
        cluster_block(block, provider="openai", model="test", cache=ArtifactCache(None), timeout_seconds=1)


@pytest.mark.parametrize(
    ("alias", "support", "confidence", "expected_review"),
    [
        ("planung", 2, 0.95, False),
        ("planung", 1, 0.95, True),
        ("planung", 2, 0.8, True),
        ("pf", 2, 0.95, True),
    ],
)
def test_cluster_block_marks_low_evidence_or_short_alias_for_review(
    monkeypatch, alias, support, confidence, expected_review
):
    canonical = "pflegeplanung"
    block = _block(
        [
            {"term_key": canonical, "canonical": "Pflegeplanung", "source_refs_count": 3, "sample_phrases": ["Pflege planen"]},
            {"term_key": alias, "canonical": alias.title(), "source_refs_count": support, "sample_phrases": ["Planung"]},
        ]
    )
    payload = _cluster_payload(block, canonical=canonical, alias=alias, confidence=confidence)
    monkeypatch.setattr(synonyms_cluster, "validated_completion", lambda **_kwargs: payload)

    proposals = cluster_block(block, provider="openai", model="test", cache=ArtifactCache(None), timeout_seconds=1)

    assert len(proposals) == 1
    assert proposals[0]["requires_review"] is expected_review


def test_build_proposals_marks_conflicting_canonicals_for_review(monkeypatch):
    rows = [_row(term, f"R-{term}") for term in ("alias", "canonical one", "canonical two", "other")]
    blocks = [
        _block([{"term_key": "alias"}, {"term_key": "canonical-one"}]),
        _block([{"term_key": "alias"}, {"term_key": "canonical-two"}]),
    ]
    monkeypatch.setattr(synonyms_cluster, "build_blocks", lambda *_args: blocks)

    def cluster(block, **_kwargs):
        canonical = block["terms"][1]["term_key"]
        return [{
            **block["block"],
            "alias": "alias",
            "canonical": canonical,
            "confidence": 0.95,
            "source_refs_count": 2,
            "sample_phrases": [],
            "requires_review": False,
            "reason": "test",
        }]

    monkeypatch.setattr(synonyms_cluster, "cluster_block", cluster)

    proposals = build_proposals(
        rows, provider="openai", model="test", cache=ArtifactCache(None), block_size=4, timeout_seconds=1
    )

    assert {item["canonical"] for item in proposals} == {"canonical-one", "canonical-two"}
    assert all(item["requires_review"] for item in proposals)


def _fallback_rows():
    return [
        _row("Pflegeplanung", "H-1"),
        _row("Pflegeplanung", "H-2"),
        _row("Pflege Plan", "H-3"),
        _row("Pflege Plan", "H-4"),
    ]


def _cluster_payload_from_request(kwargs, *, changed_scope=False):
    block = json.loads(kwargs["messages"][1]["content"])
    terms = block["terms"]
    payload = _cluster_payload(
        block,
        canonical=terms[0]["term_key"],
        alias=terms[1]["term_key"],
    )
    if changed_scope:
        payload["clusters"][0]["domain"] = "logistics"
    return payload


def test_build_proposals_uses_strong_fallback_and_records_its_provider(monkeypatch):
    original_cluster_block = synonyms_cluster.cluster_block
    cluster_calls = []
    completion_calls = []

    def record_cluster_call(block, **kwargs):
        cluster_calls.append({"provider": kwargs["provider"], "model": kwargs["model"],
                              "strong": kwargs.get("strong", False)})
        return original_cluster_block(block, **kwargs)

    def validated_completion(**kwargs):
        completion_calls.append(kwargs)
        # Weak model changes the scope. The configured strong model returns a
        # schema-valid cluster in the exact supplied scope.
        return _cluster_payload_from_request(
            kwargs,
            changed_scope=kwargs["model"] == "weak-model",
        )

    monkeypatch.setattr(synonyms_cluster, "cluster_block", record_cluster_call)
    monkeypatch.setattr(synonyms_cluster, "validated_completion", validated_completion)

    proposals = build_proposals(
        _fallback_rows(),
        provider="openai",
        model="weak-model",
        review_provider="openrouter",
        review_model="strong-model",
        cache=ArtifactCache(None),
        block_size=10,
        timeout_seconds=1,
    )

    assert len(completion_calls) == 2
    assert cluster_calls == [
        {"provider": "openai", "model": "weak-model", "strong": False},
        {"provider": "openrouter", "model": "strong-model", "strong": True},
    ]
    assert len(proposals) == 1
    assert proposals[0]["reviewed_by_strong"] is True
    assert proposals[0]["provider"] == "openrouter"
    assert proposals[0]["model"] == "strong-model"


def test_build_proposals_propagates_when_all_three_models_fail(monkeypatch):
    calls = []

    def validated_completion(**kwargs):
        calls.append(kwargs["model"])
        return _cluster_payload_from_request(kwargs, changed_scope=True)

    monkeypatch.setattr(synonyms_cluster, "validated_completion", validated_completion)

    with pytest.raises(ValueError, match="synonym scope"):
        build_proposals(
            _fallback_rows(),
            provider="openai",
            model="weak-model",
            review_provider="openrouter",
            review_model="strong-model",
            cache=ArtifactCache(None),
            block_size=10,
            timeout_seconds=1,
        )

    assert calls == ["weak-model", "strong-model", "xiaomi/mimo-v2.6-pro"]


def test_invalid_flash_clustering_uses_pro_and_preserves_flash_failure(monkeypatch):
    calls = []

    def complete(**kwargs):
        calls.append(kwargs["model"])
        return _cluster_payload_from_request(kwargs, changed_scope=kwargs["model"] != "pro-model")

    monkeypatch.setattr(synonyms_cluster, "validated_completion", complete)
    proposals = build_proposals(_fallback_rows(), provider="openrouter", model="nemo-model",
        review_model="flash-model", escalation_model="pro-model",
        cache=ArtifactCache(None), block_size=10, timeout_seconds=1)

    assert calls == ["nemo-model", "flash-model", "pro-model"]
    assert proposals[0]["review_stage"] == "pro"
    assert proposals[0]["model"] == "pro-model"
    assert proposals[0]["flash_failure"]["model"] == "flash-model"
    assert proposals[0]["flash_failure"]["status"] == "invalid"


def test_clustering_does_not_escalate_provider_api_errors(monkeypatch):
    calls = []

    def complete(**kwargs):
        calls.append(kwargs["model"])
        raise ValueError("OpenRouter upstream error code 402")

    monkeypatch.setattr(synonyms_cluster, "validated_completion", complete)
    with pytest.raises(ValueError, match="402"):
        build_proposals(_fallback_rows(), provider="openrouter", model="nemo-model",
            cache=ArtifactCache(None), block_size=10, timeout_seconds=1)
    assert calls == ["nemo-model"]


def test_repeated_anchor_keeps_pro_decision_instead_of_later_weak_proposal(monkeypatch):
    blocks = [_block([{"term_key": "alias"}, {"term_key": "canonical"}])] * 2
    monkeypatch.setattr(synonyms_cluster, "build_blocks", lambda *_args: blocks)
    calls = []

    def cluster(block, **kwargs):
        calls.append(kwargs["model"])
        if len(calls) <= 2:
            raise LLMResponseError("invalid response")
        pro = kwargs["model"] == "pro-model"
        return [{**block["block"], "alias":"alias", "canonical":"Canonical",
                 "confidence":0.95 if pro else 0.65, "source_refs_count":2,
                 "sample_phrases":["Alias or Canonical"], "requires_review":not pro,
                 "reviewed_by_strong":pro, "provider":kwargs["provider"],
                 "model":kwargs["model"], "reason":"same meaning"}]

    monkeypatch.setattr(synonyms_cluster, "cluster_block", cluster)
    proposals = build_proposals(_fallback_rows(), provider="openrouter", model="nemo-model",
        review_model="flash-model", escalation_model="pro-model",
        cache=ArtifactCache(None), block_size=10, timeout_seconds=1)
    assert calls == ["nemo-model", "flash-model", "pro-model", "nemo-model"]
    assert len(proposals) == 1
    assert proposals[0]["model"] == "pro-model"
    assert proposals[0]["review_stage"] == "pro"
    assert proposals[0]["requires_review"] is False


def _minimal_schema():
    return {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }


def _llm_response(content, finish_reason="stop"):
    return SimpleNamespace(content=content, finish_reason=finish_reason)


def test_validated_completion_caches_identical_validated_request(tmp_path, monkeypatch):
    calls = []

    def complete(request):
        calls.append(request)
        return _llm_response('{"answer":"ok"}')

    monkeypatch.setattr(dictionary_llm, "chat_completion", complete)
    cache = ArtifactCache(tmp_path / "cache.sqlite")
    kwargs = {
        "provider": "openai",
        "model": "test-model",
        "messages": [{"role": "user", "content": "same input"}],
        "schema": _minimal_schema(),
        "cache": cache,
    }

    assert dictionary_llm.validated_completion(**kwargs) == {"answer": "ok"}
    assert dictionary_llm.validated_completion(**kwargs) == {"answer": "ok"}
    cache.close()
    assert len(calls) == 1


def test_validated_completion_strips_provider_unsupported_keyword_but_locally_validates(
    tmp_path, monkeypatch
):
    schema = {
        "type": "object",
        "required": ["aliases"],
        "properties": {
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            }
        },
        "additionalProperties": False,
    }
    original_schema = json.loads(json.dumps(schema))
    calls = []

    def complete(request):
        calls.append(request)
        return _llm_response(json.dumps({"aliases": ["pflege", "pflege"]}))

    monkeypatch.setattr(dictionary_llm, "chat_completion", complete)
    cache = ArtifactCache(tmp_path / "duplicate-aliases.sqlite")
    kwargs = {
        "provider": "openrouter",
        "model": "mistralai/mistral-nemo",
        "messages": [{"role": "user", "content": "return unique aliases"}],
        "schema": schema,
        "cache": cache,
    }

    with pytest.raises(ValidationError):
        dictionary_llm.validated_completion(**kwargs)
    with pytest.raises(ValidationError):
        dictionary_llm.validated_completion(**kwargs)
    cache.close()

    assert len(calls) == 2
    provider_schema = calls[0].response_format["schema"]
    assert "uniqueItems" not in provider_schema["properties"]["aliases"]
    assert schema == original_schema


@pytest.mark.parametrize(
    ("content", "finish_reason", "error_type"),
    [
        ("not json", "stop", ValueError),
        (json.dumps({"wrong": "shape"}), "stop", ValidationError),
        (json.dumps({"answer": "partial"}), "length", ValueError),
    ],
)
def test_validated_completion_does_not_cache_invalid_or_incomplete_responses(
    tmp_path, monkeypatch, content, finish_reason, error_type
):
    calls = []
    monkeypatch.setattr(
        dictionary_llm,
        "chat_completion",
        lambda request: (calls.append(request) or _llm_response(content, finish_reason)),
    )
    cache = ArtifactCache(tmp_path / "cache.sqlite")
    kwargs = {
        "provider": "openai",
        "model": "test-model",
        "messages": [{"role": "user", "content": "bad input"}],
        "schema": _minimal_schema(),
        "cache": cache,
    }

    with pytest.raises(error_type):
        dictionary_llm.validated_completion(**kwargs)
    with pytest.raises(error_type):
        dictionary_llm.validated_completion(**kwargs)
    cache.close()

    assert len(calls) == 2


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openrouter", "vendor/model:free"),
        ("openrouter", "openrouter/free"),
        ("openrouter_api", "vendor/model:free"),
    ],
)
def test_validated_completion_rejects_free_openrouter_models(provider, model):
    with pytest.raises(ValueError, match="paid OpenRouter"):
        dictionary_llm.validated_completion(
            provider=provider,
            model=model,
            messages=[],
            schema=_minimal_schema(),
            cache=ArtifactCache(None),
        )
