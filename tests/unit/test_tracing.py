"""
Unit tests for OpenTelemetry tracing integration.

Tests both the OTel-available and OTel-unavailable code paths to ensure
the module degrades gracefully when the ``otel`` extras are not installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mdb_engine.observability.tracing import (
    _OTEL_AVAILABLE,
    _NoOpSpan,
    _NoOpSpanContext,
    _NoOpTracer,
    create_span,
    get_current_trace_context,
    get_tracer,
    init_tracer_provider,
    instrument_fastapi,
    instrument_pymongo,
    otel_available,
    shutdown_tracer_provider,
)

# ---------------------------------------------------------------------------
# Basic availability checks
# ---------------------------------------------------------------------------


class TestOtelAvailability:
    """Test the otel_available() helper."""

    def test_otel_available_returns_bool(self):
        result = otel_available()
        assert isinstance(result, bool)

    def test_otel_available_matches_module_flag(self):
        assert otel_available() == _OTEL_AVAILABLE


# ---------------------------------------------------------------------------
# No-op fallbacks (always work, regardless of OTel installation)
# ---------------------------------------------------------------------------


class TestNoOpFallbacks:
    """Test that no-op stand-ins do not raise."""

    def test_noop_span_set_attribute(self):
        span = _NoOpSpan()
        span.set_attribute("key", "value")

    def test_noop_span_set_status(self):
        span = _NoOpSpan()
        span.set_status("OK")

    def test_noop_span_record_exception(self):
        span = _NoOpSpan()
        span.record_exception(RuntimeError("boom"))

    def test_noop_span_context_manager(self):
        span = _NoOpSpan()
        with span as s:
            assert s is span

    def test_noop_span_get_span_context(self):
        span = _NoOpSpan()
        ctx = span.get_span_context()
        assert isinstance(ctx, _NoOpSpanContext)
        assert ctx.trace_id == 0
        assert ctx.span_id == 0

    def test_noop_tracer_start_as_current_span(self):
        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test.op")
        assert isinstance(span, _NoOpSpan)

    def test_noop_tracer_start_span(self):
        tracer = _NoOpTracer()
        span = tracer.start_span("test.op")
        assert isinstance(span, _NoOpSpan)


# ---------------------------------------------------------------------------
# create_span context manager
# ---------------------------------------------------------------------------


class TestCreateSpan:
    """Test the create_span context manager."""

    def test_create_span_yields_without_error(self):
        with create_span("test.operation", {"key": "val"}) as span:
            assert span is not None

    def test_create_span_yields_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            with create_span("test.op") as span:
                assert isinstance(span, _NoOpSpan)

    def test_create_span_attributes_dont_raise(self):
        with create_span("test.op") as span:
            span.set_attribute("foo", "bar")


# ---------------------------------------------------------------------------
# get_tracer
# ---------------------------------------------------------------------------


class TestGetTracer:
    """Test get_tracer returns an appropriate object."""

    def test_get_tracer_returns_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            tracer = get_tracer("test")
            assert isinstance(tracer, _NoOpTracer)

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_get_tracer_returns_real_tracer_when_otel_present(self):
        tracer = get_tracer("test")
        assert not isinstance(tracer, _NoOpTracer)


# ---------------------------------------------------------------------------
# get_current_trace_context
# ---------------------------------------------------------------------------


class TestGetCurrentTraceContext:
    """Test trace context extraction."""

    def test_returns_empty_dict_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            ctx = get_current_trace_context()
            assert ctx == {}

    def test_returns_dict(self):
        ctx = get_current_trace_context()
        assert isinstance(ctx, dict)


# ---------------------------------------------------------------------------
# init / shutdown (safe no-ops when OTel is absent)
# ---------------------------------------------------------------------------


class TestTracerProviderLifecycle:
    """Test init_tracer_provider and shutdown_tracer_provider."""

    def test_init_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            init_tracer_provider("test-service")

    def test_shutdown_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            shutdown_tracer_provider()

    def test_shutdown_noop_when_not_initialized(self):
        with patch("mdb_engine.observability.tracing._tracer_provider", None):
            shutdown_tracer_provider()

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_init_and_shutdown_with_console_exporter(self):
        """Full init → shutdown cycle with console exporter (no network)."""
        import mdb_engine.observability.tracing as tracing_mod

        # Reset module state
        tracing_mod._initialized = False
        tracing_mod._tracer_provider = None

        try:
            init_tracer_provider(
                service_name="test-unit",
                exporter="console",
                sample_rate=1.0,
            )
            assert tracing_mod._initialized is True
            assert tracing_mod._tracer_provider is not None
        finally:
            shutdown_tracer_provider()
            assert tracing_mod._initialized is False
            assert tracing_mod._tracer_provider is None

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_init_skips_when_already_initialized(self):
        """Second call to init_tracer_provider is a no-op."""
        import mdb_engine.observability.tracing as tracing_mod

        tracing_mod._initialized = False
        tracing_mod._tracer_provider = None

        try:
            init_tracer_provider(service_name="first", exporter="none")
            assert tracing_mod._initialized is True

            init_tracer_provider(service_name="second", exporter="console")
            # Still the same provider — second call was skipped
            assert tracing_mod._initialized is True
        finally:
            shutdown_tracer_provider()

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_init_with_sample_rate(self):
        """TracerProvider initializes with a fractional sample rate."""
        import mdb_engine.observability.tracing as tracing_mod

        tracing_mod._initialized = False
        tracing_mod._tracer_provider = None

        try:
            init_tracer_provider(
                service_name="sampled-test",
                exporter="none",
                sample_rate=0.5,
            )
            assert tracing_mod._initialized is True
        finally:
            shutdown_tracer_provider()


# ---------------------------------------------------------------------------
# Auto-instrumentation helpers
# ---------------------------------------------------------------------------


class TestAutoInstrumentation:
    """Test instrument_fastapi and instrument_pymongo."""

    def test_instrument_fastapi_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            instrument_fastapi(MagicMock())

    def test_instrument_pymongo_noop_when_otel_absent(self):
        with patch("mdb_engine.observability.tracing._OTEL_AVAILABLE", False):
            instrument_pymongo()


# ---------------------------------------------------------------------------
# Integration: metrics.record_operation span annotation
# ---------------------------------------------------------------------------


class TestRecordOperationSpanAnnotation:
    """Test that record_operation annotates the active span."""

    def test_record_operation_works_without_otel(self):
        """Calling record_operation without OTel should not raise."""
        from mdb_engine.observability.metrics import record_operation

        record_operation("test.op", 42.0, success=True, app_slug="test")

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_record_operation_sets_span_attributes(self):
        """When inside a span, record_operation should set attributes."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        from mdb_engine.observability.metrics import record_operation

        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test-parent") as span:
            record_operation("db.find", 12.5, success=True, collection="users")

            # Verify the span has the expected attributes
            attrs = dict(span.attributes) if span.attributes else {}
            assert attrs.get("operation.name") == "db.find"
            assert attrs.get("operation.success") is True

        provider.shutdown()


