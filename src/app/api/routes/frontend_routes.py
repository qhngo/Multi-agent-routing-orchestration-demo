from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from src.app.api.app_context import AppContext


def build_router(context: AppContext) -> APIRouter:
    """Frontend page routes (HTML redirects/pages), separate from JSON APIs."""
    router = APIRouter(tags=["frontend"])

    @router.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/login")

    @router.get("/login")
    def login_page() -> FileResponse:
        return FileResponse(context.static_dir / "login.html")

    @router.get("/app")
    def app_page(request: Request) -> Response:
        token = request.cookies.get("session_token")
        # Guard the chat page behind an authenticated session.
        if not context.auth_service.get_current_user(token):
            return RedirectResponse(url="/login")
        return FileResponse(context.static_dir / "chat.html")

    @router.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    return router
