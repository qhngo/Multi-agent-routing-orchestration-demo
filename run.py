from __future__ import annotations

import os

import uvicorn

from src.app.config.settings import load_settings
from src.app.config.worker_config import calculate_worker_count


FORCED_WORKERS: int | None = 4
ALLOW_WINDOWS_MULTIWORKER = True


def main() -> None:
    settings = load_settings()
    workers = calculate_worker_count(
        forced_workers=FORCED_WORKERS,
        allow_windows_multiworker=ALLOW_WINDOWS_MULTIWORKER,
    )
    worker_label = "worker" if workers == 1 else "workers"
    windows_note = ""
    if os.name == "nt" and workers == 1 and FORCED_WORKERS is None and not ALLOW_WINDOWS_MULTIWORKER:
        windows_note = " (Windows safety mode)"
    print(
        f"Starting server on {settings.web_app_host}:{settings.web_app_port} "
        f"with {workers} {worker_label}{windows_note}."
    )
    uvicorn.run(
        "src.app.api.main:app",
        host=settings.web_app_host,
        port=settings.web_app_port,
        workers=workers,
    )


if __name__ == "__main__":
    main()
