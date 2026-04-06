"""
Engine

The core orchestration engine for MDB_ENGINE that manages:
- Database connections
- Experiment registration
- Authentication/authorization
- Index management
- Resource lifecycle
- FastAPI integration with lifespan management

This module is part of MDB_ENGINE - MongoDB Engine.

Usage:
    # Simple usage (most common)
    engine = MongoDBEngine(mongo_uri=..., db_name=...)
    await engine.initialize()
    db = await engine.get_scoped_db("my_app")

    # With FastAPI integration
    app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from ..auth import AuthorizationProvider
    from .csfle import CSFLEConfig

# Import engine components
from ..constants import DEFAULT_MAX_POOL_SIZE, DEFAULT_MIN_POOL_SIZE
from ..observability import (
    HealthChecker,
    check_engine_health,
    check_mongodb_health,
    check_pool_health,
)

# Import mixins
from .app_lifecycle import AppLifecycleMixin
from .app_registration import AppRegistrationManager
from .app_secrets import AppSecretsManager
from .connection import ConnectionManager
from .encryption import EnvelopeEncryptionService
from .fastapi_app import FastAPIAppMixin
from .gdpr_ops import GDPRMixin
from .index_management import IndexManager
from .manifest import ManifestParser, ManifestValidator
from .multi_app import MultiAppMixin
from .scoped_access import ScopedAccessMixin
from .service_initialization import ServiceInitializer
from .websocket_ops import WebSocketMixin

logger = logging.getLogger(__name__)


class MongoDBEngine(
    ScopedAccessMixin,
    AppLifecycleMixin,
    FastAPIAppMixin,
    MultiAppMixin,
    GDPRMixin,
    WebSocketMixin,
):
    """
    The MongoDB Engine for managing multi-app applications.

    This class orchestrates all engine components including:
    - Database connections and scoping
    - Manifest validation and parsing
    - App registration
    - Index management
    - Authentication/authorization setup
    - FastAPI integration with lifespan management

    Example:
        # Simple usage
        engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="mydb")
        await engine.initialize()
        db = await engine.get_scoped_db("my_app")

        # With FastAPI
        app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
    """

    def __init__(
        self,
        mongo_uri: str | None = None,
        db_name: str | None = None,
        manifests_dir: Path | None = None,
        authz_provider: Optional["AuthorizationProvider"] = None,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
        # Optional CSFLE support
        csfle_config: Optional["CSFLEConfig"] = None,
    ) -> None:
        """
        Initialize the MongoDB Engine.

        Args:
            mongo_uri: MongoDB connection URI. If not provided, reads from
                MONGODB_URI or MDB_MONGO_URI env vars, falling back to
                ``mongodb://localhost:27017``.
            db_name: Database name. If not provided, reads from MDB_DB_NAME
                env var, falling back to ``mdb_engine``.
            manifests_dir: Path to manifests directory (optional)
            authz_provider: Authorization provider instance (optional, can be set later)
            max_pool_size: Maximum MongoDB connection pool size
            min_pool_size: Minimum MongoDB connection pool size
            csfle_config: Optional CSFLE configuration for field-level encryption.
                Use build_csfle_config_from_manifests() to build from manifest files,
                or CSFLEConfig.from_memory_config() for simple memory encryption.
        """
        from ..env import get_db_name, get_mongo_uri

        self.mongo_uri = mongo_uri or get_mongo_uri()
        self.db_name = db_name or get_db_name()
        self.manifests_dir = manifests_dir
        self.authz_provider = authz_provider
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self.csfle_config = csfle_config

        # Initialize component managers
        self._connection_manager = ConnectionManager(
            mongo_uri=self.mongo_uri,
            db_name=self.db_name,
            max_pool_size=max_pool_size,
            min_pool_size=min_pool_size,
            csfle_config=csfle_config,
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
        self._websocket_session_manager: Any | None = None  # WebSocketSessionManager
        self._websocket_ticket_store: Any | None = None  # WebSocketTicketStore

        # Store app read_scopes mapping for validation
        self._app_read_scopes: dict[str, list[str]] = {}

        # Store app token cache for auto-retrieval
        self._app_token_cache: dict[str, str] = {}

        # Async lock for thread-safe shared user pool initialization
        self._shared_user_pool_lock = asyncio.Lock()
        self._shared_user_pool_initializing = False

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
        # Instrument PyMongo before any MongoClient is created so OTel can
        # monkey-patch the driver.  Safe no-op when OTel is not installed.
        from ..observability.tracing import instrument_pymongo

        instrument_pymongo()

        # Initialize connection
        await self._connection_manager.initialize()

        # Initialize encryption service
        from .encryption import MASTER_KEY_ENV_VAR

        try:
            self._encryption_service = EnvelopeEncryptionService()
        except ValueError as e:
            logger.warning(
                f"Encryption service not initialized: {e}. "
                "Envelope encryption for app secrets is disabled. "
                "Cookie/session authentication is not affected. "
                f"Set {MASTER_KEY_ENV_VAR} to enable app-secret encryption."
            )
            # Continue without encryption (backward compatibility)
            self._encryption_service = None

        # Initialize app secrets manager (only if encryption service available)
        if self._encryption_service:
            self._app_secrets_manager = AppSecretsManager(
                mongo_db=self._connection_manager.mongo_db,
                encryption_service=self._encryption_service,
            )
            # Initialize WebSocket session manager for secure-by-default WebSocket auth
            from ..auth.websocket_sessions import WebSocketSessionManager

            self._websocket_session_manager = WebSocketSessionManager(
                mongo_db=self._connection_manager.mongo_db,
                encryption_service=self._encryption_service,
            )

        # Initialize WebSocket ticket store (in-memory, no dependencies needed)
        # Tickets are preferred for multi-app SSO setups (short-lived, single-use)
        from ..auth.websocket_tickets import WebSocketTicketStore

        self._websocket_ticket_store = WebSocketTicketStore()
        logger.info("WebSocket ticket store initialized")

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
            connection_manager=self._connection_manager,
        )

    @property
    def connection_manager(self):
        """
        Get the connection manager.

        Returns:
            ConnectionManager instance
        """
        return self._connection_manager

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
            # CORRECT: Use for health checks
            health = await check_mongodb_health(engine.mongo_client)

            # WRONG: Don't use for data access
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
                db = await engine.get_scoped_db("my_app")
        """
        return self._connection_manager.initialized

    async def shutdown(self) -> None:
        """
        Shutdown the MongoDB Engine and clean up resources.

        This method:
        1. Closes MongoDB connections
        2. Clears app registrations
        3. Resets initialization state

        This method is idempotent - it's safe to call multiple times.
        """
        start = time.monotonic()
        logger.info("[Engine] Shutdown initiated")

        if self._service_initializer:
            self._service_initializer.clear_services()

        if self._app_registration_manager:
            self._app_registration_manager.clear_apps()

        await self._connection_manager.shutdown()

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info("[Engine] Shutdown complete in %dms", elapsed_ms)

    # NOTE: Synchronous __enter__/__exit__ intentionally omitted.
    # The engine requires async lifecycle management (initialize/shutdown).
    # Use ``async with MongoDBEngine(...) as engine:`` instead.

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

        health_checker.register_check(lambda: check_mongodb_health(self._connection_manager.mongo_client))

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


