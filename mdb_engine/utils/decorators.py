"""
Decorators and utilities for MDB_ENGINE.

These are internal helper functions that don't affect the public API.
They provide common patterns for error handling and validation.
"""
import logging
from functools import wraps
from typing import Callable, TypeVar, Awaitable, Any

from pymongo.errors import OperationFailure, AutoReconnect

logger = logging.getLogger(__name__)

T = TypeVar('T')


def handle_mongo_errors(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Decorator for consistent MongoDB error handling.
    
    This is a private helper decorator for internal use.
    It doesn't change the public API contract.
    
    The decorator:
    - Catches specific MongoDB exceptions (OperationFailure, AutoReconnect)
    - Logs errors with context
    - Re-raises exceptions for caller to handle
    
    Args:
        func: Async function to wrap
        
    Returns:
        Wrapped function with MongoDB error handling
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await func(*args, **kwargs)
        except OperationFailure as e:
            logger.error(f"MongoDB operation failed in {func.__name__}: {e.details}", exc_info=True)
            raise
        except AutoReconnect as e:
            logger.warning(f"MongoDB reconnection in {func.__name__}: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            raise
    
    return wrapper


def require_initialized(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Decorator to ensure engine is initialized before method execution.
    
    This is a private helper decorator for internal use.
    It doesn't change the public API contract.
    
    The decorator checks for a `_initialized` attribute on the instance
    and raises RuntimeError if not initialized.
    
    Args:
        func: Async method to wrap
        
    Returns:
        Wrapped method with initialization check
    """
    @wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
        if not getattr(self, '_initialized', False):
            raise RuntimeError(
                f"{self.__class__.__name__} not initialized. Call initialize() first."
            )
        return await func(self, *args, **kwargs)
    
    return wrapper

