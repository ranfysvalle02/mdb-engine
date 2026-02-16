"""
Memory Versioning System
Track belief evolution over time.

This module implements memory versioning - tracking how beliefs change
over time, enabling "what I believed then vs now" queries.

Key principle: "Memories evolve, facts version"
"""

import logging
from datetime import datetime
from typing import Any

from ._async_compat import maybe_await as _maybe_await

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from ..core.validation import validate_user_id
from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class MemoryVersioningError(MemoryServiceError):
    """Base exception for Memory Versioning errors."""

    pass


class MemoryVersioning:
    """
    Manages memory versioning and history.

    Tracks how memories evolve over time, enabling:
    - "What I believed then vs now" queries
    - Confidence change tracking
    - Belief evolution analysis
    - Audit trail for memory changes

    Example:
        ```python
        from mdb_engine.memory.versioning import MemoryVersioning

        versioning = MemoryVersioning(collection=entity_collection)

        # Get version history
        history = await versioning.get_version_history(
            entity_name="user_preferences",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31)
        )

        # Get belief at a specific time
        belief = await versioning.get_belief_at_time(
            entity_name="user_preferences",
            timestamp=datetime(2024, 6, 15)
        )
        ```
    """

    def __init__(self, collection: Any):
        """
        Initialize MemoryVersioning manager.

        Args:
            collection: MongoDB collection with versioned memories
        """
        self.collection = collection

    async def get_version_history(
        self,
        entity_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        scope: str = "user",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get version history for an entity.

        Returns all versions of an entity's attributes over time.

        Args:
            entity_name: Entity identifier
            start_date: Optional start date filter
            end_date: Optional end date filter
            scope: Memory scope ("user", "family", "system")
            user_id: User ID if scope is "user"

        Returns:
            List of version history entries

        Example:
            ```python
            history = await versioning.get_version_history(
                entity_name="user_preferences",
                start_date=datetime(2024, 1, 1),
                end_date=datetime(2024, 12, 31)
            )
            ```
        """
        user_id = validate_user_id(user_id, allow_none=True)
        try:
            query = {
                "entity": entity_name.lower(),
                "scope": scope,
            }

            if user_id:
                query["user_id"] = str(user_id)

            entity = await _maybe_await(self.collection.find_one(query))
            if not entity:
                logger.debug(f"Entity not found: {entity_name}")
                return []

            # Get history array
            history = entity.get("history", [])

            # Filter by date range if provided
            if start_date or end_date:
                filtered_history = []
                for entry in history:
                    entry_time = entry.get("timestamp")
                    if isinstance(entry_time, str):
                        try:
                            entry_time = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            continue
                    elif not isinstance(entry_time, datetime):
                        continue

                    if start_date and entry_time < start_date:
                        continue
                    if end_date and entry_time > end_date:
                        continue

                    filtered_history.append(entry)

                return filtered_history

            return history

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get version history: {e}")
            return []

    async def get_belief_at_time(
        self,
        entity_name: str,
        timestamp: datetime,
        scope: str = "user",
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get what the system believed about an entity at a specific time.

        Args:
            entity_name: Entity identifier
            timestamp: Point in time to query
            scope: Memory scope
            user_id: User ID if scope is "user"

        Returns:
            Entity state at the specified time, or None if not found

        Example:
            ```python
            belief = await versioning.get_belief_at_time(
                entity_name="user_preferences",
                timestamp=datetime(2024, 6, 15)
            )
            # Returns: {"theme": "dark", "language": "Python", "confidence": 0.8}
            ```
        """
        user_id = validate_user_id(user_id, allow_none=True)
        try:
            query = {
                "entity": entity_name.lower(),
                "scope": scope,
            }

            if user_id:
                query["user_id"] = str(user_id)

            entity = await _maybe_await(self.collection.find_one(query))
            if not entity:
                return None

            # Check if entity existed at that time
            created_at = entity.get("created_at")
            if created_at:
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass
                if isinstance(created_at, datetime) and timestamp < created_at:
                    return None  # Entity didn't exist yet

            # Get current state
            current_attrs = entity.get("attr", {})
            current_confidence = entity.get("confidence", 1.0)

            # Check history for changes before timestamp
            history = entity.get("history", [])
            for entry in reversed(history):  # Start from most recent
                entry_time = entry.get("timestamp")
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        continue
                elif not isinstance(entry_time, datetime):
                    continue

                if entry_time <= timestamp:
                    # This is the state at the requested time
                    return {
                        "attributes": entry.get("attributes", {}),
                        "confidence": entry.get("confidence", current_confidence),
                        "timestamp": entry_time,
                    }

            # No history entry found, return current state
            return {
                "attributes": current_attrs,
                "confidence": current_confidence,
                "timestamp": timestamp,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get belief at time: {e}")
            return None

    async def get_confidence_timeline(
        self,
        entity_name: str,
        scope: str = "user",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get confidence changes over time for an entity.

        Args:
            entity_name: Entity identifier
            scope: Memory scope
            user_id: User ID if scope is "user"

        Returns:
            List of confidence values with timestamps

        Example:
            ```python
            timeline = await versioning.get_confidence_timeline(
                entity_name="user_preferences"
            )
            # Returns: [
            #   {"confidence": 0.8, "timestamp": "2024-01-01"},
            #   {"confidence": 0.75, "timestamp": "2024-06-15"},
            #   {"confidence": 0.7, "timestamp": "2024-12-31"}
            # ]
            ```
        """
        user_id = validate_user_id(user_id, allow_none=True)
        try:
            history = await self.get_version_history(entity_name, scope=scope, user_id=user_id)

            timeline = []
            for entry in history:
                timeline.append(
                    {
                        "confidence": entry.get("confidence", 1.0),
                        "timestamp": entry.get("timestamp"),
                    }
                )

            # Sort by timestamp
            timeline.sort(key=lambda x: x.get("timestamp") or datetime.min)

            return timeline

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get confidence timeline: {e}")
            return []

    async def compare_versions(
        self,
        entity_name: str,
        timestamp1: datetime,
        timestamp2: datetime,
        scope: str = "user",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Compare entity state at two different times.

        Args:
            entity_name: Entity identifier
            timestamp1: First point in time
            timestamp2: Second point in time
            scope: Memory scope
            user_id: User ID if scope is "user"

        Returns:
            Dictionary with comparison results

        Example:
            ```python
            comparison = await versioning.compare_versions(
                entity_name="user_preferences",
                timestamp1=datetime(2024, 1, 1),
                timestamp2=datetime(2024, 12, 31)
            )
            # Returns: {
            #   "changed_attributes": ["theme"],
            #   "confidence_change": -0.1,
            #   "then": {"theme": "light", "confidence": 0.8},
            #   "now": {"theme": "dark", "confidence": 0.7}
            # }
            ```
        """
        user_id = validate_user_id(user_id, allow_none=True)
        try:
            belief1 = await self.get_belief_at_time(entity_name, timestamp1, scope, user_id)
            belief2 = await self.get_belief_at_time(entity_name, timestamp2, scope, user_id)

            if not belief1 or not belief2:
                raise MemoryVersioningError("Could not retrieve beliefs for one or both timestamps")

            attrs1 = belief1.get("attributes", {})
            attrs2 = belief2.get("attributes", {})

            # Find changed attributes
            changed_attributes = []
            for key in set(list(attrs1.keys()) + list(attrs2.keys())):
                if attrs1.get(key) != attrs2.get(key):
                    changed_attributes.append(key)

            confidence_change = belief2.get("confidence", 0.0) - belief1.get("confidence", 0.0)

            return {
                "changed_attributes": changed_attributes,
                "confidence_change": confidence_change,
                "then": {
                    "attributes": attrs1,
                    "confidence": belief1.get("confidence", 0.0),
                    "timestamp": timestamp1,
                },
                "now": {
                    "attributes": attrs2,
                    "confidence": belief2.get("confidence", 0.0),
                    "timestamp": timestamp2,
                },
            }

        except (PyMongoError, OperationFailure, ValueError) as e:
            raise MemoryVersioningError(f"Failed to compare versions: {e}") from e
