from __future__ import annotations

from datetime import datetime, timezone

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.repositories.interfaces import ConversationStateRepository


class SQLConversationStateRepository(ConversationStateRepository):
    def __init__(self, sql: SQLInterface) -> None:
        self._sql = sql

    def initialize(self) -> None:
        self._sql.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_state (
                session_id TEXT PRIMARY KEY,
                last_selected_agent TEXT NOT NULL,
                updated_date TEXT NOT NULL
            )
            """
        )

    def get_last_selected_agent(self, session_id: str) -> str | None:
        row = self._sql.fetchone(
            """
            SELECT last_selected_agent
            FROM conversation_state
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if not row:
            return None
        value = str(row[0]).strip()
        return value or None

    def set_last_selected_agent(self, session_id: str, agent_id: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self._sql.execute(
            """
            INSERT INTO conversation_state (session_id, last_selected_agent, updated_date)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_selected_agent = excluded.last_selected_agent,
                updated_date = excluded.updated_date
            """,
            (session_id, agent_id, now_iso),
        )
