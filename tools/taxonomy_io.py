#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.taxonomy_transform import now_utc_iso


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_process_log(path: Path, payload: dict[str, Any], *, level: str = "summary") -> None:
    event = str(payload.get("event") or "")
    if level == "summary" and event == "term_normalized":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row["ts"] = now_utc_iso()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_checkpoint(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    seen: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ref = str(row.get("referenznummer") or "").strip()
            status = str(row.get("status") or "").strip()
            if ref and status:
                seen[ref] = status
    return seen


def append_checkpoint(path: Path, *, run_id: str, referenznummer: str, status: str) -> None:
    append_jsonl(
        path,
        [
            {
                "run_id": run_id,
                "referenznummer": referenznummer,
                "status": status,
                "created_at": now_utc_iso(),
            }
        ],
    )
