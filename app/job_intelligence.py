"""Explainable local job matching using the existing MongoDB/Qdrant services.

    python -m app.job_intelligence --help

Collection is independent: this command never starts, stops, or modifies the
vacancy collectors. No profiles or vacancy texts are sent to external models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
from contextlib import nullcontext
from itertools import chain
from pathlib import Path

from dotenv import load_dotenv

from app.core.config import load_app_config, project_root
from app.core.i18n import resolve_language, set_language, reset_language
from app.core.logging import get_logger
from app.db.mongo import get_database, resolve_collection_name
from app.matching.intelligence_data import DerivedCache, all_job_batches, digest, load_file_jobs, load_mongo_jobs, load_reference_terms
from app.matching.profile import normalize_profile, profile_search_text
from app.matching.cover_letters import candidate_contact, application_context, load_application_details
from app.matching.intelligence_report import MAX_RESULTS
from app.matching.reference_relations import load_reference_graph, relation_settings

LOGGER = get_logger('job_intelligence')


def load_settings(path: Path) -> dict:
    settings = json.loads(path.read_text())
    settings.setdefault('processing_batch_size', 200)
    settings['reference_relations'] = relation_settings(
        settings, os.getenv('JOB_INTELLIGENCE_RELATIONS_ENABLED'))
    for name in ('candidate_limit', 'limit', 'embedding_batch_size', 'embedding_threads',
                 'skill_evidence_target', 'processing_batch_size'):
        if not isinstance(settings[name], int) or settings[name] <= 0:
            raise ValueError(f'{name} must be a positive integer')
    weights = settings['weights']
    if set(weights) != {'role', 'skills', 'semantic'} or any(v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError('Invalid ranking weights')
    if weights['role'] + weights['skills'] <= 0:
        raise ValueError('Non-embedding weights must be positive')
    for name in ('required_gap_penalty', 'max_required_gap_penalty', 'direct_role_threshold'):
        if not 0 <= settings[name] <= 1:
            raise ValueError(f'{name} must be in [0,1]')
    return settings


def _read_profile(path: Path, key: str) -> dict:
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(raw, list):
        selected = [p for p in raw if isinstance(p, dict) and p.get('key') == key]
        if len(selected) != 1:
            raise ValueError('--profile-key must select exactly one profile from the list')
        raw = selected[0]
    if not isinstance(raw, dict):
        raise ValueError('Profile must be a JSON object or a list with a matching key')
    return {**raw, 'key': raw.get('key') or key}


def _candidate(args: argparse.Namespace, db, config: dict) -> tuple[dict, str]:
    if args.profile_file:
        raw = _read_profile(args.profile_file, args.profile_key)
        source = str(args.profile_file)
    else:
        raw = db[resolve_collection_name(config, 'profiles', default='profiles')].find_one({'key': args.profile_key})
        if not raw:
            raise ValueError('Selected Mongo profile not found; use --profile-file')
        source = f'Mongo profiles[key={args.profile_key}]'
    result = normalize_profile(raw, source=source)
    result['application_contact'] = candidate_contact(raw)
    for fact in result['skills']:
        fact['source_path'] = source + '#' + fact['source_path']
    if args.supplement_profile:
        supplement_source = str(args.supplement_profile)
        supplement_raw = _read_profile(args.supplement_profile, args.profile_key)
        supplement = normalize_profile(supplement_raw, source=supplement_source)
        result['application_contact'] = {**candidate_contact(supplement_raw), **result['application_contact']}
        for fact in supplement['skills']:
            fact['source_path'] = supplement_source + '#' + fact['source_path']
        for field in ('roles', 'skills', 'education', 'projects'):
            for value in supplement[field]:
                if value not in result[field]:
                    result[field].append(value)
        for field in ('constraints', 'languages', 'preferences'):
            result[field] = {**supplement[field], **result[field]}
        if 'licenses' not in result and 'licenses' in supplement:
            result['licenses'] = supplement['licenses']
        result['profile_hash'] = digest([result['profile_hash'], supplement['profile_hash']])
        source += ' + ' + supplement_source
    if not result['skills'] and not result['roles'] and not result['target_roles']:
        raise ValueError('Profile has no supported professional fields')
    return result, source


def main(argv: list[str] | None = None, *, experimental_default: bool = False) -> int:
    root = project_root()
    load_dotenv(root / '.env')
    description = ('Experimental requirement-to-profile matching with a separate ranking and exact quotes. '
                   'Local MiniLM only; no Qdrant, LLM or automatic download. '
                   'File input works without MongoDB. Results require manual review.'
                   if experimental_default else __doc__)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--profile-file', type=Path, required=experimental_default,
                        help='Source profile JSON object' if experimental_default
                        else 'Full/legacy/short local JSON; otherwise Mongo profiles')
    parser.add_argument('--profile-key', default='default',
                        help=argparse.SUPPRESS if experimental_default else 'Mongo key or key in JSON profile list')
    parser.add_argument('--supplement-profile', type=Path, help='Explicit additional facts, e.g. legacy education/constraints')
    parser.add_argument('--vacancies-file', type=Path, help='Fixed JSON/JSONL/CSV import for local evaluation; does not overwrite collection')
    parser.add_argument('--query', default='',
                        help=argparse.SUPPRESS if experimental_default else 'Optional professional retrieval query')
    parser.add_argument('--candidate-limit', type=int,
                        help=argparse.SUPPRESS if experimental_default else 'Maximum bounded candidate pool')
    parser.add_argument('--all-vacancies', action='store_true', help='Scan the entire selected source in batches; ignore configured candidate_limit')
    parser.add_argument('--vacancy-scope', choices=('latest-run', 'active', 'all-stored'), default='latest-run',
                        help='Full scan source: completed list run (default), active/legacy records, or every stored record')
    parser.add_argument('--batch-size', type=int, help='Full scan processing batch, 1..1000 (config default: 200)')
    parser.add_argument('--limit', type=int, help=f'Number of vacancies in report/export, 1..{MAX_RESULTS}')
    parser.add_argument('--config', type=Path, default=root / 'conf/job-intelligence.json')
    parser.add_argument('--experimental-skill-matching', action=argparse.BooleanOptionalAction,
                        default=True if experimental_default else None,
                        help=argparse.SUPPRESS if experimental_default
                        else 'Independent experimental requirement/fact ranking; no Anschreiben')
    parser.add_argument('--usage-policy', type=Path,
                        help='Source-bound project usage resolution, experimental mode only')
    parser.add_argument('--output-language', choices=('ru', 'de'),
                        help='Output language; CLI > ENV > selected JSON > app JSON > ru')
    parser.add_argument('--model-dir', type=Path, default=Path(os.getenv('JOB_INTELLIGENCE_MODEL_DIR', str(root / 'models/embeddings'))))
    default_output = 'reports/private/skill-matching/report' if experimental_default else 'reports/private/job-intelligence/report'
    parser.add_argument('--output', type=Path, default=root / default_output,
                        help='Prefix for private .skills.json/.md/.csv' if experimental_default
                        else 'Prefix for private .md/.json/.csv')
    parser.add_argument('--export-documents', action='store_true',
                        help='Also write the experimental PDF/RTF report' if experimental_default
                        else f'Full source scan and top 50 PDF/RTF dossiers (limit 1..{MAX_RESULTS}); requires WeasyPrint/Pandoc')
    parser.add_argument('--export-json-md', action='store_true',
                        help='JSON/Markdown are always written in this mode' if experimental_default
                        else 'Full source scan and complete JSON/Markdown dossiers; no PDF converters required')
    parser.add_argument('--documents-dir', type=Path,
                        help=argparse.SUPPRESS if experimental_default
                        else 'Dossier root; overrides document_export.output_dir; default: documents beside --output reports')
    parser.add_argument('--application-file', type=Path,
                        help=argparse.SUPPRESS if experimental_default
                        else 'Private candidate/recipient contacts for German Anschreiben; overrides JOB_APPLICATION_FILE and config')
    parser.add_argument('--no-embeddings', action='store_true',
                        help=argparse.SUPPRESS if experimental_default else 'Lexical/evidence baseline; Qdrant/model not required')
    parser.add_argument('--no-llm', action='store_true', help='Explicit local mode; this release never calls a generative model')
    parser.add_argument('--prepare-model', action='store_true', help='Download the one public MiniLM model, then exit')
    args = parser.parse_args(argv)
    if experimental_default and not args.experimental_skill_matching:
        parser.error('Use app.job_intelligence for the ordinary matching mode')
    started = time.perf_counter()
    db = None
    language_token = None
    try:
        settings = load_settings(args.config)
        config = load_app_config()
        settings['output_language'] = resolve_language(settings, cli=args.output_language, fallback=config)
        language_token = set_language(settings['output_language'])
        from app.matching.vacancy_documents import export_settings
        documents = export_settings(
            settings, json_md_override='true' if args.export_json_md
            else os.getenv('JOB_INTELLIGENCE_EXPORT_JSON_MD'))
        settings['document_export'] = documents
        from app.matching.skill_matching import settings_for
        settings['experimental_skill_matching'] = settings_for(
            settings, enabled=args.experimental_skill_matching,
            env=os.getenv('JOB_INTELLIGENCE_SKILL_MATCHING'))
        experimental = settings['experimental_skill_matching']['enabled']
        if args.usage_policy and not experimental:
            parser.error('--usage-policy requires --experimental-skill-matching')
        if experimental:
            from app.skill_matching import validate_args
            validate_args(args)
            args.all_vacancies = True
        export_documents = args.export_documents or documents['enabled']
        export_json_md = documents['json_md_enabled']
        capture_documents = export_documents or export_json_md
        if args.documents_dir and not capture_documents:
            parser.error('--documents-dir requires document export')
        if args.application_file and (not capture_documents or not documents['cover_letter']['enabled']):
            parser.error('--application-file requires document export with cover_letter.enabled')
        if capture_documents:
            args.all_vacancies = True
            if args.limit is None:
                args.limit = documents['limit']
        if args.all_vacancies and args.candidate_limit is not None:
            parser.error('--all-vacancies cannot be combined with --candidate-limit')
        if not args.all_vacancies and (args.vacancy_scope != 'latest-run' or args.batch_size is not None):
            parser.error('--vacancy-scope and --batch-size require --all-vacancies')
        if args.vacancies_file and args.vacancy_scope != 'latest-run':
            parser.error('--vacancy-scope applies to MongoDB, not --vacancies-file')
        if args.batch_size is not None:
            settings['processing_batch_size'] = args.batch_size
        if not 1 <= settings['processing_batch_size'] <= 1000:
            parser.error('--batch-size / processing_batch_size must be in 1..1000')
        for name in ('limit', 'candidate_limit'):
            value = getattr(args, name)
            if value is not None:
                if value <= 0 or value > MAX_RESULTS:
                    parser.error(f'--{name.replace("_", "-")} must be in 1..{MAX_RESULTS}')
                settings[name] = value
        if args.prepare_model:
            from app.matching.intelligence_vectors import MODEL
            from app.vector.embedder import FastEmbedSettings, FastEmbedTextEmbedder
            FastEmbedTextEmbedder(FastEmbedSettings(MODEL, cache_dir=str(args.model_dir), threads=settings['embedding_threads']))
            LOGGER.info('Public MiniLM prepared; no profiles were read')
            return 0
        if experimental:
            from app.skill_matching import run
            paths, metrics = run(args, settings, config)
            print(json.dumps({'reports': {k: str(v) for k, v in paths.items()},
                              'metrics': metrics}, ensure_ascii=False))
            return 0
        renderer = None
        application_details = {'candidate': {}, 'recipients': {}}
        if export_documents:
            from app.matching.vacancy_documents import DocumentRenderer
            renderer = DocumentRenderer(documents['conversion_timeout_seconds'])
        if capture_documents:
            if documents['cover_letter']['enabled']:
                application_env = os.getenv('JOB_APPLICATION_FILE')
                application_path = args.application_file or Path(
                    application_env or documents['cover_letter']['details_file'])
                application_details = load_application_details(
                    application_path, required=bool(args.application_file or application_env))
        db = get_database(config)
        profile, source = _candidate(args, db, config)
        if args.all_vacancies:
            settings['candidate_limit'] = None
            batches, scope = all_job_batches(db, config, path=args.vacancies_file,
                                             vacancy_scope=args.vacancy_scope,
                                             batch_size=settings['processing_batch_size'],
                                             include_source=capture_documents)
            first = next(batches, [])
            if not first:
                raise ValueError('No candidate vacancies available in selected source')
            batches = chain([first], batches)
        elif args.vacancies_file:
            jobs, scope = load_file_jobs(args.vacancies_file, settings['candidate_limit'])
        else:
            jobs, scope = load_mongo_jobs(db, config, profile, settings['candidate_limit'])
        if not args.all_vacancies:
            if not jobs:
                raise ValueError('No candidate vacancies available in selected source')
            batches = iter([jobs])
        terms, references = load_reference_terms(db)
        if not references:
            raise ValueError('Reference manifests missing; run tools.import_reference_data --sources both first')
        from app.matching.intelligence import ENGINE_VERSION, IntelligenceMatcher, RankingAccumulator
        from app.matching.intelligence_report import all_vacancies_csv, write_reports
        cache = DerivedCache(db)
        graph = None
        if settings['reference_relations']['enabled']:
            from pymongo.errors import PyMongoError
            try:
                graph = load_reference_graph(db, terms, references)
            except PyMongoError as exc:
                from app.matching.reference_relations import ReferenceGraph
                LOGGER.warning('Reference graph unavailable (%s); ordinary matching continues', type(exc).__name__)
                graph = ReferenceGraph(status='unavailable')
        model, index = None, None
        if not args.no_embeddings:
            from app.matching.intelligence_vectors import SemanticIndex
            index = SemanticIndex(cache, config['matching'], settings, args.model_dir)
            model = index.identity
        matcher = IntelligenceMatcher(profile, terms, cache, settings, relations=graph)
        accumulated = RankingAccumulator(settings['limit'], retain_documents=capture_documents)
        input_hasher = hashlib.sha256()
        csv_context = all_vacancies_csv(args.output) if args.all_vacancies else nullcontext((None, None))
        with csv_context as (all_csv_path, write_batch):
            for batch in batches:
                semantic_scores = index.score(batch, profile, args.query) if index else None
                results = matcher.rank(batch, semantic_scores, args.query, model_identity=model)
                accumulated.add(results)
                for job in batch:
                    input_hasher.update(bytes.fromhex(job['source_input_hash']))
                if write_batch:
                    write_batch(results)
                    LOGGER.info('Full scan: evaluated=%s read=%s source_records=%s missing_details=%s',
                                accumulated.count, scope['read'], scope.get('source_records', '?'),
                                scope['missing_details'])
        if args.all_vacancies:
            scope.update(captured_input_hash=input_hasher.hexdigest(),
                         input_hash_method='sha256-concat-source-hashes-v1',
                         all_vacancies_csv=str(all_csv_path), scan_complete=True)
        else:
            scope['captured_input_hash'] = digest([j['source_input_hash'] for j in jobs])
        elapsed = time.perf_counter() - started
        report = {'schema_version': 1, 'engine_version': ENGINE_VERSION,
                  'profile': {'key': profile['key'], 'profile_hash': profile['profile_hash'], 'source': source,
                              'search_text_length': len(profile_search_text(profile))},
                  'scope': scope,
                  'references': references, 'model': model, 'configuration': settings,
                  'reference_relations': graph.identity if graph is not None else {'status': 'disabled'},
                  'llm': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0},
                  'results': accumulated.results, 'adjacent_roles': accumulated.adjacent(),
                  'profession_ranking': accumulated.professions(),
                  'metrics': {'wall_seconds': round(elapsed, 3),
                              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              'candidates_evaluated': accumulated.count, 'cache_hits': cache.hits, 'cache_misses': cache.misses,
                              **(index.counters if index else {}), **matcher.counters}}
        paths = write_reports(report, args.output)
        if capture_documents:
            from app.matching.vacancy_documents import write_documents
            documents_root = args.documents_dir
            if documents_root is None:
                configured_dir = documents['output_dir']
                documents_root = (Path(configured_dir) if configured_dir is not None
                                  else args.output.parent / 'documents')
            application = application_context(profile['application_contact'], application_details)
            if export_documents:
                exported = write_documents(report, documents_root, renderer=renderer, application=application)
                paths['documents'] = exported['directory']
                paths['documents_manifest'] = exported['manifest']
            if export_json_md:
                exported = write_documents(report, documents_root / 'json_md',
                                           application=application, text_only=True)
                paths['json_md_documents'] = exported['directory']
                paths['json_md_manifest'] = exported['manifest']
        if all_csv_path:
            paths['all_csv'] = all_csv_path
        LOGGER.info('profile=%s candidates=%s missing_details=%s elapsed=%.3fs llm_calls=0',
                    profile['key'], accumulated.count, scope['missing_details'], elapsed)
        print(json.dumps({'reports': {k: str(v) for k, v in paths.items()}, 'metrics': report['metrics']}, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Connection errors may embed credential-bearing URIs. Expose the class,
        # and only print our own argument validation messages verbatim.
        if isinstance(exc, (ValueError, FileNotFoundError, KeyError)):
            LOGGER.error('%s', exc)
        else:
            LOGGER.error('Job intelligence failed (%s); check local services/configuration', type(exc).__name__)
        return 1
    finally:
        reset_language(language_token)
        if db is not None:
            db.client.close()


if __name__ == '__main__':
    raise SystemExit(main())
