"""Structured error handling for the MCP. Every uncaught exception is
translated into a JSON envelope ``{success:false, error:{code, message, trace_id}}``
and logged with a correlation id.
"""
from __future__ import annotations

import uuid

from app.core.logging import get_logger
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = get_logger(__name__)


def _envelope(status_code: int, code: str, message: str, trace_id: str, **extra: Any) -> JSONResponse:
    body = {
        "success": False,
        "error": {"code": code, "message": message, "trace_id": trace_id, **extra},
    }
    return JSONResponse(status_code=status_code, content=body)


def _trace_id(request: Request) -> str:
    return request.headers.get("x-request-id") or uuid.uuid4().hex


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        trace = _trace_id(request)
        log.warning(
            "http_error",
            extra={
                "trace_id": trace,
                "path": request.url.path,
                "method": request.method,
                "status": exc.status_code,
                "detail": str(exc.detail)[:200],
                "client": request.client.host if request.client else None,
                "ua": request.headers.get("user-agent", "")[:120],
            },
        )
        return _envelope(exc.status_code, "http_error", str(exc.detail), trace)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace = _trace_id(request)
        log.warning("validation_error", extra={"trace_id": trace, "path": request.url.path})
        return _envelope(
            422, "validation_error", "Request payload failed validation", trace, errors=exc.errors()
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_error(request: Request, exc: ValidationError) -> JSONResponse:
        trace = _trace_id(request)
        log.warning("pydantic_error", extra={"trace_id": trace})
        return _envelope(422, "validation_error", "Invalid data", trace, errors=exc.errors())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        trace = _trace_id(request)
        log.exception("unhandled_error", extra={"trace_id": trace, "path": request.url.path})
        return _envelope(500, "internal_error", "Internal server error", trace)
