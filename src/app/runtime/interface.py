from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AgentRuntimeInterface(Protocol):
    description: str

    def run(self, message: str, session_id: str) -> tuple[str, list[str]]:
        ...


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    runtime: str
    description: str


@dataclass(frozen=True)
class RuntimeAgentSpec:
    agent_id: str
    runtime: AgentRuntimeInterface
    runtime_type: str
    keywords: tuple[str, ...] = ()
    is_fallback: bool = False
