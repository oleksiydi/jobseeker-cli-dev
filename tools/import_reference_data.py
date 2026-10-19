"""Скачивает ESCO и DKZ, сохраняет оригиналы и индекс чтения в MongoDB.

    python -m tools.import_reference_data --sources both
    python -m tools.import_reference_data --sources both --download-only

Имена и поля источников не исправляются. Производные ключи поиска лежат отдельно
от raw. Снимок становится доступен читателям только после полного импорта.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

import requests
from dotenv import load_dotenv
from gridfs import GridFS
from pymongo import UpdateOne
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import load_app_config, project_root
from app.db.mongo import get_database
from app.normalization.reference_data import ReferenceStore
from app.normalization.runtime import normalize_term_key


LOGGER = logging.getLogger("reference_data")
INDEX_VERSION = 1
SOURCE_METADATA = {
    "esco": {
        "terms_url": "https://esco.ec.europa.eu/en/about-esco/faq?page=1",
        "attribution": "This service uses the ESCO classification of the European Commission.",
        "content_modified": False,
    },
    "dkz": {
        "terms_url": "https://www.arbeitsagentur.de/institutionen/dkz-downloadportal/nutzungsbedingungen-dkz",
        "attribution": "Downloadportal der Bundesagentur für Arbeit, www.arbeitsagentur.de",
        "content_modified": False,
    },
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class SourceFile:
    path: Path
    url: str
    sha256: str
    retrieved_at: str
    last_modified: str | None = None


@dataclass(frozen=True)
class Dataset:
    source: str
    name: str
    language: str
    version: str
    files: tuple[SourceFile, ...]

    @property
    def snapshot_id(self) -> str:
        identity = {"source": self.source, "dataset": self.name, "language": self.language,
                    "version_requested": self.version, "index_version": INDEX_VERSION,
                    "files": [file.sha256 for file in self.files]}
        return _sha(json.dumps(identity, sort_keys=True).encode())


class SourceDownloader:
    """Session + ограниченные повторы. Локальный кэш проверяется по SHA256."""

    def __init__(self, *, timeout_seconds: int, max_file_bytes: int) -> None:
        if timeout_seconds <= 0 or max_file_bytes <= 0:
            raise ValueError("HTTP timeout and file size limit must be positive")
        self.timeout = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.session = requests.Session()
        retry = Retry(total=2, backoff_factor=1, backoff_max=5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods={"GET"}, respect_retry_after_header=False,
                      raise_on_status=False)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def close(self) -> None:
        self.session.close()

    def fetch(self, url: str, path: Path, *, immutable: bool = False,
              refresh: bool = False) -> SourceFile:
        metadata_path = path.with_suffix(path.suffix + ".meta.json")
        saved = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        cached = (path.exists() and saved.get("url") == url
                  and _sha(path.read_bytes()) == saved.get("sha256"))
        if cached and immutable and not refresh:
            return SourceFile(path=path, url=url, sha256=saved["sha256"],
                              retrieved_at=saved["retrieved_at"], last_modified=saved.get("last_modified"))
        headers = {}
        if cached and not refresh:
            if saved.get("etag"):
                headers["If-None-Match"] = saved["etag"]
            if saved.get("last_modified"):
                headers["If-Modified-Since"] = saved["last_modified"]
        with self.session.get(url, headers=headers, timeout=(10, self.timeout), stream=True) as response:
            if response.status_code == 304 and cached:
                return SourceFile(path=path, url=url, sha256=saved["sha256"],
                                  retrieved_at=saved["retrieved_at"], last_modified=saved.get("last_modified"))
            if response.status_code != 200:
                raise RuntimeError(f"Reference download HTTP {response.status_code}")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise ValueError("Reference response exceeds configured size limit")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise ValueError("Empty reference response")
            saved = {"url": url, "sha256": _sha(content), "retrieved_at": _now(),
                     "etag": response.headers.get("ETag"),
                     "last_modified": response.headers.get("Last-Modified")}
        _atomic_bytes(path, content)
        _atomic_bytes(metadata_path, json.dumps(saved, ensure_ascii=False, indent=2).encode())
        return SourceFile(path=path, url=url, sha256=saved["sha256"],
                          retrieved_at=saved["retrieved_at"], last_modified=saved.get("last_modified"))


def _esco_page(file: SourceFile) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(file.path.read_bytes())
    rows = payload.get("_embedded", {}).get("results")
    if not isinstance(rows, list) or not isinstance(payload.get("total"), int):
        raise ValueError("Invalid ESCO page: results/total missing")
    if not all(isinstance(row, dict) and row.get("uri") for row in rows):
        raise ValueError("Invalid ESCO resource: URI missing")
    return payload, rows


def download_esco(config: dict[str, Any], downloader: SourceDownloader, work: Path,
                  *, refresh: bool = False) -> list[Dataset]:
    version, language = config["version"], config["language"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version) or not re.fullmatch(r"[a-z]{2}", language):
        raise ValueError("ESCO version/language have an invalid format")
    page_size = int(config["page_size"])
    if not 1 <= page_size <= 1000:
        raise ValueError("ESCO page_size must be between 1 and 1000")
    datasets = []
    for kind in ("skill", "occupation"):
        page_size = int(config["page_size"])
        files = []
        total = None
        seen = set()
        page = 0
        while total is None or page < math.ceil(total / page_size):
            params = {"text": "", "type": kind, "language": language, "limit": page_size,
                      "offset": page, "full": "true", "viewObsolete": "false", "selectedVersion": version}
            url = config["api_url"].rstrip("/") + "/search?" + urlencode(params)
            file = downloader.fetch(url, work / "esco" / version / language / f"{kind}-{page:05d}.json",
                                    immutable=True, refresh=refresh)
            payload, rows = _esco_page(file)
            if total is None:
                total = payload["total"]
                if total <= 0 or total > 100000:
                    raise ValueError("Unexpected ESCO resource count")
                # Публичный API ограничивает размер страницы (сейчас 100).
                # Продолжаем с фактическим limit, а offset увеличиваем по страницам.
                actual_limit = payload.get("limit")
                if not isinstance(actual_limit, int) or not 1 <= actual_limit <= page_size:
                    raise ValueError("Invalid ESCO page size")
                page_size = actual_limit
            if payload["total"] != total or payload.get("offset") != page or payload.get("limit") != page_size:
                raise ValueError("ESCO pagination or total changed during download")
            if len(rows) != min(page_size, total - page * page_size):
                raise ValueError("Incomplete ESCO page")
            for row in rows:
                if row["uri"] in seen:
                    raise ValueError("Duplicate ESCO URI across pages")
                seen.add(row["uri"])
            files.append(file)
            page += 1
            LOGGER.info("esco kind=%s page=%s resources=%s/%s", kind, page, len(seen), total)
        if len(seen) != total:
            raise ValueError("Incomplete ESCO dataset")
        datasets.append(Dataset("esco", kind, language, version, tuple(files)))
    return datasets


def download_dkz(config: dict[str, Any], downloader: SourceDownloader, work: Path,
                 *, refresh: bool = False) -> list[Dataset]:
    datasets = []
    for filename in config["files"]:
        if Path(filename).name != filename:
            raise ValueError("DKZ filename must not contain a directory")
        file = downloader.fetch(urljoin(config["base_url"], filename), work / "dkz" / filename, refresh=refresh)
        datasets.append(Dataset("dkz", filename, "de", "daily", (file,)))
    return datasets


def _esco_records(dataset: Dataset) -> Iterator[tuple[str, list[str], dict[str, Any]]]:
    for file in dataset.files:
        _, rows = _esco_page(file)
        for row in rows:
            preferred = row.get("preferredLabel", {}).get(dataset.language)
            alternatives = row.get("alternativeLabel", {}).get(dataset.language, [])
            if isinstance(alternatives, str):
                alternatives = [alternatives]
            labels = [value for value in [preferred, *alternatives] if isinstance(value, str) and value]
            yield row["uri"], labels, row


def _dkz_records(dataset: Dataset) -> Iterator[tuple[str, list[str], dict[str, Any]]]:
    path = dataset.files[0].path
    # Иерархию компетенций сохраняем как связь с родителем. Исходный XML
    # целиком остаётся в GridFS, атрибуты читаем без исправления значений.
    stack: list[ET.Element] = []
    for event, node in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            stack.append(node)
            continue
        if node.tag in {"beruf", "kompetenzgruppe", "kompetenz", "suchwort"}:
            attributes = dict(node.attrib)
            if not attributes.get("id"):
                raise ValueError("DKZ record has no source ID")
            parent_id = stack[-2].attrib.get("id") if len(stack) > 1 else None
            labels = [attributes[key] for key in ("bezeichnung", "name", "name_technisch")
                      if attributes.get(key)]
            yield attributes["id"], labels, {"tag": node.tag, "attributes": attributes,
                                             "text": node.text, "parent_id": parent_id}
        stack.pop()
        node.clear()


def iter_records(dataset: Dataset) -> Iterator[dict[str, Any]]:
    records = _esco_records(dataset) if dataset.source == "esco" else _dkz_records(dataset)
    snapshot = dataset.snapshot_id
    for ordinal, (source_id, labels, raw) in enumerate(records):
        yield {"_id": f"{snapshot}:{ordinal}", "snapshot_id": snapshot,
               "source": dataset.source, "dataset": dataset.name, "language": dataset.language,
               "source_id": source_id, "ordinal": ordinal, "labels": labels,
               "lookup_keys": sorted({normalize_term_key(label) for label in labels}),
               "raw": raw}


def import_datasets(db: Any, datasets: list[Dataset], storage: dict[str, Any]) -> dict[str, Any]:
    """Идемпотентный импорт. Незавершённый снимок не активируется."""
    batch_size = int(storage["batch_size"])
    if batch_size <= 0:
        raise ValueError("Import batch_size must be positive")
    if not datasets:
        raise ValueError("No reference datasets selected")
    protected = {"stellenangebote", "jobdetails", "analysen", "profiles"}
    if storage["entries_collection"] in protected or storage["sources_collection"] in protected:
        raise ValueError("Reference import must use separate collections")
    if storage["entries_collection"] == storage["sources_collection"]:
        raise ValueError("Reference entries and source manifests need different collections")
    entries = db[storage["entries_collection"]]
    sources = db[storage["sources_collection"]]
    entries.create_index([("snapshot_id", 1), ("lookup_keys", 1)])
    entries.create_index([("snapshot_id", 1), ("source_id", 1)])
    fs = GridFS(db, collection=storage["files_bucket"])
    summary = {}
    for source in sorted({dataset.source for dataset in datasets}):
        selected = [dataset for dataset in datasets if dataset.source == source]
        snapshots = [dataset.snapshot_id for dataset in selected]
        previous = sources.find_one({"_id": source})
        if (previous and previous.get("active_snapshots") == snapshots
                and entries.count_documents({"snapshot_id": {"$in": snapshots}}) == previous["entries_count"]
                and previous.get("originals")
                and all(fs.exists(original["file_id"]) for original in previous["originals"])):
            summary[source] = {"status": "unchanged", "entries": previous["entries_count"]}
            LOGGER.info("source=%s unchanged entries=%s", source, previous["entries_count"])
            continue
        counts = {}
        originals = []
        for dataset in selected:
            for file in dataset.files:
                file_id = f"sha256:{file.sha256}"
                if not fs.exists(file_id):
                    with file.path.open("rb") as stream:
                        fs.put(stream, _id=file_id, filename=file.path.name,
                               metadata={"source": source, "sha256": file.sha256,
                                         "source_url": file.url, "retrieved_at": file.retrieved_at})
                originals.append({"file_id": file_id, "sha256": file.sha256, "url": file.url,
                                  "retrieved_at": file.retrieved_at, "last_modified": file.last_modified})
            count = 0
            operations = []
            for record in iter_records(dataset):
                # ordinal сохраняет даже полные дубли поисковых слов DKZ.
                operations.append(UpdateOne({"_id": record["_id"]}, {"$setOnInsert": record}, upsert=True))
                count += 1
                if len(operations) >= batch_size:
                    entries.bulk_write(operations, ordered=False)
                    operations = []
            if operations:
                entries.bulk_write(operations, ordered=False)
            if count <= 0 or entries.count_documents({"snapshot_id": dataset.snapshot_id}) != count:
                raise ValueError("Reference dataset is empty or incomplete in MongoDB")
            counts[dataset.name] = count
        manifest = {"_id": source, "active_snapshots": snapshots, "entries_count": sum(counts.values()),
                    "dataset_counts": counts, "language": selected[0].language,
                    "version_requested": selected[0].version, "activated_at": _now(),
                    "originals": originals, **SOURCE_METADATA[source]}
        if source == "dkz":
            stand = datetime.fromisoformat(originals[0]["retrieved_at"]).strftime("%m/%y")
            manifest["attribution"] += f" – Stand {stand}"
        # Одна атомарная замена переключает все справочники данного источника.
        sources.replace_one({"_id": source}, manifest, upsert=True)
        summary[source] = {"status": "imported", "entries": manifest["entries_count"], "datasets": counts}
        LOGGER.info("source=%s imported entries=%s", source, manifest["entries_count"])
    return summary


def parse_args() -> argparse.Namespace:
    load_dotenv(project_root() / ".env")
    parser = argparse.ArgumentParser(description="Получить ESCO/DKZ и импортировать неизменные данные в MongoDB.")
    parser.add_argument("--config", type=Path, default=Path("conf/reference-data.json"))
    parser.add_argument("--sources", choices=("both", "esco", "dkz"), default="both")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--esco-version", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--refresh", action="store_true", help="Повторно запросить закэшированные данные ESCO")
    parser.add_argument("--download-only", action="store_true", help="Скачать и проверить; без MongoDB")
    parser.add_argument("--lookup", default="", help="Только найти название в уже импортированных справочниках")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    config = json.loads(args.config.read_text())
    if args.lookup:
        source = None if args.sources == "both" else args.sources
        db = get_database(load_app_config())
        try:
            rows = ReferenceStore(db, config["storage"]).lookup(args.lookup, source=source)
            print(json.dumps([{**{key: row[key] for key in ("source", "dataset", "source_id", "labels")},
                               "dkz_state": row["raw"].get("attributes", {}).get("zustand")}
                              for row in rows], ensure_ascii=False, indent=2))
        finally:
            db.client.close()
        return
    work = args.work_dir or Path(os.getenv("REFERENCE_DATA_DIR", config["storage"]["work_dir"]))
    config["esco"]["version"] = args.esco_version or config["esco"]["version"]
    config["esco"]["language"] = args.language or config["esco"]["language"]
    import fcntl
    work.mkdir(parents=True, exist_ok=True)
    with (work / "reference_import.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Reference import is already running") from None
        _run_import(args, config, work)


def _run_import(args: argparse.Namespace, config: dict[str, Any], work: Path) -> None:
    downloader = SourceDownloader(**config["http"])
    datasets = []
    try:
        if args.sources in {"both", "esco"}:
            datasets.extend(download_esco(config["esco"], downloader, work, refresh=args.refresh))
        if args.sources in {"both", "dkz"}:
            datasets.extend(download_dkz(config["dkz"], downloader, work, refresh=args.refresh))
    finally:
        downloader.close()
    # Проверяем весь выбранный набор до первой записи в MongoDB.
    counts = {f"{dataset.source}:{dataset.name}": sum(1 for _ in iter_records(dataset)) for dataset in datasets}
    if not counts or not all(counts.values()):
        raise ValueError("Empty reference dataset")
    if args.download_only:
        print(json.dumps({"download_only": True, "mongo_writes": 0, "records": counts}, ensure_ascii=False))
        return
    db = get_database(load_app_config())
    try:
        print(json.dumps(import_datasets(db, datasets, config["storage"]), ensure_ascii=False))
    finally:
        db.client.close()


if __name__ == "__main__":
    main()
