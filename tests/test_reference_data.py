from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.normalization.reference_data import ReferenceStore
from tools import import_reference_data as importer


def _source_file(tmp_path: Path, name: str, content: bytes, *, url: str | None = None):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return importer.SourceFile(
        path=path,
        url=url or f"https://fixture.invalid/{name}",
        sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at="2026-10-15T12:00:00+00:00",
    )


def _esco_payload(rows, *, total=None, offset=0, limit=2):
    return {
        "total": len(rows) if total is None else total,
        "offset": offset,
        "limit": limit,
        "_embedded": {"results": rows},
    }


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, sort_spec):
        for key, direction in reversed(sort_spec):
            self.rows.sort(key=lambda row: row.get(key, ""), reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(deepcopy(self.rows))


def _matches(document, query):
    for key, expected in query.items():
        value = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
        elif isinstance(value, list) and isinstance(expected, str):
            if expected not in value:
                return False
        elif value != expected:
            return False
    return True


class MemoryCollection:
    def __init__(self):
        self.documents = {}
        self.indexes = []
        self.bulk_calls = []
        self.fail_bulk = False

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def bulk_write(self, operations, ordered=False):
        self.bulk_calls.append((list(operations), ordered))
        if self.fail_bulk:
            raise RuntimeError("simulated bulk failure")
        inserted = 0
        for operation in operations:
            selector = operation._filter
            update = operation._doc
            document_id = selector["_id"]
            if document_id in self.documents:
                continue
            record = deepcopy(update["$setOnInsert"])
            self.documents[document_id] = record
            inserted += 1
        return type("BulkResult", (), {"upserted_count": inserted})()

    def count_documents(self, query):
        return sum(_matches(document, query) for document in self.documents.values())

    def find_one(self, query):
        return next((deepcopy(document) for document in self.documents.values()
                     if _matches(document, query)), None)

    def replace_one(self, query, replacement, upsert=False):
        document_id = query.get("_id", replacement.get("_id"))
        self.documents[document_id] = deepcopy(replacement)

    def find(self, query):
        return FakeCursor(document for document in self.documents.values() if _matches(document, query))


class MemoryDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, MemoryCollection())


class FakeGridFS:
    instances = []

    def __init__(self, db, collection):
        self.files = db.collections.setdefault(f"{collection}.files", {})
        self.put_calls = []
        self.__class__.instances.append(self)

    def exists(self, file_id):
        return file_id in self.files

    def put(self, stream, *, _id, filename, metadata):
        content = stream.read()
        self.files[_id] = {"bytes": content, "filename": filename, "metadata": deepcopy(metadata)}
        self.put_calls.append(_id)
        return _id


def _storage():
    return {"entries_collection": "reference_entries", "sources_collection": "reference_sources",
            "files_bucket": "reference_files", "batch_size": 2}


def _dataset_from_bytes(tmp_path, *, source="dkz", name="skills.xml", content=b"<root />", version="v1"):
    file = _source_file(tmp_path, name, content)
    return importer.Dataset(source, name, "de", version, (file,))


def test_dkz_keeps_attribute_strings_duplicate_searchwords_and_parent_ids(tmp_path):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kompetenzen><kompetenzgruppe id="10" codenr="K 00" bezeichnung="Fachgruppe">'
        '<kompetenzgruppe id="11" codenr="K 01" bezeichnung="Gruppe">'
        '<kompetenz id="12" codenr="K 01-001" bezeichnung="Fähigkeit &amp; Praxis" />'
        '</kompetenzgruppe></kompetenzgruppe></kompetenzen>'
    ).encode("utf-8")
    dataset = _dataset_from_bytes(tmp_path, name="competences.xml", content=xml)
    records = list(importer.iter_records(dataset))

    by_id = {record["source_id"]: record for record in records}
    assert set(by_id) == {"10", "11", "12"}
    assert by_id["10"]["raw"]["attributes"] == {
        "id": "10", "codenr": "K 00", "bezeichnung": "Fachgruppe",
    }
    assert all(isinstance(value, str) for record in records for value in record["raw"]["attributes"].values())
    assert by_id["10"]["raw"]["parent_id"] is None
    assert by_id["11"]["raw"]["parent_id"] == "10"
    assert by_id["12"]["raw"]["parent_id"] == "11"
    assert by_id["12"]["raw"]["attributes"]["bezeichnung"] == "Fähigkeit & Praxis"

    words = (
        '<suchworte_kompetenzen>'
        '<suchwort id="12" codenr="K 01-001" suchwortGruppe="n" name="Türeneinbau" name_technisch="TUERENEINBAU" />'
        '<suchwort id="12" codenr="K 01-001" suchwortGruppe="n" name="Türeneinbau" name_technisch="TUERENEINBAU" />'
        '</suchworte_kompetenzen>'
    ).encode()
    word_dataset = _dataset_from_bytes(tmp_path, name="searchwords.xml", content=words)
    duplicates = list(importer.iter_records(word_dataset))
    assert len(duplicates) == 2
    assert duplicates[0]["_id"] != duplicates[1]["_id"]
    assert [row["ordinal"] for row in duplicates] == [0, 1]
    assert duplicates[0]["raw"] == duplicates[1]["raw"]
    assert duplicates[0]["raw"]["attributes"]["id"] == "12"


