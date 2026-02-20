"""
Service Scopes for Dependency Injection

Defines service lifetime scopes following enterprise patterns:
- SINGLETON: Created once, shared across all requests
- REQUEST: Created once per HTTP request, disposed after
- TRANSIENT: Created fresh on every injection
"""

import logging
from contextvars import ContextVar
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Context variable for request-scoped instances
_request_scope: ContextVar[dict[type, Any] | None] = ContextVar("request_scope", default=None)


class Scope(Enum):
    """
    Service lifetime scopes.

    SINGLETON: One instance for the entire application lifetime.
               Use for: Database connections, configuration, caches.

    REQUEST: One instance per HTTP request. Automatically disposed
             when the request ends.
             Use for: Unit of Work, request context, user session.

    TRANSIENT: New instance created every time it's requested.
               Use for: Stateless services, utilities.
    """

    SINGLETON = "singleton"
    REQUEST = "request"
    TRANSIENT = "transient"


class ScopeManager:
    """
    Manages request-scoped instance lifecycles.

    Usage with FastAPI middleware:
        @app.middleware("http")
        async def scope_middleware(request: Request, call_next):
            async with ScopeManager.request_scope():
                response = await call_next(request)
            return response
    """

    @classmethod
    def begin_request(cls) -> dict[type, Any]:
        """
        Begin a new request scope.

        Returns the scope dictionary for manual management if needed.
        """
        scope_dict: dict[type, Any] = {}
        _request_scope.set(scope_dict)
        logger.debug("Request scope started")
        return scope_dict

    @classmethod
    def end_request(cls) -> None:
        """
        End the current request scope and cleanup instances.

        Calls dispose() on any instances that have it.
        """
        scope_dict = _request_scope.get()
        if scope_dict:
            for instance in scope_dict.values():
                if hasattr(instance, "dispose"):
                    try:
                        instance.dispose()
                    except (AttributeError, RuntimeError, TypeError) as e:
                        logger.warning(f"Error disposing {type(instance).__name__}: {e}")
            scope_dict.clear()
        _request_scope.set(None)
        logger.debug("Request scope ended")

    @classmethod
    def get_request_scope(cls) -> dict[type, Any] | None:
        """Get the current request scope dictionary."""
        return _request_scope.get()

    @classmethod
    def get_or_create(cls, key: type, factory: callable) -> Any:
        """
        Get an existing instance from request scope or create one.

        Args:
            key: The type to use as cache key
            factory: Callable to create a new instance if not cached

        Returns:
            The cached or newly created instance

        Raises:
            RuntimeError: If called outside a request scope
        """
        scope_dict = _request_scope.get()
        if scope_dict is None:
            raise RuntimeError(
                "No active request scope. Ensure ScopeManager.begin_request() " "was called (usually via middleware)."
            )

        if key not in scope_dict:
            scope_dict[key] = factory()
            logger.debug(f"Created request-scoped instance: {key.__name__}")

        return scope_dict[key]

    @classmethod
    async def request_scope(cls):
        """
        Async context manager for request scope.

        Usage:
            async with ScopeManager.request_scope():
                # Request-scoped services available here
                pass
        """
        return _RequestScopeContext()


class _RequestScopeContext:
    """Async context manager for request scope."""

    async def __aenter__(self):
        ScopeManager.begin_request()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        ScopeManager.end_request()
        return False
