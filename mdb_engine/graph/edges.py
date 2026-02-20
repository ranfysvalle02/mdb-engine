"""Edge operations mixin for GraphService."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from ..core.validation import validate_node_id

logger = logging.getLogger(__name__)


class EdgeOperationsMixin:
    """Mixin providing edge (relationship) CRUD operations.

    Expects the composing class to provide:
        self.collection, self._app_slug, self._enabled
    """

    async def add_edge(
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

        validate_node_id(source_id)
        validate_node_id(target_id)

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
            result = await self.collection.update_one(
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
            result = await self.collection.update_one(
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

    async def remove_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """Remove an edge between two nodes."""
        if not self._enabled:
            return False

        try:
            result = await self.collection.update_one(
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

    async def update_edge(
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
            result = await self.collection.update_one(
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

    async def deactivate_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """Mark an edge as inactive (soft delete)."""
        return await self.update_edge(source_id, relation, target_id, {"active": False})
