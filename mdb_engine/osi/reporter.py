"""Discovery report generation: surface graph entities not yet in OSI.

Compares the knowledge graph against loaded OSI definitions and identifies
unmatched patterns that analysts may want to formalize as governed models.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.protocols import GraphServiceProtocol
    from .registry import OsiModelRegistry

logger = logging.getLogger(__name__)


class OsiDiscoveryReporter:
    """Generate reports of discovered entities/relationships not in OSI models.

    Compares the knowledge graph against loaded OSI definitions and
    surfaces unmatched patterns for analyst review.
    """

    def __init__(
        self,
        graph_service: GraphServiceProtocol,
        registry: OsiModelRegistry,
    ):
        self._graph_service = graph_service
        self._registry = registry

    async def generate_report(self) -> dict[str, Any]:
        """Generate a discovery report.

        Returns:
            Dict with:
            - new_entity_types: Node types with no matching OSI dataset
            - new_relationships: Edge patterns with no matching OSI relationship
            - new_properties: Property keys not in any OSI field definition
            - provisional_concepts: Concept nodes awaiting review
            - stats: Summary statistics
        """
        report: dict[str, Any] = {
            "new_entity_types": [],
            "new_relationships": [],
            "new_properties": [],
            "provisional_concepts": [],
            "stats": {},
        }

        try:
            # Get graph stats
            stats = await self._graph_service.get_stats()
            graph_types = set(stats.get("nodes_by_type", {}).keys())
            osi_dataset_names = {d.get("name", "").lower() for d in self._registry.list_datasets()}

            # Find new entity types
            for node_type in sorted(graph_types):
                if node_type.lower() not in osi_dataset_names:
                    count = stats.get("nodes_by_type", {}).get(node_type, 0)
                    report["new_entity_types"].append(
                        {
                            "type": node_type,
                            "instance_count": count,
                            "confidence": min(0.5 + count * 0.05, 0.95),
                            "suggestion": f"Consider adding a '{node_type}' dataset to your OSI model",
                        }
                    )

            # Find provisional concepts (pending review)
            try:
                query = {
                    "type": "concept",
                    "properties.is_definition": True,
                    "properties.status": "provisional",
                }
                cursor = self._graph_service.collection.find(query).limit(50)
                concepts = await cursor.to_list(length=50)

                for concept in concepts:
                    props = concept.get("properties", {})
                    report["provisional_concepts"].append(
                        {
                            "id": concept.get("_id"),
                            "name": concept.get("name"),
                            "definition": props.get("definition"),
                            "confidence": props.get("confidence"),
                            "has_yaml": bool(props.get("osi_yaml")),
                        }
                    )
            except (AttributeError, TypeError) as e:
                logger.debug("Could not iterate provisional concepts: %s", e)

            # Summary stats
            report["stats"] = {
                "total_graph_types": len(graph_types),
                "total_osi_datasets": len(osi_dataset_names),
                "unmatched_types": len(report["new_entity_types"]),
                "provisional_concepts": len(report["provisional_concepts"]),
            }

        except (AttributeError, TypeError, RuntimeError) as e:
            logger.warning(f"Failed to generate discovery report: {e}")
            report["error"] = str(e)

        return report
