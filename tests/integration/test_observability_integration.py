"""
Integration tests for the observability stack.

Verifies that correlation IDs, tracing spans, and metrics work together
across a real FastAPI application lifecycle.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from mdb_engine.observability.logging import (
    _app_context,
    get_correlation_id,
)
from mdb_engine.observability.middleware import ObservabilityMiddleware

# ---------------------------------------------------------------------------
# Helpers — a minimal FastAPI app with the middleware stack
# ---------------------------------------------------------------------------


def _create_test_app(app_slug: str = "test-app"):
    """Create a minimal FastAPI app with ObservabilityMiddleware."""
    from fastapi import FastAPI

    app = FastAPI()
    app.state.app_slug = app_slug

    @app.get("/ping")
    async def ping():
        cid = get_correlation_id()
        ctx = _app_context.get() or {}
        return {
            "correlation_id": cid,
            "app_slug": ctx.get("app_slug"),
        }

    @app.get("/error")
    async def error():
        raise ValueError("boom")

    app.add_middleware(ObservabilityMiddleware)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorrelationIdFlow:
    """Verify that a correlation ID flows from request to response."""

    @pytest.mark.asyncio
    async def test_custom_correlation_id_round_trip(self):
        app = _create_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ping", headers={"X-Correlation-ID": "my-trace-123"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["correlation_id"] == "my-trace-123"
        assert resp.headers["x-correlation-id"] == "my-trace-123"

    @pytest.mark.asyncio
    async def test_auto_generated_correlation_id(self):
        app = _create_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ping")

        assert resp.status_code == 200
        cid = resp.headers["x-correlation-id"]
        uuid.UUID(cid)  # Must be a valid UUID
        assert resp.json()["correlation_id"] == cid

    @pytest.mark.asyncio
    async def test_x_request_id_header_accepted(self):
        app = _create_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ping", headers={"X-Request-ID": "req-456"})

        assert resp.headers["x-correlation-id"] == "req-456"
        assert resp.json()["correlation_id"] == "req-456"


class TestAppContextPropagation:
    """Verify that app_slug is available inside route handlers."""

    @pytest.mark.asyncio
    async def test_app_slug_propagated(self):
        app = _create_test_app(app_slug="my-cool-app")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ping")

        assert resp.status_code == 200
        assert resp.json()["app_slug"] == "my-cool-app"


class TestContextCleanupOnError:
    """Verify context variables are cleaned up even when the route raises."""

    @pytest.mark.asyncio
    async def test_cleanup_after_error(self):
        app = _create_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/error",
                headers={"X-Correlation-ID": "error-cid"},
            )

        assert resp.status_code == 500
        # Context should be cleaned up after the error
        assert get_correlation_id() is None
        assert _app_context.get() is None


class TestMultiAppIsolation:
    """Verify that two apps have independent correlation IDs."""

    @pytest.mark.asyncio
    async def test_two_apps_isolated(self):
        app_a = _create_test_app(app_slug="app-a")
        app_b = _create_test_app(app_slug="app-b")

        transport_a = ASGITransport(app=app_a)
        transport_b = ASGITransport(app=app_b)

        async with AsyncClient(transport=transport_a, base_url="http://test") as client_a:
            resp_a = await client_a.get("/ping", headers={"X-Correlation-ID": "cid-a"})

        async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
            resp_b = await client_b.get("/ping", headers={"X-Correlation-ID": "cid-b"})

        assert resp_a.json()["correlation_id"] == "cid-a"
        assert resp_a.json()["app_slug"] == "app-a"
        assert resp_b.json()["correlation_id"] == "cid-b"
        assert resp_b.json()["app_slug"] == "app-b"


class TestCreateSpanIntegration:
    """Verify that create_span works inside route handlers (no OTel installed)."""

    @pytest.mark.asyncio
    async def test_span_no_op_without_otel(self):
        from fastapi import FastAPI

        from mdb_engine.observability.tracing import create_span

        app = FastAPI()

        @app.get("/span-test")
        async def span_test():
            with create_span("test.operation", {"key": "value"}) as span:
                span.set_attribute("custom", "attr")
            return {"ok": True}

        app.add_middleware(ObservabilityMiddleware)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/span-test")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestMetricsCollectorOTelBridge:
    """Verify the MetricsCollector OTel meter bridge works."""

    def test_configure_otel_bridge(self):
        from unittest.mock import MagicMock

        from mdb_engine.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_histogram.return_value = mock_histogram

        collector.configure_otel(mock_meter)
        collector.record_operation("test.op", 42.5, success=True, app_slug="demo")

        mock_meter.create_counter.assert_called_once()
        mock_meter.create_histogram.assert_called_once()
        mock_counter.add.assert_called_once_with(1, attributes={"success": "True", "app_slug": "demo"})
        mock_histogram.record.assert_called_once_with(42.5, attributes={"success": "True", "app_slug": "demo"})

    def test_no_otel_bridge_by_default(self):
        from mdb_engine.observability.metrics import MetricsCollector

        collector = MetricsCollector()
        # Should not raise when OTel is not configured
        collector.record_operation("test.op", 10.0, success=True)
        assert collector.get_operation_count("test.op") == 1
