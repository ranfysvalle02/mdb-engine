"""
Global error handlers for MDB-Engine exceptions.

Registers FastAPI exception handlers that convert ``MongoDBEngineError``
subclasses into structured JSON responses with appropriate HTTP status codes.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..exceptions import (
    ConfigurationError,
    InitializationError,
    LLMAPIError,
    LLMAuthenticationError,
    LLMNotFoundError,
    LLMRateLimitError,
    ManifestValidationError,
    MongoDBEngineError,
    QueryValidationError,
    ResourceLimitExceeded,
)

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[type, int] = {
    QueryValidationError: 400,
    LLMAuthenticationError: 401,
    LLMNotFoundError: 404,
    ManifestValidationError: 422,
    LLMRateLimitError: 429,
    ResourceLimitExceeded: 429,
    ConfigurationError: 500,
    LLMAPIError: 502,
    InitializationError: 503,
}


async def handle_engine_error(request: Request, exc: MongoDBEngineError) -> JSONResponse:
    """Convert a ``MongoDBEngineError`` into a structured JSON response."""
    status = _STATUS_MAP.get(type(exc), 500)
    body: dict[str, Any] = {
        "error": type(exc).__name__,
        "code": exc.code,
        "message": str(exc),
    }
    if exc.context:
        body["context"] = exc.context
    if status >= 500:
        logger.error("Unhandled engine error: %s", exc, exc_info=True)
    else:
        logger.warning("Client error: %s", exc)
    return JSONResponse(status_code=status, content=body)


def register_error_handlers(app: Any) -> None:
    """Register engine exception handlers on a FastAPI/Starlette application."""
    app.add_exception_handler(MongoDBEngineError, handle_engine_error)
