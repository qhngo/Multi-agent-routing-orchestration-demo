from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from src.app.api.app_context import AppContext
from src.app.api.frontend_adapter import mount_frontend_assets
from src.app.api.middleware.request_context_middleware import RequestContextMiddleware
from src.app.api.routes.router_loader import load_routers
from src.app.config.logging_config import configure_logging
from src.app.config.settings import load_settings
from src.app.factories.service_factory import build_services


def create_app() -> FastAPI:
    """Create and wire the FastAPI app with config, services, and routes."""
    settings = load_settings()
    logs_root = configure_logging(
        root_dir=settings.root_dir,
        app_log_level=settings.app_log_level,
        log_retention_days=settings.log_retention_days,
    )
    logger = logging.getLogger(__name__)
    logger.info(
        "App settings loaded. url=%s port=%s host=%s log_retention_days=%s log_root=%s",
        settings.web_app_url,
        settings.web_app_port,
        settings.web_app_host,
        settings.log_retention_days,
        logs_root,
    )

    app = FastAPI(title="Agentic System Skeleton")
    app.add_middleware(RequestContextMiddleware)
    static_dir = Path(__file__).resolve().parents[1] / "web" / "static"
    # Frontend assets are served directly by FastAPI for local web UI pages.
    mount_frontend_assets(app, static_dir)
    logger.info("Static directory mounted at '%s'.", static_dir)

    # Application services and adapters are composed in a centralized factory.
    service_bundle = build_services(settings)

    context = AppContext(
        settings=settings,
        logger=logger,
        static_dir=static_dir,
        auth_service=service_bundle.auth_service,
        chat_service=service_bundle.chat_service,
        available_agents=service_bundle.available_agents,
        active_agent_id=service_bundle.active_agent_id,
    )
    logger.info(
        "Agent catalog initialized. active_agent='%s' available_agents=%s",
        context.active_agent_id,
        [agent.agent_id for agent in context.available_agents],
    )
    logger.info("Available agents:")
    for agent in context.available_agents:
        logger.info(
            "  - agent_id='%s' runtime='%s' description='%s'",
            agent.agent_id,
            agent.runtime,
            agent.description,
        )
    app.state.context = context

    # Route modules are auto-discovered and mounted dynamically.
    for router in load_routers("src.app.api.routes", context):
        app.include_router(router)

    return app


app = create_app()
