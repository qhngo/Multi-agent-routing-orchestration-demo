from __future__ import annotations

import logging
import multiprocessing
import os
import re
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from src.app.api.request_context import request_id_ctx


class WorkerIdFilter(logging.Filter):
    """Inject worker identifier into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        process_name = multiprocessing.current_process().name
        match = re.search(r"(\d+)$", process_name)
        if match:
            record.worker_id = match.group(1)
        else:
            record.worker_id = str(os.getpid())
        record.request_id = request_id_ctx.get()
        return True


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    Windows-safe timed file rotation for multi-worker processes.

    TimedRotatingFileHandler is not process-safe; on Windows, another worker can
    hold the file handle during rollover and raise PermissionError. In that case,
    keep writing to the current file and schedule the next rollover check.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # Another process is likely rotating or holding the file. Avoid
            # crashing logging and retry rollover on a later emit.
            self.rolloverAt = self.computeRollover(int(time.time()))


def configure_logging(
    root_dir: Path,
    app_log_level: str,
    log_retention_days: int,
) -> Path:
    log_level = getattr(logging, app_log_level, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [worker=%(worker_id)s] [request_id=%(request_id)s] [%(name)s] %(message)s"
    )
    worker_filter = WorkerIdFilter()

    logs_dir = root_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    stream_handlers = [
        handler
        for handler in root_logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    has_stream_handler = bool(stream_handlers)
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(log_level)
        stream_handler.addFilter(worker_filter)
        root_logger.addHandler(stream_handler)
    else:
        for stream_handler in stream_handlers:
            stream_handler.setFormatter(formatter)
            stream_handler.setLevel(log_level)
            stream_handler.addFilter(worker_filter)

    module_files = {
        "src.app.api": logs_dir / "api" / "api.log",
        "src.app.auth": logs_dir / "auth" / "auth.log",
        "src.app.core": logs_dir / "core" / "core.log",
        "src.app.factories": logs_dir / "factories" / "factories.log",
        "src.app.tools": logs_dir / "tools" / "tools.log",
    }

    for module_name, module_log_file in module_files.items():
        module_log_file.parent.mkdir(parents=True, exist_ok=True)
        module_logger = logging.getLogger(module_name)
        module_logger.setLevel(log_level)
        module_logger.propagate = True

        file_exists = any(
            isinstance(handler, SafeTimedRotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")) == module_log_file
            for handler in module_logger.handlers
        )
        if file_exists:
            continue

        file_handler = SafeTimedRotatingFileHandler(
            filename=str(module_log_file),
            when="midnight",
            interval=1,
            backupCount=log_retention_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        file_handler.addFilter(worker_filter)
        module_logger.addHandler(file_handler)

    return logs_dir
