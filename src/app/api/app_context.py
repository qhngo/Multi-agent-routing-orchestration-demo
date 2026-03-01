from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.app.config.settings import AppSettings
from src.app.runtime.interface import AgentDescriptor
from src.app.services.auth_service import AuthService
from src.app.services.chat_service import ChatService


@dataclass
class AppContext:
    settings: AppSettings
    logger: logging.Logger
    static_dir: Path
    auth_service: AuthService
    chat_service: ChatService
    available_agents: list[AgentDescriptor]
    active_agent_id: str
