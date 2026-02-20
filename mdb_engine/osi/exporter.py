"""Export MDB-Engine knowledge graph as OSI-compatible YAML.

Aggregates instance-level graph nodes into type-level OSI datasets,
synthesizes relationships from edge patterns, and generates ai_context
from community summaries.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.protocols import GraphServiceProtocol

logger = logging.getLogger(__name__)


class OsiExporter:
    """Exports a knowledge graph as an OSI semantic model.

    Aggregates instance-level graph nodes into type-level OSI datasets,
    discovers relationships from edge patterns, and generates ai_context
    from community summaries.
    """

    def __init__(
        self,
        app_slug: str,
        graph_service: GraphServiceProtocol,
    ):
        self._app_slug = app_slug
        self._graph_service = graph_service

    async def export(
        self,
        user_id: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Export the knowledge graph as an OSI semantic model.

        Args:
            user_id: Optional user scope (None = all users in app).
            model_name: Name for the exported model (default: app_slug_discovered).

        Returns:
            Dict representing an OSI semantic_model YAML structure.
        """
        model_name = model_name or f"{self._app_slug}_discovered"

        # Step 1: Gather all nodes grouped by type
        nodes_by_type = await self._gather_nodes_by_type(user_id)

        # Step 2: Synthesize datasets from node type groups
        from .mapper import nodes_to_dataset_schema

        datasets = []
        for node_type, nodes in nodes_by_type.items():
            datasets.append(nodes_to_dataset_schema(nodes, node_type))

        # Step 3: Discover relationships from edge patterns
        from .mapper import edges_to_relationships

        relationships = edges_to_relationships(nodes_by_type)

        # Step 4: Build metadata
        total_nodes = sum(len(n) for n in nodes_by_type.values())
        created_at = datetime.now(timezone.utc).isoformat()

        custom_data = json.dumps(
            {
                "source": "conversational_discovery",
                "app_slug": self._app_slug,
                "node_count": total_nodes,
                "type_count": len(nodes_by_type),
                "exported_at": created_at,
            }
        )

        # Step 5: Assemble OSI structure
        semantic_model: dict[str, Any] = {
            "semantic_model": [
                {
                    "name": model_name,
                    "description": (f"Semantic model discovered from conversations in '{self._app_slug}'"),
                    "ai_context": {
                        "description": "Discovered from conversational interaction via MDB-Engine",
                    },
                    "datasets": datasets,
                    "relationships": relationships,
                    "metrics": [],  # Metrics are discovered via concept nodes, not bulk export
                    "custom_extensions": [
                        {
                            "vendor_name": "MDB_ENGINE",
                            "data": custom_data,
                        }
                    ],
                }
            ]
        }

        logger.info(
            f"OSI export complete: {len(datasets)} datasets, "
            f"{len(relationships)} relationships, {total_nodes} total nodes"
        )

        return semantic_model

    async def _gather_nodes_by_type(
        self,
        user_id: str | None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch all nodes grouped by type."""
        nodes_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

        try:
            query: dict[str, Any] = {"app_slug": self._app_slug}
            if user_id:
                query["user_id"] = user_id

            cursor = self._graph_service.collection.find(query).limit(10000)
            all_nodes = await cursor.to_list(length=10000)

            for node in all_nodes:
                node_type = node.get("type", "unknown")
                nodes_by_type[node_type].append(node)

        except (AttributeError, TypeError, RuntimeError) as e:
            logger.warning(f"Failed to gather nodes for export: {e}")

        return dict(nodes_by_type)
