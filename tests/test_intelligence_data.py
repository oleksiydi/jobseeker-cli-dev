from __future__ import annotations

import csv
import json
import re
from copy import deepcopy

import pytest

from app.matching.intelligence_data import all_job_batches, load_file_jobs, load_mongo_jobs, load_reference_terms


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, fields, direction=None):
        if isinstance(fields, str):
            fields = [(fields, direction)]
        for key, direction in reversed(fields):
            self.rows.sort(key=lambda row: row.get(key, ""), reverse=direction < 0)
        return self

    def limit(self, amount):
        self.rows = self.rows[:amount]
        return self

    def batch_size(self, _amount):
        return self

    def close(self):
        pass

    def __iter__(self):
        return iter(deepcopy(self.rows))


def _field(document, path):
    value = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(document, query):
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(document, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
                return False
            continue
        value = _field(document, key)
        if isinstance(expected, dict):
            if "$ne" in expected and value == expected["$ne"]:
                return False
            if "$in" in expected and value not in expected["$in"]:
                return False
            if "$regex" in expected:
                flags = re.I if expected.get("$options") == "i" else 0
                if value is None or not re.search(expected["$regex"], str(value), flags):
                    return False
        elif isinstance(value, list):
            if expected not in value:
                return False
        elif value != expected:
            return False
    return True


class FakeCollection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))
        self.reads = []
        self.writes = []

    def find_one(self, query, projection=None):
        self.reads.append(("find_one", deepcopy(query)))
        row = next((row for row in self.rows if _matches(row, query)), None)
        return deepcopy(row)

    def find(self, query, projection=None):
        self.reads.append(("find", deepcopy(query), deepcopy(projection)))
        return FakeCursor(row for row in self.rows if _matches(row, query))

    def distinct(self, field, query):
        self.reads.append(("distinct", field, deepcopy(query)))
        return list(dict.fromkeys(_field(row, field) for row in self.rows
                                  if _matches(row, query) and _field(row, field) is not None))

    def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    def __getattr__(self, name):
        if name in {"insert_one", "update_one", "replace_one", "delete_one", "bulk_write"}:
            def record_write(*args, **kwargs):
                self.writes.append((name, args, kwargs))
                raise AssertionError(f"unexpected Mongo write: {name}")
            return record_write
        raise AttributeError(name)


class FakeDB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.mark.parametrize("extension", ["json", "jsonl", "csv"])
def test_file_formats_share_normalization_and_strip_description_markup(tmp_path, extension):
    row = {
        "ref": "123-abc",
        "titel": "  Data   Analyst ",
        "beruf": "  Analyst   Data ",
        "location": " Berlin   Mitte ",
        "description": "<p>Build &amp; maintain</p><br>data pipelines",
        "schedule": "Vollzeit",
        "employer": "Example GmbH",
        "url": "https://jobs.example/123",
    }
    path = tmp_path / f"vacancies.{extension}"
    if extension == "json":
        path.write_text(json.dumps({"vacancies": [row]}), encoding="utf-8")
    elif extension == "jsonl":
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=row.keys())
            writer.writeheader()
            writer.writerow(row)

    jobs, counts = load_file_jobs(path, limit=10)

    assert len(jobs) == 1
    assert jobs[0]["ref"] == "123-abc"
    assert jobs[0]["title"] == "Data Analyst"
    assert jobs[0]["role"] == "Analyst Data"
    assert jobs[0]["location"] == "Berlin Mitte"
    assert jobs[0]["description"] == "Build & maintain\n\ndata pipelines"
    assert jobs[0]["details_status"] == "available"
    assert counts["unique"] == 1


def test_file_deduplication_uses_content_even_when_source_ids_differ(tmp_path):
    rows = [
        {"id": "job-1", "title": "Data Engineer", "role": "Engineer", "description": "Python pipelines"},
        {"id": "job-2", "title": "  DATA engineer ", "role": "Engineer", "description": "Python   pipelines"},
        {"id": "job-3", "title": "Platform Engineer", "description": "Kubernetes"},
    ]
    path = tmp_path / "duplicate.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    jobs, counts = load_file_jobs(path, limit=10)

    assert [job["ref"] for job in jobs] == ["job-1", "job-3"]
    assert counts["read"] == 3
    assert counts["duplicates"] == 1


