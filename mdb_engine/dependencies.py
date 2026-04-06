"""
FastAPI Dependencies for MDB Engine

Provides:
1. RequestContext - All-in-one request-scoped dependency
2. Individual dependencies for fine-grained control
3. DI container integration

Usage:
    from fastapi import Depends
    from mdb_engine.dependencies import RequestContext, get_request_context

    @app.get("/users/{user_id}")
    async def get_user(user_id: str, ctx: RequestContext = Depends(get_request_context)):
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
    from .llm.service import LLMService
    from .memory import BaseMemoryService
    from .profile.service import ProfileService
    from .uploads.service import UploadService

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
    return await engine.get_scoped_db(slug)


async def get_unit_of_work(request: Request) -> UnitOfWork:
    """Get a request-scoped UnitOfWork."""
    db = await get_scoped_db(request)
    return UnitOfWork(db)


# =============================================================================
# AI/ML Service Dependencies
# =============================================================================


async def get_embedding_service(request: Request) -> "EmbeddingService":
    """Get the EmbeddingService for text embeddings.

    Returns the shared service created during app initialization.

    Raises:
        HTTPException(503): If no embedding service is available.
    """
    engine = await get_engine(request)
    slug = await get_app_slug(request)

    service = engine.get_embedding_service(slug)
    if service is not None:
        return service

    raise HTTPException(
        503,
        f"Embedding service not available for app '{slug}'. "
        "Configure embedding_config or memory_config in your manifest.",
    )


async def get_llm_service(request: Request) -> "LLMService":
    """Get the LLMService for the current app.

    Returns the cached service from memory/app initialization when available.

    Raises:
        HTTPException(503): If no LLM service is configured for this app.
    """
    engine = await get_engine(request)
    slug = await get_app_slug(request)

    service = engine.get_llm_service(slug)
    if service is not None:
        return service

    raise HTTPException(
        503,
        f"LLM service not configured for app '{slug}'. " "Add llm_config with providers to your manifest.",
    )


async def get_perfect_brain(request: Request) -> Any:
    """Get the PerfectBrain container for the current app.

    Raises:
        HTTPException(503): If the engine or PerfectBrain is not available.
    """
    engine = await get_engine(request)
    slug = await get_app_slug(request)

    brain = engine.get_perfect_brain(slug)
    if brain is None:
        raise HTTPException(
            503,
            f"PerfectBrain not configured for app '{slug}'. "
            "Enable it with perfect_brain.enabled=true in your manifest.",
        )
    return brain


async def get_memory_service(request: Request) -> "BaseMemoryService":
    """Get the memory service for the current app.

    Raises:
        HTTPException(503): If the engine, app slug, or memory service is
            not available.
    """
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise HTTPException(503, "MDB-Engine not initialized")
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        raise HTTPException(503, "App slug not configured")
    service = engine.get_memory_service(slug)
    if service is None:
        raise HTTPException(
            503,
            f"Memory service not configured for app '{slug}'. "
            "Enable it with memory_config.enabled=true in your manifest.",
        )
    return service


async def get_profile_service(request: Request) -> "ProfileService":
    """Get the profile service for the current app.

    Raises:
        HTTPException(503): If the engine, app slug, or profile service is
            not available.
    """
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise HTTPException(503, "MDB-Engine not initialized")
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        raise HTTPException(503, "App slug not configured")
    service = engine.get_profile_service(slug)
    if service is None:
        raise HTTPException(
            503,
            f"Profile service not configured for app '{slug}'. "
            "Enable it with profile_config.enabled=true in your manifest.",
        )
    return service


async def get_graph_service(request: Request) -> Any:
    """Get the graph/knowledge-graph service for the current app.

    Raises:
        HTTPException(503): If the engine or graph service is not available.
    """
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise HTTPException(503, "MDB-Engine not initialized")
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        raise HTTPException(503, "App slug not configured")
    service = engine.get_graph_service(slug)
    if service is None:
        raise HTTPException(
            503,
            f"Graph service not configured for app '{slug}'. "
            "Enable it with graph_config.enabled=true in your manifest.",
        )
    return service


async def get_graph_service_optional(request: Request) -> Any | None:
    """Get the graph service for the current app, or None if unavailable.

    Unlike ``get_graph_service``, this does NOT raise ``HTTPException``
    when the graph service isn't configured. Use for endpoints where graph
    enhances results but isn't required.
    """
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        return None
    slug = getattr(request.app.state, "app_slug", None)
    if not slug:
        return None
    return engine.get_graph_service(slug)


async def get_upload_service(request: Request) -> "UploadService":
    """Get the upload service for the current app.

    Raises:
        HTTPException(503): If the upload service is not configured.
    """
    service = getattr(request.app.state, "upload_service", None)
    if service is None:
        raise HTTPException(
            503,
            "Upload service not configured. " "Enable it with uploads.enabled=true in your manifest.",
        )
    return service


async def get_upload_service_optional(request: Request) -> "UploadService | None":
    """Get the upload service, or None if not configured."""
    return getattr(request.app.state, "upload_service", None)


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


async def get_oauth_service(request: Request) -> Any:
    """Get the OAuthService for the current app (if OAuth is configured).

    Returns the :class:`~mdb_engine.auth.oauth.OAuthService` instance stored
    on ``app.state`` during lifespan initialization, or ``None`` when OAuth
    is not enabled for this app.
    """
    return getattr(request.app.state, "oauth_service", None)


async def get_current_user(request: Request) -> dict[str, Any] | None:
    """Get the current authenticated user."""
    return getattr(request.state, "user", None)


async def get_user_roles(request: Request) -> list[str]:
    """Get the current user's roles."""
    return getattr(request.state, "user_roles", [])


