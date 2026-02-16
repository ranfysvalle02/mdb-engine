"""
MDB_ENGINE - MongoDB Engine

Build MongoDB-backed Python apps with automatic data isolation,
manifest-driven configuration, and optional AI services.

Quick Start (zero-config):

    from mdb_engine import quickstart
    from mdb_engine.dependencies import get_scoped_db
    from fastapi import Depends

    app = quickstart("my_app")

    @app.get("/items")
    async def list_items(db=Depends(get_scoped_db)):
        return await db.items.find({}).to_list(10)

Configurable Setup (manifest-based):

    from mdb_engine import MongoDBEngine
    app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

In routes — use RequestContext for clean DI:

    from mdb_engine import RequestContext, get_request_context

    @app.get("/users/{user_id}")
    async def get_user(user_id: str, ctx: RequestContext = Depends(get_request_context)):
        user = await ctx.uow.users.get(user_id)
        return user
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

# Authentication
from .auth import AuthorizationProvider, require_admin
from .auth import get_current_user as auth_get_current_user  # noqa: F401

# Core MongoDB Engine
from .core import (
    ManifestParser,
    ManifestValidator,
    MongoDBEngine,
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
    get_request_context,
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

# Memory — simplified public names
from .memory import ChatEngine, MemoryService

# Repository pattern
from .repositories import Entity, MongoRepository, Repository, UnitOfWork

# Utilities
from .utils import clean_mongo_doc, clean_mongo_docs

__version__ = "0.7.8"


# ---------------------------------------------------------------------------
# quickstart — zero-config entry point
# ---------------------------------------------------------------------------


def quickstart(
    slug: str,
    name: str | None = None,
    mongo_uri: str | None = None,
    db_name: str | None = None,
    manifest: dict[str, Any] | Path | None = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """Create a FastAPI app with MDB Engine in one line.

    This is the fastest way to get started. It combines engine creation
    and app creation into a single call with sensible defaults.

    Args:
        slug: Unique application identifier (e.g. ``"my_app"``).
        name: Human-readable app name. Defaults to a title-cased *slug*.
        mongo_uri: MongoDB connection URI. Falls back to the ``MONGODB_URI``
            or ``MDB_MONGO_URI`` env var, then ``mongodb://localhost:27017``.
        db_name: Database name. Falls back to ``MDB_DB_NAME`` env var,
            then ``mdb_engine``.
        manifest: Optional manifest — a ``dict``, a ``Path`` to a JSON file,
            or ``None`` to auto-generate a minimal manifest.
        **fastapi_kwargs: Extra keyword arguments forwarded to ``FastAPI()``.

    Returns:
        A fully configured ``FastAPI`` application with engine lifecycle
        management, data scoping, and dependency injection ready to use.

    Example::

        from mdb_engine import quickstart
        from mdb_engine.dependencies import get_scoped_db
        from fastapi import Depends

        app = quickstart("my_app")

        @app.get("/items")
        async def list_items(db=Depends(get_scoped_db)):
            return await db.items.find({}).to_list(10)
    """
    engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)
    return engine.create_app(
        slug=slug,
        manifest=manifest,
        name=name,
        **fastapi_kwargs,
    )


__all__ = [
    # Zero-config entry point
    "quickstart",
    # Core Engine
    "MongoDBEngine",
    "ManifestValidator",
    "ManifestParser",
    # Simplified memory names
    "MemoryService",
    "ChatEngine",
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
    "get_request_context",
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