# ---------------------------------------------------------------------------
# Integration: timed_operation decorator span creation
# ---------------------------------------------------------------------------


class TestTimedOperationSpan:
    """Test that @timed_operation creates spans when OTel is available."""

    def test_timed_operation_sync_without_otel(self):
        from mdb_engine.observability.metrics import timed_operation

        @timed_operation("test.sync_op")
        def my_func():
            return 42

        assert my_func() == 42

    @pytest.mark.asyncio
    async def test_timed_operation_async_without_otel(self):
        from mdb_engine.observability.metrics import timed_operation

        @timed_operation("test.async_op")
        async def my_func():
            return 99

        assert await my_func() == 99

    def test_timed_operation_sync_records_failure(self):
        from mdb_engine.observability.metrics import timed_operation

        @timed_operation("test.fail_op")
        def failing_func():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing_func()


# ---------------------------------------------------------------------------
# Integration: logging context includes trace IDs
# ---------------------------------------------------------------------------


class TestLoggingTraceCorrelation:
    """Test that get_logging_context includes trace_id/span_id."""

    def test_logging_context_has_timestamp(self):
        from mdb_engine.observability.logging import get_logging_context

        ctx = get_logging_context()
        assert "timestamp" in ctx

    @pytest.mark.skipif(not _OTEL_AVAILABLE, reason="OTel SDK not installed")
    def test_logging_context_includes_trace_id_in_span(self):
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        from mdb_engine.observability.logging import get_logging_context

        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("log-test"):
            ctx = get_logging_context()
            assert "trace_id" in ctx
            assert "span_id" in ctx
            assert len(ctx["trace_id"]) == 32
            assert len(ctx["span_id"]) == 16

        provider.shutdown()
