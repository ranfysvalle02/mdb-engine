"""
Engine

The core orchestration engine for MDB_ENGINE that manages:
- Database connections
- Experiment registration
- Authentication/authorization
- Index management
- Resource lifecycle
- Optional Ray integration for distributed processing
- FastAPI integration with lifespan management

This module is part of MDB_ENGINE - MongoDB Engine.

Usage:
    # Simple usage (most common)
    engine = MongoDBEngine(mongo_uri=..., db_name=...)
    await engine.initialize()
    db = engine.get_scoped_db("my_app")

    # With FastAPI integration
    app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

    # With Ray support (optional)
    engine = MongoDBEngine(mongo_uri=..., db_name=..., enable_ray=True)
"""

import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..auth import AuthorizationProvider
    from .types import ManifestDict

# Import engine components
from ..constants import DEFAULT_MAX_POOL_SIZE, DEFAULT_MIN_POOL_SIZE
from ..database import ScopedMongoWrapper
from ..observability import (
    HealthChecker,
    check_engine_health,
    check_mongodb_health,
    check_pool_health,
)
from ..observability import get_logger as get_contextual_logger
from .app_registration import AppRegistrationManager
from .app_secrets import AppSecretsManager
from .connection import ConnectionManager
from .encryption import EnvelopeEncryptionService
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
    - Optional Ray integration for distributed processing
    - FastAPI integration with lifespan management

    Example:
        # Simple usage
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="mydb")
        await engine.initialize()
        db = engine.get_scoped_db("my_app")

        # With FastAPI
        app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

        # With Ray
        engine = MongoDBEngine(..., enable_ray=True)
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        manifests_dir: Path | None = None,
        authz_provider: Optional["AuthorizationProvider"] = None,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
        # Optional Ray support
        enable_ray: bool = False,
        ray_namespace: str = "modular_labs",
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
            enable_ray: Enable Ray support for distributed processing.
                Default: False. Only activates if Ray is installed.
            ray_namespace: Ray namespace for actor isolation.
                Default: "modular_labs"
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.manifests_dir = manifests_dir
        self.authz_provider = authz_provider
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size

        # Ray configuration (optional)
        self.enable_ray = enable_ray
        self.ray_namespace = ray_namespace
        self.ray_actor = None  # Populated if Ray is enabled and available

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
        self._app_registration_manager: AppRegistrationManager | None = None
        self._index_manager: IndexManager | None = None
        self._service_initializer: ServiceInitializer | None = None
        self._encryption_service: EnvelopeEncryptionService | None = None
        self._app_secrets_manager: AppSecretsManager | None = None

        # Store app read_scopes mapping for validation
        self._app_read_scopes: dict[str, list[str]] = {}

        # Store app token cache for auto-retrieval
        self._app_token_cache: dict[str, str] = {}

        # Async lock for thread-safe shared user pool initialization
        import asyncio

        self._shared_user_pool_lock = asyncio.Lock()
        self._shared_user_pool_initializing = False

    async def initialize(self) -> None:
        """
        Initialize the MongoDB Engine.

        This method:
        1. Connects to MongoDB
        2. Validates the connection
        3. Sets up initial state
        4. Initializes Ray if enabled and available

        Raises:
            InitializationError: If initialization fails (subclass of RuntimeError
                for backward compatibility)
            RuntimeError: If initialization fails (for backward compatibility)
        """
        # Initialize connection
        await self._connection_manager.initialize()

        # Initialize encryption service
        try:
            from .encryption import MASTER_KEY_ENV_VAR

            self._encryption_service = EnvelopeEncryptionService()
        except ValueError as e:
            from .encryption import MASTER_KEY_ENV_VAR

            logger.warning(
                f"Encryption service not initialized: {e}. "
                "App-level authentication will not be available. "
                f"Set {MASTER_KEY_ENV_VAR} environment variable."
            )
            # Continue without encryption (backward compatibility)
            self._encryption_service = None

        # Initialize app secrets manager (only if encryption service available)
        if self._encryption_service:
            self._app_secrets_manager = AppSecretsManager(
                mongo_db=self._connection_manager.mongo_db,
                encryption_service=self._encryption_service,
            )

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

        # Initialize Ray if enabled
        if self.enable_ray:
            await self._initialize_ray()

    async def _initialize_ray(self) -> None:
        """
        Initialize Ray support (only if enabled and available).

        This is called automatically during initialize() if enable_ray=True.
        Gracefully degrades if Ray is not installed.
        """
        try:
            from .ray_integration import RAY_AVAILABLE, get_ray_actor_handle

            if not RAY_AVAILABLE:
                logger.warning("Ray enabled but not installed. " "Install with: pip install ray")
                return

            # Initialize base Ray actor for this engine
            self.ray_actor = await get_ray_actor_handle(
                app_slug="engine",
                namespace=self.ray_namespace,
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                create_if_missing=True,
            )

            if self.ray_actor:
                logger.info(f"Ray initialized in namespace '{self.ray_namespace}'")
            else:
                logger.warning("Failed to initialize Ray actor")

        except ImportError:
            logger.warning("Ray integration module not available")

    @property
    def has_ray(self) -> bool:
        """Check if Ray is enabled and initialized."""
        return self.enable_ray and self.ray_actor is not None

    @property
    def mongo_client(self) -> AsyncIOMotorClient:
        """
        Get the MongoDB client for observability and health checks.

        **SECURITY WARNING:** This property exposes the raw MongoDB client.
        It should ONLY be used for:
        - Health checks and observability (`check_mongodb_health`, `get_pool_metrics`)
        - Administrative operations that don't involve data access

        **DO NOT use this for data access.** Always use `get_scoped_db()` for
        all data operations to ensure proper app scoping and security.

        Returns:
            AsyncIOMotorClient instance

        Raises:
            RuntimeError: If engine is not initialized

        Example:
            # ✅ CORRECT: Use for health checks
            health = await check_mongodb_health(engine.mongo_client)

            # ❌ WRONG: Don't use for data access
            db = engine.mongo_client["my_database"]  # Bypasses scoping!
        """
        return self._connection_manager.mongo_client

    @property
    def _initialized(self) -> bool:
        """Check if engine is initialized (internal)."""
        return self._connection_manager.initialized

    @property
    def initialized(self) -> bool:
        """
        Check if engine is initialized.

        Returns:
            True if the engine has been initialized, False otherwise.

        Example:
            if engine.initialized:
                db = engine.get_scoped_db("my_app")
        """
        return self._connection_manager.initialized

    def get_scoped_db(
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
            >>> db = engine.get_scoped_db("my_app", app_token="secret-token")
            >>> # All queries are automatically scoped to "my_app"
            >>> doc = await db.my_collection.find_one({"name": "test"})
        """
        if not self._initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        # Verify app token if secrets manager is available
        # Token verification will happen lazily in ScopedMongoWrapper if called from async context
        if self._app_secrets_manager:
            if app_token is None:
                # Check if app has stored secret (backward compatibility)
                # Use sync wrapper that handles async context
                has_secret = self._app_secrets_manager.app_secret_exists_sync(app_slug)
                if has_secret:
                    # Log detailed info
                    logger.warning(f"App token required for '{app_slug}'")
                    # Generic error message
                    raise ValueError("App token required. Provide app_token parameter.")
                # No stored secret - allow (backward compatibility for apps without secrets)
                logger.debug(
                    f"App '{app_slug}' has no stored secret, "
                    f"allowing access (backward compatibility)"
                )
            else:
                # Try to verify synchronously if possible, otherwise pass to wrapper
                # for lazy verification
                import asyncio

                try:
                    # Check if we're in an async context
                    asyncio.get_running_loop()
                    # We're in async context - can't verify synchronously without blocking
                    # Pass token to wrapper for lazy verification on first database operation
                    logger.debug(
                        f"Token verification deferred to first database operation for '{app_slug}' "
                        f"(async context detected)"
                    )
                except RuntimeError:
                    # No event loop - safe to use sync verification
                    is_valid = self._app_secrets_manager.verify_app_secret_sync(app_slug, app_token)
                    if not is_valid:
                        # Log detailed info with app_slug
                        logger.warning(f"Security: Invalid app token for '{app_slug}'")
                        # Generic error message (from None: unrelated to RuntimeError)
                        raise ValueError("Invalid app token") from None

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

    async def get_scoped_db_async(
        self,
        app_slug: str,
        app_token: str | None = None,
        read_scopes: list[str] | None = None,
        write_scope: str | None = None,
        auto_index: bool = True,
    ) -> ScopedMongoWrapper:
        """
        Asynchronous version of get_scoped_db that properly verifies tokens.

        This method is preferred in async contexts to ensure token verification
        happens correctly.

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
        if not self._initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        # Verify app token if secrets manager is available
        if self._app_secrets_manager:
            if app_token is None:
                # Check if app has stored secret
                has_secret = await self._app_secrets_manager.app_secret_exists(app_slug)
                if has_secret:
                    raise ValueError(
                        f"App token required for '{app_slug}'. " "Provide app_token parameter."
                    )
                # No stored secret - allow (backward compatibility)
                logger.debug(
                    f"App '{app_slug}' has no stored secret, "
                    f"allowing access (backward compatibility)"
                )
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

    async def validate_manifest(
        self, manifest: "ManifestDict"
    ) -> tuple[bool, str | None, list[str] | None]:
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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return await self._app_registration_manager.load_manifest(path)

    async def register_app(self, manifest: "ManifestDict", create_indexes: bool = True) -> bool:
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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        # Create callbacks for service initialization
        async def create_indexes_callback(slug: str, manifest: "ManifestDict") -> None:
            if self._index_manager and create_indexes:
                await self._index_manager.create_app_indexes(slug, manifest)

        async def seed_data_callback(slug: str, initial_data: dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.seed_initial_data(slug, initial_data)

        async def initialize_memory_callback(slug: str, memory_config: dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.initialize_memory_service(slug, memory_config)

        async def register_websockets_callback(
            slug: str, websockets_config: dict[str, Any]
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.register_websockets(slug, websockets_config)

        async def setup_observability_callback(
            slug: str,
            manifest: "ManifestDict",
            observability_config: dict[str, Any],
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.setup_observability(
                    slug, manifest, observability_config
                )

        # Register app first (this validates and stores the manifest)
        result = await self._app_registration_manager.register_app(
            manifest=manifest,
            create_indexes_callback=create_indexes_callback if create_indexes else None,
            seed_data_callback=seed_data_callback,
            initialize_memory_callback=initialize_memory_callback,
            register_websockets_callback=register_websockets_callback,
            setup_observability_callback=setup_observability_callback,
        )

        # Extract and store data_access configuration AFTER registration
        slug = manifest.get("slug")
        if slug:
            data_access = manifest.get("data_access", {})
            read_scopes = data_access.get("read_scopes")
            if read_scopes:
                self._app_read_scopes[slug] = read_scopes
            else:
                # Default to app_slug if not specified
                self._app_read_scopes[slug] = [slug]

            # Generate and store app secret if secrets manager is available
            if self._app_secrets_manager:
                # Check if secret already exists (don't overwrite)
                secret_exists = await self._app_secrets_manager.app_secret_exists(slug)
                if not secret_exists:
                    app_secret = secrets.token_urlsafe(32)
                    await self._app_secrets_manager.store_app_secret(slug, app_secret)
                    logger.info(
                        f"Generated and stored encrypted secret for app '{slug}'. "
                        "Store this secret securely and provide it as app_token in get_scoped_db()."
                    )
                    # Note: In production, the secret should be retrieved via rotation API
                    # For now, we log it (in production, this should be handled differently)

        return result

    def get_websocket_config(self, slug: str) -> dict[str, Any] | None:
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

                print(f"✅ Registered WebSocket route '{path}' for app '{slug}' using APIRouter")
                print(f"   Handler type: {type(handler).__name__}, Callable: {callable(handler)}")
                print(f"   Route name: {slug}_{endpoint_name}, Auth required: {require_auth}")
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
                print(f"❌ Failed to register WebSocket route '{path}' for app '{slug}': {e}")
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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
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
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return await self._app_registration_manager.get_manifest(slug)

    def get_memory_service(self, slug: str) -> Any | None:
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

    def get_embedding_service(self, slug: str) -> Any | None:
        """
        Get EmbeddingService for an app.

        Auto-detects OpenAI or AzureOpenAI from environment variables.
        Uses embedding_config from manifest.json if available.

        Args:
            slug: App slug

        Returns:
            EmbeddingService instance if embedding is enabled for this app, None otherwise

        Example:
            ```python
            embedding_service = engine.get_embedding_service("my_app")
            if embedding_service:
                vectors = await embedding_service.embed_chunks(["Hello world"])
            ```
        """
        from ..embeddings.dependencies import get_embedding_service_for_app

        return get_embedding_service_for_app(slug, self)

    @property
    def _apps(self) -> dict[str, Any]:
        """
        Get the apps dictionary (for backward compatibility with tests).

        Returns:
            Dictionary of registered apps

        Raises:
            RuntimeError: If engine is not initialized
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return self._app_registration_manager._apps

    def list_apps(self) -> list[str]:
        """
        List all registered app slugs.

        Returns:
            List of app slugs
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
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
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
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
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
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

    async def get_health_status(self) -> dict[str, Any]:
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
                    metrics = await get_pool_metrics(self._connection_manager.mongo_client)
                    # Add MongoDBEngine's pool configuration if not already in metrics
                    if metrics.get("status") == "connected":
                        if "max_pool_size" not in metrics or metrics.get("max_pool_size") is None:
                            metrics["max_pool_size"] = self.max_pool_size
                        if "min_pool_size" not in metrics or metrics.get("min_pool_size") is None:
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
                        from ..observability.health import (
                            HealthCheckResult,
                            HealthStatus,
                        )

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

    def get_metrics(self) -> dict[str, Any]:
        """
        Get metrics for the MongoDB Engine.

        Returns:
            Dictionary with operation metrics
        """
        from ..observability import get_metrics_collector

        collector = get_metrics_collector()
        return collector.get_summary()

    # =========================================================================
    # FastAPI Integration Methods
    # =========================================================================

    def create_app(
        self,
        slug: str,
        manifest: Path,
        title: str | None = None,
        on_startup: Callable[["FastAPI", "MongoDBEngine", dict[str, Any]], Awaitable[None]]
        | None = None,
        on_shutdown: Callable[["FastAPI", "MongoDBEngine", dict[str, Any]], Awaitable[None]]
        | None = None,
        is_sub_app: bool = False,
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """
        Create a FastAPI application with proper lifespan management.

        This method creates a FastAPI app that:
        1. Initializes the engine on startup (unless is_sub_app=True)
        2. Loads and registers the manifest
        3. Auto-detects multi-site mode from manifest
        4. Auto-configures auth based on manifest auth.mode:
           - "app" (default): Per-app token authentication
           - "shared": Shared user pool with SSO, auto-adds SharedAuthMiddleware
        5. Auto-retrieves app tokens (for "app" mode)
        6. Calls on_startup callback (if provided)
        7. Shuts down the engine on shutdown (calls on_shutdown first if provided)

        Args:
            slug: Application slug (must match manifest slug)
            manifest: Path to manifest.json file
            title: FastAPI app title. Defaults to app name from manifest
            on_startup: Optional async callback called after engine initialization.
                       Signature: async def callback(app, engine, manifest) -> None
            on_shutdown: Optional async callback called before engine shutdown.
                        Signature: async def callback(app, engine, manifest) -> None
            is_sub_app: If True, skip engine initialization and lifespan management.
                       Used when mounting as a child app in create_multi_app().
                       Defaults to False for backward compatibility.
            **fastapi_kwargs: Additional arguments passed to FastAPI()

        Returns:
            Configured FastAPI application

        Example:
            async def my_startup(app, engine, manifest):
                db = engine.get_scoped_db("my_app")
                await db.config.insert_one({"initialized": True})

            engine = MongoDBEngine(mongo_uri=..., db_name=...)
            app = engine.create_app(
                slug="my_app",
                manifest=Path("manifest.json"),
                on_startup=my_startup,
            )

            @app.get("/")
            async def index():
                db = engine.get_scoped_db("my_app")
                return {"status": "ok"}

        Auth Modes (configured in manifest.json):
            # Per-app auth (default)
            {"auth": {"mode": "app"}}

            # Shared user pool with SSO
            {"auth": {"mode": "shared", "roles": ["viewer", "editor", "admin"],
                      "require_role": "viewer", "public_routes": ["/health"]}}
        """
        import json

        from fastapi import FastAPI

        engine = self
        manifest_path = Path(manifest)

        # Pre-load manifest synchronously to detect auth mode BEFORE creating app
        # This allows us to add middleware at app creation time (before startup)
        with open(manifest_path) as f:
            pre_manifest = json.load(f)

        # Extract auth configuration
        auth_config = pre_manifest.get("auth", {})
        auth_mode = auth_config.get("mode", "app")

        # Determine title from pre-loaded manifest or slug
        app_title = title or pre_manifest.get("name", slug)

        # State that will be populated during initialization
        app_manifest: dict[str, Any] = {}
        is_multi_site = False

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """Lifespan context manager for initialization and cleanup."""
            nonlocal app_manifest, is_multi_site

            # Initialize engine (skip if sub-app - parent manages lifecycle)
            if not is_sub_app:
                await engine.initialize()

            # Load and register manifest
            app_manifest = await engine.load_manifest(manifest_path)
            await engine.register_app(app_manifest)

            # Auto-detect multi-site mode from manifest
            data_access = app_manifest.get("data_access", {})
            read_scopes = data_access.get("read_scopes", [slug])
            cross_app_policy = data_access.get("cross_app_policy", "none")

            # Multi-site if: cross_app_policy is "explicit" OR read_scopes has multiple apps
            is_multi_site = cross_app_policy == "explicit" or (
                len(read_scopes) > 1 and read_scopes != [slug]
            )

            if is_multi_site:
                logger.info(
                    f"Multi-site mode detected for '{slug}': "
                    f"read_scopes={read_scopes}, cross_app_policy={cross_app_policy}"
                )
            else:
                logger.info(f"Single-app mode for '{slug}'")

            # Handle auth based on mode
            if auth_mode == "shared":
                logger.info(f"Shared auth mode for '{slug}' - SSO enabled")
                # Initialize shared user pool and set on app.state
                # Middleware was already added at app creation time (lazy version)
                # For sub-apps, check if parent already initialized user pool
                if is_sub_app:
                    # Check if parent app has user_pool (set by parent's initialization)
                    # If not, initialize it (shouldn't happen, but handle gracefully)
                    if not hasattr(app.state, "user_pool") or app.state.user_pool is None:
                        logger.warning(
                            f"Sub-app '{slug}' uses shared auth but user_pool not found. "
                            "Initializing now (parent should have initialized it)."
                        )
                        await engine._initialize_shared_user_pool(app, app_manifest)
                    else:
                        logger.debug(f"Sub-app '{slug}' using shared user_pool from parent app")
                else:
                    await engine._initialize_shared_user_pool(app, app_manifest)
            else:
                logger.info(f"Per-app auth mode for '{slug}'")
                # Auto-retrieve app token for "app" mode
                await engine.auto_retrieve_app_token(slug)

            # Auto-initialize authorization provider from manifest config
            try:
                logger.info(
                    f"🔍 Checking auth config for '{slug}': "
                    f"auth_config keys={list(auth_config.keys())}"
                )
                auth_policy = auth_config.get("policy", {})
                logger.info(f"🔍 Auth policy for '{slug}': {auth_policy}")
                authz_provider_type = auth_policy.get("provider")
                logger.info(f"🔍 Authz provider type for '{slug}': {authz_provider_type}")
            except (KeyError, AttributeError, TypeError) as e:
                logger.exception(f"❌ Error reading auth config for '{slug}': {e}")
                authz_provider_type = None

            if authz_provider_type == "oso":
                # Initialize OSO Cloud provider
                try:
                    from ..auth.oso_factory import initialize_oso_from_manifest

                    authz_provider = await initialize_oso_from_manifest(engine, slug, app_manifest)
                    if authz_provider:
                        app.state.authz_provider = authz_provider
                        logger.info(f"✅ OSO Cloud provider auto-initialized for '{slug}'")
                    else:
                        logger.warning(
                            f"⚠️  OSO provider not initialized for '{slug}' - "
                            "check OSO_AUTH and OSO_URL environment variables"
                        )
                except ImportError as e:
                    logger.warning(
                        f"⚠️  OSO Cloud SDK not available for '{slug}': {e}. "
                        "Install with: pip install oso-cloud"
                    )
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.exception(f"❌ Failed to initialize OSO provider for '{slug}': {e}")

            elif authz_provider_type == "casbin":
                # Initialize Casbin provider
                logger.info(f"🔧 Initializing Casbin provider for '{slug}'...")
                try:
                    from ..auth.casbin_factory import initialize_casbin_from_manifest

                    logger.debug(f"Calling initialize_casbin_from_manifest for '{slug}'")
                    authz_provider = await initialize_casbin_from_manifest(
                        engine, slug, app_manifest
                    )
                    logger.debug(
                        f"initialize_casbin_from_manifest returned: {authz_provider is not None}"
                    )
                    if authz_provider:
                        app.state.authz_provider = authz_provider
                        logger.info(
                            f"✅ Casbin provider auto-initialized for '{slug}' "
                            f"and set on app.state"
                        )
                        logger.info(
                            f"✅ Provider type: {type(authz_provider).__name__}, "
                            f"initialized: {getattr(authz_provider, '_initialized', 'unknown')}"
                        )
                        # Verify it's actually set
                        if hasattr(app.state, "authz_provider") and app.state.authz_provider:
                            logger.info("✅ Verified: app.state.authz_provider is set and not None")
                        else:
                            logger.error(
                                "❌ CRITICAL: app.state.authz_provider was set but is now "
                                "None or missing!"
                            )
                    else:
                        logger.error(
                            f"❌ Casbin provider initialization returned None for '{slug}' - "
                            f"check logs above for errors"
                        )
                        logger.error(f"❌ This means authorization will NOT work for '{slug}'")
                except ImportError as e:
                    # ImportError is expected if Casbin is not installed
                    logger.warning(
                        f"❌ Casbin not available for '{slug}': {e}. "
                        "Install with: pip install mdb-engine[casbin]"
                    )
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.exception(f"❌ Failed to initialize Casbin provider for '{slug}': {e}")
                    # Informational message, not exception logging
                    logger.error(  # noqa: TRY400
                        f"❌ This means authorization will NOT work for '{slug}' - "
                        f"app.state.authz_provider will remain None"
                    )
                except (
                    RuntimeError,
                    ValueError,
                    AttributeError,
                    TypeError,
                    ConnectionError,
                    OSError,
                ) as e:
                    # Catch specific exceptions that might occur during initialization
                    logger.exception(
                        f"❌ Unexpected error initializing Casbin provider for '{slug}': {e}"
                    )
                    # Informational message, not exception logging
                    logger.error(  # noqa: TRY400
                        f"❌ This means authorization will NOT work for '{slug}' - "
                        f"app.state.authz_provider will remain None"
                    )

            elif authz_provider_type is None and auth_policy:
                # Default to Casbin if provider not specified but auth.policy exists
                logger.info(
                    f"⚠️  No provider specified in auth.policy for '{slug}', "
                    f"defaulting to Casbin"
                )
                try:
                    from ..auth.casbin_factory import initialize_casbin_from_manifest

                    authz_provider = await initialize_casbin_from_manifest(
                        engine, slug, app_manifest
                    )
                    if authz_provider:
                        app.state.authz_provider = authz_provider
                        logger.info(f"✅ Casbin provider auto-initialized for '{slug}' (default)")
                    else:
                        logger.warning(
                            f"⚠️  Casbin provider not initialized for '{slug}' "
                            f"(default attempt failed)"
                        )
                except ImportError as e:
                    logger.warning(
                        f"⚠️  Casbin not available for '{slug}': {e}. "
                        "Install with: pip install mdb-engine[casbin]"
                    )
                except (
                    ValueError,
                    TypeError,
                    RuntimeError,
                    AttributeError,
                    KeyError,
                ) as e:
                    logger.exception(
                        f"❌ Failed to initialize Casbin provider for '{slug}' (default): {e}"
                    )
            elif authz_provider_type:
                logger.warning(
                    f"⚠️  Unknown authz provider type '{authz_provider_type}' for '{slug}' - "
                    f"skipping initialization"
                )

            # Auto-seed demo users if configured in manifest
            users_config = auth_config.get("users", {})
            if users_config.get("enabled") and users_config.get("demo_users"):
                try:
                    from ..auth import ensure_demo_users_exist

                    db = engine.get_scoped_db(slug)
                    demo_users = await ensure_demo_users_exist(
                        db=db,
                        slug_id=slug,
                        config=app_manifest,
                    )
                    if demo_users:
                        logger.info(f"✅ Seeded {len(demo_users)} demo user(s) for '{slug}'")
                except (
                    ImportError,
                    ValueError,
                    TypeError,
                    RuntimeError,
                    AttributeError,
                    KeyError,
                ) as e:
                    logger.warning(f"⚠️  Failed to seed demo users for '{slug}': {e}")

            # Expose engine state on app.state
            app.state.engine = engine
            app.state.app_slug = slug
            app.state.manifest = app_manifest
            app.state.is_multi_site = is_multi_site
            app.state.auth_mode = auth_mode
            app.state.ray_actor = engine.ray_actor

            # Initialize DI container (if not already set)
            from ..di import Container

            if not hasattr(app.state, "container") or app.state.container is None:
                app.state.container = Container()
                logger.debug(f"DI Container initialized for '{slug}'")

            # Call on_startup callback if provided
            if on_startup:
                try:
                    await on_startup(app, engine, app_manifest)
                    logger.info(f"on_startup callback completed for '{slug}'")
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.exception(f"on_startup callback failed for '{slug}': {e}")
                    raise

            yield

            # Call on_shutdown callback if provided
            if on_shutdown:
                try:
                    await on_shutdown(app, engine, app_manifest)
                    logger.info(f"on_shutdown callback completed for '{slug}'")
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.warning(f"on_shutdown callback failed for '{slug}': {e}")

            # Shutdown engine (skip if sub-app - parent manages lifecycle)
            if not is_sub_app:
                await engine.shutdown()

        # Create FastAPI app
        app = FastAPI(title=app_title, lifespan=lifespan, **fastapi_kwargs)

        # Add request scope middleware (innermost layer - runs first on request)
        # This sets up the DI request scope for each request
        from starlette.middleware.base import BaseHTTPMiddleware

        from ..di import ScopeManager

        class RequestScopeMiddleware(BaseHTTPMiddleware):
            """Middleware that manages request-scoped DI instances."""

            async def dispatch(self, request, call_next):
                ScopeManager.begin_request()
                try:
                    response = await call_next(request)
                    return response
                finally:
                    ScopeManager.end_request()

        app.add_middleware(RequestScopeMiddleware)
        logger.debug(f"RequestScopeMiddleware added for '{slug}'")

        # Add rate limiting middleware FIRST (outermost layer)
        # This ensures rate limiting happens before auth validation
        rate_limits_config = auth_config.get("rate_limits", {})
        if rate_limits_config or auth_mode == "shared":
            from ..auth.rate_limiter import create_rate_limit_middleware

            rate_limit_middleware = create_rate_limit_middleware(
                manifest_auth=auth_config,
            )
            app.add_middleware(rate_limit_middleware)
            logger.info(
                f"AuthRateLimitMiddleware added for '{slug}' "
                f"(endpoints: {list(rate_limits_config.keys()) or 'defaults'})"
            )

        # Add shared auth middleware (after rate limiting)
        # Uses lazy version that reads user_pool from app.state
        if auth_mode == "shared":
            from ..auth.shared_middleware import create_shared_auth_middleware_lazy

            middleware_class = create_shared_auth_middleware_lazy(
                app_slug=slug,
                manifest_auth=auth_config,
            )
            app.add_middleware(middleware_class)
            logger.info(
                f"LazySharedAuthMiddleware added for '{slug}' "
                f"(require_role={auth_config.get('require_role')})"
            )

        # Add CSRF middleware (after auth - auto-enabled for shared mode)
        # CSRF protection is enabled by default for shared auth mode
        csrf_config = auth_config.get("csrf_protection", True if auth_mode == "shared" else False)
        if csrf_config:
            from ..auth.csrf import create_csrf_middleware

            csrf_middleware = create_csrf_middleware(
                manifest_auth=auth_config,
            )
            app.add_middleware(csrf_middleware)
            logger.info(f"CSRFMiddleware added for '{slug}'")

        # Add security middleware (HSTS, headers)
        security_config = auth_config.get("security", {})
        hsts_config = security_config.get("hsts", {})
        if hsts_config.get("enabled", True) or auth_mode == "shared":
            from ..auth.middleware import SecurityMiddleware

            app.add_middleware(
                SecurityMiddleware,
                require_https=False,  # HSTS handles this in production
                csrf_protection=False,  # Handled by CSRFMiddleware above
                security_headers=True,
                hsts_config=hsts_config,
            )
            logger.info(f"SecurityMiddleware added for '{slug}'")

        logger.debug(f"FastAPI app created for '{slug}'")

        return app

    def _validate_path_prefixes(self, apps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        """
        Validate path prefixes for multi-app mounting.

        Checks:
        - All prefixes start with '/'
        - No prefix is a prefix of another (e.g., '/app' conflicts with '/app/v2')
        - No conflicts with reserved paths ('/health', '/docs', '/openapi.json', '/_mdb')
        - Slug matches manifest slug (if manifest is readable)

        Args:
            apps: List of app configs with 'path_prefix' keys

        Returns:
            Tuple of (is_valid, list_of_errors)
        """

        errors: list[str] = []
        reserved_paths = {"/health", "/docs", "/openapi.json", "/redoc", "/_mdb"}

        # Extract path prefixes
        path_prefixes: list[str] = []
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            path_prefix = app_config.get("path_prefix", f"/{slug}")

            if not path_prefix.startswith("/"):
                errors.append(f"Path prefix '{path_prefix}' must start with '/' (app: '{slug}')")
                continue

            # Check for common mistakes
            if path_prefix.endswith("/") and path_prefix != "/":
                logger.warning(
                    f"Path prefix '{path_prefix}' ends with '/'. "
                    f"Consider removing trailing slash for app '{slug}'"
                )

            path_prefixes.append(path_prefix)

        # Check for conflicts with reserved paths
        for prefix in path_prefixes:
            if prefix in reserved_paths:
                errors.append(
                    f"Path prefix '{prefix}' conflicts with reserved path. "
                    "Reserved paths: /health, /docs, /openapi.json, /redoc, /_mdb"
                )

        # Check for prefix conflicts (one prefix being a prefix of another)
        path_prefixes_sorted = sorted(path_prefixes)
        for i, prefix1 in enumerate(path_prefixes_sorted):
            for prefix2 in path_prefixes_sorted[i + 1 :]:
                # Normalize by ensuring both end with / for comparison
                p1_norm = prefix1 if prefix1.endswith("/") else prefix1 + "/"
                p2_norm = prefix2 if prefix2.endswith("/") else prefix2 + "/"

                if p1_norm.startswith(p2_norm) or p2_norm.startswith(p1_norm):
                    # Find which apps these belong to for better error message
                    app1_slug = next(
                        (a.get("slug", "unknown") for a in apps if a.get("path_prefix") == prefix1),
                        "unknown",
                    )
                    app2_slug = next(
                        (a.get("slug", "unknown") for a in apps if a.get("path_prefix") == prefix2),
                        "unknown",
                    )
                    errors.append(
                        f"Path prefix conflict: '{prefix1}' (app: '{app1_slug}') and "
                        f"'{prefix2}' (app: '{app2_slug}') overlap. "
                        "One cannot be a prefix of another."
                    )

        # Check for duplicates
        if len(path_prefixes) != len(set(path_prefixes)):
            seen = {}
            for app_config in apps:
                prefix = app_config.get("path_prefix")
                slug = app_config.get("slug", "unknown")
                if prefix in seen:
                    first_slug = seen[prefix]
                    errors.append(
                        f"Duplicate path prefix: '{prefix}' used by both "
                        f"'{first_slug}' and '{slug}'"
                    )
                else:
                    seen[prefix] = slug

        return len(errors) == 0, errors

    def _discover_apps_from_directory(
        self,
        apps_dir: Path,
        path_prefix_template: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Auto-discover apps by scanning directory for manifest.json files.

        Args:
            apps_dir: Directory to scan for apps
            path_prefix_template: Template for path prefixes (e.g., "/app-{index}")

        Returns:
            List of app configurations
        """
        import json

        apps_dir = Path(apps_dir)
        if not apps_dir.exists():
            raise ValueError(f"Apps directory does not exist: {apps_dir}")

        discovered_apps = []
        manifest_files = list(apps_dir.rglob("manifest.json"))

        if not manifest_files:
            raise ValueError(f"No manifest.json files found in {apps_dir}")

        for idx, manifest_path in enumerate(sorted(manifest_files), start=1):
            try:
                with open(manifest_path) as f:
                    manifest_data = json.load(f)

                slug = manifest_data.get("slug")
                if not slug:
                    logger.warning(f"Skipping manifest without slug: {manifest_path}")
                    continue

                # Generate path prefix
                if path_prefix_template:
                    path_prefix = path_prefix_template.format(index=idx, slug=slug)
                else:
                    path_prefix = f"/{slug}"

                discovered_apps.append(
                    {
                        "slug": slug,
                        "manifest": manifest_path,
                        "path_prefix": path_prefix,
                    }
                )
                logger.info(
                    f"Discovered app '{slug}' at {manifest_path} " f"(will mount at {path_prefix})"
                )
            except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to read manifest at {manifest_path}: {e}")
                continue

        if not discovered_apps:
            raise ValueError(f"No valid apps discovered in {apps_dir}")

        return discovered_apps

    def _validate_manifests(self, apps: list[dict[str, Any]], strict: bool) -> list[str]:
        """Validate all app manifests."""
        import json

        logger.info("Validating all manifests before mounting...")
        validation_errors = []
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            manifest_path = app_config.get("manifest")
            try:
                with open(manifest_path) as f:
                    manifest_data = json.load(f)

                # Validate manifest
                from .manifest import validate_manifest

                is_valid, error_msg, error_paths = validate_manifest(manifest_data)

                if not is_valid:
                    error_detail = f"App '{slug}' at {manifest_path}: {error_msg}"
                    if error_paths:
                        error_detail += f" (paths: {', '.join(error_paths)})"
                    validation_errors.append(error_detail)
                    if strict:
                        raise ValueError(
                            f"Manifest validation failed for app '{slug}': {error_msg}"
                        ) from None

                # Validate slug matches manifest slug
                manifest_slug = manifest_data.get("slug")
                if manifest_slug and manifest_slug != slug:
                    error_msg = (
                        f"Slug mismatch: config slug '{slug}' does not match "
                        f"manifest slug '{manifest_slug}' in {manifest_path}"
                    )
                    validation_errors.append(error_msg)
                    if strict:
                        raise ValueError(error_msg) from None
            except FileNotFoundError as e:
                error_msg = f"Manifest file not found for app '{slug}': {manifest_path}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in manifest for app '{slug}' at {manifest_path}: {e}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e

        return validation_errors

    def _import_app_routes(self, child_app: "FastAPI", manifest_path: Path, slug: str) -> None:
        """
        Automatically discover and import route modules for a child app.

        This method looks for route modules (web.py, routes.py) in the same directory
        as the manifest and imports them so that route decorators are executed and
        routes are registered on the child app.

        Args:
            child_app: The FastAPI child app to register routes on
            manifest_path: Path to the manifest.json file
            slug: App slug for logging

        The method tries multiple strategies:
        1. Look for 'web.py' in the manifest directory
        2. Look for 'routes.py' in the manifest directory
        3. Check manifest for explicit 'routes_module' field (future support)

        When importing, the method ensures that route decorators in the imported module
        reference the child_app by temporarily injecting it into the module namespace.
        """
        import importlib.util
        import sys

        manifest_dir = manifest_path.parent

        # Try to find route modules in order of preference
        route_module_paths = [
            manifest_dir / "web.py",
            manifest_dir / "routes.py",
        ]

        # Also check for routes_module in manifest (future support)
        try:
            import json

            with open(manifest_path) as f:
                manifest_data = json.load(f)
            routes_module = manifest_data.get("routes_module")
            if routes_module:
                # Support both relative (to manifest dir) and absolute paths
                if routes_module.startswith("/"):
                    route_module_paths.insert(0, Path(routes_module))
                else:
                    route_module_paths.insert(0, manifest_dir / routes_module)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

        imported = False
        module_name = None
        route_module = None
        manifest_dir_str = None
        path_inserted = False

        for route_module_path in route_module_paths:
            if not route_module_path.exists():
                continue

            # Create a unique module name to avoid conflicts
            module_name = f"mdb_engine_imported_routes_{slug}_{id(child_app)}"

            try:
                # Validate file is actually a Python file
                if not route_module_path.suffix == ".py":
                    logger.debug(f"Skipping non-Python file '{route_module_path}' for app '{slug}'")
                    continue

                # Load the module spec
                spec = importlib.util.spec_from_file_location(module_name, route_module_path)
                if spec is None or spec.loader is None:
                    logger.warning(
                        f"Could not create spec for route module '{route_module_path}' "
                        f"for app '{slug}'"
                    )
                    continue

                route_module = importlib.util.module_from_spec(spec)

                # CRITICAL: Inject child_app into module namespace BEFORE loading
                # This ensures that @app.get(), @app.post(), etc. decorators in the
                # imported module will reference our child_app instead of creating a new one
                route_module.app = child_app
                route_module.engine = self  # Also provide engine reference for dependencies

                # Add to sys.modules temporarily to handle relative imports
                # Use a try-finally to ensure cleanup even on exceptions
                sys.modules[module_name] = route_module

                # Store route count before import
                routes_before = len(child_app.routes)

                # Add manifest directory to Python path temporarily for relative imports
                # This allows route modules to import sibling modules
                manifest_dir_str = str(manifest_dir.resolve())
                path_inserted = manifest_dir_str not in sys.path
                if path_inserted:
                    sys.path.insert(0, manifest_dir_str)

                try:
                    # Execute the module (runs route decorators with injected app)
                    spec.loader.exec_module(route_module)
                except SyntaxError as e:
                    logger.warning(
                        f"Syntax error in route module '{route_module_path}' "
                        f"for app '{slug}': {e}. Skipping this module."
                    )
                    continue
                except ImportError as e:
                    # ImportError might be due to missing dependencies - log but don't fail
                    logger.debug(
                        f"Import error in route module '{route_module_path}' "
                        f"for app '{slug}': {e}. "
                        "This may be OK if dependencies are optional."
                    )
                    # Check if it's a critical import (like FastAPI) vs optional dependency
                    error_str = str(e).lower()
                    if "fastapi" in error_str or "starlette" in error_str:
                        logger.warning(
                            f"Route module '{route_module_path}' for app '{slug}' "
                            "requires FastAPI/Starlette but they're not available. "
                            "Routes will not be registered."
                        )
                    continue
                finally:
                    # Remove from path only if we added it
                    if path_inserted and manifest_dir_str and manifest_dir_str in sys.path:
                        try:
                            sys.path.remove(manifest_dir_str)
                        except ValueError:
                            # Path might have been removed already - ignore
                            pass

                # Check if module overwrote app (shouldn't happen in well-structured modules)
                module_app = getattr(route_module, "app", None)
                if module_app is not None and module_app is not child_app:
                    import warnings

                    warning_msg = (
                        f"Route module '{route_module_path.name}' for app '{slug}' "
                        "created its own app instance. Routes defined before app creation "
                        "are registered, but routes defined after may not be. "
                        "Consider restructuring the module to use the injected 'app' variable."
                    )
                    logger.warning(warning_msg)
                    warnings.warn(warning_msg, UserWarning, stacklevel=2)

                routes_after = len(child_app.routes)
                routes_added = routes_after - routes_before

                if routes_added > 0:
                    logger.info(
                        f"✅ Auto-imported routes from '{route_module_path.name}' "
                        f"for app '{slug}'. Added {routes_added} route(s) "
                        f"(total: {routes_after})"
                    )
                else:
                    logger.debug(
                        f"Route module '{route_module_path.name}' for app '{slug}' "
                        "was imported but no new routes were registered. "
                        "This may be expected if routes are registered conditionally."
                    )

                imported = True
                break

            except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as e:
                logger.warning(
                    f"Unexpected error importing route module '{route_module_path}' "
                    f"for app '{slug}': {e}",
                    exc_info=True,
                )
                continue
            finally:
                # Clean up temporary module from sys.modules
                if module_name and module_name in sys.modules:
                    try:
                        del sys.modules[module_name]
                    except KeyError:
                        # Already removed - ignore
                        pass
                # Ensure path is cleaned up even if exception occurred
                if path_inserted and manifest_dir_str and manifest_dir_str in sys.path:
                    try:
                        sys.path.remove(manifest_dir_str)
                    except ValueError:
                        pass

        if not imported:
            logger.debug(
                f"No route modules found for app '{slug}' in {manifest_dir}. "
                "Routes may be defined elsewhere or app may not have HTTP routes."
            )

    def create_multi_app(  # noqa: C901
        self,
        apps: list[dict[str, Any]] | None = None,
        multi_app_manifest: Path | None = None,
        apps_dir: Path | None = None,
        path_prefix_template: str | None = None,
        validate: bool = False,
        strict: bool = False,
        title: str = "Multi-App API",
        root_path: str = "",
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """
        Create a parent FastAPI app that mounts multiple child apps.

        Each child app is mounted at a path prefix (e.g., /auth-hub, /pwd-zero) and
        maintains its own routes, middleware, and state while sharing the engine instance.

        Args:
            apps: List of app configurations. Each dict should have:
                - slug: App slug (required)
                - manifest: Path to manifest.json (required)
                - path_prefix: Optional path prefix (defaults to /{slug})
                - on_startup: Optional startup callback function
                - on_shutdown: Optional shutdown callback function
            multi_app_manifest: Path to a multi-app manifest.json that defines all apps.
                Format:
                {
                    "multi_app": {
                        "enabled": true,
                        "apps": [
                            {
                                "slug": "app1",
                                "manifest": "./app1/manifest.json",
                                "path_prefix": "/app1",
                            }
                        ]
                    }
                }
            apps_dir: Directory to scan for apps (auto-discovery). If provided and
                apps is None, will recursively scan for manifest.json files and
                auto-discover apps. Takes precedence over multi_app_manifest.
            path_prefix_template: Template for auto-generated path prefixes when using
                apps_dir. Use {index} for app index and {slug} for app slug.
                Example: "/app-{index}" or "/{slug}"
            validate: If True, validate all manifests before mounting (default: False)
            strict: If True, fail fast on any validation error (default: False).
                Only used when validate=True.
            title: Title for the parent FastAPI app
            root_path: Root path prefix for all mounted apps (optional)
            **fastapi_kwargs: Additional arguments passed to FastAPI()

        Returns:
            Parent FastAPI application with all child apps mounted

        Raises:
            ValueError: If configuration is invalid or path prefixes conflict
            RuntimeError: If engine is not initialized

        Features:
            - Built-in app context helpers: Each mounted app has access to:
              - request.state.app_base_path: Path prefix (e.g., "/app-1")
              - request.state.auth_hub_url: Auth hub URL from manifest or env
              - request.state.app_slug: App slug
              - request.state.mounted_apps: Dict of all mounted apps with paths
              - request.state.engine: MongoDBEngine instance
              - request.state.manifest: App's manifest.json
            - Unified health check: GET /health aggregates health from all apps
            - Route introspection: GET /_mdb/routes lists all routes from all apps
            - OpenAPI aggregation: /docs combines docs from all apps
            - Per-app docs: /docs/{app_slug} for individual app documentation

        Example:
            # Programmatic approach
            engine = MongoDBEngine(mongo_uri=..., db_name=...)
            app = engine.create_multi_app(
                apps=[
                    {
                        "slug": "auth-hub",
                        "manifest": Path("./auth-hub/manifest.json"),
                        "path_prefix": "/auth-hub",
                    },
                    {
                        "slug": "pwd-zero",
                        "manifest": Path("./pwd-zero/manifest.json"),
                        "path_prefix": "/pwd-zero",
                    },
                ]
            )

            # Manifest-based approach
            app = engine.create_multi_app(
                multi_app_manifest=Path("./multi_app_manifest.json")
            )

            # Auto-discovery approach
            app = engine.create_multi_app(
                apps_dir=Path("./apps"),
                path_prefix_template="/app-{index}",
                validate=True,
            )

            # Access app context in routes
            @app.get("/my-route")
            async def my_route(request: Request):
                base_path = request.state.app_base_path  # "/app-1"
                auth_url = request.state.auth_hub_url    # "/auth-hub"
                slug = request.state.app_slug            # "my-app"
                all_apps = request.state.mounted_apps     # Dict of all apps
        """
        import json

        from fastapi import FastAPI

        engine = self

        # Auto-discovery: if apps_dir is provided and apps is None, discover apps
        if apps_dir and apps is None:
            logger.info(f"Auto-discovering apps from directory: {apps_dir}")
            apps = self._discover_apps_from_directory(
                apps_dir=apps_dir,
                path_prefix_template=path_prefix_template,
            )

        # Load configuration from manifest or apps parameter
        if multi_app_manifest:
            manifest_path = Path(multi_app_manifest)
            with open(manifest_path) as f:
                multi_app_config = json.load(f)

            multi_app_section = multi_app_config.get("multi_app", {})
            if not multi_app_section.get("enabled", False):
                raise ValueError(
                    "multi_app.enabled must be True in multi_app_manifest to use multi-app mode"
                )

            apps_config = multi_app_section.get("apps", [])
            if not apps_config:
                raise ValueError("multi_app.apps must contain at least one app")

            # Resolve manifest paths relative to multi_app_manifest location
            manifest_dir = manifest_path.parent
            apps = []
            for app_config in apps_config:
                manifest_rel_path = app_config.get("manifest")
                if not manifest_rel_path:
                    raise ValueError(f"App '{app_config.get('slug')}' missing 'manifest' field")

                # Resolve relative to multi_app_manifest location
                manifest_full_path = (manifest_dir / manifest_rel_path).resolve()
                slug = app_config.get("slug")
                path_prefix = app_config.get("path_prefix", f"/{slug}")

                apps.append(
                    {
                        "slug": slug,
                        "manifest": manifest_full_path,
                        "path_prefix": path_prefix,
                    }
                )

        elif apps is not None:
            apps_config = apps
            # Convert Path objects to Path if they're strings
            apps = []
            for app_config in apps_config:
                manifest = app_config.get("manifest")
                if isinstance(manifest, str):
                    manifest = Path(manifest)
                apps.append(
                    {
                        "slug": app_config.get("slug"),
                        "manifest": manifest,
                        "path_prefix": app_config.get("path_prefix", f"/{app_config.get('slug')}"),
                        "on_startup": app_config.get("on_startup"),
                        "on_shutdown": app_config.get("on_shutdown"),
                    }
                )
        else:
            raise ValueError("Either 'apps', 'multi_app_manifest', or 'apps_dir' must be provided")

        if not apps:
            raise ValueError("At least one app must be configured")

        # Validate manifests if requested
        if validate:
            validation_errors = self._validate_manifests(apps, strict)
            if validation_errors:
                logger.warning(
                    "Manifest validation found issues:\n"
                    + "\n".join(f"  - {e}" for e in validation_errors)
                )
                if strict:
                    raise ValueError(
                        "Manifest validation failed (strict mode):\n"
                        + "\n".join(f"  - {e}" for e in validation_errors)
                    )

        # Validate path prefixes (enhanced)
        is_valid, errors = self._validate_path_prefixes(apps)
        if not is_valid:
            raise ValueError(
                "Path prefix validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        # Check if any app uses shared auth
        has_shared_auth = False
        for app_config in apps:
            try:
                manifest_path = app_config["manifest"]
                with open(manifest_path) as f:
                    app_manifest_pre = json.load(f)
                auth_config = app_manifest_pre.get("auth", {})
                if auth_config.get("mode") == "shared":
                    has_shared_auth = True
                    break
            except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Could not check auth mode for app '{app_config.get('slug')}': {e}")

        # Validate hooks before creating lifespan (fail fast)
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            on_startup = app_config.get("on_startup")
            on_shutdown = app_config.get("on_shutdown")

            if on_startup is not None and not callable(on_startup):
                raise ValueError(
                    f"on_startup hook for app '{slug}' must be callable, "
                    f"got {type(on_startup).__name__}"
                )
            if on_shutdown is not None and not callable(on_shutdown):
                raise ValueError(
                    f"on_shutdown hook for app '{slug}' must be callable, "
                    f"got {type(on_shutdown).__name__}"
                )

        # State for parent app
        # Build initial mounted_apps metadata synchronously so get_mounted_apps() works
        # immediately after create_multi_app() returns (before lifespan runs)
        mounted_apps: list[dict[str, Any]] = [
            {
                "slug": app_config["slug"],
                "path_prefix": app_config["path_prefix"],
                "status": "pending",  # Will be updated in lifespan to "mounted" or "failed"
                "manifest_path": str(app_config["manifest"]),
            }
            for app_config in apps
        ]
        shared_user_pool_initialized = False

        def _find_mounted_app_entry(slug: str) -> dict[str, Any] | None:
            """Find mounted app entry by slug."""
            for entry in mounted_apps:
                if entry.get("slug") == slug:
                    return entry
            return None

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """Lifespan context manager for parent app."""
            nonlocal mounted_apps, shared_user_pool_initialized

            # Initialize engine
            await engine.initialize()

            # Initialize shared user pool once if any app uses shared auth
            if has_shared_auth:
                logger.info("Initializing shared user pool for multi-app deployment")
                # Find first app with shared auth to get manifest for initialization
                for app_config in apps:
                    try:
                        manifest_path = app_config["manifest"]
                        with open(manifest_path) as f:
                            app_manifest_pre = json.load(f)
                        auth_config = app_manifest_pre.get("auth", {})
                        if auth_config.get("mode") == "shared":
                            await engine._initialize_shared_user_pool(app, app_manifest_pre)
                            shared_user_pool_initialized = True
                            logger.info("Shared user pool initialized for multi-app deployment")
                            break
                    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                        app_slug = app_config.get("slug", "unknown")
                        logger.warning(
                            f"Could not initialize shared user pool from app '{app_slug}': {e}"
                        )

            # Mount each child app
            for app_config in apps:
                slug = app_config["slug"]
                manifest_path = app_config["manifest"]
                path_prefix = app_config["path_prefix"]
                on_startup = app_config.get("on_startup")
                on_shutdown = app_config.get("on_shutdown")

                try:
                    # Load manifest for context helpers
                    try:
                        with open(manifest_path) as f:
                            app_manifest_data = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError) as e:
                        raise ValueError(
                            f"Failed to load manifest for app '{slug}' at {manifest_path}: {e}"
                        ) from e

                    # Log app configuration
                    auth_config = app_manifest_data.get("auth", {})
                    auth_mode = auth_config.get("mode", "app")
                    public_routes = auth_config.get("public_routes", [])
                    logger.info(
                        f"Mounting app '{slug}' at '{path_prefix}': "
                        f"auth_mode={auth_mode}, "
                        f"public_routes={len(public_routes)} routes"
                    )
                    if public_routes:
                        logger.debug(f"  Public routes for '{slug}': {public_routes}")
                    else:
                        logger.warning(
                            f"  App '{slug}' has no public routes configured. "
                            "All routes will require authentication."
                        )

                    # Create child app as sub-app (shares engine and lifecycle)
                    child_app = engine.create_app(
                        slug=slug,
                        manifest=manifest_path,
                        is_sub_app=True,  # Important: marks as sub-app
                        on_startup=on_startup,
                        on_shutdown=on_shutdown,
                    )

                    # CRITICAL: Set engine state BEFORE importing routes
                    # Routes may use dependencies that need request.app.state.engine
                    # This must be set before route decorators execute
                    child_app.state.engine = engine
                    child_app.state.app_slug = slug

                    # Automatically import routes from app module
                    # This discovers and imports route modules (web.py, routes.py, etc.)
                    # so that route decorators are executed and routes are registered
                    try:
                        self._import_app_routes(child_app, manifest_path, slug)
                    except (
                        ValueError,
                        TypeError,
                        AttributeError,
                        RuntimeError,
                        ImportError,
                        SyntaxError,
                        OSError,
                    ) as e:
                        logger.warning(
                            f"Failed to auto-import routes for app '{slug}': {e}. "
                            "Routes may need to be imported manually.",
                            exc_info=True,
                        )

                    # Share user_pool with child app if shared auth is enabled
                    if shared_user_pool_initialized and hasattr(app.state, "user_pool"):
                        child_app.state.user_pool = app.state.user_pool
                        # Also share audit_log if available
                        if hasattr(app.state, "audit_log"):
                            child_app.state.audit_log = app.state.audit_log
                        logger.debug(f"Shared user_pool with child app '{slug}'")

                    # Add middleware for app context helpers
                    from starlette.middleware.base import BaseHTTPMiddleware
                    from starlette.requests import Request

                    # Get auth_hub_url from manifest or env
                    auth_hub_url = None
                    if auth_config.get("mode") == "shared":
                        auth_hub_url = auth_config.get("auth_hub_url")
                    if not auth_hub_url:
                        auth_hub_url = os.getenv("AUTH_HUB_URL", "/auth-hub")

                    # Store parent app reference and current app info for middleware
                    # Note: engine and app_slug are already set above (before route import)
                    child_app.state.parent_app = app
                    child_app.state.app_base_path = path_prefix
                    child_app.state.app_auth_hub_url = auth_hub_url
                    child_app.state.app_manifest = app_manifest_data

                    # Create middleware factory to properly capture loop variables
                    def create_app_context_middleware(
                        app_slug: str,
                        app_path_prefix: str,
                        app_auth_hub_url_val: str,
                        app_manifest_data_val: dict[str, Any],
                    ) -> type[BaseHTTPMiddleware]:
                        """Create middleware class with captured variables."""

                        class _AppContextMiddleware(BaseHTTPMiddleware):
                            """Middleware that sets app context helpers on request.state."""

                            async def dispatch(self, request: Request, call_next):
                                # Get parent app from child app state
                                parent_app = getattr(request.app.state, "parent_app", None)

                                # Set app context helpers
                                request.state.app_base_path = getattr(
                                    request.app.state,
                                    "app_base_path",
                                    app_path_prefix,
                                )
                                request.state.auth_hub_url = getattr(
                                    request.app.state,
                                    "app_auth_hub_url",
                                    app_auth_hub_url_val,
                                )
                                request.state.app_slug = getattr(
                                    request.app.state, "app_slug", app_slug
                                )
                                request.state.engine = engine
                                request.state.manifest = getattr(
                                    request.app.state,
                                    "app_manifest",
                                    app_manifest_data_val,
                                )

                                # Get mounted apps from parent app state
                                if parent_app and hasattr(parent_app.state, "mounted_apps"):
                                    mounted_apps_list = parent_app.state.mounted_apps
                                    request.state.mounted_apps = {
                                        ma["slug"]: {
                                            "slug": ma["slug"],
                                            "path_prefix": ma.get("path_prefix"),
                                            "status": ma.get("status", "unknown"),
                                        }
                                        for ma in mounted_apps_list
                                    }
                                else:
                                    # Fallback: create minimal dict with current app
                                    request.state.mounted_apps = {
                                        app_slug: {
                                            "slug": app_slug,
                                            "path_prefix": app_path_prefix,
                                            "status": "mounted",
                                        }
                                    }

                                response = await call_next(request)
                                return response

                        return _AppContextMiddleware

                    middleware_class = create_app_context_middleware(
                        slug, path_prefix, auth_hub_url, app_manifest_data
                    )
                    child_app.add_middleware(middleware_class)
                    logger.debug(f"Added AppContextMiddleware to child app '{slug}'")

                    # Mount child app at path prefix
                    app.mount(path_prefix, child_app)

                    # CRITICAL FIX: Register WebSocket routes on parent app with full path
                    # FastAPI's app.mount() doesn't handle WebSocket routes correctly,
                    # so we need to register them on the parent app with the mount prefix
                    # Get WebSocket config from manifest directly (app registration happens
                    # asynchronously in lifespan, so config may not be available yet)
                    websockets_config = app_manifest_data.get("websockets")
                    if websockets_config:
                        try:
                            from fastapi import APIRouter

                            from ..routing.websockets import create_websocket_endpoint

                            for endpoint_name, endpoint_config in websockets_config.items():
                                ws_path = endpoint_config.get("path", f"/{endpoint_name}")
                                # Combine mount prefix with WebSocket path
                                full_ws_path = f"{path_prefix.rstrip('/')}{ws_path}"

                                # Handle auth configuration
                                auth_config = endpoint_config.get("auth", {})
                                if isinstance(auth_config, dict) and "required" in auth_config:
                                    require_auth = auth_config.get("required", True)
                                elif "require_auth" in endpoint_config:
                                    require_auth = endpoint_config.get("require_auth", True)
                                else:
                                    # Use app's auth_policy if available
                                    if "auth_policy" in app_manifest_data:
                                        require_auth = app_manifest_data["auth_policy"].get(
                                            "required", True
                                        )
                                    else:
                                        require_auth = True

                                ping_interval = endpoint_config.get("ping_interval", 30)

                                # Create WebSocket handler
                                # Use original path for handler (mount handled internally)
                                handler = create_websocket_endpoint(
                                    app_slug=slug,
                                    path=ws_path,
                                    endpoint_name=endpoint_name,
                                    handler=None,
                                    require_auth=require_auth,
                                    ping_interval=ping_interval,
                                )

                                # Register on parent app with full path
                                ws_router = APIRouter()
                                ws_router.websocket(full_ws_path)(handler)
                                app.include_router(ws_router)

                                logger.info(
                                    f"✅ Registered WebSocket route '{full_ws_path}' "
                                    f"for mounted app '{slug}' (mounted at '{path_prefix}')"
                                )
                        except ImportError:
                            logger.warning(
                                f"WebSocket support not available - skipping WebSocket routes "
                                f"for mounted app '{slug}'"
                            )
                        except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                            logger.error(
                                f"Failed to register WebSocket routes for mounted app "
                                f"'{slug}': {e}",
                                exc_info=True,
                            )

                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "mounted",
                                "manifest": app_manifest_data,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "mounted",
                                "manifest": app_manifest_data,
                            }
                        )
                    logger.info(f"Mounted app '{slug}' at path prefix '{path_prefix}'")

                except FileNotFoundError as e:
                    error_msg = (
                        f"Failed to mount app '{slug}' at {path_prefix}: "
                        f"manifest.json not found at {manifest_path}"
                    )
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except json.JSONDecodeError as e:
                    error_msg = (
                        f"Failed to mount app '{slug}' at {path_prefix}: "
                        f"Invalid JSON in manifest.json at {manifest_path}: {e}"
                    )
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except ValueError as e:
                    error_msg = f"Failed to mount app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except (KeyError, RuntimeError) as e:
                    error_msg = f"Failed to mount app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise RuntimeError(error_msg) from e
                    continue
                except (OSError, PermissionError, ImportError, AttributeError, TypeError) as e:
                    error_msg = f"Unexpected error mounting app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise RuntimeError(error_msg) from e
                    continue

            # Update app.state.mounted_apps with final status (entries already updated in place)
            # This ensures the state reflects the final mounted_apps list
            app.state.mounted_apps = mounted_apps

            yield

            # Shutdown is handled by parent app
            await engine.shutdown()

        # Create parent FastAPI app
        parent_app = FastAPI(title=title, lifespan=lifespan, root_path=root_path, **fastapi_kwargs)

        # Set mounted_apps immediately so get_mounted_apps() works before lifespan runs
        parent_app.state.mounted_apps = mounted_apps
        parent_app.state.is_multi_app = True
        parent_app.state.engine = engine

        # Store app reference in engine for get_mounted_apps()
        engine._multi_app_instance = parent_app

        # Add request scope middleware
        from starlette.middleware.base import BaseHTTPMiddleware

        from ..di import ScopeManager

        class RequestScopeMiddleware(BaseHTTPMiddleware):
            """Middleware that manages request-scoped DI instances."""

            async def dispatch(self, request, call_next):
                ScopeManager.begin_request()
                try:
                    response = await call_next(request)
                    return response
                finally:
                    ScopeManager.end_request()

        parent_app.add_middleware(RequestScopeMiddleware)
        logger.debug("RequestScopeMiddleware added for parent app")

        # Add shared CORS middleware if configured
        # (Individual apps can add their own CORS, but parent-level is useful)
        try:
            from fastapi.middleware.cors import CORSMiddleware

            parent_app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],  # Can be configured via manifest later
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            logger.debug("CORS middleware added for parent app")
        except ImportError:
            logger.warning("CORS middleware not available")

        # Add unified health check endpoint
        @parent_app.get("/health")
        async def health_check():
            """Unified health check for all mounted apps."""
            import time

            from ..observability import check_engine_health, check_mongodb_health

            # Both are async functions
            start_time = time.time()
            engine_health = await check_engine_health(engine)
            mongo_health = await check_mongodb_health(engine.mongo_client)
            engine_response_time = int((time.time() - start_time) * 1000)

            # Check each mounted app's status
            mounted_status = {}
            for mounted_app_info in mounted_apps:
                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]
                status = mounted_app_info["status"]

                app_status = {
                    "path_prefix": path_prefix,
                    "status": status,
                }

                if "error" in mounted_app_info:
                    app_status["error"] = mounted_app_info["error"]
                    app_status["status"] = "unhealthy"
                elif status == "mounted":
                    # App is mounted successfully
                    app_status["status"] = "healthy"
                    # Try to get response time by checking if app has routes
                    try:
                        # Find the mounted app and check its route count
                        for route in parent_app.routes:
                            if hasattr(route, "path") and route.path == path_prefix:
                                if hasattr(route, "app"):
                                    mounted_app = route.app
                                    route_count = len(mounted_app.routes)
                                    app_status["route_count"] = route_count
                                break
                    except (AttributeError, TypeError, KeyError):
                        pass

                mounted_status[app_slug] = app_status

            # Determine overall status
            all_healthy = (
                engine_health.status.value == "healthy"
                and mongo_health.status.value == "healthy"
                and all(
                    app_info.get("status") in ("healthy", "mounted")
                    for app_info in mounted_status.values()
                )
            )

            overall_status = "healthy" if all_healthy else "unhealthy"

            return {
                "status": overall_status,
                "engine": {
                    "status": engine_health.status.value,
                    "message": engine_health.message,
                    "response_time_ms": engine_response_time,
                },
                "mongodb": {
                    "status": mongo_health.status.value,
                    "message": mongo_health.message,
                },
                "apps": mounted_status,
            }

        # Add route introspection endpoint
        @parent_app.get("/_mdb/routes")
        async def list_routes():
            """List all routes from all mounted apps."""
            routes_info = {
                "parent_app": {
                    "routes": [],
                },
                "mounted_apps": {},
            }

            # Get parent app routes
            for route in parent_app.routes:
                route_info = {
                    "path": getattr(route, "path", str(route)),
                    "methods": list(getattr(route, "methods", set())),
                    "name": getattr(route, "name", None),
                }
                routes_info["parent_app"]["routes"].append(route_info)

            # Get routes from mounted apps
            for mounted_app_info in mounted_apps:
                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]
                status = mounted_app_info["status"]

                if status != "mounted":
                    routes_info["mounted_apps"][app_slug] = {
                        "path_prefix": path_prefix,
                        "status": status,
                        "routes": [],
                        "error": mounted_app_info.get("error"),
                    }
                    continue

                # Find the mounted app
                app_routes = []
                for route in parent_app.routes:
                    # Check if this route belongs to the mounted app
                    # Mounted apps appear as Mount routes
                    if hasattr(route, "path") and route.path == path_prefix:
                        # This is the mount point
                        if hasattr(route, "app"):
                            # Get routes from the mounted app
                            mounted_app = route.app
                            for child_route in mounted_app.routes:
                                route_path = getattr(child_route, "path", str(child_route))
                                # Prepend path prefix
                                full_path = (
                                    f"{path_prefix}{route_path}"
                                    if route_path != "/"
                                    else path_prefix
                                )

                                route_info = {
                                    "path": full_path,
                                    "relative_path": route_path,
                                    "methods": list(getattr(child_route, "methods", set())),
                                    "name": getattr(child_route, "name", None),
                                }
                                app_routes.append(route_info)

                routes_info["mounted_apps"][app_slug] = {
                    "path_prefix": path_prefix,
                    "status": status,
                    "routes": app_routes,
                    "route_count": len(app_routes),
                }

            return routes_info

        # Aggregate OpenAPI docs from all mounted apps
        def custom_openapi():
            """Generate aggregated OpenAPI schema from all mounted apps."""
            from fastapi.openapi.utils import get_openapi

            if parent_app.openapi_schema:
                return parent_app.openapi_schema

            # Get base schema from parent app
            openapi_schema = get_openapi(
                title=title,
                version=fastapi_kwargs.get("version", "1.0.0"),
                description=fastapi_kwargs.get("description", ""),
                routes=parent_app.routes,
            )

            # Aggregate schemas from mounted apps
            for mounted_app_info in mounted_apps:
                if mounted_app_info.get("status") != "mounted":
                    continue

                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]

                # Find the mounted app
                for route in parent_app.routes:
                    if hasattr(route, "path") and route.path == path_prefix:
                        if hasattr(route, "app"):
                            mounted_app = route.app
                            try:
                                # Get OpenAPI schema from mounted app
                                child_schema = get_openapi(
                                    title=getattr(mounted_app, "title", app_slug),
                                    version=getattr(mounted_app, "version", "1.0.0"),
                                    description=getattr(mounted_app, "description", ""),
                                    routes=mounted_app.routes,
                                )

                                # Merge paths with prefix
                                if "paths" in child_schema:
                                    for path, methods in child_schema["paths"].items():
                                        # Prepend path prefix
                                        prefixed_path = (
                                            f"{path_prefix}{path}" if path != "/" else path_prefix
                                        )
                                        openapi_schema["paths"][prefixed_path] = methods

                                # Merge components/schemas
                                if "components" in child_schema:
                                    if "components" not in openapi_schema:
                                        openapi_schema["components"] = {}
                                    if "schemas" in child_schema["components"]:
                                        if "schemas" not in openapi_schema["components"]:
                                            openapi_schema["components"]["schemas"] = {}
                                        openapi_schema["components"]["schemas"].update(
                                            child_schema["components"]["schemas"]
                                        )

                                logger.debug(f"Aggregated OpenAPI schema from app '{app_slug}'")
                            except (AttributeError, TypeError, KeyError, ValueError) as e:
                                logger.warning(
                                    f"Failed to aggregate OpenAPI schema from app '{app_slug}': {e}"
                                )
                        break

            parent_app.openapi_schema = openapi_schema
            return openapi_schema

        parent_app.openapi = custom_openapi

        # Add per-app docs endpoint
        @parent_app.get("/docs/{app_slug}")
        async def app_docs(app_slug: str):
            """Get OpenAPI docs for a specific app."""
            from fastapi.openapi.docs import get_swagger_ui_html

            # Find the app
            mounted_app = None
            path_prefix = None
            for mounted_app_info in mounted_apps:
                if mounted_app_info["slug"] == app_slug:
                    path_prefix = mounted_app_info["path_prefix"]
                    # Find the mounted app
                    for route in parent_app.routes:
                        if hasattr(route, "path") and route.path == path_prefix:
                            if hasattr(route, "app"):
                                mounted_app = route.app
                                break
                    break

            if not mounted_app:
                from fastapi import HTTPException

                raise HTTPException(404, f"App '{app_slug}' not found or not mounted")

            # Generate OpenAPI JSON for this app
            from fastapi.openapi.utils import get_openapi

            openapi_schema = get_openapi(
                title=getattr(mounted_app, "title", app_slug),
                version=getattr(mounted_app, "version", "1.0.0"),
                description=getattr(mounted_app, "description", ""),
                routes=mounted_app.routes,
            )

            # Modify paths to include prefix
            if "paths" in openapi_schema:
                new_paths = {}
                for path, methods in openapi_schema["paths"].items():
                    prefixed_path = f"{path_prefix}{path}" if path != "/" else path_prefix
                    new_paths[prefixed_path] = methods
                openapi_schema["paths"] = new_paths

            # Return Swagger UI HTML
            openapi_url = f"/_mdb/openapi/{app_slug}.json"

            # Store schema temporarily for the JSON endpoint
            if not hasattr(parent_app.state, "app_openapi_schemas"):
                parent_app.state.app_openapi_schemas = {}
            parent_app.state.app_openapi_schemas[app_slug] = openapi_schema

            return get_swagger_ui_html(
                openapi_url=openapi_url,
                title=f"{app_slug} - API Documentation",
            )

        @parent_app.get("/_mdb/openapi/{app_slug}.json")
        async def app_openapi_json(app_slug: str):
            """Get OpenAPI JSON for a specific app."""
            from fastapi import HTTPException

            if not hasattr(parent_app.state, "app_openapi_schemas"):
                raise HTTPException(404, f"OpenAPI schema for '{app_slug}' not found")

            schema = parent_app.state.app_openapi_schemas.get(app_slug)
            if not schema:
                raise HTTPException(404, f"OpenAPI schema for '{app_slug}' not found")

            return schema

        logger.info(f"Multi-app parent created with {len(apps)} app(s) configured")

        return parent_app

    def get_mounted_apps(self, app: Optional["FastAPI"] = None) -> list[dict[str, Any]]:
        """
        Get metadata about all mounted apps.

        Args:
            app: FastAPI app instance (optional, will use engine's tracked app if available)

        Returns:
            List of dicts with app metadata:
            - slug: App slug
            - path_prefix: Path prefix where app is mounted
            - status: Mount status ("mounted", "failed", etc.)
            - manifest: App manifest (if available)
            - error: Error message (if status is "failed")

        Example:
            mounted_apps = engine.get_mounted_apps(app)
            for app_info in mounted_apps:
                print(f"App {app_info['slug']} at {app_info['path_prefix']}")
        """
        if app is None:
            # Try to get from engine state if available
            if hasattr(self, "_multi_app_instance"):
                app = self._multi_app_instance
            else:
                raise ValueError(
                    "App instance required. Pass app parameter or use "
                    "app.state.mounted_apps directly."
                )

        mounted_apps = getattr(app.state, "mounted_apps", [])
        return mounted_apps

    async def _initialize_shared_user_pool(
        self,
        app: "FastAPI",
        manifest: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize shared user pool, audit log, and set them on app.state.

        Called during lifespan startup for apps using "shared" auth mode.
        The lazy middleware (added at app creation time) will read the
        user_pool from app.state at request time.

        Security Features:
        - JWT secret required (fails fast if not configured)
        - allow_insecure_dev mode for local development only
        - Audit logging for compliance and forensics

        Args:
            app: FastAPI application instance
            manifest: Optional manifest dict for seeding demo users
        """
        from ..auth.audit import AuthAuditLog
        from ..auth.shared_users import SharedUserPool

        # Determine if we're in development mode
        # Development = allow insecure auto-generated JWT secret
        is_dev = (
            os.getenv("MDB_ENGINE_ENV", "").lower() in ("dev", "development", "local")
            or os.getenv("ENVIRONMENT", "").lower() in ("dev", "development", "local")
            or os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
        )

        # Thread-safe initialization with async lock to prevent race conditions
        async with self._shared_user_pool_lock:
            # Check if another coroutine is initializing
            if self._shared_user_pool_initializing:
                # Wait for other initialization to complete
                while self._shared_user_pool_initializing:
                    import asyncio

                    await asyncio.sleep(0.01)  # Small delay to avoid busy-waiting
                # After waiting, check if pool was initialized
                if hasattr(self, "_shared_user_pool") and self._shared_user_pool is not None:
                    app.state.user_pool = self._shared_user_pool
                    return

            # Check if already initialized (double-check pattern)
            if hasattr(self, "_shared_user_pool") and self._shared_user_pool is not None:
                app.state.user_pool = self._shared_user_pool
                return

            # Mark as initializing
            self._shared_user_pool_initializing = True
            try:
                # Create shared user pool
                self._shared_user_pool = SharedUserPool(
                    self._connection_manager.mongo_db,
                    allow_insecure_dev=is_dev,
                )
                await self._shared_user_pool.ensure_indexes()
                logger.info("SharedUserPool initialized")

                # Expose user pool on app.state for middleware to access
                app.state.user_pool = self._shared_user_pool
            finally:
                # Always clear the initializing flag
                self._shared_user_pool_initializing = False

        # Seed demo users to SharedUserPool if configured in manifest
        if manifest:
            auth_config = manifest.get("auth", {})
            users_config = auth_config.get("users", {})
            demo_users = users_config.get("demo_users", [])

            if demo_users and users_config.get("demo_user_seed_strategy", "auto") != "disabled":
                for demo in demo_users:
                    try:
                        email = demo.get("email")
                        password = demo.get("password")
                        app_roles = demo.get("app_roles", {})

                        existing = await self._shared_user_pool.get_user_by_email(email)

                        if not existing:
                            await self._shared_user_pool.create_user(
                                email=email,
                                password=password,
                                app_roles=app_roles,
                            )
                            logger.info(f"✅ Created shared demo user: {email}")
                        else:
                            logger.debug(f"ℹ️  Shared demo user exists: {email}")
                    except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                        logger.warning(
                            f"⚠️  Failed to create shared demo user {demo.get('email')}: {e}"
                        )

        # Initialize audit logging if enabled
        auth_config = (manifest or {}).get("auth", {})
        audit_config = auth_config.get("audit", {})
        audit_enabled = audit_config.get("enabled", True)  # Default: enabled for shared auth

        if audit_enabled:
            retention_days = audit_config.get("retention_days", 90)
            if not hasattr(self, "_auth_audit_log") or self._auth_audit_log is None:
                self._auth_audit_log = AuthAuditLog(
                    self._connection_manager.mongo_db,
                    retention_days=retention_days,
                )
                await self._auth_audit_log.ensure_indexes()
                logger.info(f"AuthAuditLog initialized (retention: {retention_days} days)")

            app.state.audit_log = self._auth_audit_log

        logger.info("SharedUserPool and AuditLog attached to app.state")

    def lifespan(
        self,
        slug: str,
        manifest: Path,
    ) -> Callable:
        """
        Create a lifespan context manager for use with FastAPI.

        Use this when you want more control over FastAPI app creation
        but still want automatic engine lifecycle management.

        Args:
            slug: Application slug
            manifest: Path to manifest.json file

        Returns:
            Async context manager for FastAPI lifespan

        Example:
            engine = MongoDBEngine(...)
            app = FastAPI(lifespan=engine.lifespan("my_app", Path("manifest.json")))
        """
        engine = self
        manifest_path = Path(manifest)

        @asynccontextmanager
        async def _lifespan(app: Any):
            """Lifespan context manager."""
            # Initialize engine
            await engine.initialize()

            # Load and register manifest
            app_manifest = await engine.load_manifest(manifest_path)
            await engine.register_app(app_manifest)

            # Auto-retrieve app token
            await engine.auto_retrieve_app_token(slug)

            # Expose on app.state
            app.state.engine = engine
            app.state.app_slug = slug
            app.state.manifest = app_manifest

            yield

            await engine.shutdown()

        return _lifespan

    async def auto_retrieve_app_token(self, slug: str) -> str | None:
        """
        Auto-retrieve app token from environment or database.

        Follows convention: {SLUG_UPPER}_SECRET environment variable.
        Falls back to database retrieval via secrets manager.

        Args:
            slug: Application slug

        Returns:
            App token if found, None otherwise

        Example:
            # Set MY_APP_SECRET environment variable, or
            # let the engine retrieve from database
            token = await engine.auto_retrieve_app_token("my_app")
        """
        # Check cache first
        if slug in self._app_token_cache:
            logger.debug(f"Using cached token for '{slug}'")
            return self._app_token_cache[slug]

        # Try environment variable first (convention: {SLUG}_SECRET)
        env_var_name = f"{slug.upper().replace('-', '_')}_SECRET"
        token = os.getenv(env_var_name)

        if token:
            logger.info(f"App token for '{slug}' loaded from {env_var_name}")
            self._app_token_cache[slug] = token
            return token

        # Try to retrieve from database
        if self._app_secrets_manager:
            try:
                secret_exists = await self._app_secrets_manager.app_secret_exists(slug)
                if secret_exists:
                    token = await self._app_secrets_manager.get_app_secret(slug)
                    if token:
                        logger.info(f"App token for '{slug}' retrieved from database")
                        self._app_token_cache[slug] = token
                        return token
                else:
                    logger.debug(f"No stored secret found for '{slug}'")
            except PyMongoError as e:
                logger.warning(f"Error retrieving app token for '{slug}': {e}")

        logger.debug(
            f"No app token found for '{slug}'. "
            f"Set {env_var_name} environment variable or register app to generate one."
        )
        return None

    def get_app_token(self, slug: str) -> str | None:
        """
        Get cached app token for a slug.

        Returns token from cache if available. Use auto_retrieve_app_token()
        to populate the cache first.

        Args:
            slug: Application slug

        Returns:
            Cached app token or None
        """
        return self._app_token_cache.get(slug)
