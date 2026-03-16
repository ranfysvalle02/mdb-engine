"""
Observability components.

Provides structured logging, metrics collection, distributed tracing,
and health check capabilities.
"""

from .error_handlers import register_error_handlers
from .exporters import create_prometheus_endpoint, setup_otel_metrics_bridge
from .health import (
    HealthChecker,
    HealthCheckResult,
    HealthStatus,
    check_embedding_health,
    check_engine_health,
    check_graph_health,
    check_llm_health,
    check_mongodb_health,
    check_pool_health,
)
from .logging import (
    ContextualLoggerAdapter,
    clear_app_context,
    clear_correlation_id,
    get_correlation_id,
    get_logger,
    get_logging_context,
    log_operation,
    set_app_context,
    set_correlation_id,
)
from .metrics import (
    MetricsCollector,
    OperationMetrics,
    get_metrics_collector,
    record_operation,
    timed_operation,
)
from .middleware import ObservabilityMiddleware
from .tracing import (
    create_span,
    get_current_trace_context,
    get_tracer,
    init_tracer_provider,
    instrument_fastapi,
    instrument_pymongo,
    otel_available,
    shutdown_tracer_provider,
)

__all__ = [
    # Metrics
    "MetricsCollector",
    "OperationMetrics",
    "get_metrics_collector",
    "record_operation",
    "timed_operation",
    # Logging
    "get_correlation_id",
    "set_correlation_id",
    "clear_correlation_id",
    "set_app_context",
    "clear_app_context",
    "get_logging_context",
    "ContextualLoggerAdapter",
    "get_logger",
    "log_operation",
    # Health
    "HealthStatus",
    "HealthCheckResult",
    "HealthChecker",
    "check_mongodb_health",
    "check_engine_health",
    "check_pool_health",
    "check_llm_health",
    "check_embedding_health",
    "check_graph_health",
    # Error handlers
    "register_error_handlers",
    # Exporters
    "create_prometheus_endpoint",
    "setup_otel_metrics_bridge",
    # Middleware
    "ObservabilityMiddleware",
    # Tracing (OpenTelemetry)
    "otel_available",
    "init_tracer_provider",
    "shutdown_tracer_provider",
    "get_tracer",
    "create_span",
    "get_current_trace_context",
    "instrument_fastapi",
    "instrument_pymongo",
]
