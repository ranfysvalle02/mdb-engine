"""
App registration management for MongoDB Engine.

This module handles app registration, manifest validation, persistence,
and app state management.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from jsonschema import SchemaError
from jsonschema import ValidationError as JsonSchemaValidationError
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import (
    ConnectionFailure,
    InvalidOperation,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from ..observability import clear_app_context, record_operation, set_app_context
from ..observability import get_logger as get_contextual_logger
from .manifest import ManifestParser, ManifestValidator

if TYPE_CHECKING:
    from .types import ManifestDict

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)


class AppRegistrationManager:
    """
    Manages app registration and manifest handling.

    Handles manifest validation, app state management, and persistence.
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        manifest_validator: ManifestValidator,
        manifest_parser: ManifestParser,
    ) -> None:
        """
        Initialize the app registration manager.

        Args:
            mongo_db: MongoDB database instance
            manifest_validator: Manifest validator instance
            manifest_parser: Manifest parser instance
        """
        self._mongo_db = mongo_db
        self.manifest_validator = manifest_validator
        self.manifest_parser = manifest_parser
        self._apps: dict[str, dict[str, Any]] = {}

    async def validate_manifest(self, manifest: "ManifestDict") -> tuple[bool, str | None, list[str] | None]:
        """
        Validate a manifest against the schema.

        Args:
            manifest: Manifest dictionary to validate

        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        start_time = time.time()
        slug = manifest.get("slug", "unknown")

        try:
            result = await self.manifest_validator.validate(manifest)
            duration_ms = (time.time() - start_time) * 1000
            is_valid = result[0]
            record_operation(
                "app_registration.validate_manifest",
                duration_ms,
                success=is_valid,
                experiment_slug=slug,
            )
            return result
        except (JsonSchemaValidationError, SchemaError, ValueError, TypeError, KeyError):
            duration_ms = (time.time() - start_time) * 1000
            record_operation(
                "app_registration.validate_manifest",
                duration_ms,
                success=False,
                experiment_slug=slug,
            )
            raise

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
        return await self.manifest_parser.load_from_file(path, validate=True)

    async def register_app(
        self,
        manifest: "ManifestDict",
        create_indexes_callback: Callable[[str, "ManifestDict"], Any] | None = None,
        seed_data_callback: Callable[[str, dict[str, list[dict[str, Any]]]], Any] | None = None,
        initialize_osi_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        ensure_shared_services_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        initialize_graph_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        initialize_memory_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        initialize_profile_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        register_websockets_callback: Callable[[str, dict[str, Any]], Any] | None = None,
        setup_observability_callback: Callable[[str, "ManifestDict", dict[str, Any]], Any] | None = None,
    ) -> bool:
        """
        Register an app from its manifest.

        This method validates the manifest, stores the app configuration,
        and optionally creates managed indexes defined in the manifest.

        Args:
            manifest: Validated manifest dictionary containing app configuration
            create_indexes_callback: Optional callback to create indexes
            seed_data_callback: Optional callback to seed initial data
            initialize_osi_callback: Optional callback to initialize OSI registry
                (MUST run before graph callback)
            initialize_graph_callback: Optional callback to initialize graph service
            initialize_memory_callback: Optional callback to initialize memory service
            initialize_profile_callback: Optional callback to initialize profile service
                (MUST run after memory and graph)
            register_websockets_callback: Optional callback to register WebSockets
            setup_observability_callback: Optional callback to setup observability

        Returns:
            True if registration successful, False otherwise
        """
        start_time = time.time()

        slug: str | None = manifest.get("slug")
        if not slug:
            contextual_logger.error(
                "Cannot register app: missing 'slug' in manifest",
                extra={"operation": "register_app"},
            )
            return False

        # Set app context for logging
        set_app_context(app_slug=slug)

        try:
            # Normalize manifest: convert Python tuples to lists for JSON schema compatibility
            from .manifest import _convert_tuples_to_lists

            normalized_manifest = _convert_tuples_to_lists(manifest)

            # Validate manifest
            is_valid, error, paths = await self.validate_manifest(normalized_manifest)
            if not is_valid:
                duration_ms = (time.time() - start_time) * 1000
                logger.error(
                    f"[{slug}] Manifest validation FAILED: {error}. "
                    f"Error paths: {paths}. "
                    f"This blocks app registration and index creation!"
                )
                record_operation(
                    "app_registration.register_app",
                    duration_ms,
                    success=False,
                    experiment_slug=slug,
                )
                contextual_logger.error(
                    "App registration blocked: Manifest validation failed",
                    extra={
                        "experiment_slug": slug,
                        "validation_error": error,
                        "error_paths": paths,
                        "duration_ms": round(duration_ms, 2),
                    },
                )
                return False

            # Use normalized manifest for rest of registration
            manifest = normalized_manifest

            # Store app config in memory
            self._apps[slug] = manifest

            # Persist app config to MongoDB
            try:
                await self._mongo_db.apps_config.replace_one(
                    {"slug": slug},
                    manifest,
                    upsert=True,
                )
            except (
                ConnectionFailure,
                ServerSelectionTimeoutError,
                OperationFailure,
            ) as e:
                logger.warning(
                    f"Failed to persist app '{slug}' to MongoDB: {e}",
                    exc_info=True,
                )
                # Continue even if persistence fails - app is still registered in memory
            except InvalidOperation as e:
                logger.debug(f"Cannot persist app '{slug}': MongoDB client is closed: {e}")
                # Continue - app is still registered in memory

            # Invalidate auth config cache for this app
            try:
                from ..auth.integration import invalidate_auth_config_cache

                invalidate_auth_config_cache(slug)
            except (AttributeError, ImportError, RuntimeError) as e:
                logger.debug(f"Could not invalidate auth config cache for {slug}: {e}")

            # Build list of callbacks to run in parallel
            callback_tasks = []

            # Create indexes if requested
            if create_indexes_callback and "managed_indexes" in manifest:
                logger.info(f"[{slug}] Creating managed indexes " f"(has_managed_indexes=True)")
                callback_tasks.append(create_indexes_callback(slug, manifest))

            # Seed initial data if configured
            if seed_data_callback and "initial_data" in manifest:
                callback_tasks.append(seed_data_callback(slug, manifest["initial_data"]))

            # Initialize OSI registry if configured (MUST complete before graph)
            # OSI provides entity types and synonyms that graph extraction uses
            osi_config = manifest.get("osi_config")
            if initialize_osi_callback and osi_config and osi_config.get("enabled", False):
                await initialize_osi_callback(slug, osi_config)

            # Pre-create shared LLM/embedding services so graph and memory
            # share the same instances (and the same named providers).
            if ensure_shared_services_callback:
                await ensure_shared_services_callback(slug, manifest)

            # Initialize Graph service if configured (MUST complete before memory)
            # Graph is enabled by default - users can disable via manifest
            graph_config = manifest.get("graph_config", {})
            if initialize_graph_callback and graph_config.get("enabled", True):
                # Graph must be initialized BEFORE memory (memory uses graph for GraphRAG)
                await initialize_graph_callback(slug, graph_config)

            # Initialize Memory service if configured (after graph so it can use graph service)
            # Supports shorthand: true, "basic"/"smart"/"full", or full dict.
            memory_config = manifest.get("memory_config")
            _mem_enabled = (
                memory_config is True
                or isinstance(memory_config, str)
                or (
                    isinstance(memory_config, dict)
                    and (memory_config.get("enabled", False) or "preset" in memory_config)
                )
            )
            if initialize_memory_callback and _mem_enabled:
                callback_tasks.append(initialize_memory_callback(slug, memory_config))

            # Initialize Profile service if configured (after memory + graph)
            profile_config = manifest.get("profile_config")
            if initialize_profile_callback and profile_config and profile_config.get("enabled", False):
                callback_tasks.append(initialize_profile_callback(slug, profile_config))

            # Register WebSocket endpoints if configured
            websockets_config = manifest.get("websockets")
            if register_websockets_callback and websockets_config:
                callback_tasks.append(register_websockets_callback(slug, websockets_config))

            # Set up observability (health checks, metrics, logging)
            observability_config = manifest.get("observability", {})
            if setup_observability_callback and observability_config:
                callback_tasks.append(setup_observability_callback(slug, manifest, observability_config))

            # Run all callbacks in parallel
            if callback_tasks:
                results = await asyncio.gather(*callback_tasks, return_exceptions=True)
                # Log any exceptions but don't fail registration
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        callback_names = [
                            "create_indexes",
                            "seed_data",
                            "initialize_memory",
                            "register_websockets",
                            "setup_observability",
                        ]
                        callback_name = callback_names[i] if i < len(callback_names) else "unknown"
                        logger.warning(
                            f"[{slug}] Callback '{callback_name}' failed during " f"app registration: {result}",
                            exc_info=result,
                        )

            duration_ms = (time.time() - start_time) * 1000
            record_operation(
                "app_registration.register_app",
                duration_ms,
                success=True,
                app_slug=slug,
            )
            contextual_logger.info(
                "App registered successfully",
                extra={
                    "app_slug": slug,
                    "graph_enabled": bool(graph_config.get("enabled", True)),
                    "memory_enabled": bool(memory_config and memory_config.get("enabled", False)),
                    "websockets_configured": bool(websockets_config),
                    "duration_ms": round(duration_ms, 2),
                },
            )
            return True
        finally:
            clear_app_context()

    async def reload_apps(
        self,
        register_app_callback: Any,
    ) -> int:
        """
        Reload all active apps from the database.

        Args:
            register_app_callback: Callback to register each app

        Returns:
            Number of apps successfully registered, or -1 if reload failed
        """
        logger.info("Reloading active apps from database...")

        try:
            # Fetch active apps
            active_cfgs = await self._mongo_db.apps_config.find({"status": "active"}).limit(500).to_list(None)

            logger.info(f"Found {len(active_cfgs)} active app(s).")

            # Clear existing registrations
            self._apps.clear()

            # Register each app
            registered_count = 0
            for cfg in active_cfgs:
                success = await register_app_callback(cfg, create_indexes=True)
                if success:
                    registered_count += 1

            logger.info(f"App reload complete. {registered_count} app(s) registered.")
            return registered_count
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            logger.error(f"Error reloading apps: {e}", exc_info=True)
            return -1

    def get_app(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app configuration by slug.

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        return self._apps.get(slug)

    async def get_manifest(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app manifest by slug (async alias for get_app).

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        return self._apps.get(slug)

    @property
    def apps(self) -> dict[str, dict[str, Any]]:
        """
        Get all registered apps.

        Returns:
            Dictionary of registered apps
        """
        return self._apps

    def list_apps(self) -> list[str]:
        """
        List all registered app slugs.

        Returns:
            List of app slugs
        """
        return list(self._apps.keys())

    def clear_apps(self) -> None:
        """Clear all registered apps."""
        self._apps.clear()