# ============================================================================
# CSFLE UTILITY FUNCTIONS
# ============================================================================


def build_csfle_config_from_manifests(
    manifests: list[dict[str, Any]] | None = None,
    manifest_paths: list[Path] | None = None,
    manifests_dir: Path | None = None,
) -> "CSFLEConfig | None":
    """
    Build a combined CSFLEConfig from multiple manifests.

    This utility function scans manifests for encryption configuration
    (both memory_config.encrypted and encrypted_fields) and builds a
    single CSFLEConfig that can be passed to MongoDBEngine.

    Args:
        manifests: List of manifest dictionaries (already parsed)
        manifest_paths: List of paths to manifest.json files
        manifests_dir: Directory containing manifest.json files to scan

    Returns:
        Combined CSFLEConfig, or None if no encryption is configured

    Example:
        # From manifest directory
        config = build_csfle_config_from_manifests(
            manifests_dir=Path("./apps")
        )
        engine = MongoDBEngine(
            mongo_uri=...,
            db_name=...,
            csfle_config=config
        )

        # From specific manifests
        config = build_csfle_config_from_manifests(
            manifest_paths=[
                Path("./app1/manifest.json"),
                Path("./app2/manifest.json"),
            ]
        )
    """
    from .csfle import CSFLEConfig

    all_manifests: list[tuple[str, dict[str, Any]]] = []

    # Collect manifests from provided dictionaries
    if manifests:
        for manifest in manifests:
            slug = manifest.get("slug", "unknown")
            all_manifests.append((slug, manifest))

    # Load manifests from paths
    if manifest_paths:
        for path in manifest_paths:
            try:
                with open(path) as f:
                    manifest = json.load(f)
                    slug = manifest.get("slug", path.stem)
                    all_manifests.append((slug, manifest))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load manifest from {path}: {e}")

    # Scan directory for manifests
    if manifests_dir and manifests_dir.exists():
        for manifest_path in manifests_dir.rglob("manifest.json"):
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                    slug = manifest.get("slug", manifest_path.parent.name)
                    # Avoid duplicates
                    if not any(s == slug for s, _ in all_manifests):
                        all_manifests.append((slug, manifest))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load manifest from {manifest_path}: {e}")

    if not all_manifests:
        return None

    # Build combined config
    combined_config: CSFLEConfig | None = None

    for slug, manifest in all_manifests:
        app_config: CSFLEConfig | None = None

        # Check for memory_config.encrypted
        memory_config = manifest.get("memory_config", {})
        if memory_config.get("encrypted", False):
            app_config = CSFLEConfig.from_memory_config(memory_config, slug)
            logger.info(f"Found memory encryption config for app '{slug}'")

        # Check for encrypted_fields
        encrypted_fields = manifest.get("encrypted_fields", {})
        if encrypted_fields:
            encryption_config = manifest.get("encryption_config", {})
            fields_config = CSFLEConfig.from_encrypted_fields(encrypted_fields, encryption_config, slug)
            if app_config:
                app_config = app_config.merge_with(fields_config)
            else:
                app_config = fields_config
            logger.info(f"Found encrypted_fields config for app '{slug}': " f"{list(encrypted_fields.keys())}")

        # Merge into combined config
        if app_config and app_config.enabled:
            if combined_config:
                combined_config = combined_config.merge_with(app_config)
            else:
                combined_config = app_config

    if combined_config and combined_config.enabled:
        logger.info(
            f"Built combined CSFLE config: "
            f"collections={list(combined_config.encrypted_collections.keys())}, "
            f"kms_provider={combined_config.kms_provider}"
        )

    return combined_config


