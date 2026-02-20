"""
OpenTelemetry integration for MDB_ENGINE.

Provides distributed tracing with graceful fallback when OpenTelemetry
packages are not installed. All public functions are safe to call regardless
of whether the ``otel`` extras are present — they silently no-op when the
SDK is unavailable.

Install with::

    pip install mdb-engine[otel]
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful OTel imports
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

# Module-level state
_tracer_provider: Any | None = None
_initialized: bool = False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def otel_available() -> bool:
    """Return ``True`` when the OpenTelemetry SDK is importable."""
    return _OTEL_AVAILABLE


def init_tracer_provider(
    service_name: str,
    endpoint: str = "http://localhost:4317",
    exporter: str = "otlp",
    sample_rate: float = 1.0,
) -> None:
    """Initialise the global ``TracerProvider``.

    Safe to call when the OTel SDK is not installed — silently returns.

    Args:
        service_name: Logical service / app name attached to every span.
        endpoint: OTLP collector endpoint (gRPC).
        exporter: One of ``"otlp"``, ``"console"``, or ``"none"``.
        sample_rate: Fraction of traces to sample (0.0 – 1.0).
    """
    global _tracer_provider, _initialized  # noqa: PLW0603

    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry SDK not installed — tracing disabled")
        return

    if _initialized:
        logger.debug("TracerProvider already initialised — skipping")
        return

    resource = Resource.create({"service.name": service_name})

    # Sampler
    if sample_rate < 1.0:
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        sampler = TraceIdRatioBased(sample_rate)
        provider = TracerProvider(resource=resource, sampler=sampler)
    else:
        provider = TracerProvider(resource=resource)

    # Exporter
    if exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            span_exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(span_exporter))
            logger.info("OpenTelemetry OTLP exporter configured → %s", endpoint)
        except ImportError:
            logger.warning("opentelemetry-exporter-otlp not installed — falling back to console exporter")
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OpenTelemetry console exporter configured")
    else:
        logger.info("OpenTelemetry tracing enabled with no exporter (exporter=%s)", exporter)

    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    _initialized = True
    logger.info("OpenTelemetry TracerProvider initialised (service=%s, sample_rate=%s)", service_name, sample_rate)


def shutdown_tracer_provider() -> None:
    """Flush pending spans and shut down the ``TracerProvider``."""
    global _tracer_provider, _initialized  # noqa: PLW0603

    if not _OTEL_AVAILABLE or _tracer_provider is None:
        return

    try:
        _tracer_provider.force_flush(timeout_millis=5000)
        _tracer_provider.shutdown()
        logger.info("OpenTelemetry TracerProvider shut down")
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("Error shutting down TracerProvider: %s", exc)
    finally:
        _tracer_provider = None
        _initialized = False


def get_tracer(name: str = __name__) -> Tracer | _NoOpTracer:
    """Return an OTel ``Tracer`` or a lightweight no-op stand-in.

    Args:
        name: Instrumentation scope name (typically ``__name__``).
    """
    if _OTEL_AVAILABLE:
        return trace.get_tracer(name)
    return _NoOpTracer()


@contextmanager
def create_span(name: str, attributes: dict[str, Any] | None = None):
    """Context manager that yields an OTel ``Span`` or a no-op object.

    Usage::

        with create_span("my.operation", {"key": "value"}) as span:
            ...
    """
    if _OTEL_AVAILABLE:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(name, attributes=attributes) as span:
            yield span
    else:
        yield _NoOpSpan()


# ---------------------------------------------------------------------------
# Auto-instrumentation helpers
# ---------------------------------------------------------------------------


def instrument_fastapi(app: Any) -> None:
    """Apply ``FastAPIInstrumentor`` to *app* if the package is available."""
    if not _OTEL_AVAILABLE:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI auto-instrumentation enabled")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-fastapi not installed — skipping")


def instrument_pymongo() -> None:
    """Apply ``PymongoInstrumentor`` if the package is available.

    Must be called **before** any ``MongoClient`` is created so the
    instrumentation can monkey-patch the driver.
    """
    if not _OTEL_AVAILABLE:
        return

    try:
        from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

        PymongoInstrumentor().instrument()
        logger.info("PyMongo auto-instrumentation enabled")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-pymongo not installed — skipping")


# ---------------------------------------------------------------------------
# Trace-context helpers (for log correlation)
# ---------------------------------------------------------------------------


def get_current_trace_context() -> dict[str, str]:
    """Return ``trace_id`` and ``span_id`` of the active span, if any.

    Returns an empty dict when OTel is not available or there is no active span.
    """
    if not _OTEL_AVAILABLE:
        return {}

    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
        }
    return {}


# ---------------------------------------------------------------------------
# No-op fallbacks (zero overhead when OTel is absent)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Minimal stand-in so callers can use ``span.set_attribute()`` etc."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    def record_exception(self, exception: BaseException, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    def get_span_context(self) -> _NoOpSpanContext:
        return _NoOpSpanContext()

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoOpSpanContext:
    """Minimal stand-in for ``SpanContext``."""

    trace_id: int = 0
    span_id: int = 0


class _NoOpTracer:
    """Minimal stand-in for ``Tracer``."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoOpSpan:  # noqa: ARG002
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> _NoOpSpan:  # noqa: ARG002
        return _NoOpSpan()
