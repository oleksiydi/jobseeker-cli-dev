"""Prepare a deterministic, local-only vacancy fixture from the joined export."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "exports/regensburg_full_join.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "logs/local-intelligence/demo-vacancies.json"
RELEVANCE_FILE = PROJECT_ROOT / "conf/local-demo/relevance.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write the fixed local-intelligence demo vacancy fixture."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_source(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Joined vacancy source must be a JSON list")

    records: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        reference_id = str(row.get("referenznummer") or "").strip()
        if reference_id:
            records.setdefault(reference_id, row)
    return records


def _duplicate_with_new_ids(
    original: dict[str, Any], source_id: str, duplicate_id: str
) -> dict[str, Any]:
    duplicate = copy.deepcopy(original)
    duplicate["referenznummer"] = duplicate_id
    if "_id" in duplicate:
        duplicate["_id"] = f"local-demo:{duplicate_id}:joined"

    for section_name in ("stellenangebote", "jobdetails"):
        section = duplicate.get(section_name)
        if not isinstance(section, dict):
            continue
        suffix = "meta" if section_name == "stellenangebote" else "details"
        for key in ("referenznummer", "refnr"):
            if key in section:
                section[key] = duplicate_id
        if "_id" in section:
            section["_id"] = f"local-demo:{duplicate_id}:{suffix}"

    if duplicate.get("referenznummer") == source_id:
        raise AssertionError("Duplicate reference id was not replaced")
    return duplicate


def prepare(source: Path, output: Path) -> dict[str, int]:
    manifest = json.loads(RELEVANCE_FILE.read_text(encoding="utf-8"))
    reference_ids = manifest["selected_reference_ids"]
    duplicate_spec = manifest["duplicate_test"]
    duplicate_source_id = duplicate_spec["source_reference_id"]
    duplicate_id = duplicate_spec["synthetic_reference_id"]
    records_by_id = _read_source(source)

    missing = [reference_id for reference_id in reference_ids if reference_id not in records_by_id]
    if missing:
        raise ValueError(f"Source is missing {len(missing)} fixed reference IDs")
    if duplicate_source_id not in records_by_id:
        raise ValueError("Duplicate source reference ID is missing")
    if duplicate_id in records_by_id or duplicate_id in reference_ids:
        raise ValueError("Synthetic duplicate ID collides with a source reference ID")

    fixture = [copy.deepcopy(records_by_id[reference_id]) for reference_id in reference_ids]
    fixture.append(
        _duplicate_with_new_ids(
            records_by_id[duplicate_source_id], duplicate_source_id, duplicate_id
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"selected": len(reference_ids), "duplicates_added": 1, "written": len(fixture)}


def main() -> None:
    args = parse_args()
    counts = prepare(args.source, args.out)
    print(
        f"selected={counts['selected']} duplicates_added={counts['duplicates_added']} "
        f"written={counts['written']} output={args.out}"
    )


if __name__ == "__main__":
    main()
