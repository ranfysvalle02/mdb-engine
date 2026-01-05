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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

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
        manifests_dir: Optional[Path] = None,
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
        self._app_registration_manager: Optional[AppRegistrationManager] = None
        self._index_manager: Optional[IndexManager] = None
        self._service_initializer: Optional[ServiceInitializer] = None
        self._encryption_service: Optional[EnvelopeEncryptionService] = None
        self._app_secrets_manager: Optional[AppSecretsManager] = None

        # Store app read_scopes mapping for validation
        self._app_read_scopes: Dict[str, List[str]] = {}

        # Store app token cache for auto-retrieval
        self._app_token_cache: Dict[str, str] = {}

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
        app_token: Optional[str] = None,
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
        app_token: Optional[str] = None,
        read_scopes: Optional[List[str]] = None,
        write_scope: Optional[str] = None,
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

        async def seed_data_callback(slug: str, initial_data: Dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.seed_initial_data(slug, initial_data)

        async def initialize_memory_callback(slug: str, memory_config: Dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.initialize_memory_service(slug, memory_config)

        async def register_websockets_callback(
            slug: str, websockets_config: Dict[str, Any]
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.register_websockets(slug, websockets_config)

        async def setup_observability_callback(
            slug: str,
            manifest: "ManifestDict",
            observability_config: Dict[str, Any],
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

    def get_embedding_service(self, slug: str) -> Optional[Any]:
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
    def _apps(self) -> Dict[str, Any]:
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

    def list_apps(self) -> List[str]:
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

    def get_metrics(self) -> Dict[str, Any]:
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
        title: Optional[str] = None,
        on_startup: Optional[Callable[["FastAPI", "MongoDBEngine", Dict[str, Any]], Awaitable[None]]] = None,
        on_shutdown: Optional[Callable[["FastAPI", "MongoDBEngine", Dict[str, Any]], Awaitable[None]]] = None,
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """
        Create a FastAPI application with proper lifespan management.

        This method creates a FastAPI app that:
        1. Initializes the engine on startup
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
        app_manifest: Dict[str, Any] = {}
        is_multi_site = False

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """Lifespan context manager for initialization and cleanup."""
            nonlocal app_manifest, is_multi_site

            # Initialize engine
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
                await engine._initialize_shared_user_pool(app, app_manifest)
            else:
                logger.info(f"Per-app auth mode for '{slug}'")
                # Auto-retrieve app token for "app" mode
                await engine.auto_retrieve_app_token(slug)

            # Auto-initialize authorization provider from manifest config
            auth_policy = auth_config.get("policy", {})
            authz_provider_type = auth_policy.get("provider")

            if authz_provider_type == "oso":
                # Initialize OSO Cloud provider
                try:
                    from ..auth.oso_factory import initialize_oso_from_manifest

                    authz_provider = await initialize_oso_from_manifest(
                        engine, slug, app_manifest
                    )
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
                except Exception as e:
                    logger.error(f"❌ Failed to initialize OSO provider for '{slug}': {e}")

            elif authz_provider_type == "casbin":
                # Initialize Casbin provider
                try:
                    from ..auth.casbin_factory import initialize_casbin_from_manifest

                    authz_provider = await initialize_casbin_from_manifest(
                        engine, slug, app_manifest
                    )
                    if authz_provider:
                        app.state.authz_provider = authz_provider
                        logger.info(f"✅ Casbin provider auto-initialized for '{slug}'")
                    else:
                        logger.warning(
                            f"⚠️  Casbin provider not initialized for '{slug}'"
                        )
                except ImportError as e:
                    logger.warning(
                        f"⚠️  Casbin not available for '{slug}': {e}. "
                        "Install with: pip install mdb-engine[casbin]"
                    )
                except Exception as e:
                    logger.error(f"❌ Failed to initialize Casbin provider for '{slug}': {e}")

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
                        logger.info(
                            f"✅ Seeded {len(demo_users)} demo user(s) for '{slug}'"
                        )
                except Exception as e:
                    logger.warning(f"⚠️  Failed to seed demo users for '{slug}': {e}")

            # Expose engine state on app.state
            app.state.engine = engine
            app.state.app_slug = slug
            app.state.manifest = app_manifest
            app.state.is_multi_site = is_multi_site
            app.state.auth_mode = auth_mode
            app.state.ray_actor = engine.ray_actor

            # Call on_startup callback if provided
            if on_startup:
                try:
                    await on_startup(app, engine, app_manifest)
                    logger.info(f"on_startup callback completed for '{slug}'")
                except Exception as e:
                    logger.error(f"on_startup callback failed for '{slug}': {e}")
                    raise

            yield

            # Call on_shutdown callback if provided
            if on_shutdown:
                try:
                    await on_shutdown(app, engine, app_manifest)
                    logger.info(f"on_shutdown callback completed for '{slug}'")
                except Exception as e:
                    logger.warning(f"on_shutdown callback failed for '{slug}': {e}")

            await engine.shutdown()

        # Create FastAPI app
        app = FastAPI(title=app_title, lifespan=lifespan, **fastapi_kwargs)

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

    async def _initialize_shared_user_pool(
        self,
        app: "FastAPI",
        manifest: Optional[Dict[str, Any]] = None,
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

        # Create or get shared user pool
        if not hasattr(self, "_shared_user_pool") or self._shared_user_pool is None:
            self._shared_user_pool = SharedUserPool(
                self._connection_manager.mongo_db,
                allow_insecure_dev=is_dev,
            )
            await self._shared_user_pool.ensure_indexes()
            logger.info("SharedUserPool initialized")

        # Expose user pool on app.state for middleware to access
        app.state.user_pool = self._shared_user_pool

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
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to create shared demo user {demo.get('email')}: {e}")

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

    async def auto_retrieve_app_token(self, slug: str) -> Optional[str]:
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

    def get_app_token(self, slug: str) -> Optional[str]:
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
