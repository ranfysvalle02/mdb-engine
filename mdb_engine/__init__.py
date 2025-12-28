"""
MDB_ENGINE - MongoDB Engine

Enterprise-grade engine for building applications
with automatic database scoping, authentication, and resource management.
"""

# Authentication
from .auth import AuthorizationProvider, get_current_user, require_admin
# Core MongoDB Engine
from .core import ManifestParser, ManifestValidator, MongoDBEngine
# Database layer
from .database import AppDB, ScopedMongoWrapper, get_shared_mongo_client
# Index management
from .indexes import (AsyncAtlasIndexManager, AutoIndexManager,
                      run_index_creation_for_collection)

__version__ = "0.1.6"

__all__ = [
    # Core
    "MongoDBEngine",
    "ManifestValidator",
    "ManifestParser",
    # Database
    "ScopedMongoWrapper",
    "AppDB",
    "get_shared_mongo_client",
    # Auth
    "AuthorizationProvider",
    "get_current_user",
    "require_admin",
    # Indexes
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    "run_index_creation_for_collection",
]
