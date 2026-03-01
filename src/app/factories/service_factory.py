from __future__ import annotations

import logging
from dataclasses import dataclass

from src.app.config.settings import AppSettings
from src.app.infrastructure.sql.factory import SQLProviderFactory
from src.app.repositories.sql_conversation_repository import SQLConversationRepository
from src.app.repositories.sql_conversation_state_repository import SQLConversationStateRepository
from src.app.repositories.sql_user_repository import SQLUserRepository
from src.app.repositories.sql_user_session_repository import SQLUserSessionRepository
from src.app.repositories.sql_web_session_repository import SQLWebSessionRepository
from src.app.runtime.agent_loader import discover_runtime_agent_specs
from src.app.runtime.agent_registry import AgentRegistry, RegisteredAgent
from src.app.runtime.agent_router import AgentRouter
from src.app.runtime.interface import AgentDescriptor, RuntimeAgentSpec
from src.app.runtime.routed_runtime import RoutedRuntime
from src.app.security.pbkdf2_hasher import PBKDF2PasswordHasher
from src.app.services.auth_service import AuthService
from src.app.services.chat_service import ChatService


@dataclass
class ServiceBundle:
    auth_service: AuthService
    chat_service: ChatService
    available_agents: list[AgentDescriptor]
    active_agent_id: str


def build_services(settings: AppSettings) -> ServiceBundle:
    core_logger = logging.getLogger("src.app.core")

    sql_factory = SQLProviderFactory(
        provider_name=settings.sql_provider,
        base_dir=settings.root_dir / "local_db",
    )

    shared_sql = sql_factory.create("app")
    user_repo = SQLUserRepository(shared_sql)
    web_session_repo = SQLWebSessionRepository(shared_sql)
    user_session_repo = SQLUserSessionRepository(shared_sql)
    conversation_repo = SQLConversationRepository(shared_sql)
    conversation_state_repo = SQLConversationStateRepository(shared_sql)

    user_repo.initialize()
    web_session_repo.initialize()
    user_session_repo.initialize()
    conversation_repo.initialize()
    conversation_state_repo.initialize()

    registry = _build_agent_registry(settings)

    hasher = PBKDF2PasswordHasher()
    runtime = RoutedRuntime(
        registry=registry,
        router=AgentRouter(
            registry=registry,
            local_api_url=settings.local_api_url,
            local_api_timeout_seconds=settings.local_api_timeout_seconds,
            logger=core_logger,
        ),
        conversation_repo=conversation_repo,
        conversation_state_repo=conversation_state_repo,
        logger=core_logger,
        local_api_url=settings.local_api_url,
        local_api_timeout_seconds=settings.local_api_timeout_seconds,
    )

    auth_service = AuthService(
        user_repo=user_repo,
        web_session_repo=web_session_repo,
        password_hasher=hasher,
        logger=logging.getLogger("src.app.auth"),
    )
    chat_service = ChatService(
        logger=core_logger,
        user_session_repo=user_session_repo,
        conversation_repo=conversation_repo,
        runtime=runtime,
        last_interaction_threshold_days=settings.last_interaction_threshold_days,
    )

    return ServiceBundle(
        auth_service=auth_service,
        chat_service=chat_service,
        available_agents=registry.list_descriptors(),
        active_agent_id="router",
    )


def _build_agent_registry(settings: AppSettings) -> AgentRegistry:
    logger = logging.getLogger("src.app.core")
    specs = discover_runtime_agent_specs(
        settings=settings,
        logger=logger,
    )
    fallback_agent_id = _resolve_fallback_agent_id(specs)

    return AgentRegistry(
        agents=[
            RegisteredAgent(
                agent_id=spec.agent_id,
                runtime=spec.runtime,
                runtime_type=spec.runtime_type,
                description=spec.runtime.description,
                keywords=spec.keywords,
            )
            for spec in specs
        ],
        fallback_agent_id=fallback_agent_id,
        logger=logger,
    )


def _resolve_fallback_agent_id(specs: list[RuntimeAgentSpec]) -> str:
    fallback_specs = [spec for spec in specs if getattr(spec, "is_fallback", False)]
    if len(fallback_specs) == 1:
        return fallback_specs[0].agent_id
    if len(fallback_specs) > 1:
        raise ValueError("Multiple fallback agents discovered. Mark only one spec with is_fallback=True.")
    for spec in specs:
        if spec.agent_id == "generic":
            return "generic"
    raise ValueError("No fallback agent discovered. Add one RuntimeAgentSpec with is_fallback=True.")
