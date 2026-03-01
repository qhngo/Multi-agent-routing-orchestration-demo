from __future__ import annotations

from fastapi.responses import JSONResponse


def error_response(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "detail": detail},
    )
