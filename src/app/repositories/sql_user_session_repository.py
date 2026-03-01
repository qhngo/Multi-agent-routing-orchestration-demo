from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.repositories.interfaces import UserSessionRepository


class SQLUserSessionRepository(UserSessionRepository):
    def __init__(self, sql: SQLInterface) -> None:
        self._sql = sql

    def initialize(self) -> None:
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                created_date TEXT NOT NULL,
                last_interaction_date TEXT NOT NULL
            )
            """
        )
        self._sql.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_sessions_username "
            "ON user_sessions(username)"
        )

    def get_or_create_active_session(
        self, username: str, threshold_days: int
    ) -> tuple[str, bool]:
        latest = self._latest(username)
        now = datetime.now(timezone.utc)
        if latest:
            last_interaction = datetime.fromisoformat(latest["last_interaction_date"])
            if now - last_interaction <= timedelta(days=threshold_days):
                self.touch_session(latest["session_id"])
                return latest["session_id"], False

        return self.create_new_session(username), True

    def create_new_session(self, username: str) -> str:
        session_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        self._sql.execute(
            """
            INSERT INTO user_sessions (username, session_id, created_date, last_interaction_date)
            VALUES (?, ?, ?, ?)
            """,
            (username, session_id, now_iso, now_iso),
        )
        return session_id

    def touch_session(self, session_id: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self._sql.execute(
            """
            UPDATE user_sessions
            SET last_interaction_date = ?
            WHERE session_id = ?
            """,
            (now_iso, session_id),
        )

    def _latest(self, username: str) -> dict[str, str] | None:
        row = self._sql.fetchone(
            """
            SELECT session_id, last_interaction_date
            FROM user_sessions
            WHERE username = ?
            ORDER BY last_interaction_date DESC
            LIMIT 1
            """,
            (username,),
        )
        if not row:
            return None
        return {"session_id": str(row[0]), "last_interaction_date": str(row[1])}
