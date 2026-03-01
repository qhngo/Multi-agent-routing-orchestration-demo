from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppSettings:
    root_dir: Path
    web_app_url: str
    web_app_port: int
    web_app_host: str
    app_log_level: str
    log_retention_days: int
    last_interaction_threshold_days: int
    sql_provider: str
    local_api_url: str
    local_api_timeout_seconds: float


def load_settings() -> AppSettings:
    root_dir = Path(__file__).resolve().parents[3]
    load_dotenv(root_dir / ".env")

    web_app_url = os.getenv("WEB_APP_URL", "http://127.0.0.1")
    web_app_port = int(os.getenv("WEB_APP_PORT", "8000"))
    parsed_url = urlparse(web_app_url)
    web_app_host = parsed_url.hostname or "127.0.0.1"
    app_log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    retention_raw = os.getenv("LOG_RETENTION", "7")
    log_retention_days = max(1, int(retention_raw))
    interaction_raw = os.getenv("LAST_INTERACTION_THRESHOLD", "7")
    last_interaction_threshold_days = max(1, int(interaction_raw))
    sql_provider = os.getenv("SQL_PROVIDER", "sqlite")
    local_api_url = os.getenv("LOCAL_API_URL") or os.getenv("ANSWER_API_URL", "http://127.0.0.1:8081/answer")
    local_api_timeout_seconds = float(
        os.getenv("LOCAL_API_TIMEOUT_SECONDS") or os.getenv("ANSWER_API_TIMEOUT_SECONDS", "20")
    )

    return AppSettings(
        root_dir=root_dir,
        web_app_url=web_app_url,
        web_app_port=web_app_port,
        web_app_host=web_app_host,
        app_log_level=app_log_level,
        log_retention_days=log_retention_days,
        last_interaction_threshold_days=last_interaction_threshold_days,
        sql_provider=sql_provider,
        local_api_url=local_api_url,
        local_api_timeout_seconds=local_api_timeout_seconds,
    )
