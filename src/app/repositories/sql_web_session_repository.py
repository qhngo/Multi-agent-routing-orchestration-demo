from __future__ import annotations

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.repositories.interfaces import WebSessionRepository


class SQLWebSessionRepository(WebSessionRepository):
    def __init__(self, sql: SQLInterface) -> None:
        self._sql = sql

    def initialize(self) -> None:
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def create_session(self, username: str, token: str) -> None:
        self._sql.execute(
            "INSERT INTO web_sessions (token, username) VALUES (?, ?)",
            (token, username),
        )

    def get_username(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self._sql.fetchone(
            "SELECT username FROM web_sessions WHERE token = ?",
            (token,),
        )
        if not row:
            return None
        return str(row[0])

    def delete_session(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self._sql.fetchone(
            "SELECT username FROM web_sessions WHERE token = ?",
            (token,),
        )
        self._sql.execute("DELETE FROM web_sessions WHERE token = ?", (token,))
        if not row:
            return None
        return str(row[0])
