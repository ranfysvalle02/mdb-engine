"""
Service initialization for MongoDB Engine.

This module handles initialization of optional services:
- Memory service (Mem0)
- WebSocket endpoints
- Observability (health checks, metrics, logging)
- Data seeding

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from pymongo.errors import (ConnectionFailure, OperationFailure,
                            ServerSelectionTimeoutError)

from ..database import ScopedMongoWrapper
from ..observability import get_logger as get_contextual_logger

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)


class ServiceInitializer:
    """
    Manages initialization of optional services for apps.

    Handles memory service, WebSocket endpoints, observability, and data seeding.
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        get_scoped_db_fn: Callable[[str], ScopedMongoWrapper],
    ) -> None:
        """
        Initialize the service initializer.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            get_scoped_db_fn: Function to get scoped database wrapper
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.get_scoped_db_fn = get_scoped_db_fn
        self._memory_services: Dict[str, Any] = {}
        self._websocket_configs: Dict[str, Dict[str, Any]] = {}

    async def initialize_memory_service(
        self, slug: str, memory_config: Dict[str, Any]
    ) -> None:
        """
        Initialize Mem0 memory service for an app.

        Memory support is OPTIONAL - only processes if dependencies are available.
        mem0 handles embeddings and LLM via environment variables (.env).

        Args:
            slug: App slug
            memory_config: Memory configuration from manifest (already validated)
        """
        # Try to import Memory service (optional dependency)
        try:
            from ..memory import Mem0MemoryService, Mem0MemoryServiceError
        except ImportError as e:
            contextual_logger.warning(
                f"Memory configuration found for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"Memory support will be disabled for this app. Install with: "
                f"pip install mem0ai"
            )
            return

        contextual_logger.info(
            f"Initializing Mem0 memory service for app '{slug}'",
            extra={
                "app_slug": slug,
                "collection_name": memory_config.get(
                    "collection_name", f"{slug}_memories"
                ),
                "enable_graph": memory_config.get("enable_graph", False),
                "embedding_model_dims": memory_config.get("embedding_model_dims", 1536),
                "infer": memory_config.get("infer", True),
            },
        )

        try:
            # Extract memory config (exclude 'enabled')
            service_config = {
                k: v
                for k, v in memory_config.items()
                if k != "enabled"
                and k
                in [
                    "collection_name",
                    "embedding_model_dims",
                    "enable_graph",
                    "infer",
                    "async_mode",
                    "embedding_model",
                    "chat_model",
                    "temperature",
                ]
            }

            # Set default collection name if not provided
            if "collection_name" not in service_config:
                service_config["collection_name"] = f"{slug}_memories"
            else:
                # Ensure collection name is prefixed with app slug
                collection_name = service_config["collection_name"]
                if not collection_name.startswith(f"{slug}_"):
                    service_config["collection_name"] = f"{slug}_{collection_name}"
                    contextual_logger.info(
                        f"Prefixed memory collection name: "
                        f"'{collection_name}' -> "
                        f"'{service_config['collection_name']}'",
                        extra={
                            "app_slug": slug,
                            "original": collection_name,
                            "prefixed": service_config["collection_name"],
                        },
                    )

            # Create Memory service with MongoDB integration
            memory_service = Mem0MemoryService(
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                app_slug=slug,
                config=service_config,
            )
            self._memory_services[slug] = memory_service

            contextual_logger.info(
                f"Mem0 memory service initialized for app '{slug}'",
                extra={"app_slug": slug},
            )
        except Mem0MemoryServiceError as e:
            contextual_logger.error(
                f"Failed to initialize memory service for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )
        except (ImportError, AttributeError, TypeError, ValueError) as e:
            contextual_logger.error(
                f"Error initializing memory service for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )

    async def register_websockets(
        self, slug: str, websockets_config: Dict[str, Any]
    ) -> None:
        """
        Register WebSocket endpoints for an app.

        WebSocket support is OPTIONAL - only processes if dependencies are available.

        Args:
            slug: App slug
            websockets_config: WebSocket configuration from manifest
        """
        # Try to import WebSocket support (optional dependency)
        try:
            from ..routing.websockets import get_websocket_manager
        except ImportError as e:
            contextual_logger.warning(
                f"WebSocket configuration found for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"WebSocket support will be disabled for this app. "
                f"Install FastAPI with WebSocket support."
            )
            return

        contextual_logger.info(
            f"Registering WebSocket endpoints for app '{slug}'",
            extra={"app_slug": slug, "endpoint_count": len(websockets_config)},
        )

        # Store WebSocket configuration for later route registration
        self._websocket_configs[slug] = websockets_config

        # Pre-initialize WebSocket managers
        for endpoint_name, endpoint_config in websockets_config.items():
            path = endpoint_config.get("path", f"/{endpoint_name}")
            try:
                await get_websocket_manager(slug)
            except (ImportError, AttributeError, RuntimeError) as e:
                contextual_logger.warning(
                    f"Could not initialize WebSocket manager for {slug}: {e}"
                )
                continue
            contextual_logger.debug(
                f"Configured WebSocket endpoint '{endpoint_name}' at path '{path}'",
                extra={"app_slug": slug, "endpoint": endpoint_name, "path": path},
            )

    async def seed_initial_data(
        self, slug: str, initial_data: Dict[str, List[Dict[str, Any]]]
    ) -> None:
        """
        Seed initial data into collections for an app.

        Args:
            slug: App slug
            initial_data: Dictionary mapping collection names to arrays of documents
        """
        try:
            from .seeding import seed_initial_data

            db = self.get_scoped_db_fn(slug)
            results = await seed_initial_data(db, slug, initial_data)

            total_inserted = sum(results.values())
            if total_inserted > 0:
                contextual_logger.info(
                    f"Seeded initial data for app '{slug}'",
                    extra={
                        "app_slug": slug,
                        "collections_seeded": len(
                            [c for c, count in results.items() if count > 0]
                        ),
                        "total_documents": total_inserted,
                    },
                )
            else:
                contextual_logger.debug(
                    f"No initial data seeded for app '{slug}' "
                    f"(collections already had data or were empty)",
                    extra={"app_slug": slug},
                )
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            contextual_logger.error(
                f"Failed to seed initial data for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )

    async def setup_observability(
        self, slug: str, manifest: Dict[str, Any], observability_config: Dict[str, Any]
    ) -> None:
        """
        Set up observability features (health checks, metrics, logging) from manifest.

        Args:
            slug: App slug
            manifest: Full manifest dictionary
            observability_config: Observability configuration from manifest
        """
        try:
            # Set up health checks
            health_config = observability_config.get("health_checks", {})
            if health_config.get("enabled", True):
                endpoint = health_config.get("endpoint", "/health")
                contextual_logger.info(
                    f"Health checks configured for {slug}",
                    extra={
                        "endpoint": endpoint,
                        "interval_seconds": health_config.get("interval_seconds", 30),
                    },
                )

            # Set up metrics
            metrics_config = observability_config.get("metrics", {})
            if metrics_config.get("enabled", True):
                contextual_logger.info(
                    f"Metrics collection configured for {slug}",
                    extra={
                        "operation_metrics": metrics_config.get(
                            "collect_operation_metrics", True
                        ),
                        "performance_metrics": metrics_config.get(
                            "collect_performance_metrics", True
                        ),
                        "custom_metrics": metrics_config.get("custom_metrics", []),
                    },
                )

            # Set up logging
            logging_config = observability_config.get("logging", {})
            if logging_config:
                log_level = logging_config.get("level", "INFO")
                log_format = logging_config.get("format", "json")
                contextual_logger.info(
                    f"Logging configured for {slug}",
                    extra={
                        "level": log_level,
                        "format": log_format,
                        "include_request_id": logging_config.get(
                            "include_request_id", True
                        ),
                    },
                )

        except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
            contextual_logger.warning(
                f"Could not set up observability for {slug}: {e}", exc_info=True
            )

    def get_websocket_config(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get WebSocket configuration for an app.

        Args:
            slug: App slug

        Returns:
            WebSocket configuration dict or None if not configured
        """
        return self._websocket_configs.get(slug)

    def get_memory_service(self, slug: str) -> Optional[Any]:
        """
        Get Mem0 memory service for an app.

        Args:
            slug: App slug

        Returns:
            Mem0MemoryService instance if memory is enabled for this app, None otherwise
        """
        try:
            service = self._memory_services.get(slug)
            if service is not None:
                # Quick health check - ensure it has the memory attribute
                if not hasattr(service, "memory"):
                    contextual_logger.warning(
                        f"Memory service for '{slug}' is missing 'memory' "
                        f"attribute, returning None",
                        extra={"app_slug": slug},
                    )
                    return None
            return service
        except (KeyError, AttributeError, TypeError) as e:
            contextual_logger.error(
                f"Error retrieving memory service for '{slug}': {e}",
                exc_info=True,
                extra={"app_slug": slug, "error": str(e)},
            )
            return None

    def clear_services(self) -> None:
        """Clear all service state."""
        self._memory_services.clear()
        self._websocket_configs.clear()