def test_incomplete_records_do_not_abort_usable_vacancies(tmp_path):
    rows = [
        {'ref': 'unusable'},
        {'ref': 'role-only', 'role': 'Support Specialist'},
        {'ref': 'complete', 'title': 'Data Engineer', 'description': 'Python required'},
    ]
    path = tmp_path / 'incomplete.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    jobs, counts = load_file_jobs(path, limit=10)
    assert counts['invalid_records'] == 1
    assert counts['missing_details'] == 1
    assert jobs[0]['title'] == 'Support Specialist'
    assert jobs[0]['title_source'] == 'occupation_fallback'
    assert [j['ref'] for j in jobs] == ['role-only', 'complete']


def test_language_punctuation_is_not_lost_during_deduplication(tmp_path):
    path = tmp_path / 'languages.json'
    path.write_text(json.dumps([
        {'ref': 'cpp', 'title': 'Developer', 'description': 'C++ erforderlich'},
        {'ref': 'csharp', 'title': 'Developer', 'description': 'C# erforderlich'},
    ]), encoding='utf-8')
    jobs, counts = load_file_jobs(path, limit=10)
    assert len(jobs) == 2
    assert counts['duplicates'] == 0


def test_mongo_loader_uses_last_completed_seen_refs_and_never_writes_core_collections(monkeypatch):
    monkeypatch.delenv("COLLECTION_STELLENANGEBOTE", raising=False)
    monkeypatch.delenv("COLLECTION_JOBDETAILS", raising=False)
    state = FakeCollection([{"key": "job_list_collector_query_scope",
                            "last_run_id": "run-7", "last_completed_full_scan": True}])
    history = FakeCollection([{"event_type": "list_seen", "run_id": "run-7",
                               "referenznummer": "ref-1"},
                              {"event_type": "list_seen", "run_id": "run-7",
                               "referenznummer": "ref-2"}])
    listings = FakeCollection([
        {"referenznummer": "ref-1", "titel": "Data Analyst", "beruf": "Analyst"},
        {"referenznummer": "ref-2", "titel": "Office Manager", "beruf": "Administration"},
        {"referenznummer": "ref-outside", "titel": "Data Analyst", "beruf": "Analyst"},
        {"referenznummer": "ref-inactive", "titel": "Data Analyst", "is_active": False},
    ])
    details = FakeCollection([
        {"referenznummer": "ref-1", "stellenangebotsTitel": "  Senior   Data Analyst  ",
         "stellenangebotsBeschreibung": "<p>Build reliable dashboards.</p>", "hauptberuf": "Analyst"},
    ])
    db = FakeDB({"pipeline_state": state, "stellenangebote_history": history,
                 "stellenangebote": listings, "jobdetails": details})

    jobs, scope = load_mongo_jobs(db, {}, {"target_roles": ["Data Analyst"]}, limit=2)

    assert {job["ref"] for job in jobs} == {"ref-1", "ref-2"}
    by_ref = {job["ref"]: job for job in jobs}
    assert by_ref["ref-1"]["title"] == "Senior Data Analyst"
    assert by_ref["ref-1"]["description"] == "Build reliable dashboards."
    assert by_ref["ref-1"]["details_status"] == "available"
    assert by_ref["ref-2"]["details_status"] == "missing"
    assert scope["list_run_id"] == "run-7"
    assert scope["scope_total"] == 2
    assert scope["missing_details"] == 1
    assert "ref-outside" not in {job["ref"] for job in jobs}
    assert "ref-inactive" not in {job["ref"] for job in jobs}
    assert all(not collection.writes for collection in (state, history, listings, details))


