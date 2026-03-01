from __future__ import annotations

from typing import Protocol, Any


class SQLInterface(Protocol):
    """Minimal SQL operations used by repository/store classes."""

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        ...

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        ...

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        ...
