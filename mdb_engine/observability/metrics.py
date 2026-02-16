"""
Metrics collection for MDB_ENGINE.

This module provides metrics collection for monitoring engine performance,
operations, and health.
"""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OperationMetrics:
    """Metrics for a single operation."""

    operation_name: str
    count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float = float("inf")
    max_duration_ms: float = 0.0
    error_count: int = 0
    last_execution: datetime | None = None

    @property
    def avg_duration_ms(self) -> float:
        """Calculate average duration in milliseconds."""
        return self.total_duration_ms / self.count if self.count > 0 else 0.0

    @property
    def error_rate(self) -> float:
        """Calculate error rate as percentage."""
        return (self.error_count / self.count * 100) if self.count > 0 else 0.0

    def record(self, duration_ms: float, success: bool = True) -> None:
        """Record a single operation execution."""
        self.count += 1
        self.total_duration_ms += duration_ms
        self.min_duration_ms = min(self.min_duration_ms, duration_ms)
        self.max_duration_ms = max(self.max_duration_ms, duration_ms)
        if not success:
            self.error_count += 1
        self.last_execution = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "operation": self.operation_name,
            "count": self.count,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "min_duration_ms": (round(self.min_duration_ms, 2) if self.min_duration_ms != float("inf") else 0.0),
            "max_duration_ms": round(self.max_duration_ms, 2),
            "error_count": self.error_count,
            "error_rate_percent": round(self.error_rate, 2),
            "last_execution": (self.last_execution.isoformat() if self.last_execution else None),
        }


