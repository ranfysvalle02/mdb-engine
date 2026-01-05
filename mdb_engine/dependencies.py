"""
Request-Scoped FastAPI Dependencies for MDB Engine

This module provides a unified set of FastAPI dependency functions that retrieve
engine services from the request's app state. All dependencies are request-scoped,
meaning they automatically use the engine and app configuration set up by
`engine.create_app()`.

Usage:
    from fastapi import Depends
    from mdb_engine.dependencies import (
        get_engine,
        get_scoped_db,
        get_embedding_service,
        get_memory_service,
        get_app_config,
        get_app_slug,
        get_llm_client,
        get_authz_provider,
        AppContext,
    )

    # Simple dependency injection
    @app.post("/ingest")
    async def ingest(
        db=Depends(get_scoped_db),
        embedding_service=Depends(get_embedding_service),
    ):
        await embedding_service.process_and_store(...)

    # Get everything at once with AppContext
    @app.post("/chat")
    async def chat(ctx: AppContext = Depends()):
        # ctx.db, ctx.slug, ctx.config, ctx.engine all available
        docs = await ctx.db.messages.find({}).to_list(10)
        return {"app": ctx.slug, "count": len(docs)}

How it works:
    1. `engine.create_app()` sets `app.state.engine`, `app.state.app_slug`, etc.
    2. These dependencies read from `request.app.state` at request time
    3. No global state or manual binding required
"""

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from fastapi import Depends, HTTPException, Request

if TYPE_CHECKING:
    from openai import AzureOpenAI, OpenAI

    from .auth.provider import AuthorizationProvider
    from .core.engine import MongoDBEngine
    from .database.scoped_wrapper import ScopedMongoWrapper
    from .embeddings.service import EmbeddingService
    from .memory.service import Mem0MemoryService

logger = logging.getLogger(__name__)


# =============================================================================
# Core Dependencies
# =============================================================================


async def get_engine(request: Request) -> "MongoDBEngine":
    """
    Get the MongoDBEngine instance from request state.

    This dependency retrieves the engine that was set up by `engine.create_app()`.
    The engine is stored on `app.state.engine` during app initialization.

    Args:
        request: FastAPI Request object

    Returns:
        MongoDBEngine instance

    Raises:
        HTTPException(503): If engine is not initialized or not attached to app state

    Example:
        @app.get("/health")
        async def health(engine=Depends(get_engine)):
            return {"initialized": engine.initialized}
    """
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        logger.error("get_engine: Engine not found on app.state")
        raise HTTPException(
            status_code=503,
            detail="Engine not initialized. Ensure app was created with engine.create_app().",
        )
    if not engine.initialized:
        logger.error("get_engine: Engine found but not initialized")
        raise HTTPException(
            status_code=503,
            detail="Engine not fully initialized. Application is still starting up.",
        )
    return engine


async def get_app_slug(request: Request) -> str:
    """
    Get the current app's slug from request state.

    The app slug is set by `engine.create_app()` and identifies the current
    application for scoping database operations and retrieving app-specific
    configuration.

    Args:
        request: FastAPI Request object

    Returns:
        App slug string

    Raises:
        HTTPException(503): If app_slug is not set on app state

    Example:
        @app.get("/info")
        async def info(slug=Depends(get_app_slug)):
            return {"app": slug}
    """
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        logger.error("get_app_slug: app_slug not found on app.state")
        raise HTTPException(
            status_code=503,
            detail="App slug not configured. Ensure app was created with engine.create_app().",
        )
    return slug


