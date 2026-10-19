"""Safety and cache contracts for the bounded local Qwen experiment."""

import hashlib
import json
from types import SimpleNamespace

import jsonschema
import pytest

from app.llm.client import LLMChatResponse
from app.llm.providers import llama_cpp
from app.matching.intelligence_data import digest
from app.matching.llm_experiment import (
    RejectedAnswer,
    request_for,
    run_case,
    validate_answer,
    validate_cases,
)
from app.matching.project_episodes import project_projection, read_source


def fact(value, pointer="/experience_projects/0/responsibilities/0", source=None):
    result = {
        "id": "fact-1", "source_sha256": source["sha256"] if source else "synthetic-hash",
        "pointer": pointer, "value": value,
    }
    if source:
        result["source"] = source["path"]
    return result


def classification_case(facts=None, **extra):
    return {
        "id": "control-case",
        "requirement": {"text": "Automate synthetic release checks", "label": "synthetic-label"},
        "context": "Synthetic vacancy context",
        "facts": facts if facts is not None else [fact("Automated synthetic release checks")],
        **extra,
    }


def editorial_case():
    return {
        "id": "editor-case", "task": "Draft one short synthetic sentence",
        "target": "Explain the relevant historical project action",
        "context": "Synthetic role context", "word_range": [8, 20],
        "must_preserve": ["historical scope"], "forbidden": ["invented qualification"],
        "editor_baseline": "BASELINE MUST NEVER ENTER THE PROMPT",
        "baseline": "CASE BASELINE MUST NEVER ENTER THE PROMPT",
        "facts": [fact("Automated synthetic release checks")],
        "control": {
            "label": "CONTROL LABEL MUST NEVER ENTER THE PROMPT",
            "rationale": "CONTROL RATIONALE MUST NEVER ENTER THE PROMPT",
        },
        "editor": {"baseline": "EDITOR BASELINE MUST NEVER ENTER THE PROMPT"},
    }


def test_prompts_exclude_control_labels_rationales_and_editor_baseline():
    control = classification_case(control={
        "label": "PRIVATE CONTROL LABEL TOKEN",
        "rationale": "PRIVATE CONTROL RATIONALE TOKEN",
    }, expected_relation="direct")
    pair_request, _ = request_for(control, "classification", port=18432,
                                  model="synthetic-qwen", timeout=73)
    editor_request, _ = request_for(editorial_case(), "editorial", port=18433,
                                   model="synthetic-qwen", timeout=73)
    all_prompts = json.dumps(pair_request.messages + editor_request.messages, ensure_ascii=False)

    for marker in (
        "PRIVATE CONTROL LABEL TOKEN", "PRIVATE CONTROL RATIONALE TOKEN",
        "EDITOR BASELINE MUST NEVER ENTER THE PROMPT",
        "CONTROL LABEL MUST NEVER ENTER THE PROMPT",
        "CONTROL RATIONALE MUST NEVER ENTER THE PROMPT",
    ):
        assert marker not in all_prompts
    assert pair_request.provider == "llama_cpp"
    assert pair_request.base_url == "http://127.0.0.1:18432"
    assert pair_request.timeout_seconds == 73
    assert pair_request.chat_template_kwargs == {"enable_thinking": False}
    assert editor_request.provider == "llama_cpp"
    assert editor_request.base_url == "http://127.0.0.1:18433"
    assert editor_request.chat_template_kwargs == {"enable_thinking": False}


