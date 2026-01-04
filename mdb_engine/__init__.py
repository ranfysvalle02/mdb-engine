"""
MDB_ENGINE - MongoDB Engine

Enterprise-grade engine for building applications
with automatic database scoping, authentication, and resource management.

Usage:
    # Simple usage
    from mdb_engine import MongoDBEngine
    engine = MongoDBEngine(mongo_uri=..., db_name=...)
    await engine.initialize()
    db = engine.get_scoped_db("my_app")

    # With FastAPI integration
    app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

    # With Ray support (optional)
    engine = MongoDBEngine(..., enable_ray=True)
"""

# Authentication
from .auth import AuthorizationProvider, get_current_user, require_admin

# Optional Ray integration
# Core MongoDB Engine
from .core import (
    RAY_AVAILABLE,
    AppRayActor,
    ManifestParser,
    ManifestValidator,
    MongoDBEngine,
    get_ray_actor_handle,
    ray_actor_decorator,
)

# Database layer
from .database import AppDB, ScopedMongoWrapper

# Index management
from .indexes import (
    AsyncAtlasIndexManager,
    AutoIndexManager,
    run_index_creation_for_collection,
)

__version__ = "0.1.6"

__all__ = [
    # Core (includes FastAPI integration and optional Ray)
    "MongoDBEngine",
    "ManifestValidator",
    "ManifestParser",
    # Ray Integration (optional - only active if Ray installed)
    "RAY_AVAILABLE",
    "AppRayActor",
    "get_ray_actor_handle",
    "ray_actor_decorator",
    # Database
    "ScopedMongoWrapper",
    "AppDB",
    # Auth
    "AuthorizationProvider",
    "get_current_user",
    "require_admin",
    # Indexes
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    "run_index_creation_for_collection",
]
