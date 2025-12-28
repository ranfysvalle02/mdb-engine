"""
Database abstraction layer.

Provides scoped database access with automatic app isolation
and MongoDB-style API for familiarity.
"""

from .abstraction import AppDB, Collection, get_app_db
from .connection import (close_shared_client, get_pool_metrics,
                         get_shared_mongo_client, register_client_for_metrics,
                         verify_shared_client)
from .scoped_wrapper import (AsyncAtlasIndexManager, AutoIndexManager,
                             ScopedCollectionWrapper, ScopedMongoWrapper)

__all__ = [
    # Scoped wrappers
    "ScopedMongoWrapper",
    "ScopedCollectionWrapper",
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    # Database abstraction
    "AppDB",
    "Collection",
    "get_app_db",
    # Connection pooling
    "get_shared_mongo_client",
    "verify_shared_client",
    "get_pool_metrics",
    "register_client_for_metrics",
    "close_shared_client",
]
