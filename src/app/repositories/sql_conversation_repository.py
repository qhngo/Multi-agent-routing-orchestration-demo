from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.repositories.interfaces import ConversationRepository


class SQLConversationRepository(ConversationRepository):
    def __init__(self, sql: SQLInterface) -> None:
        self._sql = sql

    def initialize(self) -> None:
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                creator TEXT NOT NULL,
                message TEXT NOT NULL,
                handling_agent TEXT,
                processing_time_s REAL,
                total_tokens INTEGER,
                created_date TEXT NOT NULL,
                updated_date TEXT NOT NULL
            )
            """
        )
        if not self._has_column("handling_agent"):
            self._sql.execute("ALTER TABLE conversations ADD COLUMN handling_agent TEXT")
        if not self._has_column("processing_time_s"):
            self._sql.execute("ALTER TABLE conversations ADD COLUMN processing_time_s REAL")
        if not self._has_column("total_tokens"):
            self._sql.execute("ALTER TABLE conversations ADD COLUMN total_tokens INTEGER")
        self._sql.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_session_id "
            "ON conversations(session_id)"
        )

    def add_message(
        self,
        session_id: str,
        creator: str,
        message: str,
        processing_time_s: float | None = None,
        total_tokens: int | None = None,
        handling_agent: str | None = None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self._sql.execute(
            """
            INSERT INTO conversations (
                session_id,
                creator,
                message,
                handling_agent,
                processing_time_s,
                total_tokens,
                created_date,
                updated_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                creator,
                message,
                handling_agent,
                processing_time_s,
                total_tokens,
                now_iso,
                now_iso,
            ),
        )

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._sql.fetchall(
            """
            SELECT creator, message, handling_agent, processing_time_s, total_tokens, created_date, updated_date
            FROM conversations
            WHERE session_id = ?
            ORDER BY created_date ASC, id ASC
            """,
            (session_id,),
        )
        return [
            {
                "creator": str(row[0]),
                "message": str(row[1]),
                "handling_agent": str(row[2]) if row[2] is not None else None,
                "processing_time_s": float(row[3]) if row[3] is not None else None,
                "total_tokens": int(row[4]) if row[4] is not None else None,
                "created_date": str(row[5]),
                "updated_date": str(row[6]),
            }
            for row in rows
        ]

    def clear_history(self, session_id: str) -> int:
        row = self._sql.fetchone(
            """
            SELECT COUNT(*)
            FROM conversations
            WHERE session_id = ?
            """,
            (session_id,),
        )
        deleted_count = int(row[0]) if row else 0
        self._sql.execute(
            """
            DELETE FROM conversations
            WHERE session_id = ?
            """,
            (session_id,),
        )
        return deleted_count

    def _has_column(self, column_name: str) -> bool:
        rows = self._sql.fetchall("PRAGMA table_info(conversations)")
        for row in rows:
            if str(row[1]) == column_name:
                return True
        return False
