"""
Community Detection and Summary Generation for GraphRAG

This module implements hierarchical community detection using MongoDB aggregation
and generates LLM-powered community summaries following Microsoft Research's GraphRAG architecture.

Key Features:
- Hierarchical community detection (local → regional → global)
- Bottom-up community summary generation
- Community-based retrieval for Global Search
- Automatic community rebuilding on graph changes
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..embeddings.service import EmbeddingService
    from ..llm.service import LLMService

logger = logging.getLogger(__name__)

# Prompt safety — wraps user-derived content in XML delimiters
try:
    from ..core.prompt_safety import sanitize_for_prompt as _sanitize
except ImportError:
    _sanitize = None  # type: ignore[assignment]


def _safe(text: str, tag: str = "data") -> str:
    """Sanitize text for prompt interpolation if the module is available."""
    return _sanitize(text, tag=tag) if _sanitize else text


# PyMongo imports
try:
    from pymongo import ASCENDING, DESCENDING
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None


class CommunityService:
    """
    Service for community detection and summary generation.

    Implements GraphRAG's hierarchical community structure:
    - Local communities: Densely connected groups of nodes
    - Regional communities: Broader groupings of local communities
    - Global communities: Highest-level themes

    Communities are stored in a separate collection with summaries and embeddings
    for efficient retrieval during Global Search.
    """

    def __init__(
        self,
        app_slug: str,
        node_collection: Any,  # Graph nodes collection
        community_collection: Any,  # Community documents collection
        config: dict[str, Any] | None = None,
        llm_service: LLMService | None = None,
        embedding_service: EmbeddingService | None = None,
    ):
        """
        Initialize CommunityService.

        Args:
            app_slug: Application slug for scoping
            node_collection: Motor AsyncIOMotorCollection for graph nodes
            community_collection: Motor AsyncIOMotorCollection for community documents
            config: Configuration dictionary
            llm_service: LLMService for summary generation
            embedding_service: EmbeddingService for community embeddings
        """
        self.app_slug = app_slug
        self.node_collection = node_collection
        self.community_collection = community_collection
        self.config = config or {}
        self.llm_service = llm_service
        self.embedding_service = embedding_service

        # Configuration
        self.enabled = self.config.get("enabled", True)
        self.min_community_size = self.config.get("min_community_size", 2)
        self.max_community_size = self.config.get("max_community_size", 1000)
        self.rebuild_threshold = self.config.get("rebuild_threshold", 100)
        self.rebuild_interval_hours = self.config.get("rebuild_interval_hours", 24)

        # Indexes are created lazily on first async call via _ensure_ready()
        self._indexes_created = False

        if self.enabled:
            logger.info(
                f"CommunityService initialized: app_slug={app_slug}, "
                f"min_size={self.min_community_size}, max_size={self.max_community_size}"
            )

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first async call (Motor requires await)."""
        if self._indexes_created:
            return
        self._indexes_created = True
        try:
            # Index for level-based queries
            await self.community_collection.create_index(
                [("app_slug", ASCENDING), ("level", ASCENDING)],
                name="app_level_idx",
                background=True,
            )
            # Index for parent-child relationships
            await self.community_collection.create_index(
                [("app_slug", ASCENDING), ("parent_id", ASCENDING)],
                name="app_parent_idx",
                background=True,
            )
            # Index for node membership queries
            await self.community_collection.create_index(
                [("app_slug", ASCENDING), ("node_ids", ASCENDING)],
                name="app_nodes_idx",
                background=True,
            )
            # Vector index will be created separately for embeddings
            logger.debug("CommunityService indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to create CommunityService indexes: {e}")

    async def detect_communities(
        self,
        level: str = "local",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Detect communities at the specified level using MongoDB aggregation.

        This implements a Leiden-like algorithm using MongoDB aggregation pipelines:
        1. Build adjacency lists from edges
        2. Calculate node degrees and edge weights
        3. Group nodes by connectivity patterns
        4. Create hierarchical structure

        Args:
            level: Community level ("local", "regional", "global")
            user_id: Optional user ID to scope detection

        Returns:
            Dict with:
                - communities_created: Number of communities created
                - nodes_assigned: Number of nodes assigned to communities
                - stats: Community statistics
        """
        if not self.enabled:
            return {"communities_created": 0, "nodes_assigned": 0, "stats": {}}

        await self._ensure_ready()

        try:
            # Step 1: Get all nodes with edges
            node_query: dict[str, Any] = {
                "app_slug": self.app_slug,
                "edges": {"$exists": True, "$ne": []},
            }
            if user_id:
                node_query["user_id"] = str(user_id)

            # Step 2: Build adjacency information using aggregation
            # This creates a simplified community detection based on edge density

            # Pipeline to find connected components
            pipeline = [
                {"$match": node_query},
                # Unwind edges to get all connections
                {"$unwind": "$edges"},
                {"$match": {"edges.active": {"$ne": False}}},
                # Group by source node and collect neighbors
                {
                    "$group": {
                        "_id": "$_id",
                        "neighbors": {"$push": "$edges.target"},
                        "node_type": {"$first": "$type"},
                        "node_name": {"$first": "$name"},
                        "edge_count": {"$sum": 1},
                    }
                },
                # Calculate degree (number of connections)
                {
                    "$addFields": {
                        "degree": {"$size": "$neighbors"},
                    }
                },
                # Filter nodes with sufficient connections
                {"$match": {"degree": {"$gte": self.min_community_size - 1}}},
            ]

            cursor = self.node_collection.aggregate(pipeline)
            nodes_with_neighbors = await cursor.to_list(length=10_000)

            if not nodes_with_neighbors:
                logger.info("No nodes with sufficient connections for community detection")
                return {"communities_created": 0, "nodes_assigned": 0, "stats": {}}

            # Step 3: Group nodes into communities based on shared neighbors
            # This is a simplified approach - full Leiden would require iterative refinement
            communities: dict[str, list[str]] = {}
            node_to_community: dict[str, str] = {}
            community_counter = 0

            # Sort nodes by degree (highest first) to start with well-connected nodes
            nodes_with_neighbors.sort(key=lambda x: x.get("degree", 0), reverse=True)

            for node_data in nodes_with_neighbors:
                node_id = node_data["_id"]
                neighbors = set(node_data.get("neighbors", []))

                # Check if node already assigned
                if node_id in node_to_community:
                    continue

                # Find best matching community (most shared neighbors)
                best_community_id = None
                best_overlap = 0

                for comm_id, comm_nodes in communities.items():
                    # Get neighbors of community nodes
                    comm_neighbors = set()
                    for comm_node_id in comm_nodes[:10]:  # Limit check to first 10 nodes
                        comm_node = await self.node_collection.find_one(
                            {"_id": comm_node_id, "app_slug": self.app_slug}
                        )
                        if comm_node:
                            comm_edges = comm_node.get("edges", [])
                            comm_neighbors.update(e["target"] for e in comm_edges if e.get("active", True))

                    # Calculate overlap
                    overlap = len(neighbors & comm_neighbors)
                    if overlap > best_overlap and len(comm_nodes) < self.max_community_size:
                        best_overlap = overlap
                        best_community_id = comm_id

                # Create new community if no good match or overlap too low
                if best_community_id is None or best_overlap < 2:
                    community_id = f"{level}:{uuid.uuid4().hex[:8]}"
                    communities[community_id] = [node_id]
                    node_to_community[node_id] = community_id
                    community_counter += 1
                else:
                    # Add to existing community
                    communities[best_community_id].append(node_id)
                    node_to_community[node_id] = best_community_id

            # Step 4: Filter small communities and merge if needed
            filtered_communities = {}
            for comm_id, node_ids in communities.items():
                if len(node_ids) >= self.min_community_size:
                    filtered_communities[comm_id] = node_ids

            # Step 5: Store communities
            communities_created = 0
            nodes_assigned = 0

            for comm_id, node_ids in filtered_communities.items():
                # Determine parent relationship based on level.
                # After storing the community we update the child communities
                # so that their ``parent_id`` points back to this one.
                parent_id = None  # parent_id on self stays None; children get updated below

                # Create community document
                community_doc = {
                    "_id": comm_id,
                    "level": level,
                    "community_id": comm_id.split(":")[-1],
                    "parent_id": parent_id,
                    "node_ids": node_ids,
                    "summary": "",  # Will be generated later
                    "properties": {
                        "size": len(node_ids),
                        "density": await self._calculate_density(node_ids),
                        "modularity": 0.0,  # Simplified - would need full calculation
                    },
                    "app_slug": self.app_slug,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }

                # Upsert community
                await self.community_collection.update_one(
                    {"_id": comm_id, "app_slug": self.app_slug},
                    {"$set": community_doc},
                    upsert=True,
                )
                communities_created += 1
                nodes_assigned += len(node_ids)

                # Link child communities at the lower level to this parent.
                # Regional communities parent local ones; global parents regional.
                child_level = {"regional": "local", "global": "regional"}.get(level)
                if child_level:
                    await self.community_collection.update_many(
                        {
                            "app_slug": self.app_slug,
                            "level": child_level,
                            "node_ids": {"$in": node_ids},
                        },
                        {"$set": {"parent_id": comm_id}},
                    )

            logger.info(
                f"Community detection complete: {communities_created} {level} communities, "
                f"{nodes_assigned} nodes assigned"
            )

            return {
                "communities_created": communities_created,
                "nodes_assigned": nodes_assigned,
                "stats": {
                    "level": level,
                    "avg_size": nodes_assigned / communities_created if communities_created > 0 else 0,
                    "max_size": max((len(nodes) for nodes in filtered_communities.values()), default=0),
                    "min_size": min((len(nodes) for nodes in filtered_communities.values()), default=0),
                },
            }

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Community detection failed: {e}")
            return {"communities_created": 0, "nodes_assigned": 0, "stats": {}, "error": str(e)}

    async def _calculate_density(self, node_ids: list[str]) -> float:
        """Calculate community density (ratio of actual edges to possible edges)."""
        if len(node_ids) < 2:
            return 0.0

        try:
            # Count actual edges within community
            edge_count = 0
            for node_id in node_ids[:100]:  # Limit for performance
                node = await self.node_collection.find_one({"_id": node_id, "app_slug": self.app_slug})
                if node:
                    edges = node.get("edges", [])
                    edge_count += sum(1 for e in edges if e.get("target") in node_ids and e.get("active", True))

            # Possible edges = n * (n-1) / 2 (undirected)
            possible_edges = len(node_ids) * (len(node_ids) - 1) / 2
            if possible_edges == 0:
                return 0.0

            return min(1.0, edge_count / possible_edges)
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to calculate density: {e}")
            return 0.0

    async def get_community(self, community_id: str) -> dict[str, Any] | None:
        """Get a community by ID."""
        try:
            return await self.community_collection.find_one({"_id": community_id, "app_slug": self.app_slug})
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get community {community_id}: {e}")
            return None

    async def list_communities(
        self,
        level: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List communities with optional filtering."""
        query: dict[str, Any] = {"app_slug": self.app_slug}
        if level:
            query["level"] = level

        try:
            cursor = self.community_collection.find(query).sort("properties.size", DESCENDING).limit(limit)
            return await cursor.to_list(length=None)
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to list communities: {e}")
            return []

    async def get_communities_for_nodes(
        self,
        node_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Get all communities that contain any of the given nodes."""
        try:
            cursor = self.community_collection.find(
                {
                    "app_slug": self.app_slug,
                    "node_ids": {"$in": node_ids},
                }
            )
            return await cursor.to_list(length=500)
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get communities for nodes: {e}")
            return []

    async def generate_community_summaries(
        self,
        level: str = "local",
        regenerate: bool = False,
    ) -> dict[str, Any]:
        """
        Generate summaries for communities at the specified level.

        Implements bottom-up summarization:
        - Entity summaries → Local community summaries
        - Local summaries → Regional summaries
        - Regional summaries → Global summaries

        Args:
            level: Community level to summarize
            regenerate: Whether to regenerate existing summaries

        Returns:
            Dict with summary generation statistics
        """
        if not self.enabled or not self.llm_service:
            logger.warning("Community summary generation disabled or LLM service not available")
            return {"summaries_generated": 0, "errors": 0}

        await self._ensure_ready()

        try:
            # Get communities at this level
            communities = await self.list_communities(level=level, limit=1000)

            if not communities:
                logger.info(f"No communities found at level {level}")
                return {"summaries_generated": 0, "errors": 0}

            summaries_generated = 0
            errors = 0

            for community in communities:
                community_id = community["_id"]

                # Skip if summary exists and not regenerating
                if community.get("summary") and not regenerate:
                    continue

                try:
                    summary = await self._summarize_community(community, level)

                    if summary:
                        # Generate embedding for summary
                        embedding = None
                        if self.embedding_service:
                            try:
                                embeddings = await self.embedding_service.embed([summary])
                                if embeddings and len(embeddings) > 0:
                                    embedding = embeddings[0]
                            except (ConnectionError, TimeoutError, ValueError) as e:
                                logger.warning(f"Failed to generate embedding for community {community_id}: {e}")

                        # Update community document
                        update_doc: dict[str, Any] = {
                            "summary": summary,
                            "updated_at": datetime.now(timezone.utc),
                        }
                        if embedding:
                            update_doc["embedding"] = embedding

                        await self.community_collection.update_one(
                            {"_id": community_id, "app_slug": self.app_slug},
                            {"$set": update_doc},
                        )
                        summaries_generated += 1
                    else:
                        errors += 1

                except (ConnectionError, TimeoutError, ValueError, PyMongoError) as e:
                    logger.warning(f"Failed to generate summary for community {community_id}: {e}")
                    errors += 1

            logger.info(
                f"Community summary generation complete: {summaries_generated} summaries, "
                f"{errors} errors at level {level}"
            )

            return {
                "summaries_generated": summaries_generated,
                "errors": errors,
                "level": level,
            }

        except (PyMongoError, OperationFailure, ConnectionError, TimeoutError, ValueError) as e:
            logger.exception(f"Community summary generation failed: {e}")
            return {"summaries_generated": 0, "errors": 1, "error": str(e)}

    async def _summarize_community(
        self,
        community: dict[str, Any],
        level: str,
    ) -> str | None:
        """
        Generate a summary for a single community.

        Args:
            community: Community document
            level: Community level

        Returns:
            Generated summary text or None if failed
        """
        if not self.llm_service:
            return None

        try:
            from .prompts import (
                COMMUNITY_SUMMARY_SYSTEM_PROMPT,
                GLOBAL_COMMUNITY_SUMMARY_PROMPT,
                LOCAL_COMMUNITY_SUMMARY_PROMPT,
                REGIONAL_COMMUNITY_SUMMARY_PROMPT,
            )

            node_ids = community.get("node_ids", [])

            if level == "local":
                # Generate entity summaries first
                entity_summaries = []
                for node_id in node_ids[:20]:  # Limit for performance
                    entity_summary = await self._summarize_entity(node_id)
                    if entity_summary:
                        entity_summaries.append(entity_summary)

                if not entity_summaries:
                    return None

                # Generate local community summary (sanitize user-derived content)
                prompt = LOCAL_COMMUNITY_SUMMARY_PROMPT.format(
                    size=len(node_ids),
                    entity_summaries="\n".join(f"- {_safe(s, tag='entity_summary')}" for s in entity_summaries[:10]),
                )

            elif level == "regional":
                # Get child local communities that belong to this regional community
                community_id = community["_id"]
                child_cursor = self.community_collection.find(
                    {
                        "app_slug": self.app_slug,
                        "level": "local",
                        "parent_id": community_id,
                    }
                )
                local_communities = await child_cursor.to_list(length=100)
                local_summaries = [c.get("summary", "") for c in local_communities[:10] if c.get("summary")]

                if not local_summaries:
                    return None

                prompt = REGIONAL_COMMUNITY_SUMMARY_PROMPT.format(
                    size=len(local_summaries),
                    local_summaries="\n".join(f"- {_safe(s, tag='community_summary')}" for s in local_summaries),
                )

            elif level == "global":
                # Get child regional communities that belong to this global community
                community_id = community["_id"]
                child_cursor = self.community_collection.find(
                    {
                        "app_slug": self.app_slug,
                        "level": "regional",
                        "parent_id": community_id,
                    }
                )
                regional_communities = await child_cursor.to_list(length=100)
                regional_summaries = [c.get("summary", "") for c in regional_communities[:10] if c.get("summary")]

                if not regional_summaries:
                    return None

                prompt = GLOBAL_COMMUNITY_SUMMARY_PROMPT.format(
                    size=len(regional_summaries),
                    regional_summaries="\n".join(f"- {_safe(s, tag='community_summary')}" for s in regional_summaries),
                )
            else:
                logger.warning(f"Unknown community level: {level}")
                return None

            # Call LLM for summary generation
            provider_name = "chat"  # Use default chat provider
            response = await self.llm_service.chat_completion(
                provider_name=provider_name,
                messages=[
                    {"role": "system", "content": COMMUNITY_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,  # Lower temperature for more consistent summaries
            )

            return response.strip() if response else None

        except (ConnectionError, TimeoutError, ValueError, ImportError) as e:
            logger.warning(f"Failed to summarize community: {e}")
            return None

    async def _summarize_entity(self, node_id: str) -> str | None:
        """Generate a summary for an individual entity/node."""
        if not self.llm_service:
            return None

        try:
            # Get node data
            node = await self.node_collection.find_one({"_id": node_id, "app_slug": self.app_slug})

            if not node:
                return None

            from .prompts import COMMUNITY_SUMMARY_SYSTEM_PROMPT, ENTITY_SUMMARY_PROMPT

            # Format relationships
            edges = node.get("edges", [])
            relationships = []
            for edge in edges[:10]:  # Limit for performance
                if edge.get("active", True):
                    target = edge.get("target", "")
                    relation = edge.get("relation", "")
                    relationships.append(f"{relation} → {target}")

            prompt = ENTITY_SUMMARY_PROMPT.format(
                entity_id=_safe(str(node_id), tag="entity_id"),
                entity_type=_safe(str(node.get("type", "unknown")), tag="entity_type"),
                entity_name=_safe(str(node.get("name", node_id)), tag="entity_name"),
                properties=_safe(str(node.get("properties", {})), tag="entity_props"),
                relationships=_safe(
                    ", ".join(relationships) if relationships else "None",
                    tag="entity_rels",
                ),
            )

            # Call LLM for summary
            provider_name = "chat"
            response = await self.llm_service.chat_completion(
                provider_name=provider_name,
                messages=[
                    {"role": "system", "content": COMMUNITY_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )

            return response.strip() if response else None

        except (
            PyMongoError,
            OperationFailure,
            ConnectionError,
            TimeoutError,
            ValueError,
            ImportError,
        ) as e:
            logger.debug(f"Failed to summarize entity {node_id}: {e}")
            return None


def get_community_service(
    app_slug: str,
    node_collection: Any,
    community_collection: Any,
    config: dict[str, Any] | None = None,
    llm_service: LLMService | None = None,
    embedding_service: EmbeddingService | None = None,
) -> CommunityService:
    """
    Factory function to create a CommunityService.

    Args:
        app_slug: Application slug
        node_collection: Motor AsyncIOMotorCollection for graph nodes
        community_collection: Motor AsyncIOMotorCollection for community documents
        config: Configuration dictionary
        llm_service: LLMService for summary generation
        embedding_service: EmbeddingService for community embeddings

    Returns:
        Configured CommunityService instance
    """
    return CommunityService(
        app_slug=app_slug,
        node_collection=node_collection,
        community_collection=community_collection,
        config=config,
        llm_service=llm_service,
        embedding_service=embedding_service,
    )
