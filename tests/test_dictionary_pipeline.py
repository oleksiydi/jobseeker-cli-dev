from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from tools import run_dictionary_pipeline as mod


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values = {
        "provider": "openrouter",
        "model": "mistralai/mistral-nemo",
        "review_provider": "openrouter",
        "review_model": "xiaomi/mimo-v2.6-flash",
        "escalation_provider": "openrouter",
        "escalation_model": "xiaomi/mimo-v2.6-pro",
        "max_vacancies": 25,
        "work_dir": tmp_path / "work",
        "out": tmp_path / "synonyms.auto.json",
        "allow_learning": True,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("overrides", [{"dry_run": True}, {"allow_learning": False}])
def test_read_only_modes_do_not_spawn_stages_or_create_work_dir(monkeypatch, tmp_path, overrides):
    args = _args(tmp_path, **overrides)
    monkeypatch.setattr(mod, "parse_args", lambda: args)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("read-only mode must not start a subprocess")

    monkeypatch.setattr(mod.subprocess, "run", fail_if_called)

    mod.main()

    assert not args.work_dir.exists()


@pytest.mark.parametrize(
    ("provider", "model", "review_provider", "review_model"),
    [
        ("openrouter", "vendor/model:free", "openrouter", "xiaomi/mimo-v2.6-flash"),
        ("openrouter", "mistralai/mistral-nemo", "openrouter", "openrouter/free"),
    ],
)
def test_free_openrouter_model_is_rejected_before_stages(
    monkeypatch, tmp_path, provider, model, review_provider, review_model
):
    args = _args(
        tmp_path,
        provider=provider,
        model=model,
        review_provider=review_provider,
        review_model=review_model,
    )
    monkeypatch.setattr(mod, "parse_args", lambda: args)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("free endpoint must be rejected before subprocess launch")

    monkeypatch.setattr(mod.subprocess, "run", fail_if_called)

    with pytest.raises(ValueError, match="paid OpenRouter models"):
        mod.main()

    assert not args.work_dir.exists()


def test_build_commands_keep_stage_order_and_model_roles_separate(tmp_path):
    args = _args(tmp_path)

    commands = mod.build_commands(args)

    assert [command[2] for command in commands] == [
        "tools.build_taxonomy_from_vacancies",
        "tools.build_synonyms_from_taxonomy",
        "tools.classify_synonyms_candidates",
        "tools.adjudicate_synonyms_candidates",
        "tools.adjudicate_synonyms_candidates",
        "tools.publish_synonyms_auto",
    ]
    assert commands[0][commands[0].index("--provider") + 1] == "openrouter"
    assert commands[0][commands[0].index("--model") + 1] == "mistralai/mistral-nemo"
    assert commands[1][commands[1].index("--provider") + 1] == "openrouter"
    assert commands[1][commands[1].index("--model") + 1] == "mistralai/mistral-nemo"
    assert commands[3][commands[3].index("--provider") + 1] == "openrouter"
    assert commands[3][commands[3].index("--model") + 1] == "xiaomi/mimo-v2.6-flash"
    assert commands[3][commands[3].index("--stage") + 1] == "flash"
    assert commands[4][commands[4].index("--model") + 1] == "xiaomi/mimo-v2.6-pro"
    assert commands[4][commands[4].index("--stage") + 1] == "pro"
    assert commands[3][commands[3].index("--cache-path") + 1] == commands[4][commands[4].index("--cache-path") + 1]


def test_subprocess_failure_stops_pipeline_before_publication(monkeypatch, tmp_path):
    args = _args(tmp_path)
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    calls: list[list[str]] = []

    def fail_builder(command, **_kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr(mod.subprocess, "run", fail_builder)

    with pytest.raises(subprocess.CalledProcessError):
        mod.main()

    assert len(calls) == 1
    assert calls[0][2] == "tools.build_taxonomy_from_vacancies"
    assert calls[0][2] != "tools.publish_synonyms_auto"


def test_pro_failure_stops_before_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "parse_args", lambda: _args(tmp_path))
    calls = []

    def execute(command, **_kwargs):
        calls.append(command)
        if "--stage" in command and command[command.index("--stage") + 1] == "pro":
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr(mod.subprocess, "run", execute)
    with pytest.raises(subprocess.CalledProcessError):
        mod.main()
    assert len(calls) == 5
    assert all(command[2] != "tools.publish_synonyms_auto" for command in calls)


def test_free_pro_model_is_rejected_before_any_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "parse_args", lambda: _args(tmp_path, escalation_model="vendor/pro:free"))
    monkeypatch.setattr(mod.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not run"))
    with pytest.raises(ValueError, match="paid OpenRouter"):
        mod.main()
    assert not (tmp_path / "work").exists()
