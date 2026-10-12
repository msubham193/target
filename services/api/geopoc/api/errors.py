"""Consistent error responses (RFC 9457 problem details) carrying a stable code and the request ID."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("geopoc.api")

PROBLEM_MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: str
    request_id: str | None = None
    errors: list[dict[str, str]] = []


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors or []


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI documentation for the error statuses a route can return."""
    schema = Problem.model_json_schema()
    return {
        status: {"description": "Error", "content": {PROBLEM_MEDIA_TYPE: {"schema": schema}}}
        for status in statuses
    }


def _respond(request: Request, status: int, code: str, title: str, detail=None, errors=None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = Problem(
        title=title, status=status, detail=detail, code=code, request_id=request_id, errors=errors or []
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(body.model_dump(), status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _respond(request, exc.status, exc.code, exc.title, exc.detail, exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": str(err["msg"])}
            for err in exc.errors()
        ]
        return _respond(request, 422, "VALIDATION_ERROR", "Request validation failed", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        code = codes.get(exc.status_code, "HTTP_ERROR")
        return _respond(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Details go to the log, not to the client.
        logger.exception("Unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return _respond(request, 500, "INTERNAL_ERROR", "Internal server error")
