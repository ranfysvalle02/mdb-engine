"""Bidirectional mapping between OSI types (datasets) and MDB-Engine instances (nodes).

OSI defines type-level schemas (e.g., "all customers share these fields").
MDB-Engine stores instance-level entities (e.g., "person:alex is one specific person").
This mapper bridges the granularity gap.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def nodes_to_dataset_schema(
    nodes: list[dict[str, Any]],
    node_type: str,
) -> dict[str, Any]:
    """Aggregate instance-level nodes into a type-level OSI dataset definition.

    Infers fields from the union of all properties across nodes of the same type.

    Args:
        nodes: List of MDB-Engine node dicts (all same type).
        node_type: The node type to create a dataset for.

    Returns:
        An OSI-compatible dataset dict.
    """
    # Collect all property keys across all nodes of this type
    all_property_keys: set[str] = set()
    for node in nodes:
        for key in node.get("properties", {}):
            if not key.startswith("_"):  # Skip internal properties
                all_property_keys.add(key)

    # Build fields from property keys
    fields: list[dict[str, Any]] = []

    # Always include a name field first
    fields.append(
        {
            "name": f"{node_type}_name",
            "expression": {
                "dialects": [{"dialect": "ANSI_SQL", "expression": "name"}],
            },
            "ai_context": {"synonyms": [node_type, f"{node_type} name"]},
        }
    )

    for key in sorted(all_property_keys):
        fields.append(
            {
                "name": key,
                "expression": {
                    "dialects": [{"dialect": "ANSI_SQL", "expression": key}],
                },
            }
        )

    return {
        "name": node_type,
        "source": f"mdb_engine.{node_type}",
        "primary_key": [f"{node_type}_id"],
        "fields": fields,
        "ai_context": {
            "synonyms": [node_type],
            "instance_count": len(nodes),
            "sample_names": [n.get("name", "") for n in nodes[:5]],
        },
    }


def edges_to_relationships(
    nodes_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Discover OSI relationship definitions from edge patterns.

    Groups edges by (source_type, relation, target_type) and creates
    one OSI relationship definition per unique pattern.

    Args:
        nodes_by_type: Dict mapping node type -> list of node dicts.

    Returns:
        List of OSI-compatible relationship dicts.
    """
    from collections import defaultdict

    # Count edge patterns
    pattern_counts: dict[tuple[str, str, str], int] = defaultdict(int)

    for node_type, nodes in nodes_by_type.items():
        for node in nodes:
            for edge in node.get("edges", []):
                if not edge.get("active", True):
                    continue
                target_id = edge.get("target", "")
                target_type = target_id.split(":")[0] if ":" in target_id else "unknown"
                relation = edge.get("relation", "related_to")
                pattern_counts[(node_type, relation, target_type)] += 1

    # Build relationships
    relationships: list[dict[str, Any]] = []
    for (from_type, relation, to_type), count in pattern_counts.items():
        relationships.append(
            {
                "name": f"{from_type}_{relation}_{to_type}",
                "from": from_type,
                "to": to_type,
                "from_columns": [f"{from_type}_id"],
                "to_columns": [f"{to_type}_id"],
                "custom_extensions": [
                    {
                        "vendor_name": "MDB_ENGINE",
                        "data": f'{{"relation_type": "{relation}", "edge_count": {count}}}',
                    }
                ],
            }
        )

    return relationships