def test_esco_records_preserve_complete_raw_resource_and_german_labels(tmp_path):
    resource = {
        "uri": "http://data.europa.eu/esco/occupation/123",
        "preferredLabel": {"de": "Datenanalyst/in", "en": "Data analyst"},
        "alternativeLabel": {"de": ["Datenanalyse-Fachkraft", "Analyst/in Daten"]},
        "description": {"de": {"literal": "Analysiert Daten."}},
        "extraNested": {"keep": [1, 2]},
    }
    content = json.dumps(_esco_payload([resource], total=1, offset=0, limit=2), ensure_ascii=False).encode()
    dataset = importer.Dataset("esco", "occupation", "de", "v1.2.1",
                               (_source_file(tmp_path, "esco.json", content),))
    record = next(importer.iter_records(dataset))

    assert record["source_id"] == resource["uri"]
    assert record["labels"] == ["Datenanalyst/in", "Datenanalyse-Fachkraft", "Analyst/in Daten"]
    assert record["raw"] == resource
    assert "datenanalyst/in" in record["lookup_keys"]


def test_snapshot_id_tracks_original_bytes_and_requested_version(tmp_path):
    first = _dataset_from_bytes(tmp_path, content=b"<items><item id='1'/></items>", version="2026.1")
    changed_bytes = _dataset_from_bytes(tmp_path, name="other.xml", content=b"<items><item id='2'/></items>", version="2026.1")
    changed_version = _dataset_from_bytes(tmp_path, name="version.xml", content=b"<items><item id='1'/></items>", version="2026.2")
    assert len({first.snapshot_id, changed_bytes.snapshot_id, changed_version.snapshot_id}) == 3
    assert first.snapshot_id == _dataset_from_bytes(
        tmp_path, name="skills.xml", content=b"<items><item id='1'/></items>", version="2026.1"
    ).snapshot_id


class FakeDownloader:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, url, path, *, immutable=False, refresh=False):
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        kind = params["type"][0]
        page = int(params["offset"][0])
        self.calls.append((kind, page, params))
        content = json.dumps(self.pages[kind][page], ensure_ascii=False).encode()
        return _source_file(path.parent, path.name, content, url=url)


def _esco_api_row(uri, label):
    return {"uri": uri, "preferredLabel": {"de": label}, "alternativeLabel": {"de": []}}


def _esco_config(page_size=2):
    return {"api_url": "https://api.example/esco", "version": "v1.2.1", "language": "de",
            "page_size": page_size}


def test_esco_pagination_uses_page_offset_keeps_version_and_avoids_resource_fetches(tmp_path):
    pages = {}
    for kind in ("skill", "occupation"):
        pages[kind] = [
            _esco_payload([_esco_api_row(f"{kind}:1", "Eins")], total=2, offset=0, limit=1),
            _esco_payload([_esco_api_row(f"{kind}:2", "Zwei")], total=2, offset=1, limit=1),
        ]
    downloader = FakeDownloader(pages)
    datasets = importer.download_esco(_esco_config(page_size=1), downloader, tmp_path)

    assert len(downloader.calls) == 4
    assert [(kind, page) for kind, page, _params in downloader.calls] == [
        ("skill", 0), ("skill", 1), ("occupation", 0), ("occupation", 1),
    ]
    assert all(params["selectedVersion"] == ["v1.2.1"] for _kind, _page, params in downloader.calls)
    assert all(params["offset"] == [str(page)] for _kind, page, params in downloader.calls)
    assert all(len(dataset.files) == 2 for dataset in datasets)