def build_csfle_config_from_manifest(
    manifest: dict[str, Any],
    app_slug: str | None = None,
) -> "CSFLEConfig | None":
    """
    Build CSFLEConfig from a single manifest.

    Convenience function for single-app scenarios.

    Args:
        manifest: Manifest dictionary
        app_slug: Optional app slug (uses manifest slug if not provided)

    Returns:
        CSFLEConfig or None if no encryption configured

    Example:
        manifest = json.load(open("manifest.json"))
        config = build_csfle_config_from_manifest(manifest)
        if config:
            engine = MongoDBEngine(..., csfle_config=config)
    """
    from .csfle import CSFLEConfig

    slug = app_slug or manifest.get("slug", "app")
    config: CSFLEConfig | None = None

    # Check memory_config.encrypted
    memory_config = manifest.get("memory_config", {})
    if memory_config.get("encrypted", False):
        config = CSFLEConfig.from_memory_config(memory_config, slug)

    # Check encrypted_fields
    encrypted_fields = manifest.get("encrypted_fields", {})
    if encrypted_fields:
        encryption_config = manifest.get("encryption_config", {})
        fields_config = CSFLEConfig.from_encrypted_fields(encrypted_fields, encryption_config, slug)
        if config:
            config = config.merge_with(fields_config)
        else:
            config = fields_config

    return config if config and config.enabled else None
