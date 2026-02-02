"""
FastAPI Dependencies for MDB Engine

Provides:
1. RequestContext - All-in-one request-scoped dependency
2. Individual dependencies for fine-grained control
3. DI container integration

Usage:
    from fastapi import Depends
    from mdb_engine.dependencies import RequestContext

    @app.get("/users/{user_id}")
    async def get_user(user_id: str, ctx: RequestContext = Depends()):
        user = await ctx.uow.users.get(user_id)
        return user
"""

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional, TypeVar, Union

from fastapi import HTTPException, Request

from .di import Container
from .repositories import UnitOfWork

if TYPE_CHECKING:
    from openai import AzureOpenAI, OpenAI

    from .auth.provider import AuthorizationProvider
    from .core.engine import MongoDBEngine
    from .database.scoped_wrapper import ScopedMongoWrapper
    from .embeddings.service import EmbeddingService
    from .memory import BaseMemoryService

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Core Engine Dependencies
# =============================================================================


async def get_engine(request: Request) -> "MongoDBEngine":
    """Get the MongoDBEngine instance from app state."""
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise HTTPException(503, "Engine not initialized")
    if not engine.initialized:
        raise HTTPException(503, "Engine not fully initialized")
    return engine


async def get_app_slug(request: Request) -> str:
    """Get the current app's slug."""
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        raise HTTPException(503, "App slug not configured")
    return slug


async def get_app_config(request: Request) -> dict[str, Any]:
    """Get the app's manifest configuration."""
    manifest = getattr(request.app.state, "manifest", None)
    if manifest is None:
        engine = getattr(request.app.state, "engine", None)
        slug = getattr(request.app.state, "app_slug", None)
        if engine and slug:
            manifest = engine.get_app(slug)
    if manifest is None:
        raise HTTPException(503, "App configuration not available")
    return manifest


# =============================================================================
# Database Dependencies
# =============================================================================


async def get_scoped_db(request: Request) -> "ScopedMongoWrapper":
    """Get a scoped database wrapper for the current app."""
    engine = await get_engine(request)
    slug = await get_app_slug(request)
    return engine.get_scoped_db(slug)


async def get_unit_of_work(request: Request) -> UnitOfWork:
    """Get a request-scoped UnitOfWork."""
    db = await get_scoped_db(request)
    return UnitOfWork(db)


# =============================================================================
# AI/ML Service Dependencies
# =============================================================================


async def get_embedding_service(request: Request) -> "EmbeddingService":
    """Get the EmbeddingService for text embeddings."""
    engine = await get_engine(request)
    slug = await get_app_slug(request)

    app_config = engine.get_app(slug)
    if not app_config:
        raise HTTPException(503, f"App configuration not found for '{slug}'")

    embedding_config = app_config.get("embedding_config", {})
    if not embedding_config.get("enabled", True):
        raise HTTPException(503, "Embedding service is disabled")

    from .embeddings.service import EmbeddingServiceError
    from .embeddings.service import get_embedding_service as create_service

    try:
        return create_service(config=embedding_config)
    except (EmbeddingServiceError, ValueError, RuntimeError, ImportError, AttributeError) as e:
        raise HTTPException(503, f"Failed to initialize embedding service: {e}") from e


async def get_memory_service(request: Request) -> Optional["BaseMemoryService"]:
    """Get the memory service if configured (defaults to Mem0 implementation)."""
    engine = getattr(request.app.state, "engine", None)
    slug = getattr(request.app.state, "app_slug", None)
    if not engine or not slug:
        return None
    return engine.get_memory_service(slug)


async def get_llm_client(request: Request) -> Union["AzureOpenAI", "OpenAI"]:
    """Get an OpenAI/AzureOpenAI client."""
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    if azure_key and azure_endpoint:
        from openai import AzureOpenAI

        return AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        from openai import OpenAI

        return OpenAI(api_key=openai_key)

    raise HTTPException(503, "No LLM API key configured")


def get_llm_model_name() -> str:
    """Get the configured LLM model/deployment name."""
    if os.getenv("AZURE_OPENAI_API_KEY"):
        return os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    return os.getenv("OPENAI_MODEL", "gpt-4o")


# =============================================================================
# Auth Dependencies
# =============================================================================


async def get_authz_provider(request: Request) -> Optional["AuthorizationProvider"]:
    """Get the authorization provider if configured."""
    return getattr(request.app.state, "authz_provider", None)


async def get_current_user(request: Request) -> dict[str, Any] | None:
    """Get the current authenticated user."""
    return getattr(request.state, "user", None)


async def get_user_roles(request: Request) -> list[str]:
    """Get the current user's roles."""
    return getattr(request.state, "user_roles", [])


def require_user():
    """Dependency that requires authentication."""

    async def _require_user(request: Request) -> dict[str, Any]:
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        return user

    return _require_user


