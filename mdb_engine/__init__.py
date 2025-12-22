"""
MDB_ENGINE - MongoDB Engine

Enterprise-grade engine for building applications
with automatic database scoping, authentication, and resource management.
"""

# Core MongoDB Engine
from .core import MongoDBEngine, ManifestValidator, ManifestParser

# Database layer
from .database import (
    ScopedMongoWrapper,
    AppDB,
    get_shared_mongo_client,
)

# Authentication
from .auth import (
    AuthorizationProvider,
    get_current_user,
    require_admin,
)

# Index management
from .indexes import (
    AsyncAtlasIndexManager,
    AutoIndexManager,
    run_index_creation_for_collection,
)

__version__ = "0.1.5"

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
