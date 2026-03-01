from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(default="default-session")
    message: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    trace: list[str]


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    username: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class RegisterResponse(BaseModel):
    ok: bool
    username: str


class ErrorResponse(BaseModel):
    code: str
    detail: str


class HistoryItem(BaseModel):
    creator: str
    message: str
    handling_agent: str | None = None
    processing_time_s: float | None = None
    total_tokens: int | None = None
    created_date: str
    updated_date: str


class WebMeResponse(BaseModel):
    username: str
    session_id: str
    history: list[HistoryItem]


class NewConversationResponse(BaseModel):
    ok: bool
    session_id: str
    history: list[HistoryItem]


class ClearConversationResponse(BaseModel):
    ok: bool
    session_id: str
    history: list[HistoryItem]


class AgentItem(BaseModel):
    agent_id: str
    runtime: str
    description: str


class AgentsResponse(BaseModel):
    active_agent_id: str
    agents: list[AgentItem]
