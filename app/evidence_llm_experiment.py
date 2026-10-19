"""Run a small offline-only local model experiment against rules and templates.

This command owns one CPU llama-server, binds it only to 127.0.0.1 and terminates
only that process on exit. No model downloads, remote inference or DB writes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import requests

from app.core.config import project_root
from app.core.logging import get_logger
from app.matching.episode_report import private_json
from app.matching.intelligence_data import digest
from app.matching.llm_experiment import ProcessMemory, RejectedAnswer, file_sha256, run_case, validate_cases
from app.matching.project_episodes import project_projection, read_source

LOGGER = get_logger('evidence_llm_experiment')


def rules_answer(case: dict) -> dict:
    started = time.perf_counter()
    from app.matching.episode_retrieval import EpisodeRetriever

    episode = {'id': case['episode_id'], 'actions': [], 'technologies': [], 'results': []}
    for fact in case['facts']:
        if '/responsibilities/' in fact['pointer']:
            episode['actions'].append(fact)
        elif '/tech_stack/' in fact['pointer']:
            episode['technologies'].append(fact)
        else:
            episode['results'].append({'fact': fact, 'scope': 'project', 'action_link': None})
    requirement = {'quote': case['requirement']['text'], 'modality': 'unknown'}
    rows = EpisodeRetriever([episode], []).compare([requirement], 1)[0]['evidence']
    return {'relation': rows[0]['relation'] if rows else 'unknown',
            'wall_seconds': round(time.perf_counter() - started, 6),
            'basis': 'existing task/exact-action rules on the same selected facts; no embeddings or reference lookup'}


def local_get(session: requests.Session, port: int, route: str) -> dict:
    response = session.get(f'http://127.0.0.1:{port}/{route}', timeout=(2, 5), allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError('Redirect from local server is forbidden')
    response.raise_for_status()
    return response.json()


def main(argv: list[str] | None = None) -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-file', type=Path, default=root / 'reports/private/qwen35-experiment/cases.json')
    parser.add_argument('--profile-file', type=Path, default=root / 'conf/profiles/default.profile.ext.json')
    parser.add_argument('--supplement-profile', type=Path, default=root / 'conf/profiles/default.profile.json')
    parser.add_argument('--usage-policy', type=Path, default=root / 'reports/private/application-evidence/usage-policy.json')
    parser.add_argument('--manifest', type=Path, default=root / 'models/local-llm/qwen35-2b/manifest.json')
    parser.add_argument('--runtime', type=Path, default=root / 'models/local-llm/llama.cpp/bin/llama-server')
    parser.add_argument('--output', type=Path, default=root / 'reports/private/qwen35-experiment/run')
    parser.add_argument('--threads', type=int, default=2, help='CPU threads, 1..4')
    parser.add_argument('--port', type=int, default=18081, help='Owned server bound only to 127.0.0.1')
    parser.add_argument('--timeout', type=int, default=180, help='Per-call timeout, 10..600 seconds')
    parser.add_argument('--case-id', action='append', help='Optional repeated case selection')
    parser.add_argument('--repeat-controls', action='store_true', help='Fresh second trials for P04/P09/E01')
    args = parser.parse_args(argv)
    if not 1 <= args.threads <= 4 or not 1024 <= args.port <= 65535 or not 10 <= args.timeout <= 600:
        parser.error('Invalid threads, port or timeout')
    server = None
    log_stream = None
    started = time.perf_counter()
    try:
        # Validate every source/permission before starting a model or reading cache.
        source, supplement = read_source(args.profile_file), read_source(args.supplement_profile)
        policy = json.loads(args.usage_policy.read_text())
        projection = project_projection(source, supplement, policy)
        cases = json.loads(args.cases_file.read_text())
        validate_cases(cases, projection)
        manifest = json.loads(args.manifest.read_text())
        weights = args.manifest.parent / manifest['model_file']['rfilename']
        outputs = {args.output.with_suffix(s).resolve() for s in
                   ('.json', '.md', '.server.log', '.props.json', '.partial.json')}
        inputs = [args.cases_file, args.profile_file, args.supplement_profile,
                  args.usage_policy, args.manifest, args.runtime, weights]
        if any(path.resolve() in outputs for path in inputs):
            raise ValueError('Output must not overwrite any input')
        if file_sha256(weights) != manifest['model_file']['lfs']['sha256']:
            raise ValueError('Model checksum mismatch')
        version = subprocess.check_output([str(args.runtime), '--version'], stderr=subprocess.STDOUT, text=True)
        identity = {'alias': manifest.get('model_alias', 'qwen35-2b-local-experiment'), 'manifest': manifest,
                    'runtime_sha256': file_sha256(args.runtime), 'runtime_version': version.strip(),
                    'runtime_libraries': {p.name: file_sha256(p) for p in sorted(args.runtime.parent.glob('*.so*'))
                                          if p.is_file()},
                    'threads': args.threads, 'context': 4096, 'batch': 256, 'ubatch': 128,
                    'gpu_layers': 0, 'parallel': 1, 'temperature': 0, 'seed': 42,
                    'cache_ram_mib': 0, 'offline': True, 'thinking': False}
        tasks = [(task, case) for task in ('classification', 'editorial') for case in cases[task]
                 if not args.case_id or case['id'] in args.case_id]
        if not tasks or (args.case_id and set(args.case_id) - {c['id'] for _, c in tasks}):
            raise ValueError('Unknown or empty case selection')
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', args.port))
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = args.output.with_suffix('.server.log')
        log_stream = log_path.open('w', encoding='utf-8')
        os.chmod(log_path, 0o600)
        command = [str(args.runtime), '-m', str(weights), '--alias', identity['alias'],
                   '--host', '127.0.0.1', '--port', str(args.port), '-ngl', '0',
                   '-t', str(args.threads), '-tb', str(args.threads), '-c', '4096',
                   '-b', '256', '-ub', '128', '-np', '1', '--seed', '42', '--temp', '0',
                   '--reasoning', 'off', '--jinja', '--no-webui', '--log-verbosity', '1',
                   '--offline', '--cache-ram', '0']
        server = subprocess.Popen(command, stdout=log_stream, stderr=subprocess.STDOUT)
        records = []
        rules = {case['id']: rules_answer(case) for task, case in tasks if task == 'classification'}
        with ProcessMemory(server.pid) as memory, requests.Session() as session:
            session.trust_env = False
            deadline = time.monotonic() + 120
            while True:
                if server.poll() is not None:
                    raise ValueError('Owned server exited; inspect private server log')
                try:
                    local_get(session, args.port, 'health')
                    break
                except requests.RequestException:
                    if time.monotonic() > deadline:
                        raise TimeoutError('Local server startup timeout')
                    time.sleep(.2)
            startup_seconds = round(time.perf_counter() - started, 3)
            props = local_get(session, args.port, 'props')
            private_json(args.output.with_suffix('.props.json'), props)
            for task, case in tasks:
                trials = (1, 2) if args.repeat_controls and case['id'] in {'P04', 'P09', 'E01'} else (1,)
                for trial in trials:
                    try:
                        record = run_case(case, task, trial, identity=identity,
                                          cache_dir=args.output.parent / 'cache', port=args.port, timeout=args.timeout)
                    except RejectedAnswer as exc:
                        record = exc.record
                    records.append(record)
                    LOGGER.info('case=%s trial=%s valid=%s cache_hit=%s elapsed=%.3fs',
                                case['id'], trial, record['validated'], record['cache_hit'], record['wall_seconds'])
                    # Retain completed work if a later case fails.
                    private_json(args.output.with_suffix('.partial.json'), {'identity': identity, 'records': records})
        result = {'schema_version': 1, 'annotation_status': cases['annotation_status'],
                  'case_file_sha256': file_sha256(args.cases_file), 'identity': identity,
                  'rules': rules, 'records': records,
                  'metrics': {'wall_seconds': round(time.perf_counter() - started, 3),
                              'startup_seconds': startup_seconds, **memory.peaks,
                              'llm_calls': sum(not r['cache_hit'] for r in records),
                              'rejected_answers': sum(not r['validated'] for r in records),
                              'cache_hits': sum(r['cache_hit'] for r in records)},
                  'limits': 'Agent controls/review, not human ground truth; process RSS is not physical 8/16 GB certification.'}
        result['input_hash'] = digest([result['case_file_sha256'], identity, args.case_id, args.repeat_controls])
        from app.matching.llm_experiment_report import summarize, write_summary_markdown

        result['comparison'] = summarize(result, cases)
        private_json(args.output.with_suffix('.json'), result)
        write_summary_markdown(result, cases, args.output.with_suffix('.md'))
        print(json.dumps({'report': str(args.output.with_suffix('.json')), 'metrics': result['metrics']}))
        return 0
    except Exception as exc:
        LOGGER.error('Local experiment failed (%s); inspect local inputs/server log', type(exc).__name__)
        return 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if log_stream:
            log_stream.close()


if __name__ == '__main__':
    raise SystemExit(main())
