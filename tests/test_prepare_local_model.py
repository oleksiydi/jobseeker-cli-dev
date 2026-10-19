import hashlib
import json
from types import SimpleNamespace

import pytest

from tools import prepare_local_model as prepare


def _spec(content=b"synthetic weights"):
    return {
        "model_repository": "org/test-model",
        "model_revision": "a" * 40,
        "model_file": {
            "rfilename": "model.gguf",
            "size": len(content),
            "lfs": {"sha256": hashlib.sha256(content).hexdigest()},
        },
    }


def _config(tmp_path, spec):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


@pytest.mark.parametrize("change", [
    lambda spec: spec.update(model_revision="abc"),
    lambda spec: spec.update(model_repository="https://example.com/model"),
    lambda spec: spec["model_file"].update(rfilename="../model.gguf"),
    lambda spec: spec["model_file"].update(size=0),
    lambda spec: spec["model_file"]["lfs"].update(sha256="0" * 63),
])
def test_prepare_model_rejects_invalid_pinned_spec(tmp_path, change):
    spec = _spec()
    change(spec)

    with pytest.raises(ValueError):
        prepare.prepare_model(_config(tmp_path, spec), tmp_path / "models")


def test_prepare_model_checks_free_space_before_download(tmp_path, monkeypatch):
    spec = _spec()
    monkeypatch.setattr(prepare.shutil, "disk_usage",
                        lambda _path: SimpleNamespace(free=spec["model_file"]["size"] - 1))
    monkeypatch.setattr(prepare, "download", lambda *_args: pytest.fail("download should not run"))

    with pytest.raises(OSError, match="Insufficient free disk space"):
        prepare.prepare_model(_config(tmp_path, spec), tmp_path / "models")


def test_prepare_model_reuses_valid_weights_and_refuses_to_overwrite(tmp_path, monkeypatch):
    content = b"synthetic weights"
    spec = _spec(content)
    output = tmp_path / "models"
    output.mkdir()
    weights = output / "model.gguf"
    weights.write_bytes(content)
    monkeypatch.setattr(prepare, "download", lambda *_args: pytest.fail("valid weights must be reused"))

    report = prepare.prepare_model(_config(tmp_path, spec), output)
    assert report["reused"] is True
    assert report["model_path"] == str(weights)
    assert json.loads((output / "manifest.json").read_text()) == spec

    weights.write_bytes(b"different existing artifact")
    with pytest.raises(FileExistsError, match="Existing model artifact differs"):
        prepare.prepare_model(_config(tmp_path, spec), output)
    assert weights.read_bytes() == b"different existing artifact"


def test_prepare_model_refuses_existing_partial_file(tmp_path, monkeypatch):
    spec = _spec()
    output = tmp_path / "models"
    output.mkdir()
    partial = output / "model.gguf.part"
    partial.write_bytes(b"partial bytes")
    monkeypatch.setattr(prepare, "download", lambda *_args: pytest.fail("partial must be preserved"))

    with pytest.raises(FileExistsError, match="Partial model artifact already exists"):
        prepare.prepare_model(_config(tmp_path, spec), output)
    assert partial.read_bytes() == b"partial bytes"
