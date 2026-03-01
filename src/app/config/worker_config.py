from __future__ import annotations

import os


def calculate_worker_count(
    total_cores: int | None = None,
    forced_workers: int | None = None,
    allow_windows_multiworker: bool = False,
) -> int:
    """
    Worker policy:
    - start from all logical CPU cores
    - reserve 3 cores for OS/background tasks
    - cap workers at 4
    - always keep at least 1 worker
    """
    if forced_workers is not None:
        return max(1, forced_workers)

    # Uvicorn multi-worker socket sharing is often unstable on Windows.
    # Keep single-worker default unless explicitly allowed.
    if os.name == "nt" and not allow_windows_multiworker:
        return 1

    cores = total_cores if total_cores is not None else (os.cpu_count() or 1)
    return max(1, min(4, cores - 3))