def test_reference_terms_include_only_active_snapshots_and_valid_dkz_concepts():
    active = "active-snapshot"
    manifests = FakeCollection([
        {"_id": "esco", "active_snapshots": [active], "version_requested": "v1.2.1",
         "dataset_counts": {"skill": 1}},
        {"_id": "dkz", "active_snapshots": [active], "version_requested": "daily",
         "dataset_counts": {"DKZ_Taetigkeiten_gueltig.xml": 1}},
    ])
    entries = FakeCollection([
        {"snapshot_id": active, "source": "esco", "dataset": "skill", "source_id": "esco:1",
         "labels": ["Python", "Python Programmierung"]},
        {"snapshot_id": "retired", "source": "esco", "dataset": "skill", "source_id": "old:1",
         "labels": ["Retired skill"]},
        {"snapshot_id": active, "source": "dkz", "dataset": "DKZ_Kompetenzen_gueltig.xml",
         "source_id": "20", "labels": ["Datenanalyse"],
         "raw": {"tag": "kompetenz", "attributes": {"zustand": "E"}}},
        {"snapshot_id": active, "source": "dkz", "dataset": "DKZ_Taetigkeiten_gueltig.xml",
         "source_id": "30", "labels": ["Analyst/in"],
         "raw": {"tag": "beruf", "attributes": {"zustand": "E"}}},
        {"snapshot_id": active, "source": "dkz", "dataset": "DKZ_Taetigkeiten_gueltig.xml",
         "source_id": "31", "labels": ["Abgelaufene Rolle"],
         "raw": {"tag": "beruf", "attributes": {"zustand": "U"}}},
        {"snapshot_id": active, "source": "dkz", "dataset": "DKZ_Kompetenzen_gueltig.xml",
         "source_id": "21", "labels": ["Kompetenzgruppe"],
         "raw": {"tag": "kompetenzgruppe", "attributes": {"zustand": "E"}}},
        {"snapshot_id": active, "source": "dkz", "dataset": "DKZ_Suchworte_Kompetenzen_gueltig.xml",
         "source_id": "22", "labels": ["Suchwort Alias"],
         "raw": {"tag": "suchwort", "attributes": {"zustand": "E"}}},
    ])
    db = FakeDB({"reference_sources": manifests, "reference_entries": entries})

    terms, public_sources = load_reference_terms(db)

    assert {term["id"] for term in terms} == {"esco:1", "dkz:skill:20", "dkz:occupation:30"}
    assert all(term["version"].endswith(":" + active) for term in terms)
    assert "Retired skill" not in {term["label"] for term in terms}
    assert "Abgelaufene Rolle" not in {term["label"] for term in terms}
    assert "Kompetenzgruppe" not in {term["label"] for term in terms}
    assert "Suchwort Alias" not in {term["label"] for term in terms}
    entry_query = entries.reads[0][1]
    assert entry_query["snapshot_id"] == {"$in": [active, active]}
    assert "DKZ_Suchworte_Kompetenzen_gueltig.xml" not in entry_query["dataset"]["$in"]
    assert {source["source"] for source in public_sources} == {"esco", "dkz"}


@pytest.mark.parametrize('vacancy_scope,expected', [('latest-run', 2301), ('active', 2302), ('all-stored', 2303)])
def test_full_mongo_scan_exceeds_old_cap_and_batches_details_without_core_writes(monkeypatch, vacancy_scope, expected):
    monkeypatch.delenv('COLLECTION_STELLENANGEBOTE', raising=False)
    monkeypatch.delenv('COLLECTION_JOBDETAILS', raising=False)
    listings = FakeCollection([{'referenznummer': f'ref-{i:05}', 'titel': f'Job {i}',
                                'is_active': i != 2302} for i in range(2303)])
    details = FakeCollection([{'referenznummer': f'ref-{i:05}',
                              'stellenangebotsBeschreibung': 'Linux erforderlich.'} for i in range(2303)])
    state = FakeCollection([{'key': 'job_list_collector_query_scope', 'last_run_id': 'run-full',
                             'last_completed_full_scan': True, 'params': {'wo': 'Regensburg', 'umkreis': 15}}])
    history = FakeCollection([{'event_type': 'list_seen', 'run_id': 'run-full',
                               'referenznummer': f'ref-{i:05}'} for i in range(2301)])
    db = FakeDB({'stellenangebote': listings, 'jobdetails': details,
                 'pipeline_state': state, 'stellenangebote_history': history})
    batches, scope = all_job_batches(db, {}, vacancy_scope=vacancy_scope, batch_size=128)
    sizes = [len(batch) for batch in batches]
    assert sum(sizes) == expected
    assert max(sizes) <= 128
    assert scope['read'] == scope['unique'] == scope['source_records'] == expected
    assert scope['truncated'] is False
    assert all(len(read[1]['referenznummer']['$in']) <= 128 for read in details.reads if read[0] == 'find')
    assert all(not col.writes for col in (listings, details, state, history))


