"""
Scoped Access Mixin

Provides methods for getting scoped database wrappers and service instances.
"""

import logging
from typing import TYPE_CHECKING, Any

from ..database import ScopedMongoWrapper

if TYPE_CHECKING:
    from .app_secrets import AppSecretsManager
    from .connection import ConnectionManager
    from .service_initialization import ServiceInitializer

logger = logging.getLogger(__name__)


class ScopedAccessMixin:
    """Mixin providing scoped database access and service retrieval methods.

    Expects the following attributes from the host class (MongoDBEngine):
        _initialized: Whether the engine has been initialized.
        _connection_manager: Database connection manager.
        _app_secrets_manager: App secrets manager (optional).
        _app_read_scopes: Mapping of app slugs to authorized read scopes.
        _service_initializer: Service initializer for graph/memory services (optional).
    """

    # -- Attributes provided by MongoDBEngine --
    _initialized: bool
    _connection_manager: "ConnectionManager"
    _app_secrets_manager: "AppSecretsManager | None"
    _app_read_scopes: dict[str, list[str]]
    _service_initializer: "ServiceInitializer | None"
    _app_token_cache: dict[str, str]

    async def _get_scoped_db_impl(
        self,
        app_slug: str,
        app_token: str | None = None,
        read_scopes: list[str] | None = None,
        write_scope: str | None = None,
        auto_index: bool = True,
    ) -> ScopedMongoWrapper:
        """Shared implementation for scoped database access."""
        if not self._initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        # Auto-resolve token from cache when caller didn't provide one
        if app_token is None:
            app_token = getattr(self, "_app_token_cache", {}).get(app_slug)

        # Verify app token if secrets manager is available
        if self._app_secrets_manager:
            if app_token is None:
                # Check if app has stored secret (backward compatibility)
                has_secret = await self._app_secrets_manager.app_secret_exists(app_slug)
                if has_secret:
                    # Log detailed info
                    logger.warning(f"App token required for '{app_slug}'")
                    raise ValueError(f"App token required for '{app_slug}'. " "Provide app_token parameter.")
                # No stored secret - allow (backward compatibility for apps without secrets)
                logger.debug(f"App '{app_slug}' has no stored secret, " f"allowing access (backward compatibility)")
            else:
                # Verify token asynchronously
                is_valid = await self._app_secrets_manager.verify_app_secret(app_slug, app_token)
                if not is_valid:
                    # Log detailed info with app_slug
                    logger.warning(f"Security: Invalid app token for '{app_slug}'")
                    # Generic error message
                    raise ValueError("Invalid app token")

        # Validate read_scopes type FIRST (before authorization check)
        if read_scopes is not None:
            if not isinstance(read_scopes, list):
                raise ValueError(f"read_scopes must be a list, got {type(read_scopes)}")
            if len(read_scopes) == 0:
                raise ValueError("read_scopes cannot be empty")

        # Use manifest read_scopes if not provided
        if read_scopes is None:
            read_scopes = self._app_read_scopes.get(app_slug, [app_slug])

        if write_scope is None:
            write_scope = app_slug

        # Validate requested read_scopes against manifest authorization
        authorized_scopes = self._app_read_scopes.get(app_slug, [app_slug])
        for scope in read_scopes:
            if not isinstance(scope, str) or len(scope) == 0:
                logger.warning(f"Invalid app slug in read_scopes: {scope!r}")
                raise ValueError("Invalid app slug in read_scopes")
            if scope not in authorized_scopes:
                logger.warning(
                    f"App '{app_slug}' not authorized to read from '{scope}'. "
                    f"Authorized scopes: {authorized_scopes}"
                )
                raise ValueError(
                    "App not authorized to read from requested scope. "
                    "Update manifest data_access.read_scopes to grant access."
                )
        if not read_scopes:
            raise ValueError("read_scopes cannot be empty")
        for scope in read_scopes:
            if not isinstance(scope, str) or not scope:
                logger.warning(f"Invalid app slug in read_scopes: {scope}")
                raise ValueError("Invalid app slug in read_scopes")

        # Validate write_scope
        if not isinstance(write_scope, str) or not write_scope:
            raise ValueError(f"write_scope must be a non-empty string, got {write_scope}")

        return ScopedMongoWrapper(
            real_db=self._connection_manager.mongo_db,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index,
            app_slug=app_slug,
            app_token=app_token,
            app_secrets_manager=self._app_secrets_manager,
        )

    async def get_scoped_db(
        self,
        app_slug: str,
        app_token: str | None = None,
        read_scopes: list[str] | None = None,
        write_scope: str | None = None,
        auto_index: bool = True,
    ) -> ScopedMongoWrapper:
        """
        Get a scoped database wrapper for an app.

        The scoped database wrapper automatically filters queries by app_id
        to ensure data isolation between apps. All read operations are
        scoped to the specified read_scopes, and all write operations are
        tagged with the write_scope.

        Args:
            app_slug: App slug (used as default for both read and write scopes)
            app_token: App secret token for authentication. Required if app
                secrets manager is initialized. If None and app has stored secret,
                will attempt migration (backward compatibility).
            read_scopes: List of app slugs to read from. If None, uses manifest
                read_scopes or defaults to [app_slug]. Allows cross-app data access
                when needed.
            write_scope: App slug to write to. If None, defaults to app_slug.
                All documents inserted through this wrapper will have this as their
                app_id.
            auto_index: Whether to enable automatic index creation based on query
                patterns. Defaults to True. Set to False to disable automatic indexing.

        Returns:
            ScopedMongoWrapper instance configured with the specified scopes.

        Raises:
            RuntimeError: If engine is not initialized.
            ValueError: If app_token is invalid or read_scopes are unauthorized.

        Example:
            >>> db = await engine.get_scoped_db("my_app", app_token="secret-token")
            >>> # All queries are automatically scoped to "my_app"
            >>> doc = await db.my_collection.find_one({"name": "test"})
        """
        return await self._get_scoped_db_impl(
            app_slug=app_slug,
            app_token=app_token,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index,
        )

    async def get_scoped_db_async(
        self,
        app_slug: str,
        app_token: str | None = None,
        read_scopes: list[str] | None = None,
        write_scope: str | None = None,
        auto_index: bool = True,
    ) -> ScopedMongoWrapper:
        """
        Alias for get_scoped_db (async implementation).

        Args:
            app_slug: App slug (used as default for both read and write scopes)
            app_token: App secret token for authentication. Required if app
                secrets manager is initialized.
            read_scopes: List of app slugs to read from. If None, uses manifest
                read_scopes or defaults to [app_slug].
            write_scope: App slug to write to. If None, defaults to app_slug.
            auto_index: Whether to enable automatic index creation.

        Returns:
            ScopedMongoWrapper instance configured with the specified scopes.

        Raises:
            RuntimeError: If engine is not initialized.
            ValueError: If app_token is invalid or read_scopes are unauthorized.
        """
        return await self._get_scoped_db_impl(
            app_slug=app_slug,
            app_token=app_token,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index,
        )

    def get_graph_service(self, slug: str) -> Any | None:
        """
        Get graph service for an app (returns BaseGraphService instance).

        Args:
            slug: App slug

        Returns:
            BaseGraphService instance if graph is enabled for this app, None otherwise

        Example:
            ```python
            graph_service = engine.get_graph_service("my_app")
            if graph_service:
                # Add nodes
                graph_service.upsert_node(
                    "person:alex", "person", "Alex",
                    properties={"occupation": "Engineer"}
                )
                # Traverse
                network = graph_service.traverse("person:alex", max_depth=2)
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_graph_service(slug)
        return None

    def get_memory_service(self, slug: str) -> Any | None:
        """
        Get memory service for an app (returns BaseMemoryService instance).

        Args:
            slug: App slug

        Returns:
            BaseMemoryService instance (currently CustomMemoryService) if memory is enabled
            for this app, None otherwise

        Example:
            ```python
            memory_service = engine.get_memory_service("my_app")
            if memory_service:
                memories = memory_service.add(
                    messages=[{"role": "user", "content": "I love sci-fi movies"}],
                    user_id="alice"
                )
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_memory_service(slug)
        return None

    def get_profile_service(self, slug: str) -> Any | None:
        """
        Get profile service for an app.

        Args:
            slug: App slug

        Returns:
            ProfileService instance if profile system is enabled for this app,
            None otherwise.

        Example:
            ```python
            profile_service = engine.get_profile_service("my_app")
            if profile_service:
                profile = await profile_service.get_user_profile(user_id)
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_profile_service(slug)
        return None

    def get_embedding_service(self, slug: str) -> Any | None:
        """
        Get EmbeddingService for an app.

        Returns the shared service created by ``_ensure_shared_services``
        during app initialization.

        Args:
            slug: App slug

        Returns:
            EmbeddingService instance or None

        Example:
            ```python
            embedding_service = engine.get_embedding_service("my_app")
            if embedding_service:
                vectors = await embedding_service.embed(["Hello world"])
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_embedding_service(slug)
        return None

    def get_llm_service(self, slug: str) -> Any | None:
        """
        Get LLMService for an app.

        Returns the service created during memory/app initialization,
        or None if no LLM service was created for this app.

        Args:
            slug: App slug

        Returns:
            LLMService instance or None

        Example:
            ```python
            llm_service = engine.get_llm_service("my_app")
            if llm_service:
                response = await llm_service.chat_completion(
                    messages=[{"role": "user", "content": "Hello!"}]
                )
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_llm_service(slug)
        return None

    def get_perfect_brain(self, slug: str) -> Any | None:
        """
        Get the PerfectBrain container for an app.

        Returns a ``PerfectBrain`` instance that holds all enabled advanced
        memory components (SharedMemory, MemoryVeto, Consolidator, etc.).

        Args:
            slug: App slug

        Returns:
            PerfectBrain instance or None

        Example:
            ```python
            brain = engine.get_perfect_brain("my_app")
            if brain:
                vetoes = await brain.memory_veto.get_user_vetoes(user_id)
            ```
        """
        if self._service_initializer:
            return self._service_initializer.get_perfect_brain(slug)
        return None
