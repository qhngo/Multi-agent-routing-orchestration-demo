from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_frontend_assets(app: FastAPI, static_dir: Path) -> None:
    """Mount frontend static assets. Keeps app bootstrap frontend-agnostic."""
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
