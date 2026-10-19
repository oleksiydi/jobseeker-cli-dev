"""Download and verify one pinned Hugging Face model artifact."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.prepare_qwen35_experiment import checksum, download

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DISK_RESERVE_BYTES = 256 * 1024 * 1024


def validate_spec(spec: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Validate the pinned artifact fields and return repository, revision, file."""
    repository = spec.get("model_repository")
    revision = spec.get("model_revision")
    model_file = spec.get("model_file")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("model_repository must be a Hugging Face owner/repository")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("model_revision must be a full 40-character Git SHA")
    if not isinstance(model_file, dict):
        raise ValueError("model_file must be an object")
    filename = model_file.get("rfilename")
    size = model_file.get("size")
    sha256 = (model_file.get("lfs") or {}).get("sha256")
    if (not isinstance(filename, str) or not filename.endswith(".gguf")
            or Path(filename).name != filename or "/" in filename or "\\" in filename
            or filename in {".", ".."}):
        raise ValueError("model_file.rfilename must be a .gguf basename")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("model_file.size must be a positive byte count")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError("model_file.lfs.sha256 must be a 64-character lowercase SHA256")
    return repository, revision, model_file


def model_slug(spec: dict[str, Any]) -> str:
    repository, _revision, _model_file = validate_spec(spec)
    return re.sub(r"[^a-z0-9]+", "-", repository.lower()).strip("-")


def prepare_model(config_path: Path, model_dir: Path | None = None) -> dict[str, Any]:
    """Fetch a pinned artifact, preserving any existing nonmatching weights."""
    spec = json.loads(config_path.read_text(encoding="utf-8"))
    repository, revision, model_file = validate_spec(spec)
    destination_dir = model_dir or ROOT / "models/local-llm" / model_slug(spec)
    destination = destination_dir / model_file["rfilename"]
    manifest_path = destination_dir / "manifest.json"
    destination_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest:
        raise FileExistsError(f"Existing model manifest differs: {manifest_path}")

    if destination.exists():
        if (not destination.is_file() or destination.stat().st_size != model_file["size"]
                or checksum(destination) != model_file["lfs"]["sha256"]):
            raise FileExistsError(f"Existing model artifact differs: {destination}")
        reused = True
    else:
        temporary = destination.with_suffix(destination.suffix + ".part")
        if temporary.exists():
            raise FileExistsError(f"Partial model artifact already exists: {temporary}; use another model directory")
        free_bytes = shutil.disk_usage(destination_dir).free
        required_bytes = model_file["size"] + _DISK_RESERVE_BYTES
        if free_bytes < required_bytes:
            raise OSError(f"Insufficient free disk space for model artifact and reserve: need {required_bytes} bytes")
        url = (f"https://huggingface.co/{repository}/resolve/{revision}/"
               f"{model_file['rfilename']}")
        with requests.Session() as session:
            session.trust_env = False
            session.mount("https://", HTTPAdapter(max_retries=Retry(
                total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
            download(session, url, destination, model_file["lfs"]["sha256"])
        if destination.stat().st_size != model_file["size"]:
            raise ValueError("Downloaded model artifact has an unexpected size")
        reused = False

    if not manifest_path.exists():
        manifest_path.write_text(manifest, encoding="utf-8")
    return {"model_path": str(destination), "manifest_path": str(manifest_path),
            "sha256": model_file["lfs"]["sha256"], "reused": reused}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Pinned model JSON spec")
    parser.add_argument("--model-dir", type=Path, help="Output directory (defaults under models/local-llm)")
    args = parser.parse_args(argv)
    report = prepare_model(args.config, args.model_dir)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
