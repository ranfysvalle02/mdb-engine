"""
Observability components.

Provides structured logging, metrics collection, request tracing,
and health check capabilities.
"""

from .metrics import (
    MetricsCollector,
    OperationMetrics,
    get_metrics_collector,
    record_operation,
    timed_operation,
)

from .logging import (
    get_correlation_id,
    set_correlation_id,
    clear_correlation_id,
    set_app_context,
    clear_app_context,
    get_logging_context,
    ContextualLoggerAdapter,
    get_logger,
    log_operation,
)

from .health import (
    HealthStatus,
    HealthCheckResult,
    HealthChecker,
    check_mongodb_health,
    check_engine_health,
    check_pool_health,
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
]

