from __future__ import annotations

import logging
from dataclasses import dataclass

from src.app.runtime.interface import AgentDescriptor, AgentRuntimeInterface


@dataclass
class RegisteredAgent:
    agent_id: str
    runtime: AgentRuntimeInterface
    runtime_type: str
    description: str
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        runtime_description = getattr(self.runtime, "description", None)
        if not isinstance(runtime_description, str) or not runtime_description.strip():
            raise ValueError(
                f"Agent '{self.agent_id}' runtime must define a non-empty 'description' attribute."
            )
        # Keep registry description aligned with runtime-owned description.
        self.description = runtime_description.strip()

    def to_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=self.agent_id,
            runtime=self.runtime_type,
            description=self.description,
        )


class AgentRegistry:
    def __init__(
        self,
        agents: list[RegisteredAgent],
        fallback_agent_id: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger("src.app.core")
        self._agents_by_id = {agent.agent_id: agent for agent in agents}
        if fallback_agent_id not in self._agents_by_id:
            raise ValueError(f"Fallback agent '{fallback_agent_id}' is not registered.")
        self._fallback_agent_id = fallback_agent_id
        self._logger.info(
            "Agent registry initialized. fallback_agent='%s' total_agents=%s agents=%s",
            self._fallback_agent_id,
            len(self._agents_by_id),
            sorted(self._agents_by_id.keys()),
        )

    @property
    def fallback_agent_id(self) -> str:
        return self._fallback_agent_id

    def get_agent(self, agent_id: str) -> RegisteredAgent:
        agent = self._agents_by_id.get(agent_id)
        if not agent:
            self._logger.warning("Requested unknown agent from registry. agent_id='%s'", agent_id)
            raise KeyError(f"Agent '{agent_id}' is not registered.")
        return agent

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self._agents_by_id

    def list_agents(self) -> list[RegisteredAgent]:
        return [self._agents_by_id[agent_id] for agent_id in sorted(self._agents_by_id.keys())]

    def list_descriptors(self) -> list[AgentDescriptor]:
        return [agent.to_descriptor() for agent in self.list_agents()]

    def list_non_fallback_agents(self) -> list[RegisteredAgent]:
        return [
            agent
            for agent in self.list_agents()
            if agent.agent_id != self._fallback_agent_id
        ]
