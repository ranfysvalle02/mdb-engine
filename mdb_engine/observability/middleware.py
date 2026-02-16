"""
Observability ASGI middleware for automatic correlation ID and app context propagation.

This middleware extracts or generates a correlation ID for every HTTP request,
sets it in the async context (so all downstream logging includes it), adds
the correlation ID to response headers, and cleans up on completion.

When OpenTelemetry is active, the OTel trace ID is preferred as the
correlation ID so that logs and traces stay linked.
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging import clear_app_context, clear_correlation_id, set_app_context, set_correlation_id
from .tracing import get_current_trace_context, otel_available

_CORRELATION_HEADERS = ("x-correlation-id", "x-request-id")


class ObservabilityMiddleware:
    """Pure-ASGI middleware for correlation IDs and app-context propagation.

    Responsibilities:
    1. Extract ``X-Correlation-ID`` or ``X-Request-ID`` from incoming headers,
       or generate a UUID if neither is present.
    2. When OpenTelemetry is active, prefer the OTel trace ID so logs and
       traces stay linked automatically.
    3. Set :func:`set_correlation_id` and :func:`set_app_context` so every
       downstream log line includes the context.
    4. Inject ``X-Correlation-ID`` into the response headers.
    5. Clean up context vars after the response completes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        correlation_id = self._extract_correlation_id(scope)
        app_slug = self._extract_app_slug(scope)

        set_correlation_id(correlation_id)
        if app_slug:
            set_app_context(app_slug=app_slug)

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                headers.append((b"x-correlation-id", correlation_id.encode("utf-8")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            clear_correlation_id()
            clear_app_context()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_correlation_id(scope: Scope) -> str:
        """Return a correlation ID from headers, OTel context, or a new UUID."""
        # 1. Check incoming request headers
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        for name, value in headers:
            if name.decode("latin-1").lower() in _CORRELATION_HEADERS:
                return value.decode("latin-1")

        # 2. Prefer the OTel trace ID when available
        if otel_available():
            trace_ctx = get_current_trace_context()
            trace_id = trace_ctx.get("trace_id")
            if trace_id:
                return trace_id

        # 3. Fallback to a new UUID
        return str(uuid.uuid4())

    @staticmethod
    def _extract_app_slug(scope: Scope) -> str | None:
        """Extract app_slug from the ASGI scope's app state if available."""
        app: Any = scope.get("app")
        if app is not None:
            return getattr(getattr(app, "state", None), "app_slug", None)
        return None