async def get_app_config(request: Request) -> Dict[str, Any]:
    """
    Get the current app's manifest/configuration from request state.

    The manifest contains the app's configuration including auth settings,
    embedding config, memory config, managed indexes, etc.

    Args:
        request: FastAPI Request object

    Returns:
        App manifest dictionary

    Raises:
        HTTPException(503): If manifest is not available

    Example:
        @app.get("/config")
        async def config(manifest=Depends(get_app_config)):
            return {"embedding_enabled": manifest.get("embedding_config", {}).get("enabled")}
    """
    manifest = getattr(request.app.state, "manifest", None)
    if manifest is None:
        # Try to get from engine if not directly on state
        engine = getattr(request.app.state, "engine", None)
        slug = getattr(request.app.state, "app_slug", None)
        if engine and slug:
            manifest = engine.get_app(slug)

    if manifest is None:
        logger.error("get_app_config: manifest not found on app.state or engine")
        raise HTTPException(
            status_code=503,
            detail="App configuration not available. Ensure app was created with engine.create_app().",
        )
    return manifest


# =============================================================================
# Database Dependencies
# =============================================================================


async def get_scoped_db(request: Request) -> "ScopedMongoWrapper":
    """
    Get a scoped database wrapper for the current app.

    The scoped database automatically filters all queries by app_id,
    ensuring data isolation between apps. This is the primary way to
    interact with the database in route handlers.

    Args:
        request: FastAPI Request object

    Returns:
        ScopedMongoWrapper instance configured for the current app

    Raises:
        HTTPException(503): If engine or app_slug is not available

    Example:
        @app.get("/documents")
        async def list_docs(db=Depends(get_scoped_db)):
            docs = await db.my_collection.find({}).to_list(length=100)
            return {"documents": docs}

        @app.post("/documents")
        async def create_doc(db=Depends(get_scoped_db)):
            result = await db.my_collection.insert_one({"name": "test"})
            return {"id": str(result.inserted_id)}
    """
    engine = await get_engine(request)
    slug = await get_app_slug(request)
    return engine.get_scoped_db(slug)


# =============================================================================
# AI/ML Service Dependencies
# =============================================================================


async def get_embedding_service(request: Request) -> "EmbeddingService":
    """
    Get the EmbeddingService for the current app.

    The embedding service provides text chunking and embedding generation
    capabilities. It is configured via the `embedding_config` section in
    the app's manifest.json.

    Args:
        request: FastAPI Request object

    Returns:
        EmbeddingService instance configured for the current app

    Raises:
        HTTPException(503): If embedding service is not available or not configured

    Example:
        @app.post("/embed")
        async def embed_text(
            text: str,
            db=Depends(get_scoped_db),
            embedding_service=Depends(get_embedding_service),
        ):
            result = await embedding_service.process_and_store(
                text_content=text,
                source_id="user_input",
                collection=db.documents,
            )
            return {"chunks_created": result["chunks_created"]}
    """
    engine = await get_engine(request)
    slug = await get_app_slug(request)

    # Get app config to extract embedding_config
    app_config = engine.get_app(slug)
    if not app_config:
        logger.error(f"get_embedding_service: App config not found for '{slug}'")
        raise HTTPException(
            status_code=503,
            detail=f"App configuration not found for '{slug}'.",
        )

    embedding_config = app_config.get("embedding_config", {})
    if not embedding_config.get("enabled", True):
        logger.warning(f"get_embedding_service: Embedding disabled for '{slug}'")
        raise HTTPException(
            status_code=503,
            detail="Embedding service is disabled for this app. "
            "Set 'embedding_config.enabled: true' in manifest.json.",
        )

    # Import here to avoid circular imports
    from .embeddings.service import get_embedding_service as create_embedding_service

    try:
        return create_embedding_service(config=embedding_config)
    except Exception as e:
        logger.error(f"get_embedding_service: Failed to create service: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to initialize embedding service: {str(e)}. "
            "Check that AZURE_OPENAI_API_KEY/OPENAI_API_KEY environment variables are set.",
        )


