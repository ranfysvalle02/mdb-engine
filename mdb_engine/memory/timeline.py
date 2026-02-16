"""
Timeline Service
Manages timeline/multiverse branching for counterfactual reasoning.

This service enables the Cognitive Operating System's "multiverse" capability,
allowing memories to exist in parallel timelines for counterfactual reasoning,
self-debugging, and hypothetical scenario exploration.

Key Features:
- Timeline creation and management
- Timeline ancestry resolution (hierarchical inheritance)
- Timeline forking (branching realities)
- App-scoped isolation

Schema:
{
  "_id": "timeline_id",
  "name": "Display Name",
  "parent": "parent_timeline_id" | null,
  "created_at": ISODate,
  "app_slug": "app_slug",
  "user_id": "user_id"
}
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

try:
    from pymongo.errors import OperationFailure, PyMongoError
except ImportError:
    raise ImportError("Missing critical dependencies. Please install: pip install pymongo") from None

logger = logging.getLogger(__name__)


class TimelineService:
    """
    Manages timeline/multiverse branching for counterfactual reasoning.

    Enables parallel memory timelines where memories can exist in different
    "realities" while maintaining inheritance from parent timelines.

    All methods are async. Root timeline is lazily initialized on first use.

    Example:
        ```python
        timeline_service = TimelineService(timelines_collection)

        # Create root timeline (auto-created on first use)
        await timeline_service.ensure_initialized()

        # Fork a new timeline
        branch_id = await timeline_service.fork_timeline("root", "What if I quit my job?", "user123")

        # Get timeline ancestry (for inheritance)
        ancestry = await timeline_service.get_timeline_ancestry(branch_id)
        # Returns: ["branch_abc123", "root"]
        ```
    """

    def __init__(self, collection: "ScopedCollectionWrapper"):
        """
        Initialize Timeline Service.

        Args:
            collection: ScopedCollectionWrapper for timelines collection
        """
        self.collection = collection
        self._initialized = False

    async def ensure_initialized(self) -> None:
        """Ensure root timeline exists (lazy async initialization)."""
        if self._initialized:
            return
        try:
            root_exists = await self.collection.find_one({"_id": "root"})
            if not root_exists:
                await self.create_timeline("root", "Objective Reality", None)
                logger.info("Created root timeline")
            self._initialized = True
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to ensure root timeline: {e}")

    async def create_timeline(
        self,
        timeline_id: str,
        name: str,
        parent: str | None = None,
        app_slug: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new timeline.

        Args:
            timeline_id: Unique identifier for the timeline (e.g., "root", "branch_abc123")
            name: Display name for the timeline
            parent: Parent timeline ID (None for root timeline)
            app_slug: Application slug (optional, auto-injected by ScopedCollectionWrapper)
            user_id: User ID (optional, for user-specific timelines)

        Returns:
            Created timeline document
        """
        try:
            timeline_doc = {
                "_id": timeline_id,
                "name": name,
                "parent": parent,
                "created_at": datetime.now(timezone.utc),
            }

            if app_slug:
                timeline_doc["app_slug"] = app_slug
            if user_id:
                timeline_doc["user_id"] = str(user_id)

            existing = await self.collection.find_one({"_id": timeline_id})
            if existing:
                await self.collection.update_one({"_id": timeline_id}, {"$set": timeline_doc})
            else:
                await self.collection.insert_one(timeline_doc)

            logger.info(f"Created timeline: {timeline_id} (parent: {parent})")
            return timeline_doc

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to create timeline {timeline_id}: {e}")
            raise

    async def get_timeline_ancestry(self, timeline_id: str) -> list[str]:
        """
        Get timeline ancestry chain (for inheritance).

        Returns list of timeline IDs from current to root, e.g.:
        ["branch_abc123", "branch_xyz", "root"]

        This enables hierarchical lookup: memories in child timelines
        inherit from parent timelines if not found locally.

        Args:
            timeline_id: Timeline ID to get ancestry for

        Returns:
            List of timeline IDs from current to root (inclusive)
        """
        await self.ensure_initialized()

        ancestry = []
        current_id = timeline_id

        # Traverse up the parent chain
        max_depth = 100  # Prevent infinite loops
        depth = 0

        while current_id and depth < max_depth:
            ancestry.append(current_id)

            # Get parent timeline
            timeline_doc = await self.collection.find_one({"_id": current_id})
            if not timeline_doc:
                break

            parent = timeline_doc.get("parent")
            if not parent or parent == current_id:  # Prevent self-reference loops
                break

            current_id = parent
            depth += 1

        # Ensure root is included if not already
        if ancestry and ancestry[-1] != "root":
            root_exists = await self.collection.find_one({"_id": "root"})
            if root_exists and "root" not in ancestry:
                ancestry.append("root")

        logger.debug(f"Timeline ancestry for {timeline_id}: {ancestry}")
        return ancestry

    async def fork_timeline(
        self,
        current_timeline: str,
        new_name: str,
        user_id: str | None = None,
        app_slug: str | None = None,
    ) -> str:
        """
        Fork a new timeline branching from the current one.

        Creates a new timeline that inherits from the current timeline.
        New memories can be added to this branch for counterfactual reasoning.

        Args:
            current_timeline: Current timeline ID to fork from
            new_name: Display name for the new timeline
            user_id: User ID (optional, for user-specific forks)
            app_slug: Application slug (optional, auto-injected)

        Returns:
            New timeline ID (format: "branch_{uuid}")
        """
        new_timeline_id = f"branch_{str(uuid.uuid4())[:8]}"

        try:
            await self.create_timeline(
                timeline_id=new_timeline_id,
                name=new_name,
                parent=current_timeline,
                app_slug=app_slug,
                user_id=user_id,
            )

            logger.info(f"Forked timeline: {current_timeline} → {new_timeline_id} ({new_name})")
            return new_timeline_id

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to fork timeline: {e}")
            raise

    async def get_timeline(self, timeline_id: str) -> dict[str, Any] | None:
        """
        Get timeline document by ID.

        Args:
            timeline_id: Timeline ID to retrieve

        Returns:
            Timeline document or None if not found
        """
        try:
            return await self.collection.find_one({"_id": timeline_id})
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get timeline {timeline_id}: {e}")
            return None

    async def list_timelines(
        self,
        user_id: str | None = None,
        parent: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List timelines with optional filtering.

        Args:
            user_id: Filter by user ID (optional)
            parent: Filter by parent timeline ID (optional)

        Returns:
            List of timeline documents
        """
        try:
            query = {}
            if user_id:
                query["user_id"] = str(user_id)
            if parent is not None:
                query["parent"] = parent

            cursor = self.collection.find(query)
            docs = await cursor.to_list(length=None)
            return docs
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to list timelines: {e}")
            return []

    # ------------------------------------------------------------------
    # Active timeline tracking (per-user)
    # ------------------------------------------------------------------

    @staticmethod
    def _active_key(user_id: str) -> str:
        """Return the document ``_id`` used to store a user's active timeline."""
        return f"active:{user_id}"

    async def get_active_timeline(self, user_id: str) -> str:
        """
        Get the user's currently active timeline.

        Falls back to ``"root"`` if no active timeline has been set.

        Args:
            user_id: User ID

        Returns:
            Timeline ID (default: ``"root"``)
        """
        try:
            doc = await self.collection.find_one({"_id": self._active_key(user_id)})
            if doc:
                return doc.get("timeline_id", "root")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get active timeline for user {user_id}: {e}")
        return "root"

    async def set_active_timeline(self, user_id: str, timeline_id: str) -> None:
        """
        Persist the user's active timeline choice.

        Args:
            user_id: User ID
            timeline_id: Timeline ID to set as active

        Raises:
            PyMongoError: If the database operation fails
        """
        doc_id = self._active_key(user_id)
        try:
            await self.collection.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "timeline_id": timeline_id,
                        "user_id": str(user_id),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            logger.info(f"[Timeline] User {user_id} active timeline set to {timeline_id}")
        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to set active timeline for user {user_id}: {e}")
            raise
