"""Experimental local requirement matching, with an independent private ranking.

File mode needs only installed MiniLM weights; Mongo mode reuses the existing
vacancy adapters and derived cache. No Qdrant, LLM or automatic download.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import time

from app.core.i18n import localized_prefix
from app.core.logging import get_logger
from app.matching.constraints import evaluate_constraints
from app.matching.intelligence_data import DerivedCache, all_job_batches, digest, load_reference_terms
from app.matching.project_episodes import read_source
from app.matching.skill_facts import build_facts
from app.matching.skill_matching import SkillMatcher, SkillRanking, VERSION
from app.matching.skill_report import RankingCsv, write_skill_reports

LOGGER = get_logger('skill_matching')


class MemoryCache:
    """File runs retain bounded derived vectors in memory, without a database."""

    def __init__(self, limit=4096):
        from collections import OrderedDict
        self.values = OrderedDict()
        self.limit, self.hits, self.misses = limit, 0, 0

    def get(self, key):
        if key not in self.values:
            self.misses += 1
            return None
        self.hits += 1
        self.values.move_to_end(key)
        return self.values[key]

    def put(self, key, value, kind=None):
        self.values[key] = value
        self.values.move_to_end(key)
        while len(self.values) > self.limit:
            self.values.popitem(last=False)


def make_vectors(cache, model_dir, threads, facts):
    from app.matching.episode_vectors import EpisodeVectors, PreparedFactVectors
    base = EpisodeVectors(cache, model_dir, threads)
    return PreparedFactVectors(base, [f['source_fact']['value'] for f in facts]), base


def validate_args(args):
    if not args.profile_file:
        raise ValueError('Experimental skill matching requires --profile-file with exact source facts')
    if args.no_embeddings:
        raise ValueError('Experimental skill matching requires embeddings; remove --no-embeddings')
    if args.application_file:
        raise ValueError('Experimental skill matching does not create Anschreiben; omit --application-file')
    if args.documents_dir:
        raise ValueError('Experimental reports use --output; omit --documents-dir')
    if args.query:
        raise ValueError('Experimental matching uses vacancy requirements; omit --query')
    if args.candidate_limit is not None:
        raise ValueError('Experimental matching scans its selected source; omit --candidate-limit')


def run(args, settings: dict, config: dict) -> tuple[dict, dict]:
    """Scan each selected vacancy once, stream all ranks, retain a bounded top."""
    validate_args(args)
    started, db = time.perf_counter(), None
    prefix = localized_prefix(args.output)
    # Every output, including converter products, is disjoint from main reports.
    suffixes = ('.skills.json', '.skills.md', '.skills.csv', '.skills.all.csv', '.skills.pdf', '.skills.rtf')
    outputs = {(prefix.parent / (prefix.name + suffix)).resolve() for suffix in suffixes}
    inputs = [args.profile_file, args.supplement_profile, args.vacancies_file, args.usage_policy, args.config]
    if any(path and path.resolve() in outputs for path in inputs):
        raise ValueError('Experimental output must not overwrite an input file')
    source = read_source(args.profile_file)
    supplement = read_source(args.supplement_profile) if args.supplement_profile else None
    policy = json.loads(args.usage_policy.read_text()) if args.usage_policy else None
    corpus = build_facts(source, supplement, policy)
    options = settings['experimental_skill_matching']
    # Validate corpus limits before constructing any model or database client.
    SkillMatcher(corpus, options, None)
    renderer = None
    if args.export_documents or settings['document_export']['enabled']:
        from app.matching.vacancy_documents import DocumentRenderer
        renderer = DocumentRenderer(settings['document_export']['conversion_timeout_seconds'])
    temporary = None
    try:
        terms, references = [], []
        if args.vacancies_file:
            cache = MemoryCache()
        else:
            from app.db.mongo import get_database
            db = get_database(config)
            cache = DerivedCache(db)
            terms, references = load_reference_terms(db)
        vectors, base = make_vectors(cache, args.model_dir, settings['embedding_threads'], corpus['facts'])
        matcher = SkillMatcher(corpus, options, vectors, terms)
        batches, scope = all_job_batches(db, config, path=args.vacancies_file,
                                        vacancy_scope=args.vacancy_scope,
                                        batch_size=settings['processing_batch_size'])
        ranking = SkillRanking(settings['limit'])
        prefix.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=prefix.parent, prefix='.skill-ranking-')
        hasher = hashlib.sha256()
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(RankingCsv.columns)
            for batch in batches:
                for job in batch:
                    result = matcher.compare(job)
                    result['constraints'] = evaluate_constraints(job, corpus)
                    ranking.add(result)
                    writer.writerow(RankingCsv.row(result))
                    hasher.update(bytes.fromhex(result['input_hash']))
                LOGGER.info('Experimental matching: vacancies=%s requirements=%s no_candidate=%s llm_calls=0',
                            ranking.count, ranking.requirements, ranking.no_candidate)
            stream.flush()
            os.fsync(stream.fileno())
        if not ranking.count:
            raise ValueError('No candidate vacancies available in selected source')
        scope.update(scan_complete=True, captured_input_hash=hasher.hexdigest())
        metrics = {'vacancies': ranking.count, 'requirements': ranking.requirements,
                   'requirements_without_candidate': ranking.no_candidate, 'profile_facts': len(corpus['facts']),
                   'withheld_projects': len(corpus['withheld_projects']), 'excluded_facts': corpus['excluded_facts'],
                   'wall_seconds': round(time.perf_counter() - started, 3),
                   **base.counters, 'cache_hits': cache.hits, 'cache_misses': cache.misses, 'llm_calls': 0}
        report = {'schema_version': 1, 'engine_version': VERSION,
                  'output_language': settings['output_language'],
                  'assessment_origin': 'experimental_retrieval_not_verified', 'requires_review': True,
                  'parameters': options, 'model': vectors.identity, 'scope': scope,
                  'profile': {k: corpus[k] for k in ('input_hash', 'sources', 'withheld_projects')},
                  'references': references, 'results': ranking.results, 'metrics': metrics}
        report['input_hash'] = digest([VERSION, corpus['input_hash'], options, vectors.identity,
                                       references, scope['captured_input_hash']])
        paths = write_skill_reports(report, args.output, renderer=renderer)
        all_path = prefix.parent / (prefix.name + '.skills.all.csv')
        os.replace(temporary, all_path)
        temporary = None
        paths['skills_all_csv'] = all_path
        LOGGER.info('Experimental matching finished: vacancies=%s facts=%s wall=%.3fs llm_calls=0',
                    ranking.count, len(corpus['facts']), metrics['wall_seconds'])
        return paths, metrics
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        if db is not None:
            db.client.close()


def main(argv=None):
    """Offer the same experimental mode as the primary CLI, with isolated outputs."""
    import sys
    from app.job_intelligence import main as intelligence_main
    args = list(sys.argv[1:] if argv is None else argv)
    return intelligence_main(args, experimental_default=True)


if __name__ == '__main__':
    raise SystemExit(main())
