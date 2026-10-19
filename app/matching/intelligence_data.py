"""Bounded read adapters and derived caches for the existing Mongo/Qdrant stack."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from itertools import islice
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.matching.models import MergedVacancyNormalized, MergedVacancyRaw, normalize_text
from app.normalization.reference_data import ReferenceStore


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def text_key(value: str) -> str:
    # Keep language/package distinctions (C++ != C#, Node.js != Node js).
    return ' '.join(re.findall(r'[^\W_]+(?:\.[^\W_]+)*(?:\+\+|#)?', value.casefold()))


def _plain(value: str) -> str:
    value = re.sub(r'<(?:br\s*/?|/p|/div|/li)\s*>', '\n', value, flags=re.I)
    return '\n'.join(line.strip() for line in html.unescape(re.sub(r'<[^>]+>', ' ', value)).splitlines()).strip()


def normalize_job(row: dict, *, include_source: bool = False) -> dict:
    """Use the established BA merge contract; retain text for evidence offsets."""
    if 'stellenangebote' in row or 'jobdetails' in row:
        meta, details = row.get('stellenangebote') or {}, row.get('jobdetails') or {}
        merged = MergedVacancyNormalized.from_raw(MergedVacancyRaw.from_sources(meta, details))
        job = dict(ref=merged.referenznummer, title=merged.title, role=merged.role,
                   location=merged.location_summary, description=_plain(merged.description),
                   schedule=merged.employment_summary, employer=str(meta.get('arbeitgeber') or ''),
                   url=str(details.get('externeUrl') or meta.get('externeUrl') or ''),
                   source_modified_at=meta.get('modifikationsTimestamp'),
                   details_modified_at=details.get('aenderungsdatum'))
        job['source_input_hash'] = digest([meta.get('meta_hash') or meta, details.get('details_hash') or details])
    else:
        job = {k: str(row.get(k) or '') for k in ('title', 'role', 'location', 'description', 'schedule', 'employer', 'url')}
        job['ref'] = str(row.get('ref') or row.get('id') or row.get('referenznummer') or '')
        job['title'] = job['title'] or str(row.get('titel') or '')
        job['role'] = job['role'] or str(row.get('beruf') or '')
        if not job['location'] and isinstance(row.get('arbeitsort'), dict):
            job['location'] = str(row['arbeitsort'].get('ort') or '')
        job['description'] = _plain(job['description'])
        job['source_input_hash'] = digest(row)
    for field in ('title', 'role', 'location', 'schedule', 'employer'):
        job[field] = normalize_text(job[field])
    if not job['title']:
        if not job['role']:
            raise ValueError('Vacancy title and occupation are missing')
        job['title'] = job['role']
        job['title_source'] = 'occupation_fallback'
    job['ref'] = job['ref'] or digest(job)[:24]
    if not job['url'] and re.fullmatch(r'\d+-[\w-]+', job['ref']):
        job['url'] = 'https://www.arbeitsagentur.de/jobsuche/jobdetail/' + job['ref']
    if not job['url'].startswith(('http://', 'https://')):
        job['url'] = ''
    job['details_status'] = 'available' if job['description'] else 'missing'
    if include_source:
        # Экспорт использует ровно прочитанные данные, даже при параллельном сборе.
        job['source_data'] = row
    return job


def _file_rows(path: Path) -> Iterator[dict]:
    with path.open(encoding='utf-8-sig', newline='') as stream:
        if path.suffix.lower() == '.csv':
            yield from csv.DictReader(stream)
        elif path.suffix.lower() in {'.jsonl', '.ndjson'}:
            for line in stream:
                if line.strip():
                    yield json.loads(line)
        else:
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('JSON over 64 MiB: use streaming JSONL/CSV')
            data = json.load(stream)
            rows = data if isinstance(data, list) else data.get('vacancies', data.get('ergebnisliste'))
            if not isinstance(rows, list):
                raise ValueError('Expected a vacancy JSON list, vacancies, or ergebnisliste')
            yield from rows


def _scan_counts() -> dict:
    return {'read': 0, 'duplicates': 0, 'invalid_records': 0,
            'missing_details': 0, 'truncated': False, 'unique': 0}


def iter_unique_jobs(rows: Iterator[dict], counts: dict, *, include_source: bool = False) -> Iterator[dict]:
    """Stream deduplicated jobs; retain IDs/hashes, not all vacancy texts."""
    refs, hashes = set(), set()
    for row in rows:
        counts['read'] += 1
        if not isinstance(row, dict):
            raise ValueError('Each vacancy record must be a JSON object')
        try:
            job = normalize_job(row, include_source=include_source)
        except (ValueError, TypeError, AttributeError):
            # Старые внешние документы могут содержать поля неожиданных типов.
            counts['invalid_records'] += 1
            continue
        content = digest([text_key(job[k]) for k in ('title', 'role', 'location', 'employer', 'description')])
        if job['ref'] in refs or content in hashes:
            counts['duplicates'] += 1
            continue
        refs.add(job['ref']); hashes.add(content)
        counts['missing_details'] += job['details_status'] == 'missing'
        counts['unique'] += 1
        yield job


def unique_jobs(rows: Iterator[dict], limit: int) -> tuple[list[dict], dict]:
    jobs, counts = [], _scan_counts()
    for job in iter_unique_jobs(rows, counts):
        if len(jobs) >= limit:
            counts['truncated'] = True
            break
        jobs.append(job)
    counts['unique'] = len(jobs)
    counts['missing_details'] = sum(j['details_status'] == 'missing' for j in jobs)
    return jobs, counts


def load_file_jobs(path: Path, limit: int) -> tuple[list[dict], dict]:
    jobs, counts = unique_jobs(_file_rows(path), limit)
    return jobs, {'source': 'file', 'path': str(path), **counts,
                  'note': 'Fixed user-provided snapshot; current availability is not verified'}


def _batches(rows: Iterator[dict], size: int) -> Iterator[list[dict]]:
    while batch := list(islice(rows, size)):
        yield batch


def all_job_batches(db: Any, config: dict, *, path: Path | None = None,
                    vacancy_scope: str = 'latest-run', batch_size: int = 200,
                    include_source: bool = False):
    """Read every record of an explicit source, without a lexical prefilter.

    All Mongo reads are bounded by batch_size. A completed run is required for
    latest-run: a full scan must never silently expand to other geographies.
    """
    from app.db.mongo import resolve_collection_name
    from app.jobcenter.search_provenance import attach_search_scope, usable_list_run

    if batch_size <= 0:
        raise ValueError('batch_size must be positive')
    scope = {**_scan_counts(), 'source': 'file' if path else 'mongo',
             'scan_mode': 'all', 'vacancy_scope': 'file' if path else vacancy_scope,
             'captured_at': datetime.now(timezone.utc).isoformat()}
    if path:
        scope.update(path=str(path), note='Fixed snapshot; current availability is not verified')
        rows = _file_rows(path)
    else:
        selector: dict = {'is_active': {'$ne': False}}
        if vacancy_scope == 'latest-run':
            state_name = config.get('state_collections', {}).get('list', 'pipeline_state')
            history_name = config.get('history_collections', {}).get('list', 'stellenangebote_history')
            state = db[state_name].find_one({'key': 'job_list_collector_query_scope'}) or {}
            if not usable_list_run(state) or not state.get('last_run_id'):
                raise ValueError('No completed list run; use --vacancy-scope active or all-stored explicitly')
            run_id = state['last_run_id']
            refs = db[history_name].distinct('referenznummer', {'event_type': 'list_seen', 'run_id': run_id})
            selector['referenznummer'] = {'$in': refs}
            params = state.get('params') or {}
            scope.update(list_run_id=run_id, scope_total=len(refs),
                         region=params.get('wo'), radius_km=params.get('umkreis'))
            attach_search_scope(scope, state)
        elif vacancy_scope == 'all-stored':
            selector = {}
        elif vacancy_scope != 'active':
            raise ValueError('Unknown vacancy scope')
        listing = db[resolve_collection_name(config, 'list', default='stellenangebote')]
        details = db[resolve_collection_name(config, 'details', default='jobdetails')]
        scope['source_records'] = listing.count_documents(selector)
        scope['note'] = 'Complete selected source; concurrent updates are not an atomic snapshot'
        scope['selection'] = 'All selected records, no title/role prefilter'

        def mongo_rows():
            cursor = listing.find(selector).sort('referenznummer', 1).batch_size(batch_size)
            try:
                for metas in _batches(iter(cursor), batch_size):
                    refs = [m['referenznummer'] for m in metas if m.get('referenznummer')]
                    joined = {d['referenznummer']: d for d in
                              details.find({'referenznummer': {'$in': refs}})}
                    for meta in metas:
                        yield {'stellenangebote': meta,
                               'jobdetails': joined.get(meta.get('referenznummer'), {})}
            finally:
                cursor.close()

        rows = mongo_rows()
    return _batches(iter_unique_jobs(iter(rows), scope, include_source=include_source), batch_size), scope


def load_mongo_jobs(db: Any, config: dict, profile: dict, limit: int) -> tuple[list[dict], dict]:
    """Read only. Never change collectors, current records, or pipeline_state."""
    from app.db.mongo import resolve_collection_name
    from app.jobcenter.search_provenance import attach_search_scope, usable_list_run
    state_name = config.get('state_collections', {}).get('list', 'pipeline_state')
    history_name = config.get('history_collections', {}).get('list', 'stellenangebote_history')
    state = db[state_name].find_one({'key': 'job_list_collector_query_scope'}) or {}
    run_id = state.get('last_run_id') if usable_list_run(state) else None
    scope = {'source': 'mongo', 'list_run_id': run_id,
             'captured_at': datetime.now(timezone.utc).isoformat(),
             'note': 'Bounded candidate pool; concurrent collectors are not changed'}
    selector: dict = {'is_active': {'$ne': False}}
    if run_id:
        refs = db[history_name].distinct('referenznummer', {'event_type': 'list_seen', 'run_id': run_id})
        selector['referenznummer'] = {'$in': refs}
        scope['scope_total'] = len(refs)
        params = state.get('params') or {}
        if params.get('wo'):
            scope['region'] = str(params['wo'])
            scope['radius_km'] = params.get('umkreis')
        attach_search_scope(scope, state)
    else:
        scope['note'] += '; no completed list snapshot: using active records, geography not guaranteed'
    listing = db[resolve_collection_name(config, 'list', default='stellenangebote')]
    detail_col = db[resolve_collection_name(config, 'details', default='jobdetails')]
    # The already collected source is bounded before NLP/model loading. No raw CV
    # is used in a DB regex, only escaped role/title terms.
    role_words = sorted({w for role in profile.get('target_roles', []) + profile.get('roles', [])
                         for w in re.findall(r'[^\W_]{3,}', role, re.UNICODE)
                         if w.casefold() not in {'und', 'mit', 'für', 'the', 'generalist'}})
    metas = []
    if role_words:
        expression = '|'.join(re.escape(w) for w in role_words[:40])
        role_filter = {'$or': [{'titel': {'$regex': expression, '$options': 'i'}},
                               {'beruf': {'$regex': expression, '$options': 'i'}}]}
        metas = list(listing.find({'$and': [selector, role_filter]}).sort('referenznummer', 1).limit(max(1, limit * 3 // 4)))
    seen = {m['referenznummer'] for m in metas}
    for meta in listing.find(selector).sort('referenznummer', 1).limit(limit * 2):
        if len(metas) >= limit:
            break
        if meta.get('referenznummer') not in seen:
            metas.append(meta); seen.add(meta.get('referenznummer'))
    details = {d['referenznummer']: d for d in detail_col.find({'referenznummer': {'$in': list(seen)}})}
    jobs, counts = unique_jobs(({'stellenangebote': m, 'jobdetails': details.get(m['referenznummer'], {})}
                               for m in metas), limit)
    scope.update(counts)
    scope['selection'] = '75% title/role lexical candidates, then deterministic active/source fallback'
    return jobs, scope


class DerivedCache:
    """Immutable result cache in a separate Mongo collection; never core writes."""
    def __init__(self, db: Any):
        self.collection = db['job_intelligence_cache']
        self.hits = self.misses = 0

    def get(self, key: str):
        row = self.collection.find_one({'_id': key}, {'payload': 1})
        if row:
            self.hits += 1
            return row['payload']
        self.misses += 1
        return None

    def put(self, key: str, payload: Any, kind: str = 'evidence') -> None:
        self.collection.update_one({'_id': key}, {'$setOnInsert': {
            'payload': payload, 'kind': kind, 'created_at': datetime.now(timezone.utc)}}, upsert=True)


def load_reference_terms(db: Any) -> tuple[list[dict], list[dict]]:
    """Use only active immutable manifests; DKZ search words stay lookup hints."""
    store = ReferenceStore(db)
    manifests = list(store.sources.find({}, {'_id': 1, 'active_snapshots': 1, 'version_requested': 1,
                                             'attribution': 1, 'activated_at': 1, 'originals': 1,
                                             'dataset_counts': 1, 'terms_url': 1}))
    snapshots = [s for m in manifests for s in m.get('active_snapshots', [])]
    versions = {m['_id']: m.get('version_requested', 'unknown') for m in manifests}
    query = {'snapshot_id': {'$in': snapshots}, 'dataset': {'$in': [
        'skill', 'occupation', 'DKZ_Kompetenzen_gueltig.xml', 'DKZ_Taetigkeiten_gueltig.xml']}}
    terms = []
    projection = {'source': 1, 'source_id': 1, 'snapshot_id': 1, 'dataset': 1,
                  'labels': 1, 'raw.tag': 1, 'raw.attributes.zustand': 1}
    for row in store.entries.find(query, projection).batch_size(500):
        labels = row.get('labels') or []
        if not labels:
            continue
        source = row['source']
        kind = 'occupation' if row['dataset'] in {'occupation', 'DKZ_Taetigkeiten_gueltig.xml'} else 'skill'
        if source == 'dkz':
            if row.get('raw', {}).get('tag') == 'kompetenzgruppe':
                continue
            if kind == 'occupation' and row.get('raw', {}).get('attributes', {}).get('zustand') != 'E':
                continue
        aliases = labels[1:] if source == 'esco' else []
        # Unfold an explicit abbreviated canonical heading, not arbitrary DKZ
        # search words (e.g. Server -> MS-SQL would be an unsupported synonym).
        if ', -' in labels[0]:
            aliases.append(labels[0].split(', -', 1)[0])
        short = re.sub(r'^(?:Betriebssystem|Programmiersprache|Datenbanksoftware)\s+', '', labels[0], flags=re.I)
        if short != labels[0]:
            aliases.append(short)
        concept_id = row['source_id'] if source == 'esco' else f'dkz:{kind}:{row["source_id"]}'
        terms.append(dict(id=concept_id, label=labels[0], aliases=aliases, source=source,
                          source_id=row['source_id'], kind=kind,
                          version=f'{versions[source]}:{row["snapshot_id"]}'))
    public = [{'source': m['_id'], 'version': m.get('version_requested'),
               'snapshots': m.get('active_snapshots', []), 'datasets': m.get('dataset_counts', {}),
               'attribution': m.get('attribution'), 'terms_url': m.get('terms_url'),
               'originals': m.get('originals', [])} for m in manifests]
    return sorted(terms, key=lambda t: t['id']), public
