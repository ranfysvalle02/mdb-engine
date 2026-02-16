"""Tests for ObservabilityMiddleware."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.observability.logging import get_correlation_id, set_correlation_id
from mdb_engine.observability.middleware import ObservabilityMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
    app_slug: str | None = None,
) -> dict:
    """Build a minimal ASGI scope for testing."""
    scope: dict = {
        "type": scope_type,
        "headers": headers or [],
    }
    if app_slug is not None:
        state = MagicMock()
        state.app_slug = app_slug
        app = MagicMock()
        app.state = state
        scope["app"] = app
    return scope


class _ResponseCollector:
    """Captures response messages so tests can inspect them."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


async def _echo_app(scope, receive, send):
    """Trivial ASGI app that returns 200 with an empty body."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorrelationIdExtraction:
    """Verify that the middleware extracts or generates a correlation ID."""

    @pytest.mark.asyncio
    async def test_generates_uuid_when_no_header(self):
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()
        await mw(_make_scope(), AsyncMock(), collector)

        cid = _get_response_correlation_id(collector)
        # Should be a valid UUID
        uuid.UUID(cid)

    @pytest.mark.asyncio
    async def test_extracts_x_correlation_id_header(self):
        headers = [(b"x-correlation-id", b"custom-123")]
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()
        await mw(_make_scope(headers=headers), AsyncMock(), collector)

        assert _get_response_correlation_id(collector) == "custom-123"

    @pytest.mark.asyncio
    async def test_extracts_x_request_id_header(self):
        headers = [(b"x-request-id", b"req-456")]
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()
        await mw(_make_scope(headers=headers), AsyncMock(), collector)

        assert _get_response_correlation_id(collector) == "req-456"

    @pytest.mark.asyncio
    async def test_header_takes_precedence_over_otel(self):
        headers = [(b"x-correlation-id", b"from-header")]
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()

        with (
            patch("mdb_engine.observability.middleware.otel_available", return_value=True),
            patch(
                "mdb_engine.observability.middleware.get_current_trace_context",
                return_value={"trace_id": "otel-trace-id"},
            ),
        ):
            await mw(_make_scope(headers=headers), AsyncMock(), collector)

        assert _get_response_correlation_id(collector) == "from-header"

    @pytest.mark.asyncio
    async def test_otel_trace_id_used_when_no_header(self):
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()

        with (
            patch("mdb_engine.observability.middleware.otel_available", return_value=True),
            patch(
                "mdb_engine.observability.middleware.get_current_trace_context",
                return_value={"trace_id": "otel-trace-abc"},
            ),
        ):
            await mw(_make_scope(), AsyncMock(), collector)

        assert _get_response_correlation_id(collector) == "otel-trace-abc"


class TestResponseHeaderInjection:
    """Verify the correlation ID appears in the response headers."""

    @pytest.mark.asyncio
    async def test_correlation_id_in_response_headers(self):
        headers = [(b"x-correlation-id", b"resp-test")]
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()
        await mw(_make_scope(headers=headers), AsyncMock(), collector)

        start_msg = collector.messages[0]
        resp_headers = {k.decode(): v.decode() for k, v in start_msg["headers"]}
        assert resp_headers["x-correlation-id"] == "resp-test"


class TestAppContext:
    """Verify app_slug propagation into app context."""

    @pytest.mark.asyncio
    async def test_app_slug_set_from_scope(self):
        captured_slug = {}

        async def capture_app(scope, receive, send):
            from mdb_engine.observability.logging import _app_context

            ctx = _app_context.get()
            captured_slug.update(ctx or {})
            await _echo_app(scope, receive, send)

        mw = ObservabilityMiddleware(capture_app)
        collector = _ResponseCollector()
        await mw(_make_scope(app_slug="my-app"), AsyncMock(), collector)

        assert captured_slug.get("app_slug") == "my-app"


class TestContextCleanup:
    """Verify context variables are cleaned up after a request."""

    @pytest.mark.asyncio
    async def test_correlation_id_cleared_after_request(self):
        set_correlation_id("stale")
        mw = ObservabilityMiddleware(_echo_app)
        collector = _ResponseCollector()
        await mw(_make_scope(), AsyncMock(), collector)

        assert get_correlation_id() is None

    @pytest.mark.asyncio
    async def test_cleanup_on_exception(self):
        async def failing_app(scope, receive, send):
            raise RuntimeError("boom")

        set_correlation_id("stale")
        mw = ObservabilityMiddleware(failing_app)
        collector = _ResponseCollector()

        with pytest.raises(RuntimeError, match="boom"):
            await mw(_make_scope(), AsyncMock(), collector)

        assert get_correlation_id() is None


class TestNonHttpScopes:
    """Verify that non-HTTP/websocket scopes pass through untouched."""

    @pytest.mark.asyncio
    async def test_lifespan_passes_through(self):
        called = False

        async def lifespan_app(scope, receive, send):
            nonlocal called
            called = True

        mw = ObservabilityMiddleware(lifespan_app)
        await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())
        assert called


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _get_response_correlation_id(collector: _ResponseCollector) -> str:
    """Extract X-Correlation-ID from the captured response start message."""
    start_msg = collector.messages[0]
    for name, value in start_msg["headers"]:
        if name == b"x-correlation-id":
            return value.decode("utf-8")
    raise AssertionError("X-Correlation-ID header not found in response")
