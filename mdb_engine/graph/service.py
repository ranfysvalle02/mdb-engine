"""
GraphService - Knowledge Graph Service with $graphLookup Traversal

This module provides a full-featured knowledge graph service using MongoDB's
native $graphLookup aggregation stage for efficient graph traversal.

Key Features:
- Node and edge management with typed schemas
- $graphLookup-based graph traversal (multi-hop queries)
- Hybrid search combining vector similarity with graph context
- LLM-powered automatic entity/relationship extraction
- Temporal edge support (active/inactive, timestamps)
- App-scoped isolation for multi-tenant deployments

Schema:
    Node Document:
    {
        "_id": "person:alex",           # Unique node ID (type:identifier)
        "type": "person",               # Node type
        "name": "Alex",                 # Display name
        "properties": {...},            # Type-specific properties
        "edges": [                      # Outgoing relationships
            {
                "relation": "likes",
                "target": "interest:golf",
                "properties": {"since": "2020"},
                "weight": 0.9,
                "active": True,
                "created_at": ISODate
            }
        ],
        "embedding": [...],             # Optional: for hybrid search
        "app_slug": "myapp",
        "user_id": "user123",           # Owner user
        "created_at": ISODate,
        "updated_at": ISODate
    }

Usage:
    graph_service = GraphService(
        app_slug="myapp",
        collection=kg_collection,
        config={"enabled": True, "auto_extract": True},
        llm_service=llm_service,
        embedding_service=embedding_service,
    )

    # Add nodes and edges
    graph_service.upsert_node("person:alex", "person", "Alex", {"occupation": "Engineer"})
    graph_service.add_edge("person:alex", "likes", "interest:golf", weight=0.9)

    # Traverse the graph
    network = graph_service.traverse("person:alex", max_depth=2)

    # Hybrid search (vector + graph)
    results = graph_service.hybrid_search("what does Alex like?", user_id="user123")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .base import BaseGraphService, GraphServiceError

if TYPE_CHECKING:
    from ..embeddings.service import EmbeddingService
    from ..llm.service import LLMService

logger = logging.getLogger(__name__)

# PyMongo imports
try:
    from pymongo import ASCENDING, DESCENDING
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None


# Note: We use JSON mode instead of Pydantic structured output for cross-provider compatibility
# (Azure OpenAI has strict additionalProperties requirements that Pydantic doesn't satisfy)


# ============================================================================
# LLM Prompts
# ============================================================================

GRAPH_EXTRACTION_SYSTEM_PROMPT = """You are a knowledge graph extraction engine.
Extract entities (nodes) and relationships (edges) from text.

NODE TYPES:
- person: People, users, individuals
- interest: Hobbies, topics, activities someone likes
- event: Meetings, occasions, happenings
- location: Places, cities, countries, addresses
- organization: Companies, teams, groups
- product: Items, goods, services
- concept: Abstract ideas, skills, qualities

RELATIONSHIP TYPES (use lowercase with underscores):
- knows, likes, dislikes, loves, hates
- works_at, lives_in, located_in
- member_of, part_of, belongs_to
- parent_of, child_of, sibling_of, spouse_of, friend_of
- attended, participated_in, created, owns
- interested_in, skilled_at, studies

NODE ID FORMAT: type:lowercase_name (e.g., person:alex, interest:golf, location:seattle)

RULES:
1. Extract ALL entities mentioned, even implied ones
2. Create edges for ALL relationships mentioned
3. Use the user as "person:user" when they talk about themselves
4. Infer reasonable properties from context
5. Keep names lowercase in IDs but proper case in name field"""

GRAPH_EXTRACTION_USER_PROMPT = """Extract entities and relationships from this text:

TEXT: {text}

USER_ID: {user_id}

Return ONLY valid JSON in this exact format:
{{
    "nodes": [
        {{"id": "type:name", "type": "node_type", "name": "Display Name", "properties": {{}}}}
    ],
    "edges": [
        {{
            "source": "type:name",
            "relation": "relationship",
            "target": "type:name",
            "properties": {{}}
        }}
    ]
}}

