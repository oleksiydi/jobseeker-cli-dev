#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.normalization.synonyms_contract import (
    load_allowed_domains,
    validate_dictionary_payload,
    validate_pending_record,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate synonyms pending/dictionary contract.")
    parser.add_argument(
        "--mode",
        choices=("pending", "dictionary"),
        required=True,
        help="Input type: pending JSONL records or dictionary JSON payload.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Input file path.")
    parser.add_argument(
        "--domains-file",
        type=Path,
        default=Path("conf/normalization/domains.level1.de.json"),
        help="Allowed domain list source.",
    )
    return parser.parse_args()


def _validate_pending(path: Path, allowed_domains: set[str]) -> tuple[int, int]:
    total = 0
    errors = 0
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors += 1
            print(f"line={line_no} invalid_json={exc}")
            continue
        rec_errors = validate_pending_record(record, allowed_domains=allowed_domains)
        if rec_errors:
            errors += 1
            print(f"line={line_no} errors={'; '.join(rec_errors)}")
    return total, errors


def _validate_dictionary(path: Path, allowed_domains: set[str]) -> tuple[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dictionary_payload(payload, allowed_domains=allowed_domains)
    if errors:
        for err in errors:
            print(err)
        return 1, len(errors)
    entries = payload.get("entries", [])
    return len(entries), 0


def main() -> None:
    args = parse_args()
    allowed_domains = load_allowed_domains(args.domains_file)
    if args.mode == "pending":
        total, errors = _validate_pending(args.input, allowed_domains)
    else:
        total, errors = _validate_dictionary(args.input, allowed_domains)

    print(
        f"validation_done mode={args.mode} input={args.input} total={total} errors={errors} "
        f"allowed_domains={len(allowed_domains)}"
    )
    if errors > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
