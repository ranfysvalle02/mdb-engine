"""Graph traversal mixin for GraphService."""

from __future__ import annotations

import logging
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger(__name__)


class TraversalMixin:
    """Mixin providing graph traversal operations ($graphLookup, BFS path-finding).

    Expects the composing class to provide:
        self.collection, self._app_slug, self._enabled,
        self.default_max_depth, self.get_node(), self.get_neighbors()
    """

    async def traverse(
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
                                        {"$in": ["$$edge.relation", relation_filter]} if relation_filter else True,
                                        {"$eq": ["$$edge.active", True]} if not include_inactive else True,
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
                                [{"$mergeObjects": ["$$ROOT", {"hop_distance": 0}]}] if include_start else [],
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
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=500)
            logger.info(f"Traversed from {start_id}: found {len(results)} nodes " f"(max_depth={max_depth})")
            return results

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Graph traversal failed: {e}")
            return []

    async def get_neighbors(
        self,
        node_id: str,
        relation: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Get immediate neighbors of a node (1-hop traversal).

        Uses a single batch ``$in`` query instead of fetching each
        target node individually.
        """
        node = await self.get_node(node_id)
        if not node:
            return []

        edges = node.get("edges", [])

        # Filter edges first
        filtered_edges = []
        for edge in edges:
            if relation and edge.get("relation") != relation:
                continue
            if not include_inactive and not edge.get("active", True):
                continue
            filtered_edges.append(edge)

        if not filtered_edges:
            return []

        # Batch-fetch all target nodes in a single query
        target_ids = [edge["target"] for edge in filtered_edges]
        cursor = self.collection.find({"_id": {"$in": target_ids}, "app_slug": self._app_slug})
        nodes_map: dict[str, dict[str, Any]] = {doc["_id"]: doc async for doc in cursor}

        # Build result in edge order
        neighbors = []
        for edge in filtered_edges:
            target_node = nodes_map.get(edge["target"])
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

    async def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """
        Find a path between two nodes using BFS with full path reconstruction.

        Stores depth alongside each node in the queue to avoid recomputing
        it via parent-chain traversal on every iteration.

        Args:
            start_id: Starting node ID
            end_id: Target node ID
            max_depth: Maximum traversal depth

        Returns:
            List of node IDs representing the path from start to end, or None if no path found
        """
        if start_id == end_id:
            return [start_id]

        from collections import deque

        # Each queue entry is (node_id, depth)
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        visited = {start_id}
        parent: dict[str, str | None] = {start_id: None}

        while queue:
            current_id, current_depth = queue.popleft()

            # Check if we've reached the target
            if current_id == end_id:
                # Reconstruct path by following parent pointers
                path: list[str] = []
                node: str | None = end_id
                while node is not None:
                    path.append(node)
                    node = parent.get(node)
                path.reverse()
                return path

            if current_depth >= max_depth:
                continue

            # Get neighbors of current node (uses batch $in query internally)
            neighbors = await self.get_neighbors(current_id, include_inactive=False)

            for neighbor_info in neighbors:
                neighbor_id = neighbor_info["node"]["_id"]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    parent[neighbor_id] = current_id
                    queue.append((neighbor_id, current_depth + 1))

        return None
