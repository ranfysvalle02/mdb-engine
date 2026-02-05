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

from ..database import ScopedMongoWrapper
from ..observability import get_logger as get_contextual_logger
from .protocols import GraphServiceProtocol, MemoryServiceProtocol

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)

try:
    from openai import OpenAIError
except ImportError:
    OpenAIError = RuntimeError


class ServiceInitializer:
    """Service initializer for MDB-Engine optional services."""

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
            # Note: We don't add app_id filter here because memory collections
            # are already scoped by app_id through the collection name prefix
            # However, we MUST include user_id as a filter for vector search queries
            index_definition = {
                "fields": [
                    {
                        "type": "filter",
                        "path": "user_id",
                    },
                    {
                        "type": "filter",
                        "path": "is_active",
                    },
                    {
                        "type": "filter",
                        "path": "metadata.associated_bucket_id",
                    },
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": embedding_dims,
                        "similarity": "cosine",
                    },
                ]
            }

            # Check if index already exists
            existing_index = await index_manager.get_search_index(index_name)

            if existing_index:
                # Check if index definition matches (including user_id filter)
                current_def = existing_index.get(
                    "latestDefinition", existing_index.get("definition", {})
                )

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
                            f"✅ Vector search index '{index_name}' already exists and "
                            f"is queryable",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        return
                    else:
                        contextual_logger.info(
                            f"⏳ Vector search index '{index_name}' exists but not queryable yet. "
                            f"Waiting for it to become ready...",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        await index_manager._wait_for_search_index_ready(  # noqa: SLF001
                            index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                        )
                        contextual_logger.info(
                            f"✅ Vector search index '{index_name}' is now ready",
                            extra={"app_slug": slug, "index_name": index_name},
                        )
                        return
                elif existing_index.get("status") == "FAILED":
                    contextual_logger.error(
                        f"❌ Vector search index '{index_name}' exists but is in FAILED state. "
                        f"Manual intervention in Atlas UI may be required.",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                else:
                    # Index exists but definition doesn't match (missing required filters)
                    # - update it
                    contextual_logger.warning(
                        f"⚠️ Vector search index '{index_name}' exists but is missing "
                        f"required filters. Updating index to include user_id, is_active, "
                        f"and metadata.associated_bucket_id filters "
                        f"(required for vector search queries)...",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    await index_manager.update_search_index(
                        name=index_name,
                        definition=index_definition,
                        wait_for_ready=True,
                    )
                    contextual_logger.info(
                        f"✅ Successfully updated vector search index '{index_name}' "
                        f"with required filters",
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

            # Build vector search index definition
            # Note: We don't add app_id filter here because memory collections
            # are already scoped by app_id through the collection name prefix
            # However, we MUST include user_id as a filter for vector search queries
            # We also include is_active to support cognitive memory soft-delete filtering
            # We also include metadata.associated_bucket_id for bucket-aware searches
            index_definition = {
                "fields": [
                    {
                        "type": "filter",
                        "path": "user_id",
                    },
                    {
                        "type": "filter",
                        "path": "is_active",
                    },
                    {
                        "type": "filter",
                        "path": "metadata.associated_bucket_id",
                    },
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": embedding_dims,
                        "similarity": "cosine",
                    },
                ]
            }

            # Create the index
            await index_manager.create_search_index(
                name=index_name,
                definition=index_definition,
                index_type="vectorSearch",
                wait_for_ready=True,
            )

            contextual_logger.info(
                f"✅ Successfully created vector search index '{index_name}' "
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
                f"⚠️ Could not automatically create vector search index '{index_name}': {e}. "
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
                    f"✅ Created TTL index for episodic memory "
                    f"(retention: {episodic_retention_days} days)",
                    extra={"app_slug": slug, "collection_name": collection_name},
                )
            except (PyMongoError, AttributeError, TypeError) as e:
                # Index might already exist
                contextual_logger.debug(f"Episodic TTL index creation: {e}")

            # Note: Working memory TTL is handled by ChatHistoryService separately
            # This is for memory_type="working" in the main collection if used

        except (PyMongoError, AttributeError, TypeError, ValueError) as e:
            contextual_logger.warning(
                f"⚠️ Failed to create TTL indexes: {e}",
                extra={"app_slug": slug},
            )

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        get_scoped_db_fn: Callable[[str], ScopedMongoWrapper],
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
        self._websocket_configs: dict[str, dict[str, Any]] = {}

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
            # Include app_slug and user_id as filters
            index_definition = {
                "fields": [
                    {
                        "type": "filter",
                        "path": "app_slug",
                    },
                    {
                        "type": "filter",
                        "path": "user_id",
                    },
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": embedding_dims,
                        "similarity": "cosine",
                    },
                ]
            }

            # Check if index already exists
            existing_index = await index_manager.get_search_index(index_name)

            if existing_index:
                if existing_index.get("queryable"):
                    contextual_logger.info(
                        f"✅ Graph vector index '{index_name}' already exists and is queryable",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                elif existing_index.get("status") == "FAILED":
                    contextual_logger.error(
                        f"❌ Graph vector search index '{index_name}' is in FAILED state.",
                        extra={"app_slug": slug, "index_name": index_name},
                    )
                    return
                else:
                    contextual_logger.info(
                        f"⏳ Graph vector search index '{index_name}' exists but not queryable. "
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
                f"✅ Successfully created graph vector search index '{index_name}'",
                extra={"app_slug": slug, "index_name": index_name},
            )

        except (RuntimeError, ValueError, KeyError, AttributeError) as e:
            contextual_logger.warning(
                f"⚠️ Could not create graph vector search index '{index_name}': {e}. "
                f"Hybrid search may not work until the index is created.",
                extra={"app_slug": slug, "index_name": index_name, "error": str(e)},
            )

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
                "collection_name": graph_config.get("collection_name", f"{slug}__kg"),
                "auto_extract": graph_config.get("auto_extract", True),
            },
        )

        try:
            # Get PyMongo collection from MDB-Engine connection manager
            if not self._connection_manager or not self._connection_manager.initialized:
                contextual_logger.error(
                    "❌ Connection manager not available or not initialized. "
                    "Graph service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug},
                )
                return

            try:
                motor_client = self._connection_manager.mongo_client
                pymongo_client = motor_client.delegate

                pymongo_db = pymongo_client[self.db_name]
                collection_name = graph_config.get("collection_name", "__kg")
                # Ensure collection name is prefixed with app slug
                if not collection_name.startswith(f"{slug}_"):
                    collection_name = f"{slug}_{collection_name}"
                collection = pymongo_db[collection_name]

                contextual_logger.info(
                    f"✅ Using MDB-Engine connection pool for graph service: {collection_name}",
                    extra={"app_slug": slug, "collection_name": collection_name},
                )

                # Automatically ensure vector search index exists
                index_name = graph_config.get(
                    "vector_index_name", f"{collection_name}_vector_index"
                )
                embedding_dims = graph_config.get("embedding_dims", 1536)
                await self._ensure_graph_vector_index(
                    slug=slug,
                    collection_name=collection_name,
                    index_name=index_name,
                    embedding_dims=embedding_dims,
                )

            except (AttributeError, RuntimeError, KeyError) as e:
                contextual_logger.exception(
                    f"❌ Could not get collection from MDB-Engine connection manager: {e}",
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
                        f"✅ Graph service inheriting LLM model from app's " f"llm_config: {model}",
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
                embedding_service = get_embedding_service(
                    config=embedding_config if embedding_config else None
                )
            except (ImportError, RuntimeError, ValueError) as e:
                contextual_logger.warning(
                    f"Embedding service not available for graph hybrid search: {e}",
                    extra={"app_slug": slug},
                )

            # Update config with collection name for the service
            service_config = graph_config.copy()
            service_config["collection_name"] = collection_name
            service_config["vector_index_name"] = index_name

            # Get the default model from LLM service to pass to GraphStore
            # This must be done AFTER service_config is created
            if llm_service and "llm_model" not in service_config:
                default_llm_model = llm_service.llm_provider.default_model
                if default_llm_model:
                    service_config["llm_model"] = default_llm_model
                    contextual_logger.info(
                        f"✅ Graph service using LLM model: {default_llm_model}",
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
                f"✅ Graph service initialized for app '{slug}'",
                extra={
                    "app_slug": slug,
                    "collection_name": collection_name,
                    "llm_available": llm_service is not None,
                    "embedding_available": embedding_service is not None,
                },
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
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
            # Extract memory config (exclude 'enabled' and 'provider')
            # Include cognitive-specific config fields
            allowed_config_keys = [
                "collection_name",
                "index_name",  # Vector search index name
                "embedding_model_dims",
                "embedding_dims",  # Alias for embedding_model_dims
                "infer",
                "async_mode",
                "embedding_model",
                "chat_model",
                "temperature",
                # Cognitive-specific fields
                "max_depth",
                "similarity_threshold",
                "reinforcement_factor",
                "decay_factor",
                "merge_threshold_low",
                "merge_threshold_high",
                # Nested configurations
                "graph",
            ]

            service_config = {
                k: v
                for k, v in memory_config.items()
                if k != "enabled" and k != "provider" and k in allowed_config_keys
            }

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

            # Get PyMongo collection from MDB-Engine connection manager - REQUIRED
            if not self._connection_manager or not self._connection_manager.initialized:
                contextual_logger.error(
                    "❌ Connection manager not available or not initialized. "
                    "Memory service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug},
                )
                raise RuntimeError(
                    f"Memory service initialization failed for '{slug}': "
                    f"MDB-Engine connection manager is required but not available."
                )

            try:
                # Get the underlying PyMongo client from Motor's AsyncIOMotorClient
                # Motor wraps PyMongo's MongoClient, accessible via .delegate
                motor_client = self._connection_manager.mongo_client
                pymongo_client = motor_client.delegate  # Get underlying PyMongo client

                # Get the database and collection
                pymongo_db = pymongo_client[self.db_name]
                collection_name = service_config.get("collection_name", f"{slug}_memories")
                collection = pymongo_db[collection_name]

                contextual_logger.info(
                    f"✅ Using MDB-Engine connection pool for memory service: {collection_name}",
                    extra={"app_slug": slug, "collection_name": collection_name},
                )

                # Automatically ensure vector search index exists
                index_name = service_config.get("index_name", f"{collection_name}_vector_index")
                embedding_dims = service_config.get("embedding_model_dims", 1536)
                await self._ensure_memory_vector_index(
                    slug=slug,
                    collection_name=collection_name,
                    index_name=index_name,
                    embedding_dims=embedding_dims,
                )

                # Create TTL indexes for episodic and working memory (Cognitive Blueprint v2.0)
                memory_types_config = memory_config.get("memory_types", {})
                if memory_types_config.get("enabled", True):
                    await self._ensure_memory_ttl_indexes(
                        slug=slug,
                        collection_name=collection_name,
                        episodic_retention_days=memory_types_config.get(
                            "episodic_retention_days", 730
                        ),
                        working_ttl_hours=memory_types_config.get("working_ttl_hours", 24),
                    )
            except (AttributeError, RuntimeError, KeyError) as e:
                contextual_logger.exception(
                    f"❌ Could not get collection from MDB-Engine connection manager: {e}. "
                    f"Memory service REQUIRES MDB-Engine connection pool.",
                    extra={"app_slug": slug, "error": str(e)},
                )
                raise RuntimeError(
                    f"Memory service initialization failed for '{slug}': "
                    f"Could not get collection from MDB-Engine connection manager: {e}"
                ) from e

            # Get graph service if available (for GraphRAG integration)
            graph_service = self._graph_services.get(slug)

            # Inherit LLM model from app's llm_config if not explicitly set in memory_config
            if llm_config and "memory_llm_model" not in service_config:
                default_model = llm_config.get("default_model")
                if default_model:
                    service_config["memory_llm_model"] = default_model
                    contextual_logger.info(
                        f"✅ Memory service inheriting LLM model from app's "
                        f"llm_config: {default_model}",
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
                f"Memory service initialization skipped for app '{slug}': "
                f"OpenAI API error. {e}",
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

    async def seed_initial_data(
        self, slug: str, initial_data: dict[str, list[dict[str, Any]]]
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
                        "collections_seeded": len([c for c, count in results.items() if count > 0]),
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
        self, slug: str, manifest: dict[str, Any], observability_config: dict[str, Any]
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
                        "operation_metrics": metrics_config.get("collect_operation_metrics", True),
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
                        "include_request_id": logging_config.get("include_request_id", True),
                    },
                )

        except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
            contextual_logger.warning(
                f"Could not set up observability for {slug}: {e}", exc_info=True
            )

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

    def clear_services(self) -> None:
        """Clear all service state."""
        self._graph_services.clear()
        self._memory_services.clear()
        self._websocket_configs.clear()
