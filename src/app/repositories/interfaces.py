from __future__ import annotations

from typing import Any
from typing import Protocol


class UserRepository(Protocol):
    def initialize(self) -> None:
        ...

    def create_user(self, username: str, password_hash: str) -> bool:
        ...

    def get_password_hash(self, username: str) -> str | None:
        ...


class WebSessionRepository(Protocol):
    def initialize(self) -> None:
        ...

    def create_session(self, username: str, token: str) -> None:
        ...

    def get_username(self, token: str | None) -> str | None:
        ...

    def delete_session(self, token: str | None) -> str | None:
        ...


class UserSessionRepository(Protocol):
    def initialize(self) -> None:
        ...

    def create_new_session(self, username: str) -> str:
        ...

    def get_or_create_active_session(
        self, username: str, threshold_days: int
    ) -> tuple[str, bool]:
        ...

    def touch_session(self, session_id: str) -> None:
        ...


class ConversationRepository(Protocol):
    def initialize(self) -> None:
        ...

    def add_message(
        self,
        session_id: str,
        creator: str,
        message: str,
        processing_time_s: float | None = None,
        total_tokens: int | None = None,
        handling_agent: str | None = None,
    ) -> None:
        ...

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        ...

    def clear_history(self, session_id: str) -> int:
        ...


class ConversationStateRepository(Protocol):
    def initialize(self) -> None:
        ...

    def get_last_selected_agent(self, session_id: str) -> str | None:
        ...

    def set_last_selected_agent(self, session_id: str, agent_id: str) -> None:
        ...