def test_classification_schema_rejects_unknown_ids_and_relation_id_mismatch():
    _, schema = request_for(classification_case(), "classification", port=18432,
                            model="synthetic-qwen", timeout=73)

    with pytest.raises(jsonschema.ValidationError):
        validate_answer(json.dumps({
            "relation": "direct", "evidence_ids": ["unknown-fact"], "boundary": "Synthetic boundary"
        }), schema, "classification")
    with pytest.raises(ValueError, match="disagree"):
        validate_answer(json.dumps({
            "relation": "unknown", "evidence_ids": ["fact-1"], "boundary": "No evidence"
        }), schema, "classification")
    with pytest.raises(ValueError, match="disagree"):
        validate_answer(json.dumps({
            "relation": "direct", "evidence_ids": [], "boundary": "No evidence"
        }), schema, "classification")

    valid = validate_answer(json.dumps({
        "relation": "unknown", "evidence_ids": [], "boundary": "Synthetic evidence is insufficient"
    }), schema, "classification")
    assert valid["relation"] == "unknown"


def test_minimal_edit_includes_requested_draft_but_not_control_template():
    case = editorial_case()
    case['draft'] = 'Synthetic German draft with a grammar issue.'
    case['baseline'] = 'CONTROL TEMPLATE MUST STAY OUTSIDE THE PROMPT'
    request, _ = request_for(case, 'editorial', port=18432, model='synthetic-qwen', timeout=73)
    content = json.dumps(request.messages)
    assert case['draft'] in content
    assert case['baseline'] not in content
    assert 'minimally correct' in request.messages[0]['content']


def test_thinking_tags_and_nonstop_generation_are_rejected_and_cached(tmp_path, monkeypatch):
    case = classification_case()
    identity = {"alias": "synthetic-qwen"}
    calls = []

    response = response_for(case, content='{"relation":"unknown","evidence_ids":[],"boundary":"ok"}',
                            finish_reason="length")
    monkeypatch.setattr("app.matching.llm_experiment.chat_completion",
                        lambda _request: calls.append(True) or response)
    cache = tmp_path / "cache"
    with pytest.raises(RejectedAnswer, match="truncated") as rejected:
        run_case(case, "classification", 1, identity=identity, cache_dir=cache,
                 port=18432, timeout=73)
    assert rejected.value.record["validated"] is False
    rejected_files = list((cache / "rejected").glob("*.json"))
    assert len(rejected_files) == 1
    cached_rejection = run_case(case, "classification", 1, identity=identity, cache_dir=cache,
                                port=18432, timeout=73)
    assert cached_rejection["cache_hit"] is True
    assert cached_rejection["validated"] is False
    assert calls == [True]

    response = response_for(case, content='<think>private reasoning</think>{}', finish_reason="stop")
    monkeypatch.setattr("app.matching.llm_experiment.chat_completion", lambda _request: response)
    with pytest.raises(RejectedAnswer, match="Thinking tags"):
        run_case(case, "classification", 1, identity=identity, cache_dir=tmp_path / "think-cache",
                 port=18432, timeout=73)
    assert len(list((tmp_path / "think-cache" / "rejected").glob("*.json"))) == 1


def response_for(case, *, content, finish_reason="stop", reasoning=None):
    message = {"content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return LLMChatResponse(
        provider="llama_cpp", model="synthetic-qwen", response_id="synthetic-response",
        finish_reason=finish_reason, content=content, usage={}, timings={},
        started_at="synthetic-time", elapsed_ms=1.0,
        raw_response={"choices": [{"message": message, "finish_reason": finish_reason}]},
    )


def test_response_with_reasoning_content_is_rejected(tmp_path, monkeypatch):
    case = classification_case()
    response = response_for(
        case, content='{"relation":"unknown","evidence_ids":[],"boundary":"Synthetic"}',
        reasoning="synthetic hidden reasoning",
    )
    monkeypatch.setattr("app.matching.llm_experiment.chat_completion", lambda _request: response)

    with pytest.raises(ValueError, match="Thinking was not disabled"):
        run_case(case, "classification", 1, identity={"alias": "synthetic-qwen"},
                 cache_dir=tmp_path / "reasoning-cache", port=18432, timeout=73)


