from __future__ import annotations

from pathlib import Path

from src.app.infrastructure.sql.interface import SQLInterface
from src.app.infrastructure.sql.sqlite_provider import SQLiteProvider


class SQLProviderFactory:
    def __init__(self, provider_name: str, base_dir: Path) -> None:
        self._provider_name = provider_name.lower()
        self._base_dir = base_dir

    def create(self, database_name: str) -> SQLInterface:
        if self._provider_name == "sqlite":
            db_path = self._base_dir / f"{database_name}.db"
            return SQLiteProvider(db_path=db_path)
        raise ValueError(f"Unsupported SQL provider '{self._provider_name}'")
