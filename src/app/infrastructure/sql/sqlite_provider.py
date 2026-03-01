from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.app.infrastructure.sql.interface import SQLInterface


class SQLiteProvider(SQLInterface):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(query, params)
            conn.commit()

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return list(rows)