class MetricsCollector:
    """
    Centralized metrics collector for MDB_ENGINE.

    Collects and aggregates metrics for:
    - Engine operations (initialization, app registration)
    - Database operations (queries, writes)
    - Index operations (creation, updates)
    - Performance metrics (latency, throughput)
    """

    def __init__(self, max_metrics: int = 10000):
        """
        Initialize the metrics collector.

        Args:
            max_metrics: Maximum number of metrics to store before evicting oldest (LRU).
                        Defaults to 10000.
        """
        self._metrics: OrderedDict[str, OperationMetrics] = OrderedDict()
        self._lock = threading.Lock()
        self._max_metrics = max_metrics
        # Optional OpenTelemetry metric instruments
        self._otel_meter: Any | None = None
        self._otel_counters: dict[str, Any] = {}
        self._otel_histograms: dict[str, Any] = {}

    def configure_otel(self, meter: Any) -> None:
        """Attach an OpenTelemetry ``Meter`` for metric export.

        When configured, every :meth:`record_operation` call will also
        update OTel Counter and Histogram instruments, bridging the
        in-memory metrics to any OTel-compatible backend (Prometheus,
        OTLP, etc.).

        Args:
            meter: An ``opentelemetry.metrics.Meter`` instance.
        """
        self._otel_meter = meter
        logger.info("MetricsCollector: OpenTelemetry meter bridge configured")

    def record_operation(self, operation_name: str, duration_ms: float, success: bool = True, **tags: Any) -> None:
        """
        Record an operation execution.

        Args:
            operation_name: Name of the operation (e.g., "engine.initialize")
            duration_ms: Duration in milliseconds
            success: Whether the operation succeeded
            **tags: Additional tags for filtering (app_slug, collection_name, etc.)
        """
        # Create a key that includes tags for more granular metrics
        key = operation_name
        if tags:
            tag_str = "_".join(f"{k}={v}" for k, v in sorted(tags.items()))
            key = f"{operation_name}[{tag_str}]"

        with self._lock:
            # Check if this is a new metric
            is_new = key not in self._metrics

            # Evict oldest if at capacity and adding new metric
            if is_new and len(self._metrics) >= self._max_metrics:
                self._metrics.popitem(last=False)  # Remove oldest (LRU)

            if is_new:
                self._metrics[key] = OperationMetrics(operation_name=operation_name)
            else:
                # Move to end (most recently used)
                self._metrics.move_to_end(key)

            self._metrics[key].record(duration_ms, success)

        # Bridge to OpenTelemetry metrics if configured
        if self._otel_meter is not None:
            otel_attrs = {"success": str(success)}
            for tag_key, tag_value in tags.items():
                otel_attrs[tag_key] = str(tag_value)

            self._get_or_create_counter(operation_name).add(1, attributes=otel_attrs)
            self._get_or_create_histogram(operation_name).record(duration_ms, attributes=otel_attrs)

    def _get_or_create_counter(self, operation_name: str) -> Any:
        """Return (or lazily create) an OTel Counter for *operation_name*."""
        if operation_name not in self._otel_counters:
            safe_name = operation_name.replace(".", "_")
            self._otel_counters[operation_name] = self._otel_meter.create_counter(
                name=f"mdb_engine.{safe_name}.count",
                description=f"Number of {operation_name} executions",
            )
        return self._otel_counters[operation_name]

    def _get_or_create_histogram(self, operation_name: str) -> Any:
        """Return (or lazily create) an OTel Histogram for *operation_name*."""
        if operation_name not in self._otel_histograms:
            safe_name = operation_name.replace(".", "_")
            self._otel_histograms[operation_name] = self._otel_meter.create_histogram(
                name=f"mdb_engine.{safe_name}.duration_ms",
                description=f"Duration of {operation_name} in milliseconds",
                unit="ms",
            )
        return self._otel_histograms[operation_name]

    def get_metrics(self, operation_name: str | None = None) -> dict[str, Any]:
        """
        Get metrics for operations.

        Args:
            operation_name: Optional operation name to filter by

        Returns:
            Dictionary of metrics
        """
        with self._lock:
            if operation_name:
                metrics = {k: v.to_dict() for k, v in self._metrics.items() if k.startswith(operation_name)}
                # Move accessed metrics to end (LRU)
                for key in list(self._metrics.keys()):
                    if key.startswith(operation_name):
                        self._metrics.move_to_end(key)
            else:
                metrics = {k: v.to_dict() for k, v in self._metrics.items()}
                # Move all accessed metrics to end (LRU)
                for key in list(self._metrics.keys()):
                    self._metrics.move_to_end(key)

            total_operations = len(self._metrics)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "total_operations": total_operations,
        }

    def get_summary(self) -> dict[str, Any]:
        """
        Get a summary of all metrics.

        Returns:
            Summary dictionary with aggregated statistics
        """
        with self._lock:
            if not self._metrics:
                return {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_operations": 0,
                    "summary": {},
                }

            # Aggregate by base operation name (without tags)
            aggregated: dict[str, OperationMetrics] = {}

            for _key, metric in self._metrics.items():
                base_name = metric.operation_name
                if base_name not in aggregated:
                    aggregated[base_name] = OperationMetrics(operation_name=base_name)

                agg = aggregated[base_name]
                agg.count += metric.count
                agg.total_duration_ms += metric.total_duration_ms
                agg.min_duration_ms = min(agg.min_duration_ms, metric.min_duration_ms)
                agg.max_duration_ms = max(agg.max_duration_ms, metric.max_duration_ms)
                agg.error_count += metric.error_count
                if metric.last_execution and (not agg.last_execution or metric.last_execution > agg.last_execution):
                    agg.last_execution = metric.last_execution

            # Move all accessed metrics to end (LRU)
            for key in list(self._metrics.keys()):
                self._metrics.move_to_end(key)

            total_operations = len(self._metrics)
            summary = {name: m.to_dict() for name, m in aggregated.items()}

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_operations": total_operations,
            "summary": summary,
        }

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._metrics.clear()

    def get_operation_count(self, operation_name: str) -> int:
        """Get the count of executions for an operation."""
        with self._lock:
            total = 0
            for metric in self._metrics.values():
                if metric.operation_name == operation_name:
                    total += metric.count
        return total


