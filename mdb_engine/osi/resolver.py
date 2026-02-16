"""Post-extraction entity resolution against OSI definitions.

Takes LLM-extracted nodes, checks against OSI datasets by name/synonym
match, and re-maps types and IDs to align with the organization's
semantic model.

Example::

    # LLM extracts: organization:acme
    # OSI defines: dataset "customer" with synonym "company"
    # Result: customer:acme (re-typed to match OSI)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .registry import OsiModelRegistry

logger = logging.getLogger(__name__)


def resolve_entities(
    nodes: list[dict[str, Any]],
    registry: OsiModelRegistry,
) -> list[dict[str, Any]]:
    """Post-process extracted nodes using OSI definitions.

    For each node, check if its type or name matches an OSI dataset
    (by name or synonym). If a match is found:
    - Update the node type to the OSI dataset name
    - Update the node ID format to ``dataset_name:original_name``
    - Tag the node with ``_osi_dataset`` in properties

    Args:
        nodes: List of extracted node dicts from the LLM.
        registry: The loaded OSI model registry.

    Returns:
        The same list of nodes, potentially modified in-place.
    """
    resolved_count = 0

    for node in nodes:
        node_name = node.get("name", "")
        node_type = node.get("type", "")
        node_id = node.get("id", "")

        match = registry.match_entity(node_name, node_type)
        if match:
            ds_name = match.get("name", "")
            if ds_name and ds_name != node_type:
                # Re-map type to OSI dataset name
                old_type = node_type
                node["type"] = ds_name

                # Re-map ID: extract the instance name part and re-prefix
                if ":" in node_id:
                    instance_name = node_id.split(":", 1)[1]
                else:
                    instance_name = node_id
                node["id"] = f"{ds_name}:{instance_name}"

                # Tag with OSI metadata
                props = node.get("properties", {})
                props["_osi_dataset"] = ds_name
                props["_osi_original_type"] = old_type
                node["properties"] = props

                resolved_count += 1
                logger.debug(f"OSI entity resolution: {old_type}:{instance_name} -> " f"{ds_name}:{instance_name}")

    if resolved_count:
        logger.info(f"OSI entity resolution: resolved {resolved_count}/{len(nodes)} nodes")

    return nodes