async def get_memory_service(request: Request) -> Optional["Mem0MemoryService"]:
    """
    Get the Mem0 memory service for the current app, if configured.

    The memory service provides semantic memory capabilities using Mem0.
    It is configured via the `memory_config` section in the app's manifest.json.

    Unlike other dependencies, this returns None if memory is not configured,
    allowing routes to gracefully handle apps without memory support.

    Args:
        request: FastAPI Request object

    Returns:
        Mem0MemoryService instance if configured, None otherwise

    Example:
        @app.post("/chat")
        async def chat(
            query: str,
            user_id: str,
            memory=Depends(get_memory_service),
        ):
            context_memories = []
            if memory:
                # Memory is optional - only use if configured
                results = memory.search(query=query, user_id=user_id, limit=3)
                context_memories = [r.get("memory") for r in results if r.get("memory")]

            # Continue with chat logic...
            return {"memories_used": len(context_memories)}
    """
    engine = getattr(request.app.state, "engine", None)
    slug = getattr(request.app.state, "app_slug", None)

    if not engine or not slug:
        # Return None instead of raising - memory is optional
        return None

    # Use engine's get_memory_service which handles initialization
    return engine.get_memory_service(slug)


async def get_llm_client(request: Request) -> Union["AzureOpenAI", "OpenAI"]:
    """
    Get an OpenAI or AzureOpenAI client, auto-configured from environment variables.

    This dependency automatically detects whether to use Azure OpenAI or standard
    OpenAI based on environment variables and returns a configured client.

    Environment variables checked:
        Azure OpenAI (preferred if both set):
            - AZURE_OPENAI_API_KEY
            - AZURE_OPENAI_ENDPOINT
            - AZURE_OPENAI_API_VERSION (optional, defaults to "2024-02-01")

        Standard OpenAI:
            - OPENAI_API_KEY

    Args:
        request: FastAPI Request object

    Returns:
        AzureOpenAI or OpenAI client instance

    Raises:
        HTTPException(503): If no API keys are configured

    Example:
        @app.post("/chat")
        async def chat(
            message: str,
            llm=Depends(get_llm_client),
        ):
            response = llm.chat.completions.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
                messages=[{"role": "user", "content": message}],
            )
            return {"response": response.choices[0].message.content}
    """
    # Check for Azure OpenAI first (preferred)
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    if azure_key and azure_endpoint:
        from openai import AzureOpenAI

        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        return AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
        )

    # Fall back to standard OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        from openai import OpenAI

        return OpenAI(api_key=openai_key)

    raise HTTPException(
        status_code=503,
        detail="No LLM API key configured. Set AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT "
        "or OPENAI_API_KEY environment variable.",
    )


def get_llm_model_name() -> str:
    """
    Get the configured LLM model/deployment name.

    Returns the deployment name for Azure OpenAI or model name for OpenAI,
    with sensible defaults.

    Returns:
        Model/deployment name string

    Example:
        @app.post("/chat")
        async def chat(llm=Depends(get_llm_client)):
            model = get_llm_model_name()
            response = llm.chat.completions.create(
                model=model,
                messages=[...],
            )
    """
    # Azure OpenAI uses deployment names
    if os.getenv("AZURE_OPENAI_API_KEY"):
        return os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    # Standard OpenAI uses model names
    return os.getenv("OPENAI_MODEL", "gpt-4o")


# =============================================================================
# Auth Dependencies
# =============================================================================


async def get_authz_provider(request: Request) -> Optional["AuthorizationProvider"]:
    """
    Get the authorization provider from app state.

    The authorization provider (Casbin, OSO, or custom) is set up by
    `engine.create_app()` based on the auth configuration in manifest.json.

    Args:
        request: FastAPI Request object

    Returns:
        AuthorizationProvider instance if configured, None otherwise

    Example:
        @app.get("/protected")
        async def protected(
            authz=Depends(get_authz_provider),
        ):
            if authz:
                allowed = await authz.check("user@example.com", "documents", "read")
                if not allowed:
                    raise HTTPException(403, "Access denied")
            return {"data": "secret"}
    """
    return getattr(request.app.state, "authz_provider", None)