If the text mentions "I" or "my", use "person:user" as the source node."""


# ============================================================================
# GraphService Class
# ============================================================================


class GraphService(BaseGraphService):
    """
    Knowledge Graph Service with MongoDB $graphLookup traversal.

    Provides:
    - Node CRUD operations
    - Edge management with weights and temporal flags
    - Multi-hop graph traversal using $graphLookup
    - Hybrid search (vector + graph)
    - LLM-powered entity extraction

    Configuration:
        enabled: bool - Enable graph service (default: False)
        collection_name: str - Collection name (default: "__kg")
        auto_extract: bool - Auto-extract from text (default: True)
        llm_model: str - LLM for extraction (default: from llm_service)
        default_max_depth: int - Default traversal depth (default: 2)
        vector_index_name: str - Name of vector index (default: "graph_vector_index")
        node_types: list - Allowed node types
    """

    # Default node types
    DEFAULT_NODE_TYPES = [
        "person",
        "interest",
        "event",
        "location",
        "organization",
        "product",
        "concept",
    ]

    def __init__(
        self,
        app_slug: str,
        collection: Any,
        config: dict[str, Any] | None = None,
        llm_service: LLMService | None = None,
        embedding_service: EmbeddingService | None = None,
    ):
        """
        Initialize GraphService.

        Args:
            app_slug: Application slug for scoping
            collection: PyMongo collection for graph nodes
            config: Configuration dictionary
            llm_service: LLMService instance for entity extraction
            embedding_service: EmbeddingService instance for hybrid search embeddings

        Raises:
            GraphServiceError: If collection is None
        """
        if collection is None:
            raise GraphServiceError(
                "Collection is REQUIRED. GraphService must use MDB-Engine's connection pool. "
                "Pass a PyMongo Collection instance obtained from MDB-Engine's connection manager."
            )

        self._app_slug = app_slug
        self.collection = collection
        self.config = config or {}
        self.llm_service = llm_service
        self.embedding_service = embedding_service

        # Configuration
        # Graph service is enabled by default (opt-out via manifest.json)
        self._enabled = self.config.get("enabled", True)
        self.auto_extract = self.config.get("auto_extract", True)
        self.llm_model = self.config.get("llm_model")  # None means use llm_service default
        self.temperature = self.config.get("temperature", 0.0)
        self.default_max_depth = self.config.get("default_max_depth", 2)
        self.vector_index_name = self.config.get("vector_index_name", "graph_vector_index")
        self.node_types = self.config.get("node_types", self.DEFAULT_NODE_TYPES)

        # Create indexes
        self._ensure_indexes()

        if self._enabled:
            logger.info(
                f"GraphService initialized: app_slug={app_slug}, "
                f"auto_extract={self.auto_extract}, max_depth={self.default_max_depth}"
            )
        else:
            logger.info("GraphService initialized but DISABLED")

    @property
    def enabled(self) -> bool:
        """Whether the graph service is enabled."""
        return self._enabled

    @property
    def app_slug(self) -> str:
        """The application slug this service is scoped to."""
        return self._app_slug

    def _ensure_indexes(self) -> None:
        """Create necessary indexes for efficient queries."""
        try:
            # Index on _id is automatic
            # Compound index for app-scoped queries
            self.collection.create_index(
                [("app_slug", ASCENDING), ("type", ASCENDING)],
                name="app_type_idx",
                background=True,
            )
            # Index for user-scoped queries
            self.collection.create_index(
                [("app_slug", ASCENDING), ("user_id", ASCENDING)],
                name="app_user_idx",
                background=True,
            )
            # Index for edge traversal
            self.collection.create_index(
                [("edges.target", ASCENDING)],
                name="edge_target_idx",
                background=True,
            )
            logger.debug("GraphService indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to create GraphService indexes: {e}")

    async def _get_embedding(self, text: str) -> list[float] | None:
        """Generate embedding using the embedding service."""
        if not self.embedding_service:
            return None

        try:
            vectors = await self.embedding_service.embed(text)
            if vectors and len(vectors) > 0:
                return vectors[0]
            return None
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to generate embedding: {e}")
            return None

    def _get_embedding_sync(self, text: str) -> list[float] | None:
        """Generate embedding synchronously (for sync methods)."""
        if not self.embedding_service:
            return None

        try:
            # Try to get existing event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, use a thread pool
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            lambda: asyncio.run(self.embedding_service.embed(text))
                        )
                        vectors = future.result(timeout=30)
                else:
                    vectors = loop.run_until_complete(self.embedding_service.embed(text))
            except RuntimeError:
                # No event loop, create new one
                vectors = asyncio.run(self.embedding_service.embed(text))

            if vectors and len(vectors) > 0:
                return vectors[0]
            return None
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to generate embedding (sync): {e}")
            return None

    # ========================================================================
    # Node Operations
    # ========================================================================

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None,
        embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        """Create or update a node in the graph."""
        if not self._enabled:
            logger.debug("GraphService disabled, skipping upsert_node")
            return {}

        now = datetime.now(timezone.utc)

        # Build update document
        update_doc: dict[str, Any] = {
            "$set": {
                "type": node_type,
                "name": name,
                "app_slug": self._app_slug,
                "updated_at": now,
            },
            "$setOnInsert": {
                "_id": node_id,
                "edges": [],
                "created_at": now,
            },
        }

        if properties:
            update_doc["$set"]["properties"] = properties

        if user_id:
            update_doc["$set"]["user_id"] = str(user_id)

        if embedding:
            update_doc["$set"]["embedding"] = embedding
        elif self.embedding_service and not embedding:
            # Generate embedding from name + properties
            embed_text = f"{name} {node_type}"
            if properties:
                embed_text += " " + " ".join(str(v) for v in properties.values())
            generated_embedding = self._get_embedding_sync(embed_text)
            if generated_embedding:
                update_doc["$set"]["embedding"] = generated_embedding

        try:
            result = self.collection.update_one(
                {"_id": node_id, "app_slug": self._app_slug},
                update_doc,
                upsert=True,
            )

            if result.upserted_id:
                logger.info(f"Created node: {node_id} ({node_type})")
            else:
                logger.debug(f"Updated node: {node_id}")

            # Return the node
            return self.get_node(node_id) or {}

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to upsert node {node_id}: {e}")
            raise GraphServiceError(f"Failed to upsert node: {e}") from e

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""
        try:
            return self.collection.find_one({"_id": node_id, "app_slug": self._app_slug})
        except (PyMongoError, OperationFailure, Exception) as e:
            logger.warning(f"Failed to get node {node_id}: {e}")
            return None

    def delete_node(self, node_id: str) -> bool:
        """Delete a node and all its edges."""
        if not self._enabled:
            return False

        try:
            # Delete the node
            result = self.collection.delete_one({"_id": node_id, "app_slug": self._app_slug})

            if result.deleted_count > 0:
                # Also remove edges pointing to this node from other nodes
                self.collection.update_many(
                    {"app_slug": self._app_slug, "edges.target": node_id},
                    {"$pull": {"edges": {"target": node_id}}},
                )
                logger.info(f"Deleted node: {node_id}")
                return True

            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to delete node {node_id}: {e}")
            return False

    def list_nodes(
        self,
        node_type: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List nodes with optional filtering."""
        query: dict[str, Any] = {"app_slug": self._app_slug}

        if node_type:
            query["type"] = node_type
        if user_id:
            query["user_id"] = str(user_id)

        try:
            return list(self.collection.find(query).sort("updated_at", DESCENDING).limit(limit))
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to list nodes: {e}")
            return []

    # ========================================================================
    # Edge Operations
    # ========================================================================

    def add_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
        weight: float = 1.0,
        active: bool = True,
    ) -> bool:
        """Add an edge (relationship) between two nodes."""
        if not self._enabled:
            return False

        now = datetime.now(timezone.utc)

        edge_doc = {
            "relation": relation,
            "target": target_id,
            "weight": max(0.0, min(1.0, weight)),
            "active": active,
            "created_at": now,
            "updated_at": now,
        }

        if properties:
            edge_doc["properties"] = properties

        try:
            # First, try to update existing edge
            result = self.collection.update_one(
                {
                    "_id": source_id,
                    "app_slug": self._app_slug,
                    "edges": {
                        "$elemMatch": {
                            "relation": relation,
                            "target": target_id,
                        }
                    },
                },
                {
                    "$set": {
                        "edges.$.weight": edge_doc["weight"],
                        "edges.$.active": edge_doc["active"],
                        "edges.$.updated_at": now,
                        **({"edges.$.properties": properties} if properties else {}),
                    }
                },
            )

            if result.modified_count > 0:
                logger.debug(f"Updated edge: {source_id} --{relation}--> {target_id}")
                return True

            # Edge doesn't exist, add it
            result = self.collection.update_one(
                {"_id": source_id, "app_slug": self._app_slug},
                {
                    "$push": {"edges": edge_doc},
                    "$set": {"updated_at": now},
                },
            )

            if result.modified_count > 0:
                logger.info(f"Added edge: {source_id} --{relation}--> {target_id}")
                return True

            # Source node doesn't exist, cannot add edge
            logger.warning(f"Source node {source_id} not found, cannot add edge")
            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to add edge: {e}")
            return False

    def remove_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """Remove an edge between two nodes."""
        if not self._enabled:
            return False

        try:
            result = self.collection.update_one(
                {"_id": source_id, "app_slug": self._app_slug},
                {
                    "$pull": {
                        "edges": {
                            "relation": relation,
                            "target": target_id,
                        }
                    },
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                },
            )

            if result.modified_count > 0:
                logger.info(f"Removed edge: {source_id} --{relation}--> {target_id}")
                return True

            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to remove edge: {e}")
            return False

    def update_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """Update an existing edge's properties."""
        if not self._enabled:
            return False

        now = datetime.now(timezone.utc)
        set_ops: dict[str, Any] = {"edges.$.updated_at": now}

        if "weight" in updates:
            set_ops["edges.$.weight"] = max(0.0, min(1.0, updates["weight"]))
        if "active" in updates:
            set_ops["edges.$.active"] = updates["active"]
        if "properties" in updates:
            set_ops["edges.$.properties"] = updates["properties"]

        try:
            result = self.collection.update_one(
                {
                    "_id": source_id,
                    "app_slug": self._app_slug,
                    "edges": {
                        "$elemMatch": {
                            "relation": relation,
                            "target": target_id,
                        }
                    },
                },
                {"$set": set_ops},
            )

            if result.modified_count > 0:
                logger.debug(f"Updated edge: {source_id} --{relation}--> {target_id}")
                return True

            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to update edge: {e}")
            return False

    def deactivate_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """Mark an edge as inactive (soft delete)."""
        return self.update_edge(source_id, relation, target_id, {"active": False})

    # ========================================================================
    # Graph Traversal
    # ========================================================================

    def traverse(
        self,
        start_id: str,
        max_depth: int | None = None,
        relation_filter: list[str] | None = None,
        include_inactive: bool = False,
        include_start: bool = True,
    ) -> list[dict[str, Any]]:
        """Perform graph traversal using MongoDB's $graphLookup."""
        if not self._enabled:
            return []

        if max_depth is None:
            max_depth = self.default_max_depth

        # Build the $graphLookup stage
        graph_lookup: dict[str, Any] = {
            "$graphLookup": {
                "from": self.collection.name,
                "startWith": "$edges.target",
                "connectFromField": "edges.target",
                "connectToField": "_id",
                "as": "network",
                "maxDepth": max_depth - 1,  # $graphLookup depth is 0-indexed
                "depthField": "hop_distance",
                "restrictSearchWithMatch": {"app_slug": self._app_slug},
            }
        }

        # Build pipeline
        pipeline: list[dict[str, Any]] = [
            # Start from the given node
            {"$match": {"_id": start_id, "app_slug": self._app_slug}},
        ]

        # Filter edges before traversal if needed
        if relation_filter or not include_inactive:
            edge_filter: dict[str, Any] = {}
            if relation_filter:
                edge_filter["relation"] = {"$in": relation_filter}
            if not include_inactive:
                edge_filter["active"] = True

            pipeline.append(
                {
                    "$addFields": {
                        "edges": {
                            "$filter": {
                                "input": "$edges",
                                "as": "edge",
                                "cond": {
                                    "$and": [
                                        {"$in": ["$$edge.relation", relation_filter]}
                                        if relation_filter
                                        else True,
                                        {"$eq": ["$$edge.active", True]}
                                        if not include_inactive
                                        else True,
                                    ]
                                }
                                if relation_filter or not include_inactive
                                else True,
                            }
                        }
                    }
                }
            )

        # Add graph lookup
        pipeline.append(graph_lookup)

        # Process results
        pipeline.extend(
            [
                # Add the start node to the network
                {
                    "$addFields": {
                        "network": {
                            "$concatArrays": [
                                [{"$mergeObjects": ["$$ROOT", {"hop_distance": 0}]}]
                                if include_start
                                else [],
                                "$network",
                            ]
                        }
                    }
                },
                # Unwind network
                {"$unwind": {"path": "$network", "preserveNullAndEmptyArrays": False}},
                # Group to dedupe
                {
                    "$group": {
                        "_id": "$network._id",
                        "node": {"$first": "$network"},
                        "hop_distance": {"$min": "$network.hop_distance"},
                    }
                },
                # Sort by distance
                {"$sort": {"hop_distance": 1}},
                # Clean up output
                {
                    "$project": {
                        "_id": 0,
                        "node": {
                            "_id": "$node._id",
                            "type": "$node.type",
                            "name": "$node.name",
                            "properties": "$node.properties",
                            "edges": "$node.edges",
                        },
                        "hop_distance": 1,
                    }
                },
            ]
        )

        try:
            results = list(self.collection.aggregate(pipeline))
            logger.info(
                f"Traversed from {start_id}: found {len(results)} nodes " f"(max_depth={max_depth})"
            )
            return results

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Graph traversal failed: {e}")
            return []

    def get_neighbors(
        self,
        node_id: str,
        relation: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Get immediate neighbors of a node (1-hop traversal)."""
        node = self.get_node(node_id)
        if not node:
            return []

        edges = node.get("edges", [])
        neighbors = []

        for edge in edges:
            if relation and edge.get("relation") != relation:
                continue
            if not include_inactive and not edge.get("active", True):
                continue

            target_node = self.get_node(edge["target"])
            if target_node:
                neighbors.append(
                    {
                        "node": target_node,
                        "relation": edge.get("relation"),
                        "weight": edge.get("weight", 1.0),
                        "properties": edge.get("properties", {}),
                    }
                )

        return neighbors

    def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Find a path between two nodes using BFS."""
        if start_id == end_id:
            return [start_id]

        # BFS using $graphLookup results
        traversed = self.traverse(start_id, max_depth=max_depth)

        # Build adjacency info from traversal
        for item in traversed:
            if item["node"]["_id"] == end_id:
                # Found! Now reconstruct path (simplified - returns start and end)
                # Full path reconstruction would require tracking parents
                return [start_id, end_id]

        return None

    # ========================================================================
    # Hybrid Search (Vector + Graph)
    # ========================================================================

    def hybrid_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int | None = None,
        vector_limit: int = 5,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """Hybrid GraphRAG: Vector search finds entry points, graph traversal expands context."""
        if not self._enabled:
            return {"entry_nodes": [], "graph_context": [], "total_nodes": 0}

        if max_depth is None:
            max_depth = self.default_max_depth

        # Step 1: Get embedding for query
        embedding = self._get_embedding_sync(query)
        if not embedding:
            logger.warning(f"No embedding available for hybrid search query: {query}")
            return {"entry_nodes": [], "graph_context": [], "total_nodes": 0}

        # Step 2: Vector search to find entry nodes
        vector_filter: dict[str, Any] = {"app_slug": self._app_slug}
        if user_id:
            vector_filter["user_id"] = str(user_id)

        entry_pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": self.vector_index_name,
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": vector_limit * 10,
                    "limit": vector_limit,
                    "filter": vector_filter,
                }
            },
            {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
            {
                "$project": {
                    "_id": 1,
                    "type": 1,
                    "name": 1,
                    "properties": 1,
                    "edges": 1,
                    "similarity": 1,
                }
            },
        ]

        try:
            entry_nodes = list(self.collection.aggregate(entry_pipeline))
            logger.info(f"Vector search found {len(entry_nodes)} entry nodes")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Vector search failed: {e}")
            entry_nodes = []

        # Step 3: Graph traversal from each entry node
        graph_context: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for entry_node in entry_nodes:
            node_id = entry_node["_id"]
            seen_ids.add(node_id)

            # Traverse from this entry point
            traversed = self.traverse(
                start_id=node_id,
                max_depth=max_depth,
                include_inactive=include_inactive,
                include_start=False,  # Don't duplicate entry nodes
            )

            for item in traversed:
                item_id = item["node"]["_id"]
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    # Add entry node info for context
                    item["entry_node"] = node_id
                    item["entry_similarity"] = entry_node.get("similarity", 0)
                    graph_context.append(item)

        # Sort graph context by hop distance, then by entry similarity
        graph_context.sort(key=lambda x: (x["hop_distance"], -x.get("entry_similarity", 0)))

        logger.info(
            f"Hybrid search complete: {len(entry_nodes)} entry nodes, "
            f"{len(graph_context)} graph context nodes"
        )

        return {
            "entry_nodes": entry_nodes,
            "graph_context": graph_context,
            "total_nodes": len(seen_ids),
        }

    # ========================================================================
    # LLM-Based Graph Extraction
    # ========================================================================

    async def extract_graph_from_text(
        self,
        text: str,
        user_id: str,
        auto_create_nodes: bool = True,
    ) -> dict[str, Any]:
        """Extract entities and relationships from text using LLM."""
        if not self._enabled:
            return {"nodes_created": 0, "edges_created": 0, "extracted": None}

        if not self.auto_extract:
            logger.debug("Auto-extraction disabled")
            return {"nodes_created": 0, "edges_created": 0, "extracted": None}

        if not self.llm_service:
            logger.warning("LLM service not available for graph extraction")
            return {"nodes_created": 0, "edges_created": 0, "extracted": None}

        try:
            # Use LLM service's default model if llm_model not explicitly set
            model_to_use = self.llm_model
            if not model_to_use and self.llm_service:
                model_to_use = self.llm_service.llm_provider.default_model
                logger.debug(f"Using LLM service default model: {model_to_use}")

            # Call LLM for extraction using JSON mode (works with all providers)
            response = await self.llm_service.chat_completion(
                messages=[
                    {"role": "system", "content": GRAPH_EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": GRAPH_EXTRACTION_USER_PROMPT.format(
                            text=text,
                            user_id=user_id,
                        ),
                    },
                ],
                model=model_to_use,  # None means use LLM service default
                response_format={
                    "type": "json_object"
                },  # JSON mode - works with OpenAI, Azure, Gemini
                temperature=self.temperature,
            )

            # Parse JSON response manually (no Pydantic for cross-provider compatibility)
            import json

            extracted = json.loads(response)
            nodes = extracted.get("nodes", [])
            edges = extracted.get("edges", [])

            nodes_created = 0
            edges_created = 0

            if auto_create_nodes:
                # Create nodes
                for node in nodes:
                    # Replace "user" placeholder with actual user ID
                    node_id = node.get("id", "")
                    if node_id == "person:user":
                        node_id = f"person:{user_id}"

                    self.upsert_node(
                        node_id=node_id,
                        node_type=node.get("type", "concept"),
                        name=node.get("name", node_id),
                        properties=node.get("properties", {}),
                        user_id=user_id,
                    )
                    nodes_created += 1

                # Create edges
                for edge in edges:
                    # Replace "user" placeholder
                    source = edge.get("source", "")
                    target = edge.get("target", "")
                    if source == "person:user":
                        source = f"person:{user_id}"
                    if target == "person:user":
                        target = f"person:{user_id}"

                    # Ensure target node exists
                    if not self.get_node(target):
                        # Create a minimal target node
                        target_type = target.split(":")[0] if ":" in target else "concept"
                        target_name = target.split(":")[-1].replace("_", " ").title()
                        self.upsert_node(
                            node_id=target,
                            node_type=target_type,
                            name=target_name,
                            user_id=user_id,
                        )

                    if self.add_edge(
                        source_id=source,
                        relation=edge.get("relation", "related_to"),
                        target_id=target,
                        properties=edge.get("properties", {}),
                    ):
                        edges_created += 1

            logger.info(
                f"Graph extraction complete: {nodes_created} nodes, {edges_created} edges "
                f"from text: '{text[:50]}...'"
            )

            return {
                "nodes_created": nodes_created,
                "edges_created": edges_created,
                "extracted": {"nodes": nodes, "edges": edges},
            }

        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
        ) as e:
            logger.warning(f"Graph extraction failed: {e}")
            return {"nodes_created": 0, "edges_created": 0, "extracted": None, "error": str(e)}

    def extract_graph_from_memory(
        self,
        memory_text: str,
        user_id: str,
        auto_create_nodes: bool = True,
    ) -> dict[str, Any]:
        """
        Synchronous wrapper for extract_graph_from_text.

        This method is provided for compatibility with CognitiveMemoryService,
        which calls graph extraction from synchronous add() methods.

        Args:
            memory_text: The memory text to extract entities/relationships from
            user_id: The user ID for node ownership
            auto_create_nodes: Whether to automatically create extracted nodes/edges

        Returns:
            dict with nodes_created, edges_created, and extracted data
        """
        import asyncio
        import concurrent.futures

        async def _extract():
            return await self.extract_graph_from_text(
                text=memory_text,
                user_id=user_id,
                auto_create_nodes=auto_create_nodes,
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running inside an event loop (e.g., FastAPI)
                # Use thread pool to avoid blocking
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _extract())
                    return future.result(timeout=60)
            else:
                return loop.run_until_complete(_extract())
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(_extract())

    # ========================================================================
    # Context Formatting
    # ========================================================================

    def format_graph_context(
        self,
        hybrid_results: dict[str, Any],
        max_nodes: int = 10,
        include_edges: bool = True,
    ) -> str:
        """Format hybrid search results as a context string for LLM prompts."""
        if not hybrid_results.get("entry_nodes") and not hybrid_results.get("graph_context"):
            return ""

        lines = ["KNOWLEDGE GRAPH CONTEXT:"]

        # Add entry nodes
        for node in hybrid_results.get("entry_nodes", [])[:max_nodes]:
            node_str = f"- {node.get('name', node['_id'])} ({node.get('type', 'unknown')})"
            props = node.get("properties", {})
            if props:
                prop_str = ", ".join(f"{k}: {v}" for k, v in list(props.items())[:3])
                node_str += f" [{prop_str}]"
            lines.append(node_str)

            if include_edges:
                for edge in node.get("edges", [])[:5]:
                    if edge.get("active", True):
                        lines.append(f"  -> {edge['relation']} -> {edge['target']}")

        # Add graph context (related nodes)
        remaining = max_nodes - len(hybrid_results.get("entry_nodes", []))
        for item in hybrid_results.get("graph_context", [])[:remaining]:
            node = item["node"]
            distance = item.get("hop_distance", "?")
            node_name = node.get("name", node["_id"])
            node_type = node.get("type", "unknown")
            node_str = f"- {node_name} ({node_type}) [hop={distance}]"
            lines.append(node_str)

            if include_edges:
                for edge in node.get("edges", [])[:3]:
                    if edge.get("active", True):
                        lines.append(f"  -> {edge['relation']} -> {edge['target']}")

        return "\n".join(lines)

    # ========================================================================
    # Statistics
    # ========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get graph service statistics."""
        try:
            pipeline = [
                {"$match": {"app_slug": self._app_slug}},
                {
                    "$group": {
                        "_id": "$type",
                        "count": {"$sum": 1},
                        "edge_count": {"$sum": {"$size": {"$ifNull": ["$edges", []]}}},
                    }
                },
            ]

            type_stats = list(self.collection.aggregate(pipeline))

            total_nodes = sum(t["count"] for t in type_stats)
            total_edges = sum(t["edge_count"] for t in type_stats)

            return {
                "enabled": self._enabled,
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "nodes_by_type": {t["_id"]: t["count"] for t in type_stats},
                "app_slug": self._app_slug,
            }

        except (PyMongoError, OperationFailure) as e:
            return {"enabled": self._enabled, "error": str(e)}


# ============================================================================
# Factory Function
# ============================================================================


def get_graph_service(
    app_slug: str,
    collection: Any,
    config: dict[str, Any] | None = None,
    llm_service: LLMService | None = None,
    embedding_service: EmbeddingService | None = None,
) -> GraphService:
    """
    Factory function to create a GraphService.

    Args:
        app_slug: Application slug
        collection: PyMongo collection for graph nodes
        config: Graph service configuration from manifest
        llm_service: LLMService instance for entity extraction
        embedding_service: EmbeddingService instance for hybrid search

    Returns:
        Configured GraphService instance
    """
    return GraphService(
        app_slug=app_slug,
        collection=collection,
        config=config,
        llm_service=llm_service,
        embedding_service=embedding_service,
    )