def require_role(*roles: str):
    """Dependency that requires specific roles."""

    async def _require_role(request: Request) -> dict[str, Any]:
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        user_roles = set(await get_user_roles(request))
        if not any(role in user_roles for role in roles):
            raise HTTPException(403, f"Required role: {' or '.join(roles)}")
        return user

    return _require_role


# =============================================================================
# RequestContext - All-in-One Dependency (Regular class, not dataclass!)
# =============================================================================


class RequestContext:
    """
    All-in-one request context with lazy-loaded dependencies.

    This is NOT a dataclass to avoid FastAPI trying to analyze
    fields as Pydantic types.

    Usage:
        @app.post("/documents")
        async def create_doc(data: DocCreate, ctx: RequestContext = Depends()):
            doc_id = await ctx.uow.documents.add(doc)
            return {"id": doc_id}
    """

    def __init__(self, request: Request):
        self.request = request
        self._uow = None
        self._engine = None
        self._db = None
        self._slug = None
        self._config = None
        self._embedding_service = None
        self._memory = None
        self._llm = None
        self._user = None
        self._authz = None

    @property
    def engine(self):
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
    def db(self):
        """Get the scoped database wrapper."""
        if self._db is None:
            self._db = self.engine.get_scoped_db(self.slug)
        return self._db

    @property
    def uow(self) -> UnitOfWork:
        """Get the Unit of Work for repository access."""
        if self._uow is None:
            self._uow = UnitOfWork(self.db)
        return self._uow

    @property
    def config(self) -> dict[str, Any]:
        """Get the app's manifest configuration."""
        if self._config is None:
            self._config = getattr(self.request.app.state, "manifest", None)
            if self._config is None:
                self._config = self.engine.get_app(self.slug) or {}
        return self._config

    @property
    def embedding_service(self):
        """Get the embedding service (None if not configured)."""
        if self._embedding_service is None:
            embedding_config = self.config.get("embedding_config", {})
            if embedding_config.get("enabled", True):
                try:
                    from .embeddings.service import (
                        EmbeddingServiceError,
                        get_embedding_service,
                    )

                    self._embedding_service = get_embedding_service(config=embedding_config)
                except (EmbeddingServiceError, ValueError, RuntimeError, ImportError):
                    pass
        return self._embedding_service

    @property
    def memory(self):
        """Get the memory service (None if not configured)."""
        if self._memory is None:
            self._memory = self.engine.get_memory_service(self.slug)
        return self._memory

    @property
    def llm(self):
        """Get the LLM client (None if not configured)."""
        if self._llm is None:
            azure_key = os.getenv("AZURE_OPENAI_API_KEY")
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

            if azure_key and azure_endpoint:
                from openai import AzureOpenAI

                self._llm = AzureOpenAI(
                    api_key=azure_key,
                    azure_endpoint=azure_endpoint,
                    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
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
    def user(self) -> dict[str, Any] | None:
        """Get the current authenticated user."""
        if self._user is None:
            self._user = getattr(self.request.state, "user", None)
        return self._user

    @property
    def user_roles(self) -> list[str]:
        """Get the current user's roles."""
        return getattr(self.request.state, "user_roles", [])

    @property
    def authz(self):
        """Get the authorization provider."""
        if self._authz is None:
            self._authz = getattr(self.request.app.state, "authz_provider", None)
        return self._authz

    def require_user(self) -> dict[str, Any]:
        """Require authentication, raising 401 if not authenticated."""
        if not self.user:
            raise HTTPException(401, "Authentication required")
        return self.user

    def require_role(self, *roles: str) -> dict[str, Any]:
        """Require specific roles, raising 403 if not authorized."""
        user = self.require_user()
        user_roles = set(self.user_roles)
        if not any(role in user_roles for role in roles):
            roles_str = " or ".join(roles)
            raise HTTPException(403, f"Required role: {roles_str}")
        return user

    async def check_permission(
        self, resource: str, action: str, subject: str | None = None
    ) -> bool:
        """Check if current user has permission for an action."""
        if not self.authz:
            return True
        if subject is None:
            user = self.user
            subject = user.get("email", "anonymous") if user else "anonymous"
        return await self.authz.check(subject, resource, action)


async def _get_request_context(request: Request) -> RequestContext:
    """Create a RequestContext for the current request."""
    return RequestContext(request=request)


# Make RequestContext usable with Depends()
RequestContext.__call__ = staticmethod(_get_request_context)


# =============================================================================
# DI Container Integration
# =============================================================================


def inject(service_type: type[T]) -> Callable[..., T]:
    """Create a dependency that resolves a service from the DI container."""

    async def _resolve(request: Request) -> T:
        container = getattr(request.app.state, "container", None)
        if container is None:
            container = Container.get_global()
        return container.resolve(service_type)

    return _resolve


Inject = inject


__all__ = [
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
    "RequestContext",
    "inject",
    "Inject",
]