async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Get the current authenticated user from request state.

    The user is populated by auth middleware (SharedAuthMiddleware or app-specific)
    based on the auth configuration in manifest.json.

    Args:
        request: FastAPI Request object

    Returns:
        User dictionary if authenticated, None otherwise

    Example:
        @app.get("/me")
        async def me(user=Depends(get_current_user)):
            if not user:
                raise HTTPException(401, "Not authenticated")
            return {"email": user.get("email"), "roles": user.get("roles", [])}
    """
    return getattr(request.state, "user", None)


async def get_user_roles(request: Request) -> list:
    """
    Get the current user's roles for this app.

    Roles are populated by auth middleware based on the app's auth configuration.

    Args:
        request: FastAPI Request object

    Returns:
        List of role strings, empty list if not authenticated

    Example:
        @app.get("/admin")
        async def admin(roles=Depends(get_user_roles)):
            if "admin" not in roles:
                raise HTTPException(403, "Admin access required")
            return {"admin": True}
    """
    return getattr(request.state, "user_roles", [])


# =============================================================================
# AppContext - All-in-One Dependency
# =============================================================================


@dataclass
class AppContext:
    """
    A convenience class that bundles common dependencies together.

    Use this when you need access to multiple services in a single route.
    All properties are lazily loaded from the request.

    Example:
        @app.post("/process")
        async def process(ctx: AppContext = Depends()):
            # Access all dependencies through ctx
            docs = await ctx.db.documents.find({}).to_list(10)

            if ctx.embedding_service:
                embeddings = await ctx.embedding_service.embed_chunks(["hello"])

            if ctx.memory:
                memories = ctx.memory.search("query", user_id="user1")

            return {
                "app": ctx.slug,
                "user": ctx.user.get("email") if ctx.user else None,
                "docs_count": len(docs),
            }
    """

    request: Request
    _engine: Optional["MongoDBEngine"] = field(default=None, repr=False)
    _db: Optional["ScopedMongoWrapper"] = field(default=None, repr=False)
    _slug: Optional[str] = field(default=None, repr=False)
    _config: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _embedding_service: Optional["EmbeddingService"] = field(default=None, repr=False)
    _memory: Optional["Mem0MemoryService"] = field(default=None, repr=False)
    _llm: Optional[Union["AzureOpenAI", "OpenAI"]] = field(default=None, repr=False)
    _user: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _authz: Optional["AuthorizationProvider"] = field(default=None, repr=False)

    @property
    def engine(self) -> "MongoDBEngine":
        """Get the MongoDBEngine instance."""
        if self._engine is None:
            engine = getattr(self.request.app.state, "engine", None)
            if not engine or not engine.initialized:
                raise HTTPException(503, "Engine not initialized")
            self._engine = engine
        return self._engine

    @property
    def slug(self) -> str:
        """Get the current app's slug."""
        if self._slug is None:
            self._slug = getattr(self.request.app.state, "app_slug", None)
            if not self._slug:
                raise HTTPException(503, "App slug not configured")
        return self._slug

    @property
    def db(self) -> "ScopedMongoWrapper":
        """Get the scoped database for the current app."""
        if self._db is None:
            self._db = self.engine.get_scoped_db(self.slug)
        return self._db

    @property
    def config(self) -> Dict[str, Any]:
        """Get the app's manifest configuration."""
        if self._config is None:
            self._config = getattr(self.request.app.state, "manifest", None)
            if self._config is None:
                self._config = self.engine.get_app(self.slug) or {}
        return self._config

    @property
    def embedding_service(self) -> Optional["EmbeddingService"]:
        """Get the embedding service (None if not configured)."""
        if self._embedding_service is None:
            embedding_config = self.config.get("embedding_config", {})
            if embedding_config.get("enabled", True):
                try:
                    from .embeddings.service import (
                        get_embedding_service as create_embedding_service,
                    )

                    self._embedding_service = create_embedding_service(
                        config=embedding_config
                    )
                except Exception:
                    pass  # Return None if creation fails
        return self._embedding_service

    @property
    def memory(self) -> Optional["Mem0MemoryService"]:
        """Get the memory service (None if not configured)."""
        if self._memory is None:
            self._memory = self.engine.get_memory_service(self.slug)
        return self._memory

    @property
    def llm(self) -> Optional[Union["AzureOpenAI", "OpenAI"]]:
        """Get the LLM client (None if not configured)."""
        if self._llm is None:
            azure_key = os.getenv("AZURE_OPENAI_API_KEY")
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

            if azure_key and azure_endpoint:
                from openai import AzureOpenAI

                api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
                self._llm = AzureOpenAI(
                    api_key=azure_key,
                    azure_endpoint=azure_endpoint,
                    api_version=api_version,
                )
            elif os.getenv("OPENAI_API_KEY"):
                from openai import OpenAI

                self._llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        return self._llm

    @property
    def llm_model(self) -> str:
        """Get the LLM model/deployment name."""
        return get_llm_model_name()

    @property
    def user(self) -> Optional[Dict[str, Any]]:
        """Get the current authenticated user (None if not authenticated)."""
        if self._user is None:
            self._user = getattr(self.request.state, "user", None)
        return self._user

    @property
    def user_roles(self) -> list:
        """Get the current user's roles."""
        return getattr(self.request.state, "user_roles", [])

    @property
    def authz(self) -> Optional["AuthorizationProvider"]:
        """Get the authorization provider (None if not configured)."""
        if self._authz is None:
            self._authz = getattr(self.request.app.state, "authz_provider", None)
        return self._authz

    def require_user(self) -> Dict[str, Any]:
        """
        Get current user, raising 401 if not authenticated.

        Example:
            @app.get("/profile")
            async def profile(ctx: AppContext = Depends()):
                user = ctx.require_user()  # Raises 401 if not logged in
                return {"email": user["email"]}
        """
        if not self.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return self.user

    def require_role(self, *roles: str) -> Dict[str, Any]:
        """
        Require user has at least one of the specified roles.

        Args:
            *roles: Role names to check

        Returns:
            User dictionary

        Raises:
            HTTPException(401): If not authenticated
            HTTPException(403): If user lacks required role

        Example:
            @app.get("/admin")
            async def admin(ctx: AppContext = Depends()):
                user = ctx.require_role("admin", "superuser")
                return {"admin": True}
        """
        user = self.require_user()
        user_roles = set(self.user_roles)

        if not any(role in user_roles for role in roles):
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {' or '.join(roles)}. You have: {', '.join(user_roles) or 'none'}",
            )

        return user

    async def check_permission(
        self, resource: str, action: str, subject: Optional[str] = None
    ) -> bool:
        """
        Check if current user has permission for an action.

        Args:
            resource: Resource to check (e.g., "documents")
            action: Action to check (e.g., "read", "write")
            subject: Optional subject override (defaults to user email)

        Returns:
            True if allowed, False otherwise

        Example:
            @app.delete("/documents/{doc_id}")
            async def delete_doc(doc_id: str, ctx: AppContext = Depends()):
                if not await ctx.check_permission("documents", "delete"):
                    raise HTTPException(403, "Cannot delete documents")
                await ctx.db.documents.delete_one({"_id": doc_id})
        """
        if not self.authz:
            return True  # No authz configured = allow all

        if subject is None:
            user = self.user
            subject = user.get("email", "anonymous") if user else "anonymous"

        return await self.authz.check(subject, resource, action)


async def _get_app_context(request: Request) -> AppContext:
    """Internal dependency for creating AppContext."""
    return AppContext(request=request)


# Make AppContext work as a dependency
AppContext.__call__ = staticmethod(_get_app_context)


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Core
    "get_engine",
    "get_app_slug",
    "get_app_config",
    # Database
    "get_scoped_db",
    # AI/ML Services
    "get_embedding_service",
    "get_memory_service",
    "get_llm_client",
    "get_llm_model_name",
    # Auth
    "get_authz_provider",
    "get_current_user",
    "get_user_roles",
    # All-in-one
    "AppContext",
]
