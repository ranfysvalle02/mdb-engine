"""
Database abstraction layer.

Provides scoped database access with automatic app isolation
and MongoDB-style API for familiarity.
"""

from .abstraction import AppDB, Collection, get_app_db
from .connection import (
    close_shared_client,
    get_pool_metrics,
    register_client_for_metrics,
    verify_shared_client,
)
from .query_validator import QueryValidator
from .resource_limiter import ResourceLimiter
from .scoped_wrapper import (
    AsyncAtlasIndexManager,
    AutoIndexManager,
    ScopedCollectionWrapper,
    ScopedMongoWrapper,
)

__all__ = [
    # Scoped wrappers
    "ScopedMongoWrapper",
    "ScopedCollectionWrapper",
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    # Query security
    "QueryValidator",
    "ResourceLimiter",
    # Database abstraction
    "AppDB",
    "Collection",
    "get_app_db",
    # Connection pooling
    "verify_shared_client",
    "get_pool_metrics",
    "register_client_for_metrics",
    "close_shared_client",
]