def require_user() -> Callable:
    """Dependency that requires authentication."""

    async def _require_user(request: Request) -> dict[str, Any]:
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        return user

    return _require_user


def get_effective_roles(
    user: dict[str, Any],
    hierarchy: dict[str, list[str]] | None = None,
) -> set[str]:
    """Compute the full set of roles a user holds, expanding hierarchy.

    A user's roles come from ``user["role"]`` (string) and
    ``user["roles"]`` (list).  If a *hierarchy* mapping is provided,
    each role is expanded transitively (e.g. admin -> [editor, reader]).
    """
    roles: set[str] = set()
    single = user.get("role")
    if single:
        roles.add(str(single))
    multi = user.get("roles")
    if isinstance(multi, list | tuple | set | frozenset):
        roles |= {str(r) for r in multi if r}
    elif isinstance(multi, str) and multi:
        roles.add(multi)

    if not hierarchy:
        return roles

    expanded: set[str] = set()
    queue = list(roles)
    while queue:
        r = queue.pop()
        if r in expanded:
            continue
        expanded.add(r)
        for child in hierarchy.get(r, []):
            if child not in expanded:
                queue.append(child)
    return expanded


def require_role(*roles: str) -> Callable:
    """Dependency that requires specific roles."""

    async def _require_role(request: Request) -> dict[str, Any]:
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")
        hierarchy = getattr(request.app.state, "role_hierarchy", None)
        user_roles = get_effective_roles(user, hierarchy)
        state_roles = set(await get_user_roles(request))
        user_roles |= state_roles
        if not any(role in user_roles for role in roles):
            raise HTTPException(403, f"Required role: {' or '.join(roles)}")
        return user

    return _require_role