def test_esco_clamped_limit_is_used_for_following_page_requests(tmp_path):
    pages = {}
    for kind in ("skill", "occupation"):
        pages[kind] = [
            _esco_payload([_esco_api_row(f"{kind}:{index}", f"Label {index}") for index in range(100)],
                          total=101, offset=0, limit=100),
            _esco_payload([_esco_api_row(f"{kind}:100", "Label 100")], total=101, offset=1, limit=100),
        ]
    downloader = FakeDownloader(pages)
    datasets = importer.download_esco(_esco_config(page_size=200), downloader, tmp_path)

    assert len(datasets) == 2
    assert [(kind, page, params["limit"][0]) for kind, page, params in downloader.calls] == [
        ("skill", 0, "200"), ("skill", 1, "100"),
        ("occupation", 0, "200"), ("occupation", 1, "100"),
    ]
    assert all(len(dataset.files) == 2 for dataset in datasets)


@pytest.mark.parametrize("problem", ["duplicate_uri", "changed_total", "incomplete"])
def test_esco_rejects_duplicate_uri_changed_total_and_incomplete_pages(tmp_path, problem):
    if problem == "duplicate_uri":
        pages = [
            _esco_payload([_esco_api_row("same", "Eins")], total=2, offset=0, limit=1),
            _esco_payload([_esco_api_row("same", "Doppelt")], total=2, offset=1, limit=1),
        ]
        message = "Duplicate ESCO URI"
    elif problem == "changed_total":
        pages = [
            _esco_payload([_esco_api_row("one", "Eins")], total=2, offset=0, limit=1),
            _esco_payload([_esco_api_row("two", "Zwei")], total=3, offset=1, limit=1),
        ]
        message = "pagination or total changed"
    else:
        pages = [
            _esco_payload([_esco_api_row("one", "Eins"), _esco_api_row("two", "Zwei")], total=3, offset=0, limit=2),
            _esco_payload([], total=3, offset=1, limit=2),
        ]
        message = "Incomplete ESCO page"
    downloader = FakeDownloader({"skill": pages, "occupation": pages})
    page_size = 2 if problem == "incomplete" else 1
    with pytest.raises(ValueError, match=message):
        importer.download_esco(_esco_config(page_size=page_size), downloader, tmp_path)


def test_import_preserves_gridfs_bytes_and_set_on_insert_replay_is_unchanged(tmp_path, monkeypatch):
    FakeGridFS.instances.clear()
    monkeypatch.setattr(importer, "GridFS", FakeGridFS)
    original = '<alleBerufe><beruf id="15" codenr="B 01000" bezeichnung="Köchin/Koch" zustand="E" /></alleBerufe>'.encode("utf-8")
    dataset = _dataset_from_bytes(tmp_path, name="positions.xml", content=original)
    db = MemoryDB()
    first = importer.import_datasets(db, [dataset], _storage())
    entry_collection = db["reference_entries"]
    source_collection = db["reference_sources"]
    file_id = "sha256:" + dataset.files[0].sha256
    fs = FakeGridFS.instances[-1]
    assert fs.files[file_id]["bytes"] == original
    assert first["dkz"]["entries"] == 1
    row = next(iter(entry_collection.documents.values()))
    assert row["raw"]["attributes"] == {
        "id": "15", "codenr": "B 01000", "bezeichnung": "Köchin/Koch", "zustand": "E",
    }
    assert all(isinstance(value, str) for value in row["raw"]["attributes"].values())
    assert entry_collection.bulk_calls[0][1] is False
    assert all(operation._upsert is True and "$setOnInsert" in operation._doc
               for operation in entry_collection.bulk_calls[0][0])
    activated_at = source_collection.documents["dkz"]["activated_at"]
    bulk_call_count = len(entry_collection.bulk_calls)
    gridfs_call_count = len(fs.put_calls)

    repeated = importer.import_datasets(db, [dataset], _storage())
    assert repeated["dkz"]["status"] == "unchanged"
    assert len(entry_collection.bulk_calls) == bulk_call_count
    assert len(fs.put_calls) == gridfs_call_count
    assert source_collection.documents["dkz"]["activated_at"] == activated_at


def test_bulk_failure_leaves_old_source_snapshot_active(tmp_path, monkeypatch):
    FakeGridFS.instances.clear()
    monkeypatch.setattr(importer, "GridFS", FakeGridFS)
    db = MemoryDB()
    old_dataset = _dataset_from_bytes(
        tmp_path, name="positions.xml", content=b'<alleBerufe><beruf id="1" codenr="B 1" /></alleBerufe>'
    )
    importer.import_datasets(db, [old_dataset], _storage())
    old_manifest = deepcopy(db["reference_sources"].documents["dkz"])
    new_dataset = _dataset_from_bytes(
        tmp_path, name="positions-new.xml", content=b'<alleBerufe><beruf id="2" codenr="B 2" /></alleBerufe>'
    )
    db["reference_entries"].fail_bulk = True
    with pytest.raises(RuntimeError, match="simulated bulk failure"):
        importer.import_datasets(db, [new_dataset], _storage())
    assert db["reference_sources"].documents["dkz"] == old_manifest