def test_cached_answer_avoids_second_call_and_identity_trial_or_fact_changes_miss(tmp_path, monkeypatch):
    calls = []
    content = json.dumps({
        "relation": "direct", "evidence_ids": ["fact-1"], "boundary": "Synthetic direct action"
    })
    monkeypatch.setattr(
        "app.matching.llm_experiment.chat_completion",
        lambda _request: calls.append(_request) or response_for(classification_case(), content=content),
    )
    cache = tmp_path / "cache"
    identity = {"alias": "synthetic-qwen", "runtime": "synthetic-runtime-a"}
    case = classification_case()

    first = run_case(case, "classification", 1, identity=identity, cache_dir=cache,
                     port=18432, timeout=73)
    again = run_case(case, "classification", 1, identity=identity, cache_dir=cache,
                     port=18432, timeout=73)
    new_trial = run_case(case, "classification", 2, identity=identity, cache_dir=cache,
                         port=18432, timeout=73)
    new_identity = run_case(case, "classification", 1,
                            identity={"alias": "synthetic-qwen", "runtime": "synthetic-runtime-b"},
                            cache_dir=cache, port=18432, timeout=73)
    changed_case = classification_case(facts=[fact("Validated synthetic release checks")])
    changed_fact = run_case(changed_case, "classification", 1, identity=identity,
                            cache_dir=cache, port=18432, timeout=73)

    assert len(calls) == 4
    assert first["cache_hit"] is False
    assert again["cache_hit"] is True
    assert new_trial["cache_hit"] is False
    assert new_identity["cache_hit"] is False
    assert changed_fact["cache_hit"] is False
    assert first["input_hash"] != new_trial["input_hash"]
    assert first["input_hash"] != new_identity["input_hash"]
    assert first["input_hash"] != changed_fact["input_hash"]


def source_projection(tmp_path):
    profile_path = tmp_path / "synthetic-profile.json"
    profile_path.write_text(json.dumps({"experience_projects": [
        {
            "name": "Synthetic permitted project",
            "responsibilities": ["Automated synthetic release checks"],
            "tech_stack": ["Python"],
            "deliverables": ["Reduced synthetic release errors"],
        },
        {
            "name": "Synthetic private project",
            "responsibilities": ["Private synthetic action"],
            "private": True,
        },
    ]}, ensure_ascii=False), encoding="utf-8")
    source = read_source(profile_path)
    projection = project_projection(source)
    return source, projection


def cases_for(source, projection, selected_fact):
    case = classification_case(facts=[selected_fact])
    return {
        "sources": [{"path": source["path"], "sha256": source["sha256"]}],
        "projection_hash": projection["input_hash"],
        "classification": [case], "editorial": [],
    }


def test_validate_cases_rejects_stale_projection_hash_and_private_source_facts(tmp_path):
    source, projection = source_projection(tmp_path)
    allowed_fact = projection["episodes"][0]["actions"][0]
    allowed_case = cases_for(source, projection, {**allowed_fact, "id": "fact-1"})
    validate_cases(allowed_case, projection)

    stale = {**allowed_case, "projection_hash": "stale-synthetic-projection-hash"}
    with pytest.raises(ValueError, match="different permitted projection"):
        validate_cases(stale, projection)

    private_fact = {
        **fact("Private synthetic action", "/experience_projects/1/responsibilities/0", source),
    }
    private_cases = cases_for(source, projection, private_fact)
    with pytest.raises(ValueError, match="outside the permitted projection"):
        validate_cases(private_cases, projection)


def test_validate_cases_rejects_fact_value_tampered_despite_valid_pointer(tmp_path):
    source, projection = source_projection(tmp_path)
    allowed = projection["episodes"][0]["actions"][0]
    changed_fact = {**allowed, "id": "fact-1", "value": "Tampered synthetic action"}
    cases = cases_for(source, projection, changed_fact)

    with pytest.raises(ValueError, match="Source quote differs"):
        validate_cases(cases, projection)


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1

    def json(self):
        return {"ok": True}


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.trust_env = True
        self.post_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs, self.trust_env))
        return self.response


