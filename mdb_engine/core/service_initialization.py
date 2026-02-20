"""
Service initialization for MongoDB Engine.

This module handles initialization of optional services:
- Graph service (Knowledge Graph with $graphLookup traversal)
- Memory service (Custom MongoDB Atlas Vector Search)
- WebSocket endpoints
- Observability (health checks, metrics, logging)
- Data seeding

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from collections.abc import Callable
from typing import Any

from pymongo.errors import (
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)

from ..observability import get_logger as get_contextual_logger
from .protocols import GraphServiceProtocol, MemoryServiceProtocol, ProceduralMemoryProtocol

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)

try:
    from openai import OpenAIError
except ImportError:

    class OpenAIError(Exception):  # type: ignore[no-redef]
        """Placeholder when openai is not installed — will never match engine errors."""


class ServiceInitializer:
    """Service initializer for MDB-Engine optional services."""

    @staticmethod
    def _build_vector_index_definition(
        filter_paths: list[str],
        embedding_dims: int,
        vector_path: str = "embedding",
        similarity: str = "cosine",
    ) -> dict[str, Any]:
        """Build a vector search index definition with shared fields."""
        fields = [{"type": "filter", "path": path} for path in filter_paths]
        fields.append(
            {
                "type": "vector",
                "path": vector_path,
                "numDimensions": embedding_dims,
                "similarity": similarity,
            }
        )
        return {"fields": fields}

    async def _ensure_memory_vector_index(
        self,
        slug: str,
        collection_name: str,
        index_name: str,
        embedding_dims: int = 1536,
    ) -> None:
        """
        Automatically ensure vector search index exists for memory service.

        This eliminates the need for users to manually define the index in
        managed_indexes - the memory service manages its own index automatically.

        Args:
            slug: App slug
            collection_name: Memory collection name (already prefixed)
            index_name: Vector search index name
            embedding_dims: Embedding dimensions (default: 1536)
        """
        try:
            # Get AsyncIOMotorCollection for index management
            # We need the async collection for AsyncAtlasIndexManager
            # Use the connection manager's mongo_client to get the database
            motor_client = self._connection_manager.mongo_client
            db = motor_client[self.db_name]
            motor_collection = db[collection_name]

            # Import index manager (optional dependency)
            try:
                from ..database.scoped_wrapper import AsyncAtlasIndexManager
            except ImportError:
                contextual_logger.warning(
                    f"Could not import AsyncAtlasIndexManager for automatic index creation. "
                    f"Vector search index '{index_name}' may need to be created manually.",
                    extra={"app_slug": slug, "index_name": index_name},
                )
                return

            index_manager = AsyncAtlasIndexManager(motor_collection)

            # Build vector search index definition
            # Note: app_id MUST be included as a filter when using ScopedCollectionWrapper,
            # as the scoped wrapper automatically injects app_id filtering into all queries.
            # MongoDB Atlas requires all filter fields to be explicitly defined in the index.
            # We also include user_id, is_active, and metadata.associated_bucket_id filters
            # for user-scoped queries, soft-delete filtering, and bucket-aware searches.
            index_definition = self._build_vector_index_definition(
                filter_paths=[
                    "app_id",  # Required for ScopedCollectionWrapper
                    "user_id",
                    "is_active",
                    "metadata.associated_bucket_id",
                    "metadata.timeline_id",  # Required for timeline-aware searches
                    "metadata.confidence",  # Required for confidence filtering
                ],
                embedding_dims=embedding_dims,
            )

            # Check if index already exists
            existing_index = await index_manager.get_search_index(index_name)

            if existing_index:
                # Check if index definition matches (including user_id filter)
                current_def = existing_index.get("latestDefinition", existing_index.get("definition", {}))

                # Normalize definitions for comparison
                try:
                    from ..indexes.helpers import normalize_json_def

                    normalized_current = normalize_json_def(current_def)
                    normalized_expected = normalize_json_def(index_definition)
                    definitions_match = normalized_current == normalized_expected
                except ImportError:
                    # Fallback: simple comparison
                    definitions_match = current_def == index_definition

                if definitions_match:
                    # Index definition matches - check if queryable
                    if existing_index.get("queryable"):
                        contextual_logger.info(
                            f"Vector search index '{index_name}' already exists and " f"is queryable",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        return
                    else:
                        contextual_logger.info(
                            f"Vector search index '{index_name}' exists but not queryable yet. "
                            f"Waiting for it to become ready...",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        await index_manager._wait_for_search_index_ready(  # noqa: SLF001
                            index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                        )
                        contextual_logger.info(
                            f"Vector search index '{index_name}' is now ready",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        return
                elif existing_index.get("status") == "FAILED":
                    contextual_logger.error(
                        f"Vector search index '{index_name}' exists but is in FAILED state. "
                        f"Manual intervention in Atlas UI may be required.",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                else:
                    # Index exists but definition doesn't match (missing required filters)
                    # - update it
                    contextual_logger.warning(
                        f"Vector search index '{index_name}' exists but is missing "
                        f"required filters. Updating index to include app_id, user_id, "
                        f"is_active, metadata.associated_bucket_id, metadata.timeline_id, "
                        f"and metadata.confidence filters (required for vector search queries "
                        f"with ScopedCollectionWrapper)...",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    await index_manager.update_search_index(
                        name=index_name,
                        definition=index_definition,
                        wait_for_ready=True,
                    )
                    contextual_logger.info(
                        f"Successfully updated vector search index '{index_name}' " f"with required filters",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return

            # Index doesn't exist - create it
            contextual_logger.info(
                f"🔨 Automatically creating vector search index '{index_name}' "
                f"for memory collection '{collection_name}' (dimensions: {embedding_dims})",
                extra={
                    "app_slug": slug,
                    "collection_name": collection_name,
                    "index_name": index_name,
                    "embedding_dims": embedding_dims,
                },
            )

            # Create the index
            await index_manager.create_search_index(
                name=index_name,
                definition=index_definition,
                index_type="vectorSearch",
                wait_for_ready=True,
            )

            contextual_logger.info(
                f"Successfully created vector search index '{index_name}' "
                f"for memory collection '{collection_name}'",
                extra={
                    "app_slug": slug,
                    "collection_name": collection_name,
                    "index_name": index_name,
                },
            )

        except (RuntimeError, ValueError, KeyError, AttributeError) as e:
            # Don't fail memory service initialization if index creation fails
            # The index might already exist or there might be permission issues
            contextual_logger.warning(
                f"Could not automatically create vector search index '{index_name}': {e}. "
                f"Memory service will still work, but vector search may fail until the "
                f"index is created. You can create it manually in MongoDB Atlas or add "
                f"it to 'managed_indexes' in manifest.json",
                extra={
                    "app_slug": slug,
                    "index_name": index_name,
                    "error": str(e),
                },
                exc_info=True,
            )

    async def _ensure_memory_ttl_indexes(
        self,
        slug: str,
        collection_name: str,
        episodic_retention_days: int = 730,
        working_ttl_hours: int = 24,
    ) -> None:
        """
        Create TTL indexes for episodic and working memory (Cognitive Blueprint v2.0).

        Args:
            slug: App slug
            collection_name: Memory collection name
            episodic_retention_days: Retention period for episodic memories (default: 730 days)
            working_ttl_hours: TTL for working memory (default: 24 hours)
        """
        try:
            motor_client = self._connection_manager.mongo_client
            db = motor_client[self.db_name]
            motor_collection = db[collection_name]

            # Create TTL index for episodic memories (expires_at field)
            episodic_ttl_seconds = episodic_retention_days * 24 * 3600
            try:
                await motor_collection.create_index(
                    [("expires_at", 1)],
                    name="episodic_ttl_idx",
                    expireAfterSeconds=episodic_ttl_seconds,
                    partialFilterExpression={"memory_type": "episodic"},
                    background=True,
                )
                contextual_logger.info(
                    f"Created TTL index for episodic memory " f"(retention: {episodic_retention_days} days)",
                    extra={"app_slug": slug, "collection_name": collection_name},
                )
            except (PyMongoError, AttributeError, TypeError) as e:
                # Index might already exist
                contextual_logger.debug(f"Episodic TTL index creation: {e}")

            # Note: Working memory TTL is handled by ChatHistoryService separately
            # This is for memory_type="working" in the main collection if used

        except (PyMongoError, AttributeError, TypeError, ValueError) as e:
            contextual_logger.warning(
                f"Failed to create TTL indexes: {e}",
                extra={"app_slug": slug},
            )

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        get_scoped_db_fn: Callable[[str], Any],  # async fn returning ScopedMongoWrapper
        connection_manager: Any | None = None,
    ) -> None:
        """
        Initialize the service initializer.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            get_scoped_db_fn: Function to get scoped database wrapper
            connection_manager: ConnectionManager instance (optional, for getting
                              MDB-Engine connection)
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.get_scoped_db_fn = get_scoped_db_fn
        self._connection_manager = connection_manager
        # Service registries with protocol type hints for type checking
        # Protocol typing allows mocking and swapping implementations
        self._graph_services: dict[str, GraphServiceProtocol] = {}
        self._memory_services: dict[str, MemoryServiceProtocol] = {}
        self._procedural_services: dict[str, ProceduralMemoryProtocol] = {}
        self._profile_services: dict[str, Any] = {}  # str -> ProfileService
        self._websocket_configs: dict[str, dict[str, Any]] = {}
        # OSI registries (one per app)
        self._osi_registries: dict[str, Any] = {}  # str -> OsiModelRegistry

    async def _ensure_graph_vector_index(
        self,
        slug: str,
        collection_name: str,
        index_name: str,
        embedding_dims: int = 1536,
    ) -> None:
        """
        Automatically ensure vector search index exists for graph service.

        Args:
            slug: App slug
            collection_name: Graph collection name (already prefixed)
            index_name: Vector search index name
            embedding_dims: Embedding dimensions (default: 1536)
        """
        try:
            motor_client = self._connection_manager.mongo_client
            db = motor_client[self.db_name]
            motor_collection = db[collection_name]

            try:
                from ..database.scoped_wrapper import AsyncAtlasIndexManager
            except ImportError:
                contextual_logger.warning(
                    f"Could not import AsyncAtlasIndexManager for automatic index creation. "
                    f"Vector search index '{index_name}' may need to be created manually.",
                    extra={"app_slug": slug, "index_name": index_name},
                )
                return

            index_manager = AsyncAtlasIndexManager(motor_collection)

            # Build vector search index definition for graph nodes
            # Include app_id (injected by ScopedCollectionWrapper), app_slug and user_id as filters
            index_definition = self._build_vector_index_definition(
                filter_paths=["app_id", "app_slug", "user_id"],
                embedding_dims=embedding_dims,
            )

            # Check if index already exists
            existing_index = await index_manager.get_search_index(index_name)

            if existing_index:
                if existing_index.get("queryable"):
                    contextual_logger.info(
                        f"Graph vector index '{index_name}' already exists and is queryable",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                elif existing_index.get("status") == "FAILED":
                    contextual_logger.error(
                        f"Graph vector search index '{index_name}' is in FAILED state.",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                else:
                    contextual_logger.info(
                        f"Graph vector search index '{index_name}' exists but not queryable. "
                        f"Waiting for it to become ready...",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    await index_manager._wait_for_search_index_ready(  # noqa: SLF001
                        index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                    )
                    return

            # Create the index
            contextual_logger.info(
                f"🔨 Creating graph vector search index '{index_name}' "
                f"for collection '{collection_name}' (dimensions: {embedding_dims})",
                extra={
                    "app_slug": slug,
                    "collection_name": collection_name,
                    "index_name": index_name,
                },
            )

            await index_manager.create_search_index(
                name=index_name,
                definition=index_definition,
                index_type="vectorSearch",
                wait_for_ready=True,
            )

            contextual_logger.info(
                f"Successfully created graph vector search index '{index_name}'",
                extra={"app_slug": slug, "index_name": index_name},
            )

        except (RuntimeError, ValueError, KeyError, AttributeError) as e:
            contextual_logger.warning(
                f"Could not create graph vector search index '{index_name}': {e}. "
                f"Hybrid search may not work until the index is created.",
                extra={"app_slug": slug, "index_name": index_name, "error": str(e)},
            )

    async def initialize_osi_service(
        self,
        slug: str,
        osi_config: dict[str, Any] | None,
    ) -> None:
        """Initialize OSI registry for an app.

        Called BEFORE graph service initialization so the graph service
        can use OSI context for extraction prompts and entity resolution.

        If a connection manager is available, creates a MongoDB-backed store
        (``{slug}_osi_models`` collection) for persistence. Otherwise falls
        back to in-memory only.

        Args:
            slug: App slug
            osi_config: OSI configuration from manifest (``osi_config`` section).
                Can be None or empty dict to skip initialization.
        """
        if not osi_config or not osi_config.get("enabled", False):
            return

        try:
            from ..osi.registry import OsiModelRegistry

            # Try to create a MongoDB-backed store for persistence
            store = None
            if self._connection_manager and self._connection_manager.initialized:
                try:
                    from ..osi.store import OsiModelStore

                    motor_client = self._connection_manager.mongo_client
                    db = motor_client[self.db_name]
                    collection_name = f"{slug}_osi_models"
                    osi_collection = db[collection_name]
                    store = OsiModelStore(collection=osi_collection, app_slug=slug)
                    contextual_logger.info(
                        f"OSI store created: collection '{collection_name}'",
                        extra={"app_slug": slug, "collection_name": collection_name},
                    )
                except (ImportError, AttributeError, RuntimeError) as e:
                    contextual_logger.warning(
                        f"Could not create OSI store for '{slug}': {e}. " f"Using in-memory registry only.",
                        extra={"app_slug": slug, "error": str(e)},
                    )

            registry = OsiModelRegistry(app_slug=slug, config=osi_config, store=store)
            await registry.load()

            # Auto-scaffold if OSI is enabled but no YAML models were found.
            # Writes YAML to disk so it becomes the persistent source of truth.
            if not registry.models:
                try:
                    from pathlib import Path

                    from ..osi.scaffold import scaffold_osi_model, scaffold_to_yaml

                    scaffold_node_types = osi_config.get("_graph_node_types", [])
                    scaffold_categories = osi_config.get("_memory_categories", [])
                    if scaffold_node_types:
                        scaffolded = scaffold_osi_model(
                            app_slug=slug,
                            node_types=scaffold_node_types,
                            categories=scaffold_categories or None,
                        )

                        # Write YAML to disk so it persists as the source of truth.
                        # Only write when models_path is an absolute path (already
                        # resolved by the caller relative to the manifest directory).
                        # A relative or missing models_path means we don't have a
                        # reliable anchor directory — skip the disk write to avoid
                        # polluting the working directory.
                        models_path = osi_config.get("models_path")
                        if models_path and Path(models_path).is_absolute():
                            try:
                                yaml_str = scaffold_to_yaml(
                                    app_slug=slug,
                                    node_types=scaffold_node_types,
                                    categories=scaffold_categories or None,
                                )
                                output_dir = Path(models_path)
                                output_dir.mkdir(parents=True, exist_ok=True)
                                output_file = output_dir / f"{slug}_knowledge.yaml"
                                output_file.write_text(yaml_str, encoding="utf-8")
                                contextual_logger.info(
                                    f"Auto-scaffolded YAML written to {output_file}",
                                    extra={"app_slug": slug, "file": str(output_file)},
                                )
                            except (ImportError, OSError) as write_err:
                                contextual_logger.warning(
                                    f"Could not write scaffolded YAML to disk: {write_err}. "
                                    f"Using in-memory scaffold only.",
                                    extra={"app_slug": slug, "error": str(write_err)},
                                )
                        else:
                            contextual_logger.debug(
                                "Skipping scaffold YAML disk write: no absolute "
                                "models_path resolved (in-memory only).",
                                extra={"app_slug": slug},
                            )

                        await registry.add_model(scaffolded, origin="auto_scaffold", status="provisional")
                        contextual_logger.info(
                            f"Auto-scaffolded OSI model for '{slug}' from "
                            f"{len(scaffold_node_types)} node types "
                            f"(no YAML files found, generated starter model)",
                            extra={"app_slug": slug, "node_types": scaffold_node_types},
                        )
                    else:
                        contextual_logger.warning(
                            f"OSI enabled for '{slug}' but no models loaded and no "
                            f"graph_config.node_types available for auto-scaffolding",
                            extra={"app_slug": slug},
                        )
                except (ImportError, RuntimeError, TypeError) as e:
                    contextual_logger.warning(
                        f"Auto-scaffold failed for '{slug}': {e}",
                        extra={"app_slug": slug, "error": str(e)},
                    )

            self._osi_registries[slug] = registry
            contextual_logger.info(
                f"OSI registry initialized for '{slug}': "
                f"{len(registry.models)} model(s) loaded"
                f"{' (MongoDB-backed)' if store else ' (in-memory)'}",
                extra={"app_slug": slug, "model_count": len(registry.models)},
            )
        except ImportError as e:
            contextual_logger.warning(
                f"OSI configuration found for app '{slug}' but dependencies "
                f"are not available: {e}. OSI support will be disabled.",
                extra={"app_slug": slug, "error": str(e)},
            )
        except (ValueError, TypeError, RuntimeError) as e:
            contextual_logger.exception(
                f"Failed to initialize OSI registry for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
            )

    def get_osi_registry(self, slug: str) -> Any | None:
        """Get OSI model registry for an app.

        Args:
            slug: App slug

        Returns:
            OsiModelRegistry instance if OSI is enabled for this app, None otherwise.
        """
        return self._osi_registries.get(slug)

    async def initialize_graph_service(
        self,
        slug: str,
        graph_config: dict[str, Any] | None,
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize graph service for an app.

        Graph service is OPTIONAL - only processes if dependencies are available.

        Args:
            slug: App slug
            graph_config: Graph configuration from manifest (already validated).
                Can be None or empty dict to skip initialization.
            llm_config: Optional LLM configuration from manifest. If provided, services
                will use the LLM service's default_model instead of hardcoded defaults.
        """
        # Handle None or empty config
        if not graph_config:
            return

        # Check if graph is enabled (enabled by default)
        if not graph_config.get("enabled", True):
            return

        # Try to import Graph service factory (optional dependency)
        try:
            from ..graph.service import get_graph_service
        except ImportError as e:
            contextual_logger.warning(
                f"Graph configuration found for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"Graph support will be disabled for this app.",
                extra={"app_slug": slug, "error": str(e)},
            )
            return

        contextual_logger.info(
            f"Initializing graph service for app '{slug}'",
            extra={
                "app_slug": slug,
                "collection_name": graph_config.get("collection_name", "kg"),
                "auto_extract": graph_config.get("auto_extract", True),
            },
        )

        try:
            # Use Motor async collections directly - forward-facing DI
            if not self._connection_manager or not self._connection_manager.initialized:
                contextual_logger.error(
                    "Connection manager not available or not initialized. "
                    "Graph service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug},
                )
                return

            try:
                # Use MDB Engine's scoped collections for proper app_id filtering and scoping
                scoped_db = await self.get_scoped_db_fn(slug)
                base_collection_name = graph_config.get("collection_name", "kg")
                # Normalize legacy "__kg" to "kg" (private attributes are blocked by ScopedMongoWrapper)
                if base_collection_name == "__kg":
                    base_collection_name = "kg"
                # Get prefixed collection name for index management (indexes need full name)
                if base_collection_name.startswith(f"{slug}_"):
                    prefixed_collection_name = base_collection_name
                    base_collection_name = base_collection_name[len(f"{slug}_") :]
                else:
                    prefixed_collection_name = f"{slug}_{base_collection_name}"

                # Get scoped collection (base name - scoped wrapper handles prefixing)
                collection = getattr(scoped_db, base_collection_name)  # ScopedCollectionWrapper

                contextual_logger.info(
                    f"Using MDB-Engine scoped collection for graph service: "
                    f"{base_collection_name} (app: {slug}, prefixed: {prefixed_collection_name})",
                    extra={
                        "app_slug": slug,
                        "collection_name": base_collection_name,
                        "prefixed_name": prefixed_collection_name,
                    },
                )

                # Automatically ensure vector search index exists (use prefixed name for index ops)
                index_name = graph_config.get("vector_index_name", f"{prefixed_collection_name}_vector_index")
                embedding_dims = graph_config.get("embedding_dims", 1536)
                await self._ensure_graph_vector_index(
                    slug=slug,
                    collection_name=prefixed_collection_name,
                    index_name=index_name,
                    embedding_dims=embedding_dims,
                )

            except (AttributeError, RuntimeError, KeyError) as e:
                contextual_logger.exception(
                    f"Could not get collection from MDB-Engine connection manager: {e}",
                    extra={"app_slug": slug, "error": str(e)},
                )
                return

            # Try to get LLM and Embedding services (optional for graph service)
            llm_service = None
            embedding_service = None

            try:
                from ..llm.service import get_llm_service

                # Use graph_config's llm_config/llm_model if provided,
                # otherwise fall back to app's llm_config
                graph_llm_config = graph_config.get("llm_config", {})
                if graph_config.get("llm_model"):
                    graph_llm_config["default_model"] = graph_config["llm_model"]
                elif llm_config and not graph_llm_config:
                    # Inherit from app's LLM service configuration if not
                    # explicitly set in graph_config
                    graph_llm_config = llm_config.copy()
                    model = llm_config.get("default_model")
                    contextual_logger.info(
                        f"Graph service inheriting LLM model from app's " f"llm_config: {model}",
                        extra={"app_slug": slug},
                    )
                llm_service = get_llm_service(config=graph_llm_config if graph_llm_config else None)
            except (ImportError, RuntimeError, ValueError) as e:
                contextual_logger.warning(
                    f"LLM service not available for graph extraction: {e}",
                    extra={"app_slug": slug},
                )

            try:
                from ..embeddings.service import get_embedding_service

                embedding_config = graph_config.get("embedding_config", {})
                embedding_service = get_embedding_service(config=embedding_config if embedding_config else None)
            except (ImportError, RuntimeError, ValueError) as e:
                contextual_logger.warning(
                    f"Embedding service not available for graph hybrid search: {e}",
                    extra={"app_slug": slug},
                )

            # Update config with collection name for the service
            service_config = graph_config.copy()
            service_config["collection_name"] = prefixed_collection_name
            service_config["vector_index_name"] = index_name

            # Inject OSI registry if available (initialized before graph)
            osi_registry = self._osi_registries.get(slug)
            if osi_registry:
                service_config["_osi_registry"] = osi_registry
                contextual_logger.info(
                    f"Graph service for '{slug}' will use OSI registry",
                    extra={"app_slug": slug},
                )

            # Get the default model from LLM service to pass to GraphStore
            # This must be done AFTER service_config is created
            if llm_service and "llm_model" not in service_config:
                # Try to get "chat" provider first, fallback to first available provider
                chat_provider = llm_service.providers.get("chat")
                if chat_provider:
                    default_llm_model = chat_provider.default_model
                elif llm_service.providers:
                    # Use first available provider
                    first_provider = next(iter(llm_service.providers.values()))
                    default_llm_model = first_provider.default_model
                else:
                    default_llm_model = None

                if default_llm_model:
                    service_config["llm_model"] = default_llm_model
                    contextual_logger.info(
                        f"Graph service using LLM model: {default_llm_model}",
                        extra={"app_slug": slug, "llm_model": default_llm_model},
                    )

            # Create Graph service using factory function
            graph_service = get_graph_service(
                app_slug=slug,
                collection=collection,
                config=service_config,
                llm_service=llm_service,
                embedding_service=embedding_service,
            )
            self._graph_services[slug] = graph_service

            contextual_logger.info(
                f"Graph service initialized for app '{slug}'",
                extra={
                    "app_slug": slug,
                    "collection_name": prefixed_collection_name,
                    "llm_available": llm_service is not None,
                    "embedding_available": embedding_service is not None,
                },
            )

            # Cross-validate: warn if node_types are out of sync with OSI datasets
            osi_registry = self._osi_registries.get(slug)
            if osi_registry and osi_registry.loaded:
                osi_dataset_names = set(osi_registry.get_node_types())
                manifest_node_types = set(t.lower() for t in graph_config.get("node_types", []))
                in_manifest_not_osi = manifest_node_types - osi_dataset_names
                if in_manifest_not_osi:
                    contextual_logger.warning(
                        f"Node types in graph_config but not in OSI datasets: "
                        f"{sorted(in_manifest_not_osi)}. Entity resolution won't "
                        f"work for these types. Add matching datasets to your "
                        f"semantic_models/ YAML or let auto-scaffold generate them.",
                        extra={
                            "app_slug": slug,
                            "unmatched_types": sorted(in_manifest_not_osi),
                        },
                    )
                in_osi_not_manifest = osi_dataset_names - manifest_node_types
                if in_osi_not_manifest:
                    contextual_logger.info(
                        f"OSI datasets not in graph_config.node_types: "
                        f"{sorted(in_osi_not_manifest)} (automatically merged)",
                        extra={
                            "app_slug": slug,
                            "merged_types": sorted(in_osi_not_manifest),
                        },
                    )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            # Remove graph service from dict if it was partially initialized
            if slug in self._graph_services:
                del self._graph_services[slug]
            contextual_logger.error(
                f"Failed to initialize graph service for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )

    async def initialize_memory_service(
        self,
        slug: str,
        memory_config: dict[str, Any] | None,
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize memory service for an app (defaults to Custom implementation).

        Memory support is OPTIONAL - only processes if dependencies are available.
        Uses native MongoDB Atlas Vector Search with mdb_engine.embeddings for embeddings
        and OpenAI SDK directly for LLM fact extraction.

        Args:
            slug: App slug
            memory_config: Memory configuration from manifest (already validated).
                Can be None or empty dict to skip initialization.
            llm_config: Optional LLM configuration from manifest. If provided, services
                will use the LLM service's default_model instead of hardcoded defaults.
        """
        # Handle None or empty config
        if not memory_config:
            return

        # Check if memory is enabled (must be checked before import)
        if not memory_config.get("enabled", False):
            return

        # Try to import Memory service factory (optional dependency)
        try:
            from ..memory.cognitive import CognitiveMemoryServiceError
            from ..memory.service import get_memory_service
        except ImportError as e:
            contextual_logger.warning(
                f"Memory configuration found for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"Memory support will be disabled for this app. Install with: "
                f"pip install pymongo openai"
            )
            return

        # Get provider (default to "cognitive" - THE memory service)
        provider = memory_config.get("provider", "cognitive")
        if provider not in ["custom", "cognitive"]:
            contextual_logger.warning(
                f"Invalid memory provider '{provider}' for app '{slug}'. "
                f"Using 'cognitive' instead. Supported: cognitive (or 'custom' for "
                f"backwards compatibility)"
            )
            provider = "cognitive"

        contextual_logger.info(
            f"Initializing {provider} memory service for app '{slug}'",
            extra={
                "app_slug": slug,
                "provider": provider,
                "collection_name": memory_config.get("collection_name", f"{slug}_memories"),
                "embedding_model_dims": memory_config.get("embedding_model_dims", 1536),
                "infer": memory_config.get("infer", True),
            },
        )

        try:
            # Extract memory config (exclude only 'enabled' and 'provider')
            # Pass through ALL other keys so the builder can use them.
            # Previously this used a restrictive allowlist that silently
            # dropped critical keys like enable_cognitive, categories,
            # cognitive, persona, memory_llm_model, extraction_provider,
            # reflection, emotion_weight, etc.
            _excluded_keys = {"enabled", "provider"}

            service_config = {k: v for k, v in memory_config.items() if k not in _excluded_keys}

            # Normalize embedding_dims -> embedding_model_dims
            if "embedding_dims" in service_config and "embedding_model_dims" not in service_config:
                service_config["embedding_model_dims"] = service_config.pop("embedding_dims")

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

            # Auto-generate index name from collection name if not provided
            # This ensures the index name matches the collection name pattern
            if "index_name" not in service_config:
                final_collection_name = service_config["collection_name"]
                service_config["index_name"] = f"{final_collection_name}_vector_index"
                contextual_logger.info(
                    f"Auto-generated vector search index name: "
                    f"'{service_config['index_name']}' from collection '{final_collection_name}'",
                    extra={
                        "app_slug": slug,
                        "collection_name": final_collection_name,
                        "index_name": service_config["index_name"],
                    },
                )

            # Use Motor async collections directly - forward-facing DI
            if not self._connection_manager or not self._connection_manager.initialized:
                contextual_logger.error(
                    "Connection manager not available or not initialized. "
                    "Memory service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug},
                )
                raise RuntimeError(
                    f"Memory service initialization failed for '{slug}': "
                    f"MDB-Engine connection manager is required but not available."
                )

            try:
                # Use MDB Engine's scoped collections for proper app_id filtering and scoping
                scoped_db = await self.get_scoped_db_fn(slug)
                base_collection_name = service_config.get("collection_name", "user_memories")
                # Get prefixed collection name for index management (indexes need full name)
                if base_collection_name.startswith(f"{slug}_"):
                    prefixed_collection_name = base_collection_name
                    base_collection_name = base_collection_name[len(f"{slug}_") :]
                else:
                    prefixed_collection_name = f"{slug}_{base_collection_name}"

                # Get scoped collection (base name - scoped wrapper handles prefixing)
                collection = getattr(scoped_db, base_collection_name)  # ScopedCollectionWrapper

                contextual_logger.info(
                    f"Using MDB-Engine scoped collection for memory service: "
                    f"{base_collection_name} (app: {slug}, prefixed: {prefixed_collection_name})",
                    extra={
                        "app_slug": slug,
                        "collection_name": base_collection_name,
                        "prefixed_name": prefixed_collection_name,
                    },
                )

                # Automatically ensure vector search index exists (use prefixed name for index ops)
                index_name = service_config.get("index_name", f"{prefixed_collection_name}_vector_index")
                embedding_dims = service_config.get("embedding_model_dims", 1536)
                await self._ensure_memory_vector_index(
                    slug=slug,
                    collection_name=prefixed_collection_name,
                    index_name=index_name,
                    embedding_dims=embedding_dims,
                )

                # Create TTL indexes for episodic and working memory (Cognitive Blueprint v2.0)
                memory_types_config = memory_config.get("memory_types", {})
                if memory_types_config.get("enabled", True):
                    await self._ensure_memory_ttl_indexes(
                        slug=slug,
                        collection_name=prefixed_collection_name,
                        episodic_retention_days=memory_types_config.get("episodic_retention_days", 730),
                        working_ttl_hours=memory_types_config.get("working_ttl_hours", 24),
                    )
            except (AttributeError, RuntimeError, KeyError) as e:
                contextual_logger.exception(
                    f"Could not get collection from MDB-Engine connection manager: {e}. "
                    f"Memory service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug, "error": str(e)},
                )
                raise RuntimeError(
                    f"Memory service initialization failed for '{slug}': "
                    f"Could not get collection from MDB-Engine connection manager: {e}"
                ) from e

            # Get graph service if available (for GraphRAG integration)
            graph_service = self._graph_services.get(slug)
            if graph_service:
                contextual_logger.info(
                    "Graph service available for memory service integration (GraphRAG enabled)",
                    extra={"app_slug": slug},
                )
            else:
                available_graph_services = list(self._graph_services.keys())
                contextual_logger.warning(
                    f"Graph service not available for memory service (GraphRAG disabled). "
                    f"Available graph services: {available_graph_services}",
                    extra={"app_slug": slug, "available_services": available_graph_services},
                )

            # Inherit LLM model from app's llm_config if not explicitly set in memory_config
            if llm_config and "memory_llm_model" not in service_config:
                default_model = llm_config.get("default_model")
                if default_model:
                    service_config["memory_llm_model"] = default_model
                    contextual_logger.info(
                        f"Memory service inheriting LLM model from app's " f"llm_config: {default_model}",
                        extra={"app_slug": slug, "llm_model": default_model},
                    )

            # Create Memory service using factory function
            memory_service = get_memory_service(
                app_slug=slug,
                config=service_config,
                provider=provider,
                collection=collection,
                graph_service=graph_service,
            )
            self._memory_services[slug] = memory_service

            contextual_logger.info(
                f"{provider.capitalize()} memory service initialized for app '{slug}'",
                extra={"app_slug": slug, "provider": provider},
            )
        except (CognitiveMemoryServiceError, ValueError) as e:
            # ValueError can be raised by get_memory_service for invalid provider
            contextual_logger.error(
                f"Failed to initialize memory service for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )
        except OpenAIError as e:
            contextual_logger.warning(
                f"Memory service initialization skipped for app '{slug}': " f"OpenAI API error. {e}",
                extra={"app_slug": slug, "error": str(e)},
            )
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            error_msg = str(e).lower()
            error_type = type(e).__name__
            is_api_key_error = (
                "api_key" in error_msg
                or "api key" in error_msg
                or "openai" in error_type.lower()
                or "openai" in error_msg
            )
            if is_api_key_error:
                contextual_logger.warning(
                    f"Memory service initialization skipped for app '{slug}': "
                    f"Missing API key or configuration. {e}",
                    extra={"app_slug": slug, "error": str(e)},
                )
            else:
                contextual_logger.error(
                    f"Error initializing memory service for app '{slug}': {e}",
                    extra={"app_slug": slug, "error": str(e)},
                    exc_info=True,
                )

    async def initialize_procedural_service(
        self,
        slug: str,
        skills_config: dict[str, Any] | None,
    ) -> None:
        """
        Initialize procedural memory (skills) service for an app.

        Must be called AFTER memory service is initialized (needs the
        embedding service from the memory service).

        Args:
            slug: App slug
            skills_config: Skills configuration from ``memory_config.skills``.
                Can be None or empty dict to skip initialization.
        """
        if not skills_config or not skills_config.get("enabled", False):
            return

        contextual_logger.info(
            f"Initializing procedural memory (skills) for app '{slug}'",
            extra={"app_slug": slug},
        )

        try:
            from ..memory.procedural import ProceduralMemory

            # Get the embedding service from the memory service
            memory_service = self._memory_services.get(slug)
            embedding_service = None
            if memory_service:
                embedding_service = getattr(memory_service, "embedding_provider", None)

            if not embedding_service:
                contextual_logger.warning(
                    f"Procedural memory initialization skipped for app '{slug}': "
                    f"no embedding service available. "
                    f"Ensure memory service is initialized first.",
                    extra={"app_slug": slug},
                )
                return

            # Get scoped collection
            scoped_db = await self.get_scoped_db_fn(slug)
            collection_name = skills_config.get("collection_name", "skills")
            # Prefix with slug for multi-app isolation
            prefixed_name = f"{slug}_{collection_name}"
            skills_collection = getattr(scoped_db, prefixed_name)

            # Create ProceduralMemory instance
            embed_model = skills_config.get(
                "embed_model",
                getattr(memory_service, "embed_model", "text-embedding-3-small"),
            )

            procedural_memory = ProceduralMemory(
                collection=skills_collection,
                embedding_service=embedding_service,
                embed_model=embed_model,
            )

            self._procedural_services[slug] = procedural_memory

            contextual_logger.info(
                f"Procedural memory (skills) initialized for app '{slug}' " f"(collection: {prefixed_name})",
                extra={"app_slug": slug, "collection": prefixed_name},
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            if slug in self._procedural_services:
                del self._procedural_services[slug]
            contextual_logger.error(
                f"Failed to initialize procedural memory for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )

    async def initialize_profile_service(
        self,
        slug: str,
        profile_config: dict[str, Any] | None,
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize profile service for an app.

        Profile service materializes user profiles and community profiles
        from memory + graph data for instant AI context injection.

        Must be called AFTER memory and graph services are initialized.

        Args:
            slug: App slug
            profile_config: Profile configuration from manifest.
            llm_config: Optional LLM configuration from manifest.
        """
        if not profile_config or not profile_config.get("enabled", False):
            return

        try:
            from ..profile.service import ProfileService
        except ImportError as e:
            contextual_logger.warning(
                f"Profile configuration found for app '{slug}' but " f"dependencies are not available: {e}.",
                extra={"app_slug": slug, "error": str(e)},
            )
            return

        contextual_logger.info(
            f"Initializing profile service for app '{slug}'",
            extra={"app_slug": slug},
        )

        try:
            if not self._connection_manager or not self._connection_manager.initialized:
                contextual_logger.warning(
                    "Connection manager not available for profile service.",
                    extra={"app_slug": slug},
                )
                return

            # Get scoped database for collection access
            scoped_db = await self.get_scoped_db_fn(slug)

            # User profile collection
            user_cfg = profile_config.get("user_profiles", {})
            user_collection_name = user_cfg.get("collection_name", "user_profiles")
            user_profile_collection = getattr(scoped_db, user_collection_name)

            # Community profile collection (optional)
            community_cfg = profile_config.get("community_profile", {})
            community_profile_collection = None
            memory_collection = None
            graph_collection = None

            if community_cfg.get("enabled", False):
                comm_collection_name = community_cfg.get("collection_name", "community_profile")
                community_profile_collection = getattr(scoped_db, comm_collection_name)

                # For community aggregation, we also need raw access to
                # memory and graph collections (for MongoDB aggregation pipelines).
                # These bypass the scoped wrapper's user_id filtering.
                motor_client = self._connection_manager.mongo_client
                db = motor_client[self.db_name]

                # Get memory collection name from memory service
                memory_svc = self._memory_services.get(slug)
                if memory_svc and hasattr(memory_svc, "collection"):
                    memory_collection = memory_svc.collection
                else:
                    # Fallback: construct from convention
                    memory_collection = db[f"{slug}_user_memories"]

                # Get graph collection
                graph_svc = self._graph_services.get(slug)
                if graph_svc and hasattr(graph_svc, "collection"):
                    graph_collection = graph_svc.collection
                else:
                    graph_collection = db[f"{slug}_kg"]

            # Get services for profile building
            memory_service = self._memory_services.get(slug)
            graph_service = self._graph_services.get(slug)

            # Get LLM service
            llm_service = None
            try:
                from ..llm.service import get_llm_service

                profile_llm_config = llm_config.copy() if llm_config else {}
                llm_service = get_llm_service(config=profile_llm_config if profile_llm_config else None)
            except (ImportError, RuntimeError, ValueError) as e:
                contextual_logger.warning(
                    f"LLM service not available for profile synthesis: {e}",
                    extra={"app_slug": slug},
                )

            # Determine LLM model
            service_config = profile_config.copy()
            if llm_service and "llm_model" not in service_config:
                try:
                    chat_provider = llm_service.providers.get("chat")
                    if chat_provider:
                        service_config["llm_model"] = chat_provider.default_model
                except (AttributeError, KeyError):
                    pass

            # Create profile service
            profile_service = ProfileService(
                app_slug=slug,
                user_profile_collection=user_profile_collection,
                community_profile_collection=community_profile_collection,
                memory_service=memory_service,
                memory_collection=memory_collection,
                graph_service=graph_service,
                graph_collection=graph_collection,
                llm_service=llm_service,
                config=service_config,
            )

            self._profile_services[slug] = profile_service

            # Inject profile service into memory service for incremental updates
            if memory_service and hasattr(memory_service, "set_profile_service"):
                memory_service.set_profile_service(profile_service)
                contextual_logger.info(
                    f"Profile service injected into memory service for '{slug}'",
                    extra={"app_slug": slug},
                )

            contextual_logger.info(
                f"Profile service initialized for app '{slug}'",
                extra={
                    "app_slug": slug,
                    "user_profiles": user_cfg.get("enabled", True),
                    "community_profile": community_cfg.get("enabled", False),
                    "llm_available": llm_service is not None,
                    "memory_available": memory_service is not None,
                    "graph_available": graph_service is not None,
                },
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            if slug in self._profile_services:
                del self._profile_services[slug]
            contextual_logger.error(
                f"Failed to initialize profile service for app '{slug}': {e}",
                extra={"app_slug": slug, "error": str(e)},
                exc_info=True,
            )

    def get_profile_service(self, slug: str) -> Any | None:
        """Get profile service for an app.

        Args:
            slug: App slug

        Returns:
            ProfileService instance if profile is enabled for this app, None otherwise.
        """
        try:
            service = self._profile_services.get(slug)
            if service is not None:
                return service

            # Try case-insensitive lookup
            slug_lower = slug.lower()
            for stored_slug, stored_service in self._profile_services.items():
                if stored_slug.lower() == slug_lower:
                    return stored_service

            return None
        except (KeyError, AttributeError, TypeError):
            return None

    async def register_websockets(self, slug: str, websockets_config: dict[str, Any]) -> None:
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
                contextual_logger.warning(f"Could not initialize WebSocket manager for {slug}: {e}")
                continue
            contextual_logger.debug(
                f"Configured WebSocket endpoint '{endpoint_name}' at path '{path}'",
                extra={"app_slug": slug, "endpoint": endpoint_name, "path": path},
            )

    async def seed_initial_data(self, slug: str, initial_data: dict[str, list[dict[str, Any]]]) -> None:
        """
        Seed initial data into collections for an app.

        Args:
            slug: App slug
            initial_data: Dictionary mapping collection names to arrays of documents
        """
        try:
            from .seeding import seed_initial_data

            db = await self.get_scoped_db_fn(slug)
            results = await seed_initial_data(db, slug, initial_data)

            total_inserted = sum(results.values())
            if total_inserted > 0:
                contextual_logger.info(
                    f"Seeded initial data for app '{slug}'",
                    extra={
                        "app_slug": slug,
                        "collections_seeded": len([c for c, count in results.items() if count > 0]),
                        "total_documents": total_inserted,
                    },
                )
            else:
                contextual_logger.debug(
                    f"No initial data seeded for app '{slug}' " f"(collections already had data or were empty)",
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
        self, slug: str, manifest: dict[str, Any], observability_config: dict[str, Any]
    ) -> None:
        """
        Set up observability features (health checks, metrics, logging, tracing) from manifest.

        Enforces the configuration rather than just logging it:
        - Sets Python logging level and format from ``logging.level`` / ``logging.format``
        - Registers Prometheus ``/metrics`` endpoint if ``metrics.export_prometheus``
        - Configures OpenTelemetry metrics bridge if ``metrics.export_otlp``
        - Initialises distributed tracing if ``tracing.enabled``

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
                        "operation_metrics": metrics_config.get("collect_operation_metrics", True),
                        "performance_metrics": metrics_config.get("collect_performance_metrics", True),
                        "custom_metrics": metrics_config.get("custom_metrics", []),
                    },
                )

                # Prometheus export
                if metrics_config.get("export_prometheus", False):
                    from ..observability.exporters import create_prometheus_endpoint

                    app = self._get_app_for_slug(slug)
                    if app:
                        create_prometheus_endpoint(app)

                # OTel metrics bridge
                if metrics_config.get("export_otlp", False):
                    from ..observability.exporters import setup_otel_metrics_bridge

                    setup_otel_metrics_bridge(service_name=slug)

            # Set up logging — enforce level and format
            logging_config = observability_config.get("logging", {})
            if logging_config:
                import logging as stdlib_logging

                log_level = logging_config.get("level", "INFO")
                log_format = logging_config.get("format", "json")

                # Apply logging level to the mdb_engine logger hierarchy
                engine_logger = stdlib_logging.getLogger("mdb_engine")
                engine_logger.setLevel(getattr(stdlib_logging, log_level, stdlib_logging.INFO))

                # Apply JSON formatter if requested and not already set
                if log_format == "json":
                    self._apply_json_formatter(engine_logger)

                contextual_logger.info(
                    f"Logging configured for {slug}",
                    extra={
                        "level": log_level,
                        "format": log_format,
                        "include_request_id": logging_config.get("include_request_id", True),
                    },
                )

            # Set up OpenTelemetry tracing (opt-in)
            tracing_config = observability_config.get("tracing", {})
            if tracing_config.get("enabled", False):
                from ..observability.tracing import init_tracer_provider, otel_available

                if otel_available():
                    init_tracer_provider(
                        service_name=tracing_config.get("service_name") or slug,
                        endpoint=tracing_config.get("endpoint", "http://localhost:4317"),
                        exporter=tracing_config.get("exporter", "otlp"),
                        sample_rate=tracing_config.get("sample_rate", 1.0),
                    )
                    contextual_logger.info(
                        f"OpenTelemetry tracing configured for {slug}",
                        extra={
                            "exporter": tracing_config.get("exporter", "otlp"),
                            "endpoint": tracing_config.get("endpoint", "http://localhost:4317"),
                            "sample_rate": tracing_config.get("sample_rate", 1.0),
                        },
                    )
                else:
                    contextual_logger.warning(
                        f"Tracing enabled in manifest for {slug} but OpenTelemetry SDK not installed. "
                        "Install with: pip install mdb-engine[otel]"
                    )

        except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
            contextual_logger.warning(f"Could not set up observability for {slug}: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def _get_app_for_slug(self, slug: str) -> Any | None:
        """Retrieve the FastAPI app instance for *slug* (if registered)."""
        try:
            return getattr(self, "_apps", {}).get(slug)
        except (AttributeError, TypeError):
            return None

    @staticmethod
    def _apply_json_formatter(target_logger: Any) -> None:
        """Apply a JSON log formatter to *target_logger*'s handlers."""
        import json as _json
        import logging as stdlib_logging

        class _JsonFormatter(stdlib_logging.Formatter):
            def format(self, record: stdlib_logging.LogRecord) -> str:
                log_entry: dict[str, Any] = {
                    "timestamp": self.formatTime(record, self.datefmt),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                if record.exc_info and record.exc_info[1]:
                    log_entry["exception"] = self.formatException(record.exc_info)
                # Include extra fields added by ContextualLoggerAdapter
                for key in ("correlation_id", "app_slug", "trace_id", "span_id"):
                    value = getattr(record, key, None)
                    if value is not None:
                        log_entry[key] = value
                return _json.dumps(log_entry)

        json_formatter = _JsonFormatter()
        for handler in target_logger.handlers:
            handler.setFormatter(json_formatter)
        if not target_logger.handlers:
            handler = stdlib_logging.StreamHandler()
            handler.setFormatter(json_formatter)
            target_logger.addHandler(handler)

    def get_websocket_config(self, slug: str) -> dict[str, Any] | None:
        """
        Get WebSocket configuration for an app.

        Args:
            slug: App slug

        Returns:
            WebSocket configuration dict or None if not configured
        """
        return self._websocket_configs.get(slug)

    def get_graph_service(self, slug: str) -> GraphServiceProtocol | None:
        """
        Get graph service for an app.

        Args:
            slug: App slug

        Returns:
            GraphServiceProtocol instance if graph is enabled for this app, None otherwise.
            The returned service implements GraphServiceProtocol for type-safe access.
        """
        try:
            # Try exact match first
            service = self._graph_services.get(slug)
            if service is not None:
                return service

            # Try case-insensitive lookup (handle case mismatches)
            slug_lower = slug.lower()
            for stored_slug, stored_service in self._graph_services.items():
                if stored_slug.lower() == slug_lower:
                    contextual_logger.warning(
                        f"Graph service found with case mismatch: '{stored_slug}' != '{slug}'. "
                        f"Using '{stored_slug}'. Consider normalizing slug casing.",
                        extra={"requested_slug": slug, "found_slug": stored_slug},
                    )
                    return stored_service

            available_slugs = list(self._graph_services.keys())
            contextual_logger.debug(
                f"Graph service not found for '{slug}' - "
                f"it may not be initialized yet or graph is disabled. "
                f"Available services: {available_slugs}",
                extra={"app_slug": slug, "available_slugs": available_slugs},
            )
            return None
        except (KeyError, AttributeError, TypeError) as e:
            contextual_logger.error(
                f"Error retrieving graph service for '{slug}': {e}",
                exc_info=True,
                extra={"app_slug": slug, "error": str(e)},
            )
            return None

    def get_memory_service(self, slug: str) -> MemoryServiceProtocol | None:
        """
        Get memory service for an app.

        Args:
            slug: App slug

        Returns:
            MemoryServiceProtocol instance if memory is enabled for this app,
            None otherwise. The returned service implements MemoryServiceProtocol
            (typically CognitiveMemoryService).
        """
        try:
            # Try exact match first
            service = self._memory_services.get(slug)
            if service is not None:
                return service

            # Try case-insensitive lookup (handle case mismatches)
            slug_lower = slug.lower()
            for stored_slug, stored_service in self._memory_services.items():
                if stored_slug.lower() == slug_lower:
                    contextual_logger.warning(
                        f"Memory service found with case mismatch: '{stored_slug}' != '{slug}'. "
                        f"Using '{stored_slug}'. Consider normalizing slug casing.",
                        extra={"requested_slug": slug, "found_slug": stored_slug},
                    )
                    return stored_service

            # Service not found - check if it should be initialized but wasn't
            # This can happen in multi-app context if initialization was missed
            # Note: We can't do async initialization here, so we just log a warning
            # The explicit initialization in create_multi_app should handle this
            available_slugs = list(self._memory_services.keys())
            contextual_logger.debug(
                f"Memory service not found for '{slug}' - "
                f"it may not be initialized yet or memory is disabled. "
                f"Available services: {available_slugs}",
                extra={"app_slug": slug, "available_slugs": available_slugs},
            )
            return None
        except (KeyError, AttributeError, TypeError) as e:
            contextual_logger.error(
                f"Error retrieving memory service for '{slug}': {e}",
                exc_info=True,
                extra={"app_slug": slug, "error": str(e)},
            )
            return None

    def get_procedural_service(self, slug: str) -> ProceduralMemoryProtocol | None:
        """
        Get procedural memory (skills) service for an app.

        Args:
            slug: App slug

        Returns:
            ProceduralMemoryProtocol instance if skills are enabled for this app,
            None otherwise.
        """
        try:
            service = self._procedural_services.get(slug)
            if service is not None:
                return service

            # Try case-insensitive lookup
            slug_lower = slug.lower()
            for stored_slug, stored_service in self._procedural_services.items():
                if stored_slug.lower() == slug_lower:
                    contextual_logger.warning(
                        f"Procedural service found with case mismatch: '{stored_slug}' != '{slug}'. "
                        f"Using '{stored_slug}'. Consider normalizing slug casing.",
                        extra={"requested_slug": slug, "found_slug": stored_slug},
                    )
                    return stored_service

            return None
        except (KeyError, AttributeError, TypeError) as e:
            contextual_logger.error(
                f"Error retrieving procedural service for '{slug}': {e}",
                exc_info=True,
                extra={"app_slug": slug, "error": str(e)},
            )
            return None

    def clear_services(self) -> None:
        """Clear all service state."""
        self._graph_services.clear()
        self._memory_services.clear()
        self._procedural_services.clear()
        self._profile_services.clear()
        self._websocket_configs.clear()
        self._osi_registries.clear()
