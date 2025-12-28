"""
Engine

The core orchestration engine for MDB_ENGINE that manages:
- Database connections
- Experiment registration
- Authentication/authorization
- Index management
- Resource lifecycle

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

if TYPE_CHECKING:
    from ..auth import AuthorizationProvider
    from .types import ManifestDict

# Import engine components
from ..constants import DEFAULT_MAX_POOL_SIZE, DEFAULT_MIN_POOL_SIZE
from ..database import ScopedMongoWrapper
from ..observability import (HealthChecker, check_engine_health,
                             check_mongodb_health, check_pool_health)
from ..observability import get_logger as get_contextual_logger
from .app_registration import AppRegistrationManager
from .connection import ConnectionManager
from .index_management import IndexManager
from .manifest import ManifestParser, ManifestValidator
from .service_initialization import ServiceInitializer

logger = logging.getLogger(__name__)
# Use contextual logger for better observability
contextual_logger = get_contextual_logger(__name__)


class MongoDBEngine:
    """
    The MongoDB Engine for managing multi-app applications.

    This class orchestrates all engine components including:
    - Database connections and scoping
    - Manifest validation and parsing
    - App registration
    - Index management
    - Authentication/authorization setup
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        manifests_dir: Optional[Path] = None,
        authz_provider: Optional["AuthorizationProvider"] = None,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
    ) -> None:
        """
        Initialize the MongoDB Engine.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            manifests_dir: Path to manifests directory (optional)
            authz_provider: Authorization provider instance (optional, can be set later)
            max_pool_size: Maximum MongoDB connection pool size
            min_pool_size: Minimum MongoDB connection pool size
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.manifests_dir = manifests_dir
        self.authz_provider = authz_provider
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size

        # Initialize component managers
        self._connection_manager = ConnectionManager(
            mongo_uri=mongo_uri,
            db_name=db_name,
            max_pool_size=max_pool_size,
            min_pool_size=min_pool_size,
        )

        # Validators
        self.manifest_validator = ManifestValidator()
        self.manifest_parser = ManifestParser()

        # Initialize managers (will be set up after connection is established)
        self._app_registration_manager: Optional[AppRegistrationManager] = None
        self._index_manager: Optional[IndexManager] = None
        self._service_initializer: Optional[ServiceInitializer] = None

    async def initialize(self) -> None:
        """
        Initialize the MongoDB Engine.

        This method:
        1. Connects to MongoDB
        2. Validates the connection
        3. Sets up initial state

        Raises:
            InitializationError: If initialization fails (subclass of RuntimeError
                for backward compatibility)
            RuntimeError: If initialization fails (for backward compatibility)
        """
        # Initialize connection
        await self._connection_manager.initialize()

        # Set up component managers
        self._app_registration_manager = AppRegistrationManager(
            mongo_db=self._connection_manager.mongo_db,
            manifest_validator=self.manifest_validator,
            manifest_parser=self.manifest_parser,
        )

        self._index_manager = IndexManager(mongo_db=self._connection_manager.mongo_db)

        self._service_initializer = ServiceInitializer(
            mongo_uri=self.mongo_uri,
            db_name=self.db_name,
            get_scoped_db_fn=self.get_scoped_db,
        )

    @property
    def mongo_client(self) -> AsyncIOMotorClient:
        """
        Get the MongoDB client.

        Returns:
            AsyncIOMotorClient instance

        Raises:
            RuntimeError: If engine is not initialized
        """
        return self._connection_manager.mongo_client

    @property
    def mongo_db(self) -> AsyncIOMotorDatabase:
        """
        Get the MongoDB database.

        Returns:
            AsyncIOMotorDatabase instance

        Raises:
            RuntimeError: If engine is not initialized
        """
        return self._connection_manager.mongo_db

    @property
    def _initialized(self) -> bool:
        """Check if engine is initialized."""
        return self._connection_manager.initialized

    def get_scoped_db(
        self,
        app_slug: str,
        read_scopes: Optional[List[str]] = None,
        write_scope: Optional[str] = None,
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
            read_scopes: List of app slugs to read from. If None, defaults to
                [app_slug]. Allows cross-app data access when needed.
            write_scope: App slug to write to. If None, defaults to app_slug.
                All documents inserted through this wrapper will have this as their
                app_id.
            auto_index: Whether to enable automatic index creation based on query
                patterns. Defaults to True. Set to False to disable automatic indexing.

        Returns:
            ScopedMongoWrapper instance configured with the specified scopes.

        Raises:
            RuntimeError: If engine is not initialized.

        Example:
            >>> db = engine.get_scoped_db("my_app")
            >>> # All queries are automatically scoped to "my_app"
            >>> doc = await db.my_collection.find_one({"name": "test"})
        """
        if not self._initialized:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )

        if read_scopes is None:
            read_scopes = [app_slug]
        if write_scope is None:
            write_scope = app_slug

        return ScopedMongoWrapper(
            real_db=self._connection_manager.mongo_db,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index,
        )

    async def validate_manifest(
        self, manifest: "ManifestDict"
    ) -> Tuple[bool, Optional[str], Optional[List[str]]]:
        """
        Validate a manifest against the schema.

        Args:
            manifest: Manifest dictionary to validate. Must be a valid
                dictionary containing experiment configuration.

        Returns:
            Tuple of (is_valid, error_message, error_paths):
            - is_valid: True if manifest is valid, False otherwise
            - error_message: Human-readable error message if invalid, None if valid
            - error_paths: List of JSON paths with validation errors, None if valid
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return await self._app_registration_manager.validate_manifest(manifest)

    async def load_manifest(self, path: Path) -> "ManifestDict":
        """
        Load and validate a manifest from a file.

        Args:
            path: Path to manifest.json file

        Returns:
            Validated manifest dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If validation fails
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return await self._app_registration_manager.load_manifest(path)

    async def register_app(
        self, manifest: "ManifestDict", create_indexes: bool = True
    ) -> bool:
        """
        Register an app from its manifest.

        This method validates the manifest, stores the app configuration,
        and optionally creates managed indexes defined in the manifest.

        Args:
            manifest: Validated manifest dictionary containing app
                configuration. Must include 'slug' field.
            create_indexes: Whether to create managed indexes defined in
                the manifest. Defaults to True.

        Returns:
            True if registration successful, False otherwise.
            Returns False if manifest validation fails or slug is missing.

        Raises:
            RuntimeError: If engine is not initialized.
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )

        # Create callbacks for service initialization
        async def create_indexes_callback(slug: str, manifest: "ManifestDict") -> None:
            if self._index_manager and create_indexes:
                await self._index_manager.create_app_indexes(slug, manifest)

        async def seed_data_callback(slug: str, initial_data: Dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.seed_initial_data(slug, initial_data)

        async def initialize_memory_callback(
            slug: str, memory_config: Dict[str, Any]
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.initialize_memory_service(
                    slug, memory_config
                )

        async def register_websockets_callback(
            slug: str, websockets_config: Dict[str, Any]
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.register_websockets(
                    slug, websockets_config
                )

        async def setup_observability_callback(
            slug: str,
            manifest: "ManifestDict",
            observability_config: Dict[str, Any],
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.setup_observability(
                    slug, manifest, observability_config
                )

        return await self._app_registration_manager.register_app(
            manifest=manifest,
            create_indexes_callback=create_indexes_callback if create_indexes else None,
            seed_data_callback=seed_data_callback,
            initialize_memory_callback=initialize_memory_callback,
            register_websockets_callback=register_websockets_callback,
            setup_observability_callback=setup_observability_callback,
        )

    def get_websocket_config(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get WebSocket configuration for an app.

        Args:
            slug: App slug

        Returns:
            WebSocket configuration dict or None if not configured
        """
        if self._service_initializer:
            return self._service_initializer.get_websocket_config(slug)
        return None

    def register_websocket_routes(self, app: Any, slug: str) -> None:
        """
        Register WebSocket routes with a FastAPI app.

        WebSocket support is OPTIONAL - only enabled if:
        1. App defines "websockets" in manifest.json
        2. WebSocket dependencies are available

        This should be called after the FastAPI app is created to actually
        mount the WebSocket endpoints.

        Args:
            app: FastAPI application instance
            slug: App slug
        """
        # Check if WebSockets are configured for this app
        websockets_config = self.get_websocket_config(slug)
        if not websockets_config:
            contextual_logger.debug(
                f"No WebSocket configuration found for app '{slug}' - WebSocket support disabled"
            )
            return

        # Try to import WebSocket support (optional dependency)
        try:
            from ..routing.websockets import create_websocket_endpoint
        except ImportError as e:
            contextual_logger.warning(
                f"WebSocket support requested for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"WebSocket routes will not be registered. "
                f"Install FastAPI with WebSocket support."
            )
            return

        for endpoint_name, endpoint_config in websockets_config.items():
            path = endpoint_config.get("path", f"/{endpoint_name}")

            # Handle auth configuration - use app's auth_policy as default
            # Support both new nested format and old top-level format for backward compatibility
            auth_config = endpoint_config.get("auth", {})
            if isinstance(auth_config, dict) and "required" in auth_config:
                require_auth = auth_config.get("required", True)
            elif "require_auth" in endpoint_config:
                # Backward compatibility: if "require_auth" is at top level
                require_auth = endpoint_config.get("require_auth", True)
            else:
                # Default: use app's auth_policy if available, otherwise require auth
                app_config = self.get_app(slug)
                if app_config and "auth_policy" in app_config:
                    require_auth = app_config["auth_policy"].get("required", True)
                else:
                    require_auth = True  # Secure default

            ping_interval = endpoint_config.get("ping_interval", 30)

            # Create the endpoint handler with app isolation
            # Note: Apps can register message handlers later using register_message_handler()
            try:
                handler = create_websocket_endpoint(
                    app_slug=slug,
                    path=path,
                    endpoint_name=endpoint_name,  # Pass endpoint name for handler lookup
                    handler=None,  # Handlers registered via register_message_handler()
                    require_auth=require_auth,
                    ping_interval=ping_interval,
                )
                print(
                    f"✅ Created WebSocket handler for '{path}' "
                    f"(type: {type(handler).__name__}, "
                    f"callable: {callable(handler)})"
                )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                print(f"❌ Failed to create WebSocket handler for '{path}': {e}")
                import traceback

                traceback.print_exc()
                raise

            # Register with FastAPI - automatically scoped to this app
            try:
                # FastAPI WebSocket registration - use APIRouter approach (most reliable)
                from fastapi import APIRouter

                # Create a router for this WebSocket route
                ws_router = APIRouter()
                ws_router.websocket(path)(handler)

                # Include the router in the app
                app.include_router(ws_router)

                print(
                    f"✅ Registered WebSocket route '{path}' for app '{slug}' using APIRouter"
                )
                print(
                    f"   Handler type: {type(handler).__name__}, Callable: {callable(handler)}"
                )
                print(
                    f"   Route name: {slug}_{endpoint_name}, Auth required: {require_auth}"
                )
                print(f"   Route path: {path}, Full route count: {len(app.routes)}")
                contextual_logger.info(
                    f"✅ Registered WebSocket route '{path}' for app '{slug}' "
                    f"(auth: {require_auth})",
                    extra={
                        "app_slug": slug,
                        "path": path,
                        "endpoint": endpoint_name,
                        "require_auth": require_auth,
                    },
                )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                contextual_logger.error(
                    f"❌ Failed to register WebSocket route '{path}' for app '{slug}': {e}",
                    exc_info=True,
                    extra={
                        "app_slug": slug,
                        "path": path,
                        "endpoint": endpoint_name,
                        "error": str(e),
                    },
                )
                print(
                    f"❌ Failed to register WebSocket route '{path}' for app '{slug}': {e}"
                )
                import traceback

                traceback.print_exc()
                raise

    async def reload_apps(self) -> int:
        """
        Reload all active apps from the database.

        This method fetches all apps with status "active" from the
        apps_config collection and registers them. Existing
        app registrations are cleared before reloading.

        Returns:
            Number of apps successfully registered.
            Returns 0 if an error occurs during reload.

        Raises:
            RuntimeError: If engine is not initialized.
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )

        return await self._app_registration_manager.reload_apps(
            register_app_callback=self.register_app
        )

    def get_app(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app configuration by slug.

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return self._app_registration_manager.get_app(slug)

    async def get_manifest(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app manifest by slug (async alias for get_app).

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return await self._app_registration_manager.get_manifest(slug)

    def get_database(self) -> AsyncIOMotorDatabase:
        """
        Get the MongoDB database instance.

        Returns:
            AsyncIOMotorDatabase instance
        """
        return self.mongo_db

    def get_memory_service(self, slug: str) -> Optional[Any]:
        """
        Get Mem0 memory service for an app.

        Args:
            slug: App slug

        Returns:
            Mem0MemoryService instance if memory is enabled for this app, None otherwise

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

    @property
    def _apps(self) -> Dict[str, Any]:
        """
        Get the apps dictionary (for backward compatibility with tests).

        Returns:
            Dictionary of registered apps

        Raises:
            RuntimeError: If engine is not initialized
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return self._app_registration_manager._apps

    def list_apps(self) -> List[str]:
        """
        List all registered app slugs.

        Returns:
            List of app slugs
        """
        if not self._app_registration_manager:
            raise RuntimeError(
                "MongoDBEngine not initialized. Call initialize() first."
            )
        return self._app_registration_manager.list_apps()

    async def shutdown(self) -> None:
        """
        Shutdown the MongoDB Engine and clean up resources.

        This method:
        1. Closes MongoDB connections
        2. Clears app registrations
        3. Resets initialization state

        This method is idempotent - it's safe to call multiple times.
        """
        if self._service_initializer:
            self._service_initializer.clear_services()

        if self._app_registration_manager:
            self._app_registration_manager.clear_apps()

        await self._connection_manager.shutdown()

    def __enter__(self) -> "MongoDBEngine":
        """
        Context manager entry (synchronous).

        Note: This is synchronous and does not initialize the engine.
        For async initialization, use async context manager (async with).

        Returns:
            MongoDBEngine instance
        """
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        """
        Context manager exit (synchronous).

        Note: This is synchronous, so we can't await shutdown.
        Users should call await shutdown() explicitly or use async context manager.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)
        """
        # Note: This is synchronous, so we can't await shutdown
        # Users should call await shutdown() explicitly
        pass

    async def __aenter__(self) -> "MongoDBEngine":
        """
        Async context manager entry.

        Automatically initializes the engine when entering the context.

        Returns:
            Initialized MongoDBEngine instance
        """
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        """
        Async context manager exit.

        Automatically shuts down the engine when exiting the context.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)
        """
        await self.shutdown()

    async def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status of the MongoDB Engine.

        Returns:
            Dictionary with health status and component checks
        """
        health_checker = HealthChecker()

        # Register health checks
        health_checker.register_check(lambda: check_engine_health(self))
        health_checker.register_check(
            lambda: check_mongodb_health(self._connection_manager.mongo_client)
        )

        # Add pool health check if available (but don't fail overall health if it's just a warning)
        try:
            from ..database.connection import get_pool_metrics

            async def pool_check_wrapper():
                # Pass MongoDBEngine's client and pool config to get_pool_metrics
                # for accurate monitoring
                # This follows MongoDB best practice: monitor the actual client
                # being used
                async def get_metrics():
                    metrics = await get_pool_metrics(
                        self._connection_manager.mongo_client
                    )
                    # Add MongoDBEngine's pool configuration if not already in metrics
                    if metrics.get("status") == "connected":
                        if (
                            "max_pool_size" not in metrics
                            or metrics.get("max_pool_size") is None
                        ):
                            metrics["max_pool_size"] = self.max_pool_size
                        if (
                            "min_pool_size" not in metrics
                            or metrics.get("min_pool_size") is None
                        ):
                            metrics["min_pool_size"] = self.min_pool_size
                    return metrics

                result = await check_pool_health(get_metrics)
                # Only treat pool issues as unhealthy if usage is critical (>90%)
                # Otherwise treat as degraded or healthy
                if result.status.value == "unhealthy":
                    # Check if it's a critical pool usage issue
                    details = result.details or {}
                    usage = details.get("pool_usage_percent", 0)
                    if usage <= 90 and details.get("status") == "connected":
                        # Not critical, downgrade to degraded
                        from ..observability.health import (HealthCheckResult,
                                                            HealthStatus)

                        return HealthCheckResult(
                            name=result.name,
                            status=HealthStatus.DEGRADED,
                            message=result.message,
                            details=result.details,
                        )
                return result

            health_checker.register_check(pool_check_wrapper)
        except ImportError:
            pass

        return await health_checker.check_all()

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get metrics for the MongoDB Engine.

        Returns:
            Dictionary with operation metrics
        """
        from ..observability import get_metrics_collector

        collector = get_metrics_collector()
        return collector.get_summary()
