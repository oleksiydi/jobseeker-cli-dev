"""Кэш проверенных LLM-ответов: одинаковый вход не требует нового API-вызова."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def input_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ArtifactCache:
    """SQLite хранит только технический кэш; Mongo остаётся хранилищем решений."""

    def __init__(self, path: Path | None) -> None:
        self.connection = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path)
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS responses "
                "(input_hash TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def get(self, key: str) -> dict[str, Any] | None:
        if self.connection is None:
            return None
        row = self.connection.execute(
            "SELECT payload FROM responses WHERE input_hash = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        if self.connection is not None:
            with self.connection:
                self.connection.execute(
                    "INSERT OR REPLACE INTO responses VALUES (?, ?)",
                    (key, json.dumps(payload, ensure_ascii=False)),
                )

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
