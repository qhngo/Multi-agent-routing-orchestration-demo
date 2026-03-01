from __future__ import annotations

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.repositories.interfaces import UserRepository


class SQLUserRepository(UserRepository):
    def __init__(self, sql: SQLInterface) -> None:
        self._sql = sql

    def initialize(self) -> None:
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS authentication (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def create_user(self, username: str, password_hash: str) -> bool:
        try:
            self._sql.execute(
                """
                INSERT INTO authentication (username, password_hash, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (username, password_hash),
            )
            return True
        except Exception:
            return False

    def get_password_hash(self, username: str) -> str | None:
        row = self._sql.fetchone(
            "SELECT password_hash FROM authentication WHERE username = ?",
            (username,),
        )
        if not row:
            return None
        return str(row[0])