def test_local_provider_disables_proxy_environment_and_refuses_redirects(monkeypatch):
    session = FakeSession(FakeResponse())
    monkeypatch.setattr(llama_cpp.requests, "Session", lambda: session)
    request = SimpleNamespace(
        base_url="http://127.0.0.1:18888", model="synthetic-qwen", temperature=0,
        max_tokens=32, messages=[{"role": "user", "content": "synthetic prompt"}],
        response_format={"type": "json_object"}, chat_template_kwargs={"enable_thinking": False},
        timeout_seconds=19,
    )

    assert llama_cpp.llama_cpp_chat_completion(request) == {"ok": True}
    url, kwargs, trust_env = session.post_calls[0]
    assert trust_env is False
    assert url == "http://127.0.0.1:18888/v1/chat/completions"
    assert kwargs["timeout"] == 19
    assert kwargs["allow_redirects"] is False
    assert kwargs["json"]["chat_template_kwargs"] == {"enable_thinking": False}

    redirect_session = FakeSession(FakeResponse(status_code=307))
    monkeypatch.setattr(llama_cpp.requests, "Session", lambda: redirect_session)
    with pytest.raises(ValueError, match="redirects"):
        llama_cpp.llama_cpp_chat_completion(request)


def synthetic_cli_inputs(tmp_path, *, collide_output_with_cases=False):
    profile_path = tmp_path / "synthetic-profile.json"
    profile_path.write_text(json.dumps({"experience_projects": [{
        "name": "Synthetic experiment project",
        "responsibilities": ["Automated synthetic deployment checks"],
    }]}), encoding="utf-8")
    source = read_source(profile_path)
    supplement_path = tmp_path / "synthetic-supplement.json"
    supplement_path.write_text("{}", encoding="utf-8")
    policy_path = tmp_path / "synthetic-policy.json"
    policy_path.write_text(json.dumps({
        "profile_sha256": source["sha256"],
        "resolution": "listed_projects_permitted",
        "basis": "user_statement",
        "statement": "Synthetic test authorization only",
    }), encoding="utf-8")
    projection = project_projection(source, read_source(supplement_path), json.loads(policy_path.read_text()))
    allowed_fact = {**projection["episodes"][0]["actions"][0], "id": "synthetic-fact"}
    cases_path = tmp_path / "synthetic-cases.json"
    cases_path.write_text(json.dumps({
        "annotation_status": "synthetic",
        "sources": [{"path": source["path"], "sha256": source["sha256"]}],
        "projection_hash": projection["input_hash"],
        "classification": [{
            "id": "synthetic-classification",
            "requirement": {"text": "Automate synthetic deployments"},
            "context": "Synthetic role context", "facts": [allowed_fact],
        }],
        "editorial": [{
            "id": "synthetic-editorial", "task": "Draft a synthetic sentence",
            "target": "Describe the historical deployment work",
            "context": "Synthetic role context", "word_range": [8, 20],
            "must_preserve": ["historical scope"], "forbidden": ["unverified claim"],
            "baseline": "Synthetic baseline excluded from the prompt",
            "facts": [allowed_fact],
        }],
    }), encoding="utf-8")
    manifest_path = tmp_path / "synthetic-manifest.json"
    weights_path = tmp_path / "synthetic-weights.gguf"
    weights_path.write_bytes(b"synthetic model bytes")
    manifest_path.write_text(json.dumps({
        "model_file": {"rfilename": weights_path.name,
                       "lfs": {"sha256": hashlib.sha256(weights_path.read_bytes()).hexdigest()}},
    }), encoding="utf-8")
    runtime_path = tmp_path / "synthetic-llama-server"
    runtime_path.write_bytes(b"synthetic runtime bytes")
    output = cases_path.with_suffix("") if collide_output_with_cases else tmp_path / "private" / "synthetic-run"
    args = [
        "--cases-file", str(cases_path), "--profile-file", str(profile_path),
        "--supplement-profile", str(supplement_path), "--usage-policy", str(policy_path),
        "--manifest", str(manifest_path), "--runtime", str(runtime_path),
        "--output", str(output),
    ]
    return args, output


