"""
Runtime Engine

The core orchestration engine for MDB_RUNTIME that manages:
- Database connections
- Experiment registration
- Authentication/authorization
- Index management
- Resource lifecycle

This module is part of MDB_RUNTIME - MongoDB Multi-Tenant Runtime Engine.
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

if TYPE_CHECKING:
    from ..auth import AuthorizationProvider

# Import runtime components
from ..database import ScopedMongoWrapper
from ..exceptions import InitializationError
from .manifest import ManifestValidator, ManifestParser
from ..indexes import run_index_creation_for_collection
from ..constants import (
    DEFAULT_MAX_POOL_SIZE,
    DEFAULT_MIN_POOL_SIZE,
    DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
    DEFAULT_MAX_IDLE_TIME_MS,
)
from ..observability import (
    record_operation,
    get_logger as get_contextual_logger,
    set_app_context,
    check_mongodb_health,
    check_engine_health,
    check_pool_health,
    HealthChecker,
)

if not TYPE_CHECKING:
    from ..auth import AuthorizationProvider

logger = logging.getLogger(__name__)
# Use contextual logger for better observability
contextual_logger = get_contextual_logger(__name__)


class RuntimeEngine:
    """
    Core runtime engine for managing multi-app applications.
    
    This class orchestrates all runtime components including:
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
        Initialize the runtime engine.
        
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
        
        # Runtime state
        self._mongo_client: Optional[AsyncIOMotorClient] = None
        self._mongo_db: Optional[AsyncIOMotorDatabase] = None
        self._initialized: bool = False
        self._apps: Dict[str, Dict[str, Any]] = {}
        
        # Validators
        self.manifest_validator = ManifestValidator()
        self.manifest_parser = ManifestParser()
    
    async def initialize(self) -> None:
        """
        Initialize the runtime engine.
        
        This method:
        1. Connects to MongoDB
        2. Validates the connection
        3. Sets up initial state
        
        Raises:
            InitializationError: If initialization fails (subclass of RuntimeError
                for backward compatibility)
            RuntimeError: If initialization fails (for backward compatibility)
        """
        import time
        start_time = time.time()
        
        if self._initialized:
            logger.warning("RuntimeEngine already initialized. Skipping re-initialization.")
            return
        
        contextual_logger.info(
            "Initializing RuntimeEngine",
            extra={
                "mongo_uri": self.mongo_uri,
                "db_name": self.db_name,
                "max_pool_size": self.max_pool_size,
                "min_pool_size": self.min_pool_size,
            }
        )
        
        try:
            # Connect to MongoDB
            self._mongo_client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
                appname="MDB_RUNTIME",
                maxPoolSize=self.max_pool_size,
                minPoolSize=self.min_pool_size,
                maxIdleTimeMS=DEFAULT_MAX_IDLE_TIME_MS,
                retryWrites=True,
                retryReads=True,
            )
            
            # Verify connection
            await self._mongo_client.admin.command("ping")
            self._mongo_db = self._mongo_client[self.db_name]
            
            self._initialized = True
            duration_ms = (time.time() - start_time) * 1000
            record_operation("engine.initialize", duration_ms, success=True)
            contextual_logger.info(
                "RuntimeEngine initialized successfully",
                extra={
                    "db_name": self.db_name,
                    "pool_size": f"{self.min_pool_size}-{self.max_pool_size}",
                    "duration_ms": round(duration_ms, 2),
                }
            )
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            duration_ms = (time.time() - start_time) * 1000
            record_operation("engine.initialize", duration_ms, success=False)
            contextual_logger.critical(
                "MongoDB connection failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True
            )
            # Raise InitializationError but it's a subclass of RuntimeError for backward compatibility
            raise InitializationError(
                f"Failed to connect to MongoDB: {e}",
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                context={
                    "error_type": type(e).__name__,
                    "max_pool_size": self.max_pool_size,
                    "min_pool_size": self.min_pool_size,
                }
            ) from e
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            record_operation("engine.initialize", duration_ms, success=False)
            contextual_logger.critical(
                "RuntimeEngine initialization failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True
            )
            # Maintain backward compatibility: InitializationError is a RuntimeError
            raise InitializationError(
                f"RuntimeEngine initialization failed: {e}",
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                context={
                    "error_type": type(e).__name__,
                }
            ) from e
    
    @property
    def mongo_client(self) -> AsyncIOMotorClient:
        """
        Get the MongoDB client.
        
        Returns:
            AsyncIOMotorClient instance
            
        Raises:
            RuntimeError: If engine is not initialized
        """
        if not self._initialized:
            raise RuntimeError(
                "RuntimeEngine not initialized. Call initialize() first.",
            )
        assert self._mongo_client is not None, "MongoDB client should not be None after initialization"
        return self._mongo_client
    
    @property
    def mongo_db(self) -> AsyncIOMotorDatabase:
        """
        Get the MongoDB database.
        
        Returns:
            AsyncIOMotorDatabase instance
            
        Raises:
            RuntimeError: If engine is not initialized
        """
        if not self._initialized:
            raise RuntimeError(
                "RuntimeEngine not initialized. Call initialize() first.",
            )
        assert self._mongo_db is not None, "MongoDB database should not be None after initialization"
        return self._mongo_db
    
    def get_scoped_db(
        self,
        app_slug: str,
        read_scopes: Optional[List[str]] = None,
        write_scope: Optional[str] = None,
        auto_index: bool = True
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
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        if read_scopes is None:
            read_scopes = [app_slug]
        if write_scope is None:
            write_scope = app_slug
        
        return ScopedMongoWrapper(
            real_db=self._mongo_db,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index
        )
    
    async def validate_manifest(self, manifest: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[List[str]]]:
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
        import time
        start_time = time.time()
        slug = manifest.get("slug", "unknown")
        
        try:
            result = self.manifest_validator.validate(manifest)
            duration_ms = (time.time() - start_time) * 1000
            is_valid = result[0]
            record_operation(
                "engine.validate_manifest",
                duration_ms,
                success=is_valid,
                experiment_slug=slug
            )
            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            record_operation(
                "engine.validate_manifest",
                duration_ms,
                success=False,
                experiment_slug=slug
            )
            raise
    
    async def load_manifest(self, path: Path) -> Dict[str, Any]:
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
        return await self.manifest_parser.load_from_file(path, validate=True)
    
    async def register_app(
        self,
        manifest: Dict[str, Any],
        create_indexes: bool = True
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
        import time
        start_time = time.time()
        
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        slug: Optional[str] = manifest.get("slug")
        if not slug:
            contextual_logger.error(
                "Cannot register app: missing 'slug' in manifest",
                extra={"operation": "register_app"}
            )
            return False
        
        # Set app context for logging
        set_app_context(app_slug=slug)
        
        try:
            # Validate manifest
            is_valid, error, paths = await self.validate_manifest(manifest)
            if not is_valid:
                error_path_str = f" (errors in: {', '.join(paths[:3])})" if paths else ""
                duration_ms = (time.time() - start_time) * 1000
                record_operation("engine.register_experiment", duration_ms, success=False, experiment_slug=slug)
                contextual_logger.error(
                    "Experiment registration blocked: Manifest validation failed",
                    extra={
                        "experiment_slug": slug,
                        "validation_error": error,
                        "error_paths": paths,
                        "duration_ms": round(duration_ms, 2),
                    }
                )
                return False
            
            # Store app config
            self._apps[slug] = manifest
            
            # Create indexes if requested
            if create_indexes and "managed_indexes" in manifest:
                await self._create_app_indexes(slug, manifest)
            
            duration_ms = (time.time() - start_time) * 1000
            record_operation("engine.register_app", duration_ms, success=True, app_slug=slug)
            contextual_logger.info(
                "App registered successfully",
                extra={
                    "app_slug": slug,
                    "create_indexes": create_indexes,
                    "duration_ms": round(duration_ms, 2),
                }
            )
            return True
        finally:
            clear_app_context()
    
    async def _create_app_indexes(
        self,
        slug: str,
        manifest: Dict[str, Any]
    ) -> None:
        """
        Create managed indexes for an app.
        
        Args:
            slug: App slug
            manifest: App manifest
        """
        try:
            from ..indexes import validate_managed_indexes
        except ImportError:
            from mdb_runtime.indexes import validate_managed_indexes
        
        managed_indexes = manifest.get("managed_indexes", {})
        if not managed_indexes:
            return
        
        # Validate indexes
        is_valid, error = validate_managed_indexes(managed_indexes)
        if not is_valid:
            logger.warning(
                f"[{slug}] ⚠️ Invalid 'managed_indexes' configuration: {error}. "
                f"Skipping index creation."
            )
            return
        
        # Create indexes for each collection
        for collection_base_name, indexes in managed_indexes.items():
            if not collection_base_name or not isinstance(indexes, list):
                logger.warning(f"[{slug}] Invalid 'managed_indexes' for '{collection_base_name}'.")
                continue
            
            prefixed_collection_name = f"{slug}_{collection_base_name}"
            prefixed_defs = []
            
            for idx_def in indexes:
                idx_n = idx_def.get("name")
                if not idx_n or not idx_def.get("type"):
                    logger.warning(f"[{slug}] Skipping malformed index def in '{collection_base_name}'.")
                    continue
                
                idx_copy = idx_def.copy()
                idx_copy["name"] = f"{slug}_{idx_n}"
                prefixed_defs.append(idx_copy)
            
            if not prefixed_defs:
                continue
            
            logger.info(f"[{slug}] Creating indexes for '{prefixed_collection_name}'...")
            try:
                await run_index_creation_for_collection(
                    db=self._mongo_db,
                    slug=slug,
                    collection_name=prefixed_collection_name,
                    index_definitions=prefixed_defs
                )
            except Exception as e:
                logger.error(
                    f"[{slug}] Error creating indexes for '{prefixed_collection_name}': {e}",
                    exc_info=True
                )
    
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
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        logger.info("Reloading active apps from database...")
        
        try:
            # Fetch active apps
            active_cfgs = await self._mongo_db.apps_config.find(
                {"status": "active"}
            ).limit(500).to_list(None)
            
            logger.info(f"Found {len(active_cfgs)} active app(s).")
            
            # Clear existing registrations
            self._apps.clear()
            
            # Register each app
            registered_count = 0
            for cfg in active_cfgs:
                success = await self.register_app(cfg, create_indexes=True)
                if success:
                    registered_count += 1
            
            logger.info(f"✔️ App reload complete. {registered_count} app(s) registered.")
            return registered_count
        except Exception as e:
            logger.error(f"❌ Error reloading apps: {e}", exc_info=True)
            return 0
    
    def get_app(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get app configuration by slug.
        
        Args:
            slug: App slug
        
        Returns:
            App manifest dict or None if not found
        """
        return self._apps.get(slug)
    
    def list_apps(self) -> List[str]:
        """
        List all registered app slugs.
        
        Returns:
            List of app slugs
        """
        return list(self._apps.keys())
    
    async def shutdown(self) -> None:
        """
        Shutdown the runtime engine and clean up resources.
        
        This method:
        1. Closes MongoDB connections
        2. Clears app registrations
        3. Resets initialization state
        
        This method is idempotent - it's safe to call multiple times.
        """
        import time
        start_time = time.time()
        
        if not self._initialized:
            return
        
        contextual_logger.info("Shutting down RuntimeEngine...")
        
        # Close MongoDB connection
        if self._mongo_client:
            self._mongo_client.close()
            contextual_logger.info("MongoDB connection closed.")
        
        self._initialized = False
        app_count = len(self._apps)
        self._apps.clear()
        
        duration_ms = (time.time() - start_time) * 1000
        record_operation("engine.shutdown", duration_ms, success=True)
        contextual_logger.info(
            "RuntimeEngine shutdown complete",
            extra={
                "app_count": app_count,
                "duration_ms": round(duration_ms, 2),
            }
        )
    
    def __enter__(self) -> "RuntimeEngine":
        """
        Context manager entry (synchronous).
        
        Note: This is synchronous and does not initialize the engine.
        For async initialization, use async context manager (async with).
        
        Returns:
            RuntimeEngine instance
        """
        return self
    
    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any]
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
    
    async def __aenter__(self) -> "RuntimeEngine":
        """
        Async context manager entry.
        
        Automatically initializes the engine when entering the context.
        
        Returns:
            Initialized RuntimeEngine instance
        """
        await self.initialize()
        return self
    
    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any]
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
        Get health status of the runtime engine.
        
        Returns:
            Dictionary with health status and component checks
        """
        health_checker = HealthChecker()
        
        # Register health checks
        health_checker.register_check(
            lambda: check_engine_health(self)
        )
        health_checker.register_check(
            lambda: check_mongodb_health(self._mongo_client)
        )
        
        # Add pool health check if available
        try:
            from ..database.connection import get_pool_metrics
            health_checker.register_check(
                lambda: check_pool_health(get_pool_metrics)
            )
        except ImportError:
            pass
        
        return await health_checker.check_all()
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get metrics for the runtime engine.
        
        Returns:
            Dictionary with operation metrics
        """
        from ..observability import get_metrics_collector
        collector = get_metrics_collector()
        return collector.get_summary()