# Global metrics collector instance
_metrics_collector: MetricsCollector | None = None
_metrics_lock = threading.Lock()


def get_metrics_collector() -> MetricsCollector:
    """Get or create the global metrics collector (thread-safe)."""
    global _metrics_collector
    if _metrics_collector is not None:  # Fast path
        return _metrics_collector
    with _metrics_lock:  # Double-check
        if _metrics_collector is None:
            _metrics_collector = MetricsCollector()
        return _metrics_collector


def record_operation(operation_name: str, duration_ms: float, success: bool = True, **tags: Any) -> None:
    """
    Record an operation in the global metrics collector.

    When OpenTelemetry is available, also annotates the current span with
    operation attributes so that every ``record_operation`` call site
    automatically enriches the active trace.

    Args:
        operation_name: Name of the operation
        duration_ms: Duration in milliseconds
        success: Whether the operation succeeded
        **tags: Additional tags
    """
    collector = get_metrics_collector()
    collector.record_operation(operation_name, duration_ms, success, **tags)

    # Annotate the active OTel span (if any)
    from .tracing import otel_available

    if otel_available():
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute("operation.name", operation_name)
            span.set_attribute("operation.duration_ms", round(duration_ms, 2))
            span.set_attribute("operation.success", success)
            for tag_key, tag_value in tags.items():
                span.set_attribute(f"operation.{tag_key}", str(tag_value))


def timed_operation(operation_name: str, **tags: Any):
    """
    Decorator to automatically time and record an operation.

    When OpenTelemetry is available the decorated call is also wrapped in a
    span named *operation_name*.  Duration, success, tags, and any raised
    exception are recorded as span attributes / events.

    Usage::

        @timed_operation("engine.initialize")
        async def initialize(self):
            ...
    """

    # Common exceptions that indicate operation failure (not system-level exits)
    _OPERATION_FAILURES = (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        LookupError,
        IOError,
        OSError,
        ConnectionError,
        TimeoutError,
        PermissionError,
        FileNotFoundError,
        AssertionError,
        ArithmeticError,
        BufferError,
        ImportError,
        MemoryError,
        StopIteration,
        StopAsyncIteration,
        UnicodeError,
    )

    def decorator(func: Callable) -> Callable:
        if hasattr(func, "__code__") and func.__code__.co_flags & 0x80:  # CO_COROUTINE
            # Async function
            async def async_wrapper(*args, **kwargs):
                from .tracing import get_tracer, otel_available

                tracer = get_tracer(__name__) if otel_available() else None
                span_ctx = (
                    tracer.start_as_current_span(operation_name) if tracer and otel_available() else _nullcontext()
                )

                with span_ctx as span:
                    start_time = time.time()
                    success = True
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except _OPERATION_FAILURES as exc:
                        success = False
                        if span and hasattr(span, "record_exception"):
                            span.record_exception(exc)
                        raise
                    finally:
                        duration_ms = (time.time() - start_time) * 1000
                        record_operation(operation_name, duration_ms, success, **tags)

            return async_wrapper
        else:
            # Sync function
            def sync_wrapper(*args, **kwargs):
                from .tracing import get_tracer, otel_available

                tracer = get_tracer(__name__) if otel_available() else None
                span_ctx = (
                    tracer.start_as_current_span(operation_name) if tracer and otel_available() else _nullcontext()
                )

                with span_ctx as span:
                    start_time = time.time()
                    success = True
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except _OPERATION_FAILURES as exc:
                        success = False
                        if span and hasattr(span, "record_exception"):
                            span.record_exception(exc)
                        raise
                    finally:
                        duration_ms = (time.time() - start_time) * 1000
                        record_operation(operation_name, duration_ms, success, **tags)

            return sync_wrapper

    return decorator


class _nullcontext:
    """Minimal context manager that yields ``None`` (Python 3.10 compat)."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> None:
        pass
