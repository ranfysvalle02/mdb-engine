"""
Enhanced logging utilities for MDB_ENGINE.

Provides structured logging with correlation IDs, app-slug context,
request-scoped correlation IDs, and auto-configuration for multi-app
deployments.
"""

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

_logging_configured = False

# Context variable for correlation ID
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

# Context variable for app context
_app_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("app_context", default=None)


def get_correlation_id() -> str | None:
    """Get the current correlation ID from context."""
    return _correlation_id.get()


def set_correlation_id(correlation_id: str | None = None) -> str:
    """
    Set a correlation ID in the current context.

    Args:
        correlation_id: Optional correlation ID (generates new one if None)

    Returns:
        The correlation ID that was set
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    _correlation_id.set(correlation_id)
    return correlation_id


def clear_correlation_id() -> None:
    """Clear the correlation ID from context."""
    _correlation_id.set(None)


def set_app_context(app_slug: str | None = None, **kwargs: Any) -> None:
    """
    Set app context for logging.

    Args:
        app_slug: App slug
        **kwargs: Additional context (collection_name, user_id, etc.)
    """
    context = {"app_slug": app_slug, **kwargs}
    _app_context.set(context)


def clear_app_context() -> None:
    """Clear app context."""
    _app_context.set(None)


def get_logging_context() -> dict[str, Any]:
    """
    Get current logging context (correlation ID, app context, and trace IDs).

    When OpenTelemetry is active, ``trace_id`` and ``span_id`` are included
    automatically so that log lines can be correlated with distributed traces.

    Returns:
        Dictionary with context information
    """
    context: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    correlation_id = get_correlation_id()
    if correlation_id:
        context["correlation_id"] = correlation_id

    app_context = _app_context.get()
    if app_context:
        context.update(app_context)

    # Inject OTel trace/span IDs when available
    from .tracing import get_current_trace_context

    trace_ctx = get_current_trace_context()
    if trace_ctx:
        context.update(trace_ctx)

    return context


class ContextualLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that automatically adds context to log records.
    """

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Add context to log records."""
        # Get base context
        context = get_logging_context()

        # Merge with any extra context provided
        extra = kwargs.get("extra", {})
        if extra:
            context.update(extra)

        kwargs["extra"] = context
        return msg, kwargs


def get_logger(name: str) -> ContextualLoggerAdapter:
    """
    Get a contextual logger that automatically adds correlation ID and context.

    Args:
        name: Logger name (typically __name__)

    Returns:
        ContextualLoggerAdapter instance
    """
    base_logger = logging.getLogger(name)
    return ContextualLoggerAdapter(base_logger, {})


def log_operation(
    logger: logging.Logger,
    operation: str,
    level: int = logging.INFO,
    success: bool = True,
    duration_ms: float | None = None,
    **context: Any,
) -> None:
    """
    Log an operation with structured context.

    Args:
        logger: Logger instance
        operation: Operation name
        level: Log level
        success: Whether operation succeeded
        duration_ms: Operation duration in milliseconds
        **context: Additional context
    """
    log_context = get_logging_context()
    log_context.update(
        {
            "operation": operation,
            "success": success,
        }
    )

    if duration_ms is not None:
        log_context["duration_ms"] = round(duration_ms, 2)

    if context:
        log_context.update(context)

    message = f"Operation: {operation}"
    if not success:
        message = f"Operation failed: {operation}"
    if duration_ms is not None:
        message += f" (duration: {duration_ms:.2f}ms)"

    logger.log(level, message, extra=log_context)


# ── JSON Formatter ────────────────────────────────────────────────────────


class _JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge structured extras added by ContextualLoggerAdapter
        for key in ("app_slug", "correlation_id", "trace_id", "span_id", "operation"):
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = val
        if record.exc_info and record.exc_info[1]:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


class _HumanFormatter(logging.Formatter):
    """Readable log format that includes the app slug when available."""

    def format(self, record: logging.LogRecord) -> str:
        slug = getattr(record, "app_slug", None)
        prefix = f"[{slug}] " if slug else ""
        record.msg = f"{prefix}{record.msg}"
        return super().format(record)


# ── Auto-configuration ─────────────────────────────────────────────────


def configure_logging(
    *,
    json_output: bool | None = None,
    level: str | None = None,
) -> None:
    """Configure root logging for mdb-engine.

    Called once by the multi-app lifespan.  Subsequent calls are no-ops.

    Args:
        json_output: Emit JSON lines (default: ``True`` when
            ``ENVIRONMENT=production``, else ``False``).
        level: Log level name (default: ``LOG_LEVEL`` env or ``INFO``).
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    if json_output is None:
        json_output = os.getenv("ENVIRONMENT", "").lower() == "production"

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(_HumanFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    # Replace existing handlers to avoid duplicate output
    root.handlers = [handler]


def get_app_logger(app_slug: str) -> ContextualLoggerAdapter:
    """Return a logger pre-scoped to *app_slug*.

    Usage in routes::

        from mdb_engine.observability.logging import get_app_logger
        logger = get_app_logger("my-app")
        logger.info("Hello")  # -> [my-app] Hello
    """
    base = logging.getLogger(f"mdb_engine.apps.{app_slug}")
    adapter = ContextualLoggerAdapter(base, {})
    set_app_context(app_slug=app_slug)
    return adapter