def require_collection_permission(
    collection: str,
    action: str,
    *,
    fallback_roles: list[str] | None = None,
) -> Callable:
    """Dependency that delegates to ``authz_provider.check()`` when available.

    Falls back to the inline ``require_role`` behaviour if no provider is
    configured, preserving backward compatibility for apps that haven't
    opted into a Casbin/OSO provider.

    Args:
        collection: Collection name (Casbin resource / OSO resource).
        action: CRUD action (``read``, ``write``, ``create``, ``delete``).
        fallback_roles: Roles to enforce via legacy ``require_role`` check
            when no ``authz_provider`` is present on ``app.state``.
    """

    async def _check(request: Request) -> dict[str, Any] | None:
        user = await get_current_user(request)
        authz = getattr(request.app.state, "authz_provider", None)

        if authz is not None:
            subject = user.get("email", "anonymous") if user else "*"
            allowed = await authz.check(subject, collection, action, user_object=user)
            if not allowed:
                if user is None:
                    raise HTTPException(401, "Authentication required")
                raise HTTPException(403, f"Permission denied: {action} on {collection}")
            return user

        # Fallback: no provider configured
        if user is None:
            raise HTTPException(401, "Authentication required")

        if fallback_roles:
            hierarchy = getattr(request.app.state, "role_hierarchy", None)
            user_roles = get_effective_roles(user, hierarchy)
            state_roles = set(await get_user_roles(request))
            user_roles |= state_roles
            if not any(role in user_roles for role in fallback_roles):
                raise HTTPException(403, f"Required role: {' or '.join(fallback_roles)}")

        return user

    return _check


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
        async def create_doc(
            data: DocCreate,
            ctx: RequestContext = Depends(get_request_context),
        ):
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
        self._llm_service_cached = None
        self._memory = None
        self._profile = None
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

    async def get_db(self):
        """Get the scoped database wrapper."""
        if self._db is None:
            self._db = await self.engine.get_scoped_db(self.slug)
        return self._db

    @property
    def db(self):
        """Get the scoped database wrapper (cached).

        Note: Call get_db() first in an async context to initialize,
        or use get_db() directly for async access.
        """
        if self._db is None:
            raise RuntimeError(
                "Database not initialized. Call 'await ctx.get_db()' first, "
                "or use 'db = await ctx.get_db()' instead of 'ctx.db'."
            )
        return self._db

    async def get_uow(self) -> UnitOfWork:
        """Get the Unit of Work for repository access."""
        if self._uow is None:
            db = await self.get_db()
            self._uow = UnitOfWork(db)
        return self._uow

    @property
    def uow(self) -> UnitOfWork:
        """Get the Unit of Work for repository access (cached).

        Note: Call get_uow() first in an async context to initialize,
        or use get_uow() directly for async access.
        """
        if self._uow is None:
            raise RuntimeError(
                "UnitOfWork not initialized. Call 'await ctx.get_uow()' first, "
                "or use 'uow = await ctx.get_uow()' instead of 'ctx.uow'."
            )
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
    def embedding_service(self) -> "EmbeddingService | None":
        """Get the shared EmbeddingService (None if not configured)."""
        if self._embedding_service is None:
            self._embedding_service = self.engine.get_embedding_service(self.slug)
        return self._embedding_service

    @property
    def memory(self) -> "BaseMemoryService | None":
        """Get the memory service (None if not configured)."""
        if self._memory is None:
            self._memory = self.engine.get_memory_service(self.slug)
        return self._memory

    @property
    def profile(self) -> "ProfileService | None":
        """Get the profile service (None if not configured)."""
        if self._profile is None:
            self._profile = self.engine.get_profile_service(self.slug)
        return self._profile

    @property
    def llm(self) -> "OpenAI | AzureOpenAI | None":
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
    def llm_service(self) -> "LLMService | None":
        """Get the high-level LLMService (preferred over raw ``llm`` client).

        Returns the cached service created during memory/app initialization,
        or ``None`` if not configured.
        """
        if self._llm_service_cached is None:
            self._llm_service_cached = self.engine.get_llm_service(self.slug)
        return self._llm_service_cached

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
    def authz(self) -> "AuthorizationProvider | None":
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
        hierarchy = getattr(self.request.app.state, "role_hierarchy", None)
        effective = get_effective_roles(user, hierarchy)
        effective |= set(self.user_roles)
        if not any(role in effective for role in roles):
            roles_str = " or ".join(roles)
            raise HTTPException(403, f"Required role: {roles_str}")
        return user

    async def check_permission(self, resource: str, action: str, subject: str | None = None) -> bool:
        """Check if current user has permission for an action."""
        if not self.authz:
            return True
        if subject is None:
            user = self.user
            subject = user.get("email", "anonymous") if user else "anonymous"
        return await self.authz.check(subject, resource, action)


async def get_request_context(request: Request) -> RequestContext:
    """Create a RequestContext for the current request."""
    return RequestContext(request=request)


class PlatformInfo:
    """Read-only view of the multi-app platform from a child app's perspective."""

    __slots__ = ("current_slug", "current_path", "apps")

    def __init__(
        self,
        current_slug: str,
        current_path: str,
        apps: list[dict[str, Any]],
    ):
        self.current_slug = current_slug
        self.current_path = current_path
        self.apps = apps

    def get_app(self, slug: str) -> dict[str, Any] | None:
        return next((a for a in self.apps if a.get("slug") == slug), None)


async def get_platform_info(request: Request) -> "PlatformInfo":
    """Dependency providing multi-app platform context to child apps.

    Exposes sibling apps, their slugs, and path prefixes so child apps
    can build navigation, cross-app links, and platform dashboards.
    """
    mounted = getattr(request.state, "mounted_apps", None) or {}
    apps_list = list(mounted.values()) if isinstance(mounted, dict) else mounted
    return PlatformInfo(
        current_slug=getattr(request.app.state, "app_slug", "unknown"),
        current_path=getattr(request.state, "app_base_path", ""),
        apps=apps_list,
    )


async def get_app_logger(request: Request) -> logging.Logger:
    """Get a logger pre-scoped to the current app's slug."""
    from .observability.logging import get_app_logger as _get_app_logger

    slug = getattr(request.app.state, "app_slug", "unknown")
    return _get_app_logger(slug)


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
    "get_graph_service",
    "get_profile_service",
    "get_upload_service",
    "get_upload_service_optional",
    "get_llm_client",
    "get_llm_model_name",
    "get_authz_provider",
    "get_oauth_service",
    "get_current_user",
    "get_user_roles",
    "require_user",
    "require_role",
    "get_request_context",
    "get_platform_info",
    "PlatformInfo",
    "get_app_logger",
    "RequestContext",
    "inject",
    "Inject",
]
