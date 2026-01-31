"""
MDB_ENGINE - MongoDB Engine

Enterprise-grade engine for building applications with:
- Automatic database scoping and data isolation
- Proper dependency injection with service lifetimes
- Repository pattern for clean data access
- Authentication and authorization

Usage:
    # Simple usage
    from mdb_engine import MongoDBEngine
    engine = MongoDBEngine(mongo_uri=..., db_name=...)
    await engine.initialize()
    db = engine.get_scoped_db("my_app")

    # With FastAPI integration
    app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

    # In routes - use RequestContext for clean DI
    from mdb_engine import RequestContext

    @app.get("/users/{user_id}")
    async def get_user(user_id: str, ctx: RequestContext = Depends()):
        user = await ctx.uow.users.get(user_id)
        return user
"""

# Authentication
from .auth import AuthorizationProvider, require_admin
from .auth import get_current_user as auth_get_current_user  # noqa: F401

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

# FastAPI dependencies
from .dependencies import (
    Inject,
    RequestContext,
    get_app_config,
    get_app_slug,
    get_authz_provider,
    get_current_user,
    get_embedding_service,
    get_engine,
    get_llm_client,
    get_llm_model_name,
    get_memory_service,
    get_scoped_db,
    get_unit_of_work,
    get_user_roles,
    inject,
    require_role,
    require_user,
)

# DI Container
from .di import Container, Scope, ScopeManager

# Index management
from .indexes import (
    AsyncAtlasIndexManager,
    AutoIndexManager,
    run_index_creation_for_collection,
)

# Repository pattern
from .repositories import Entity, MongoRepository, Repository, UnitOfWork

# Utilities
from .utils import clean_mongo_doc, clean_mongo_docs

__version__ = (
    "0.4.8"  # Fix: WebSocket routes now work correctly with mounted apps
    # WebSocket routes are registered on parent app with full mount path prefix
)

__all__ = [
    # Core Engine
    "MongoDBEngine",
    "ManifestValidator",
    "ManifestParser",
    # Ray Integration (optional)
    "RAY_AVAILABLE",
    "AppRayActor",
    "get_ray_actor_handle",
    "ray_actor_decorator",
    # Database
    "ScopedMongoWrapper",
    "AppDB",
    # DI Container
    "Container",
    "Scope",
    "ScopeManager",
    # Repository Pattern
    "Repository",
    "MongoRepository",
    "Entity",
    "UnitOfWork",
    # Auth
    "AuthorizationProvider",
    "require_admin",
    # FastAPI Dependencies
    "RequestContext",
    "get_engine",
    "get_app_slug",
    "get_app_config",
    "get_scoped_db",
    "get_unit_of_work",
    "get_embedding_service",
    "get_memory_service",
    "get_llm_client",
    "get_llm_model_name",
    "get_authz_provider",
    "get_current_user",
    "get_user_roles",
    "require_user",
    "require_role",
    "inject",
    "Inject",
    # Indexes
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    "run_index_creation_for_collection",
    # Utilities
    "clean_mongo_doc",
    "clean_mongo_docs",
]
