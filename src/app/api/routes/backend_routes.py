from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from src.app.api.app_context import AppContext
from src.app.api.responses import error_response
from src.app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    WebMeResponse,
    NewConversationResponse,
    ClearConversationResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    AgentsResponse,
)
from src.app.services.auth_service import LoginStatus, RegisterStatus


def build_router(context: AppContext) -> APIRouter:
    """Backend API routes (auth/session/chat) consumed by the web client."""
    router = APIRouter(tags=["backend"])
    logger = context.logger

    @router.get("/health")
    def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "web_app_url": context.settings.web_app_url,
            "web_app_port": context.settings.web_app_port,
        }

    @router.get("/agents", response_model=AgentsResponse)
    def agents() -> AgentsResponse:
        return AgentsResponse(
            active_agent_id=context.active_agent_id,
            agents=[
                {
                    "agent_id": agent.agent_id,
                    "runtime": agent.runtime,
                    "description": agent.description,
                }
                for agent in context.available_agents
            ],
        )

    @router.post("/web/login", response_model=LoginResponse, responses={401: {"model": ErrorResponse}})
    def login(payload: LoginRequest) -> Response:
        # Route only maps HTTP to service contract; auth logic lives in AuthService.
        status, token = context.auth_service.login_user(payload.username, payload.password)
        if status is not LoginStatus.SUCCESS or not token:
            return error_response(401, "invalid_credentials", "Invalid credentials")

        response = JSONResponse(content={"ok": True, "username": payload.username})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return response

    @router.post(
        "/web/register",
        response_model=RegisterResponse,
        responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def register(payload: RegisterRequest) -> Response:
        username = payload.username.strip()
        status = context.auth_service.register_user(username, payload.password)
        if status is RegisterStatus.USERNAME_TOO_SHORT:
            return error_response(400, "invalid_username", "Username must be at least 3 characters.")
        if status is RegisterStatus.PASSWORD_TOO_SHORT:
            return error_response(400, "invalid_password", "Password must be at least 6 characters.")
        if status is RegisterStatus.USER_EXISTS:
            return error_response(409, "username_exists", "Username already exists.")
        return JSONResponse(content={"ok": True, "username": username})

    @router.post("/web/logout")
    def logout(request: Request) -> Response:
        token = request.cookies.get("session_token")
        context.auth_service.logout_user(token)
        response = JSONResponse(content={"ok": True})
        response.delete_cookie("session_token")
        return response

    @router.get("/web/me", response_model=WebMeResponse, responses={401: {"model": ErrorResponse}})
    def me(request: Request) -> Response:
        token = request.cookies.get("session_token")
        username = context.auth_service.get_current_user(token)
        if not username:
            logger.debug("/web/me unauthorized request.")
            return error_response(401, "not_authenticated", "Not authenticated")
        logger.debug("/web/me for username='%s'.", username)
        session_id, history = context.chat_service.resolve_user_session_and_history(username)
        return JSONResponse(
            content={
                "username": username,
                "session_id": session_id,
                "history": history,
            }
        )

    @router.post(
        "/web/conversations/new",
        response_model=NewConversationResponse,
        responses={401: {"model": ErrorResponse}},
    )
    def create_new_conversation(request: Request) -> Response:
        token = request.cookies.get("session_token")
        username = context.auth_service.get_current_user(token)
        if not username:
            logger.debug("/web/conversations/new unauthorized request.")
            return error_response(401, "not_authenticated", "Not authenticated")

        session_id = context.chat_service.create_new_conversation(username)
        return JSONResponse(
            content={
                "ok": True,
                "session_id": session_id,
                "history": [],
            }
        )

    @router.post(
        "/web/conversations/clear",
        response_model=ClearConversationResponse,
        responses={401: {"model": ErrorResponse}},
    )
    def clear_conversation(request: Request) -> Response:
        token = request.cookies.get("session_token")
        username = context.auth_service.get_current_user(token)
        if not username:
            logger.debug("/web/conversations/clear unauthorized request.")
            return error_response(401, "not_authenticated", "Not authenticated")

        session_id = context.chat_service.clear_conversation(username)
        return JSONResponse(
            content={
                "ok": True,
                "session_id": session_id,
                "history": [],
            }
        )

    @router.post("/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        token = request.cookies.get("session_token")
        username = context.auth_service.get_current_user(token)
        if username:
            session_id, answer, trace = context.chat_service.process_for_user(
                username=username,
                message=payload.message,
            )
        else:
            session_id = payload.session_id
            answer, trace = context.chat_service.process_ephemeral(
                session_id=session_id,
                message=payload.message,
            )
        return ChatResponse(
            session_id=session_id,
            answer=answer,
            trace=trace,
        )

    return router