class FakeOwnedServer:
    def __init__(self, *_args, **_kwargs):
        self.pid = 99999999
        self.returncode = None
        self.terminate_calls = 0
        self.wait_calls = []
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


def install_cli_runtime_stubs(monkeypatch, *, local_get=None):
    from app import evidence_llm_experiment as cli

    monkeypatch.setattr(cli.subprocess, "check_output", lambda *_args, **_kwargs: "synthetic-runtime-version")
    processes = []

    def spawn(*args, **kwargs):
        process = FakeOwnedServer(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(cli.subprocess, "Popen", spawn)

    class FakePortProbe:
        def setsockopt(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def bind(self, _address):
            return None

    monkeypatch.setattr(cli.socket, "socket", FakePortProbe)
    if local_get is not None:
        monkeypatch.setattr(cli, "local_get", local_get)
    return cli, processes


def test_cli_rejects_output_over_input_before_starting_any_server(tmp_path, monkeypatch):
    from app import evidence_llm_experiment as cli

    args, output = synthetic_cli_inputs(tmp_path, collide_output_with_cases=True)
    _, processes = install_cli_runtime_stubs(monkeypatch)
    result = cli.main(args)

    assert output.with_suffix(".json").is_file()
    assert result == 1
    assert processes == []


def test_unknown_case_selection_fails_before_server_launch(tmp_path, monkeypatch):
    from app import evidence_llm_experiment as cli

    args, _ = synthetic_cli_inputs(tmp_path)
    args.extend(["--case-id", "missing-synthetic-case"])
    _, processes = install_cli_runtime_stubs(monkeypatch)

    assert cli.main(args) == 1
    assert processes == []


def test_startup_failure_terminates_only_owned_process(tmp_path, monkeypatch):
    from app import evidence_llm_experiment as cli

    args, output = synthetic_cli_inputs(tmp_path)
    _, processes = install_cli_runtime_stubs(
        monkeypatch,
        local_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("synthetic startup failure")),
    )

    assert cli.main(args) == 1
    assert len(processes) == 1
    owned = processes[0]
    assert owned.terminate_calls == 1
    assert owned.wait_calls == [10]
    assert owned.kill_calls == 0
    assert output.with_suffix(".server.log").exists()


def test_report_keeps_custom_model_name_and_new_holdout_separate(tmp_path):
    from app.matching.llm_experiment_report import summarize, write_summary_markdown

    selected = {**fact('Historical synthetic task'), 'source': '/synthetic/profile.json'}
    case = classification_case(facts=[selected], split='new_holdout', control={
        'relation': 'transferable', 'scope': 'Different synthetic technology'})
    cases = {'classification': [case], 'editorial': []}
    result = {
        'identity': {'alias': 'another-local-model'},
        'rules': {case['id']: {'relation': 'unknown', 'wall_seconds': .01}},
        'records': [{'case_id': case['id'], 'task': 'classification', 'trial': 1,
                     'validated': True, 'answer': {'relation': 'transferable',
                     'evidence_ids': ['fact-1'], 'boundary': 'Andere Technologie'},
                     'wall_seconds': 2, 'reasoning_content_present': False,
                     'timings': {}, 'usage': {}}],
    }
    summary = summarize(result, cases)
    assert summary['new_holdout'] == {
        'count': 1, 'rules_agreement': 0, 'model_agreement': 1}
    output = tmp_path / 'model.md'
    write_summary_markdown(result, cases, output)
    assert 'another-local-model' in output.read_text()
    assert '# Qwen3.5-2B' not in output.read_text()
