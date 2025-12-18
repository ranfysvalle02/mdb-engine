"""
Enhanced logging utilities for MDB_RUNTIME.

Provides structured logging with correlation IDs and context.
"""
import logging
import uuid
import contextvars
from typing import Optional, Dict, Any
from datetime import datetime

# Context variable for correlation ID
_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id', default=None
)

# Context variable for app context
_app_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    'app_context', default=None
)


def get_correlation_id() -> Optional[str]:
    """Get the current correlation ID from context."""
    return _correlation_id.get()


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
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


def set_app_context(app_slug: Optional[str] = None, **kwargs: Any) -> None:
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


def get_logging_context() -> Dict[str, Any]:
    """
    Get current logging context (correlation ID and app context).
    
    Returns:
        Dictionary with context information
    """
    context: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
    }
    
    correlation_id = get_correlation_id()
    if correlation_id:
        context["correlation_id"] = correlation_id
    
    app_context = _app_context.get()
    if app_context:
        context.update(app_context)
    
    return context


class ContextualLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that automatically adds context to log records.
    """
    
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Add context to log records."""
        # Get base context
        context = get_logging_context()
        
        # Merge with any extra context provided
        extra = kwargs.get('extra', {})
        if extra:
            context.update(extra)
        
        kwargs['extra'] = context
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
    duration_ms: Optional[float] = None,
    **context: Any
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
    log_context.update({
        "operation": operation,
        "success": success,
    })
    
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

