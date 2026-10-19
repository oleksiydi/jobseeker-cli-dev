"""Prepare the single pinned public model/runtime; never read candidate data.

Downloads require network. Inference runs separately via app.evidence_llm_experiment.
Existing checked weights/runtime are reused; no package manager or Docker changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(session: requests.Session, url: str, path: Path, expected: str) -> None:
    if path.is_file() and checksum(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    for attempt in range(3):
        try:
            with session.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        stream.write(chunk)
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    if checksum(temporary) != expected:
        raise ValueError('Public artifact checksum mismatch')
    os.replace(temporary, path)
    print(json.dumps({'downloaded': path.name, 'sha256': expected}), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'conf/experiments/qwen35-2b.json')
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models/local-llm/qwen35-2b')
    parser.add_argument('--runtime-dir', type=Path, default=ROOT / 'models/local-llm/llama.cpp')
    parser.add_argument('--work-dir', type=Path, default=Path('/tmp/jobseeker-llama-build'))
    parser.add_argument('--build-jobs', type=int, default=2, choices=(1, 2))
    args = parser.parse_args(argv)
    spec = json.loads(args.config.read_text())
    if (spec['model_repository'] != 'bartowski/Qwen_Qwen3.5-2B-GGUF'
            or spec['model_file']['rfilename'] != 'Qwen_Qwen3.5-2B-Q4_K_M.gguf'):
        parser.error('This preparation command only supports the pinned 2B Q4_K_M artifact')
    args.model_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    model = spec['model_file']
    commit = spec['runtime_source_commit']
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        parser.error('Runtime commit must be a full Git SHA')
    with requests.Session() as session:
        session.trust_env = False
        session.mount('https://', HTTPAdapter(max_retries=Retry(
            total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
        download(session, f'https://huggingface.co/{spec["model_repository"]}/resolve/'
                 f'{spec["model_revision"]}/{model["rfilename"]}', args.model_dir / model['rfilename'],
                 model['lfs']['sha256'])
        archive = args.work_dir / 'source.tar.gz'
        download(session, f'https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{commit}',
                 archive, spec['runtime_source_archive_sha256'])
    runtime = args.runtime_dir / 'bin/llama-server'
    marker = args.runtime_dir / 'build-identity.json'
    reusable = False
    if marker.is_file() and runtime.is_file():
        old = json.loads(marker.read_text())
        reusable = (old.get('source_commit') == commit and old.get('build_recipe') == 'cpu-origin-v1'
                    and old.get('server_sha256') == checksum(runtime)
                    and all((runtime.parent / name).is_file()
                            and checksum(runtime.parent / name) == value
                            for name, value in old.get('libraries', {}).items()))
    if not reusable:
        with tarfile.open(archive) as source_archive:
            source_archive.extractall(args.work_dir, filter='data')
        source = args.work_dir / ('llama.cpp-' + commit)
        build = args.work_dir / 'build'
        subprocess.run(['cmake', '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
                        '-DGGML_CUDA=OFF', '-DGGML_VULKAN=OFF', '-DLLAMA_BUILD_TESTS=OFF',
                        '-DLLAMA_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_APP=OFF',
                        '-DLLAMA_BUILD_TOOLS=ON', '-DLLAMA_BUILD_SERVER=ON',
                        '-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON',
                        '-DLLAMA_BUILD_COMMIT=' + commit], check=True)
        subprocess.run(['cmake', '--build', str(build), '--target', 'llama-server',
                        '-j', str(args.build_jobs)], check=True)
        runtime.parent.mkdir(parents=True, exist_ok=True)
        for path in (build / 'bin').iterdir():
            if path.name == 'llama-server' or '.so' in path.name:
                shutil.copy2(path, runtime.parent / path.name)
        marker.write_text(json.dumps({'source_commit': commit, 'build_recipe': 'cpu-origin-v1',
                                    'server_sha256': checksum(runtime),
                                    'libraries': {p.name: checksum(p) for p in runtime.parent.glob('*.so*')
                                                  if p.is_file()}}, indent=2) + '\n')
    (args.model_dir / 'manifest.json').write_text(json.dumps(spec, indent=2) + '\n')
    print(json.dumps({'runtime': str(runtime), 'model_manifest': str(args.model_dir / 'manifest.json'),
                      'runtime_reused': reusable}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Public artifact preparation failed ({type(exc).__name__}); check paths/network/build tools')
        raise SystemExit(1) from None