def test_full_latest_run_requires_completed_scope_instead_of_expanding_geography():
    db = FakeDB({'pipeline_state': FakeCollection()})
    with pytest.raises(ValueError, match='No completed list run'):
        all_job_batches(db, {}, vacancy_scope='latest-run')


@pytest.mark.parametrize('full_scan', [False, True])
def test_limited_url_run_stays_scoped_and_preserves_collection_warnings(full_scan):
    provenance = {
        'search_url': 'https://www.arbeitsagentur.de/jobsuche/suche?wo=Berlin&umkreis=15',
        'search_id': 'stable-id', 'coverage_complete': False, 'geography_narrowed': True,
        'original_geography': {'wo': 'Berlin', 'umkreis': 15},
        'effective_geography': [{'wo': '10115', 'umkreis': 0}],
        'warnings': [{'code': 'radius_removed', 'message': 'Окружение исключено.'}],
    }
    state = FakeCollection([{
        'key': 'job_list_collector_query_scope', 'last_run_id': 'limited',
        'last_completed_full_scan': False, 'last_run_usable': True,
        'params': {'wo': 'Berlin', 'umkreis': 15}, 'search_provenance': provenance,
    }])
    history = FakeCollection([{'event_type': 'list_seen', 'run_id': 'limited', 'referenznummer': 'LOCAL'}])
    listing = FakeCollection([{'referenznummer': 'LOCAL', 'titel': 'Data Engineer'},
                              {'referenznummer': 'OUTSIDE', 'titel': 'Data Engineer'}])
    db = FakeDB({'pipeline_state': state, 'stellenangebote_history': history,
                 'stellenangebote': listing, 'jobdetails': FakeCollection()})
    if full_scan:
        batches, scope = all_job_batches(db, {})
        jobs = [job for batch in batches for job in batch]
    else:
        jobs, scope = load_mongo_jobs(db, {}, {'target_roles': ['Data Engineer']}, 5)
    assert [job['ref'] for job in jobs] == ['LOCAL']
    assert scope['collection_coverage'] == provenance
    assert scope['region'] == '10115' and scope['radius_km'] == 0
    assert not any(c.writes for c in db.collections.values())


def test_full_file_scan_deduplicates_across_processing_batches(tmp_path):
    rows = [{'ref': f'job-{i}', 'title': f'Job {i}'} for i in range(6)]
    rows += [{**rows[0], 'ref': 'different-ref-same-content'}]
    path = tmp_path / 'all.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    batches, scope = all_job_batches(None, {}, path=path, batch_size=2)
    assert [len(b) for b in batches] == [2, 2, 2]
    assert scope['read'] == 7
    assert scope['unique'] == 6
    assert scope['duplicates'] == 1


def test_malformed_legacy_location_is_counted_without_aborting_full_scan(tmp_path):
    path = tmp_path / 'legacy.jsonl'
    rows = [{'stellenangebote': {'referenznummer': 'broken', 'titel': 'Broken',
                                'arbeitsort': 'unexpected string'}},
            {'ref': 'usable', 'title': 'Usable vacancy'}]
    path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    batches, scope = all_job_batches(None, {}, path=path)
    assert [j['ref'] for batch in batches for j in batch] == ['usable']
    assert scope['invalid_records'] == 1