def test_reimport_repairs_missing_gridfs_original_and_then_uses_unchanged_fast_path(tmp_path, monkeypatch):
    FakeGridFS.instances.clear()
    monkeypatch.setattr(importer, "GridFS", FakeGridFS)
    content = b'<alleBerufe><beruf id="21" codenr="B 02100" bezeichnung="Lehrkraft" /></alleBerufe>'
    dataset = _dataset_from_bytes(tmp_path, name="positions.xml", content=content)
    db = MemoryDB()
    importer.import_datasets(db, [dataset], _storage())
    file_id = "sha256:" + dataset.files[0].sha256
    files = FakeGridFS.instances[-1].files
    assert files[file_id]["bytes"] == content
    files.pop(file_id)
    entries = db["reference_entries"]
    sources = db["reference_sources"]
    existing_row_count = len(entries.documents)
    bulk_calls_before_repair = len(entries.bulk_calls)

    repaired = importer.import_datasets(db, [dataset], _storage())

    assert repaired["dkz"]["status"] == "imported"
    assert files[file_id]["bytes"] == content
    assert len(entries.documents) == existing_row_count
    assert len(entries.bulk_calls) > bulk_calls_before_repair
    repair_manifest = deepcopy(sources.documents["dkz"])
    bulk_calls_after_repair = len(entries.bulk_calls)
    restored_file_puts = sum(len(fs.put_calls) for fs in FakeGridFS.instances)

    unchanged = importer.import_datasets(db, [dataset], _storage())

    assert unchanged["dkz"]["status"] == "unchanged"
    assert len(entries.bulk_calls) == bulk_calls_after_repair
    assert sum(len(fs.put_calls) for fs in FakeGridFS.instances) == restored_file_puts
    assert sources.documents["dkz"] == repair_manifest


@pytest.mark.parametrize(
    ("entries_name", "sources_name", "message"),
    [
        ("stellenangebote", "reference_sources", "separate collections"),
        ("reference_entries", "profiles", "separate collections"),
        ("reference_data", "reference_data", "different collections"),
    ],
)
def test_import_rejects_protected_or_shared_collection_names(entries_name, sources_name, message):
    db = MemoryDB()
    storage = _storage() | {"entries_collection": entries_name, "sources_collection": sources_name}
    dataset = importer.Dataset("dkz", "positions.xml", "de", "daily", ())

    with pytest.raises(ValueError, match=message):
        importer.import_datasets(db, [dataset], storage)
    assert db.collections == {}


def test_reference_lookup_reads_only_active_snapshots_and_keeps_raw_separate():
    db = MemoryDB()
    active = {"_id": "active:0", "snapshot_id": "active", "source": "esco", "dataset": "skill",
              "source_id": "uri:active", "ordinal": 0, "labels": ["Datenanalyse"],
              "lookup_keys": ["datenanalyse"], "raw": {"preferredLabel": {"de": "Datenanalyse"}}}
    inactive = {"_id": "inactive:0", "snapshot_id": "inactive", "source": "dkz", "dataset": "terms",
                "source_id": "123", "ordinal": 0, "labels": ["Datenanalyse"],
                "lookup_keys": ["datenanalyse"], "raw": {"attributes": {"name": "Datenanalyse"}}}
    candidate = {"_id": "candidate", "snapshot_id": "inactive", "source": "candidate",
                 "lookup_keys": ["datenanalyse"], "raw": {"secretly_candidate": True}}
    db["reference_entries"].documents = {row["_id"]: deepcopy(row) for row in (active, inactive, candidate)}
    db["reference_sources"].documents["esco"] = {"_id": "esco", "active_snapshots": ["active"]}
    before_raw = deepcopy(db["reference_entries"].documents["active:0"]["raw"])

    rows = ReferenceStore(db, _storage()).lookup("Datenanalyse")

    assert [row["_id"] for row in rows] == ["active:0"]
    assert rows[0]["labels"] == ["Datenanalyse"]
    assert rows[0]["raw"] == before_raw
    assert rows[0]["lookup_keys"] == ["datenanalyse"]
