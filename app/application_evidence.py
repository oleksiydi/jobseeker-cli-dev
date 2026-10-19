"""Inspect requirement → historical project facts locally, before writing letters.

Run ``python -m app.application_evidence --help``. Mongo supplies existing
reference terms and derived cache; existing vacancy/Qdrant pipelines are unchanged.
No generative model is called and installed embedding weights are offline-only.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path

from dotenv import load_dotenv

from app.core.config import load_app_config, project_root
from app.core.i18n import (
    resolve_language, set_language, reset_language, localized_prefix,
)
from app.core.logging import get_logger
from app.db.mongo import get_database
from app.matching.constraints import evaluate_constraints
from app.matching.episode_report import write_episode_reports
from app.matching.episode_retrieval import (
    RETRIEVAL_VERSION, EpisodeRetriever, extract_requirements, validate_requirements,
)
from app.matching.intelligence_data import DerivedCache, digest, load_file_jobs, load_reference_terms
from app.matching.project_episodes import project_projection, read_source, validate_provenance

LOGGER = get_logger('application_evidence')


def load_selected(path: Path, refs: list[str] | None) -> list[dict]:
    """Accept curated exact requirements or reuse the established file adapter."""
    if path.suffix == '.json' and path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('JSON over 64 MiB: provide a smaller selected snapshot')
    data = json.loads(path.read_text(encoding='utf-8-sig')) if path.suffix == '.json' else None
    if isinstance(data, dict) and isinstance(data.get('vacancies'), list) and all(
            isinstance(r, dict) and 'job' in r for r in data['vacancies']):
        selected = []
        for entry in data['vacancies']:
            job = entry['job']
            if refs and job['ref'] not in refs:
                continue
            requirements = validate_requirements(job, entry['requirements']) if 'requirements' in entry else None
            selected.append({'job': job, 'requirements': requirements,
                             'category': entry.get('category', 'selected'),
                             'source_snapshot': {'path': str(path), 'sha256': digest(data)},
                             'requirement_annotation': data.get('annotation_status', 'unverified_fixture')})
    else:
        jobs, scope = load_file_jobs(path, 2000)
        if scope['truncated']:
            raise ValueError('Snapshot exceeds 2000 unique jobs; provide a selected snapshot')
        selected = [{'job': j, 'requirements': None, 'category': 'selected',
                     'requirement_annotation': 'automatic_not_human_expert'}
                    for j in jobs if not refs or j['ref'] in refs]
    ids = [entry['job']['ref'] for entry in selected]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate selected vacancy references')
    if refs and set(refs) - set(ids):
        raise ValueError('Requested job reference is missing from snapshot')
    if not 1 <= len(selected) <= 10:
        raise ValueError('Select 1..10 vacancies; use --job-ref or a smaller snapshot')
    return selected


def main(argv: list[str] | None = None) -> int:
    root = project_root()
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-file', type=Path, required=True, help='Complete extended JSON; never overwritten')
    parser.add_argument('--supplement-profile', type=Path, help='Education, languages and constraints only')
    parser.add_argument('--usage-policy', type=Path, help='Source-hash-bound resolution of prose exclusions')
    parser.add_argument('--vacancies-file', type=Path, default=root / 'conf/application-evidence-demo.json')
    parser.add_argument('--job-ref', action='append', help='Repeat to select 1..10 references from a snapshot')
    parser.add_argument('--top-k', type=int, default=3, help='1..5 episodes per independent retrieval method')
    parser.add_argument('--embedding-threads', type=int, default=2, help='CPU threads, 1..8')
    parser.add_argument('--model-dir', type=Path, default=Path(os.getenv(
        'JOB_INTELLIGENCE_MODEL_DIR', str(root / 'models/embeddings'))))
    parser.add_argument('--no-embeddings', action='store_true', help='Explicit lexical baseline; no model needed')
    parser.add_argument('--output', type=Path, default=root / 'reports/private/application-evidence/report')
    parser.add_argument('--output-language', choices=('ru', 'de'), help='CLI > ENV > app JSON > ru')
    args = parser.parse_args(argv)
    if not 1 <= args.top_k <= 5 or not 1 <= args.embedding_threads <= 8:
        parser.error('--top-k must be 1..5 and --embedding-threads 1..8')
    started, db = time.perf_counter(), None
    language_token = None
    try:
        config = load_app_config()
        language = resolve_language(config, cli=args.output_language)
        language_token = set_language(language)
        outputs = {localized_prefix(args.output).with_suffix(s).resolve() for s in ('.json', '.md', '.episodes.json')}
        inputs = [args.profile_file, args.supplement_profile, args.usage_policy, args.vacancies_file]
        if any(path and path.resolve() in outputs for path in inputs):
            raise ValueError('Output must not overwrite an input file')
        source = read_source(args.profile_file)
        supplement = read_source(args.supplement_profile) if args.supplement_profile else None
        policy = json.loads(args.usage_policy.read_text()) if args.usage_policy else None
        projection = project_projection(source, supplement, policy)
        validate_provenance(projection, [s for s in (source, supplement) if s])
        if not projection['episodes']:
            raise ValueError('No permitted project episodes; resolve usage restrictions or check extended schema')
        vacancies = load_selected(args.vacancies_file, args.job_ref)
        db = get_database(config)
        terms, references = load_reference_terms(db)
        if not references:
            raise ValueError('Reference manifests missing; import ESCO/DKZ first')
        cache = DerivedCache(db)
        projection_key = digest(['project_projection', projection['input_hash']])
        if cache.get(projection_key) is None:
            cache.put(projection_key, projection, kind='project_projection')
        vectors = None
        if not args.no_embeddings:
            from app.matching.episode_vectors import EpisodeVectors
            vectors = EpisodeVectors(cache, args.model_dir, args.embedding_threads)
        retriever = EpisodeRetriever(projection['episodes'], terms, vectors)
        requirement_count = 0
        for vacancy in vacancies:
            requirements = vacancy.pop('requirements')
            if requirements is None:
                requirements = extract_requirements(vacancy['job'], retriever.matcher)
            if len(requirements) > 100:
                raise ValueError('Over 100 requirements in one vacancy; select smaller groups')
            vacancy['comparison'] = retriever.compare(requirements, args.top_k)
            vacancy['constraints'] = evaluate_constraints(vacancy['job'], projection)
            requirement_count += len(requirements)
        report = {'schema_version': 1, 'engine_version': RETRIEVAL_VERSION,
                  'output_language': language,
                  'projection': projection, 'vacancies': vacancies, 'references': references,
                  'model': vectors.identity if vectors else None,
                  'parameters': {'top_k': args.top_k, 'embedding_threads': args.embedding_threads},
                  'annotation_status': 'agent_curated_and_deterministic_rules_not_human_expert',
                  'metrics': {'wall_seconds': round(time.perf_counter() - started, 3),
                              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              'episodes': len(projection['episodes']), 'vacancies': len(vacancies),
                              'requirements': requirement_count, 'cache_hits': cache.hits, 'cache_misses': cache.misses,
                              **(vectors.counters if vectors else {'llm_calls': 0})}}
        report['input_hash'] = digest([RETRIEVAL_VERSION, projection['input_hash'], vacancies,
                                       references, report['model'], report['parameters']])
        paths = write_episode_reports(report, args.output)
        LOGGER.info('episodes=%s vacancies=%s requirements=%s wall=%.3fs llm_calls=0',
                    len(projection['episodes']), len(vacancies), requirement_count, report['metrics']['wall_seconds'])
        print(json.dumps({'reports': {k: str(v) for k, v in paths.items()}, 'metrics': report['metrics']}))
        return 0
    except Exception as exc:
        # Driver/model errors can contain credentials or private source values.
        LOGGER.error('Application evidence failed (%s); check inputs, usage policy and local services',
                     type(exc).__name__)
        return 1
    finally:
        reset_language(language_token)
        if db is not None:
            db.client.close()


if __name__ == '__main__':
    raise SystemExit(main())
