"""ProfileService — the brain's prefrontal self-model.

Materializes user profiles and community profiles from the memory
and graph services.  Profiles are stored in MongoDB and can be
retrieved instantly (<1 ms) for prompt injection.

Lifecycle:
    - **Incremental update**: Called after ``memory.add()`` stores new
      memories.  Merges new facts into the existing profile via a
      lightweight LLM call.
    - **Full rebuild**: Called on a timer or manually.  Fetches all
      memories + graph nodes and synthesizes a comprehensive profile.
    - **Community rebuild**: Runs periodic MongoDB aggregation across
      all user profiles (no LLM).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .builder import (
    build_profile_from_memories,
    format_community_for_prompt,
    format_profile_for_prompt,
    incremental_update_profile,
)
from .community import build_community_profile

logger = logging.getLogger(__name__)


class ProfileService:
    """Manages user profiles and community profiles for an app.

    Args:
        app_slug: Application slug.
        user_profile_collection: MongoDB collection for user profiles.
        community_profile_collection: MongoDB collection for community profile.
            Can be None if community profiles are disabled.
        memory_service: Memory service instance for fetching memories.
            Can be None if unavailable.
        graph_service: Graph service instance for fetching graph nodes.
            Can be None if unavailable.
        llm_service: LLM service for profile synthesis.
            Can be None (disables LLM-powered build/update).
        config: Profile configuration dict.
    """

    def __init__(
        self,
        app_slug: str,
        user_profile_collection: Any,
        community_profile_collection: Any | None = None,
        memory_service: Any | None = None,
        memory_collection: Any | None = None,
        graph_service: Any | None = None,
        graph_collection: Any | None = None,
        llm_service: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._app_slug = app_slug
        self._user_profiles = user_profile_collection
        self._community_profiles = community_profile_collection
        self._memory_service = memory_service
        self._memory_collection = memory_collection
        self._graph_service = graph_service
        self._graph_collection = graph_collection
        self._llm_service = llm_service
        self._config = config or {}

        # Config shortcuts
        user_cfg = self._config.get("user_profiles", {})
        community_cfg = self._config.get("community_profile", {})
        self._user_profiles_enabled = user_cfg.get("enabled", True)
        self._incremental_enabled = user_cfg.get("incremental_on_memory_add", True)
        self._community_enabled = community_cfg.get("enabled", False)
        self._community_min_users = community_cfg.get("min_users_for_aggregation", 3)
        self._llm_model = self._config.get("llm_model")

    # ------------------------------------------------------------------
    # User Profile — Read
    # ------------------------------------------------------------------

    async def get_user_profile(self, user_id: str) -> dict[str, Any] | None:
        """Retrieve the materialized profile for a user.

        This is a single MongoDB read — no LLM calls.

        Args:
            user_id: User identifier.

        Returns:
            Profile document or None if no profile exists.
        """
        if not self._user_profiles_enabled:
            return None

        try:
            doc = await self._user_profiles.find_one({"user_id": str(user_id)})
            return doc
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Profile] Failed to retrieve profile for {user_id}: {e}")
            return None

    async def get_user_profile_text(self, user_id: str) -> str:
        """Get the profile formatted as text for prompt injection.

        Args:
            user_id: User identifier.

        Returns:
            Formatted profile text, or fallback message.
        """
        profile = await self.get_user_profile(user_id)
        if profile:
            return format_profile_for_prompt(profile)
        return "No profile data available."

    # ------------------------------------------------------------------
    # User Profile — Build (full)
    # ------------------------------------------------------------------

    async def build_user_profile(self, user_id: str) -> dict[str, Any]:
        """Full rebuild of a user's profile from all memories + graph.

        This is expensive (1 LLM call + memory fetch + graph fetch)
        and should be called periodically, not on every request.

        Args:
            user_id: User identifier.

        Returns:
            The newly-built profile document.
        """
        if not self._user_profiles_enabled:
            return {}

        user_id_str = str(user_id)
        now = datetime.now(timezone.utc)

        # Fetch all memories
        memories: list[dict[str, Any]] = []
        if self._memory_service:
            try:
                memories = await self._memory_service.get_all(user_id=user_id_str, limit=500)
            except (TypeError, ValueError, RuntimeError) as e:
                logger.warning(f"[Profile] Failed to fetch memories for build: {e}")

        # Fetch graph nodes
        graph_nodes: list[dict[str, Any]] | None = None
        if self._graph_service:
            try:
                result = await self._graph_service.list_nodes(user_id=user_id_str, limit=100)
                graph_nodes = result if isinstance(result, list) else result.get("nodes", [])
            except (TypeError, ValueError, RuntimeError, AttributeError) as e:
                logger.warning(f"[Profile] Failed to fetch graph nodes for build: {e}")

        # Build via LLM
        if self._llm_service and memories:
            profile_data = await build_profile_from_memories(
                memories=memories,
                graph_nodes=graph_nodes,
                llm_service=self._llm_service,
                user_id=user_id_str,
                llm_model=self._llm_model,
            )
        else:
            profile_data = {}

        # Persist
        source_ids = [str(m.get("id", m.get("_id", ""))) for m in memories if m.get("id") or m.get("_id")]
        doc = {
            "user_id": user_id_str,
            "app_slug": self._app_slug,
            **profile_data,
            "version": 1,
            "memory_count_at_build": len(memories),
            "last_full_rebuild": now,
            "last_incremental": now,
            "source_memory_ids": source_ids[:500],
            "created_at": now,
            "updated_at": now,
        }

        try:
            await self._user_profiles.update_one(
                {"user_id": user_id_str},
                {"$set": doc},
                upsert=True,
            )
            logger.info(f"[Profile] Full build stored for user {user_id_str}: " f"{len(memories)} memories")
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Profile] Failed to store profile: {e}")

        return doc

    # ------------------------------------------------------------------
    # User Profile — Incremental Update
    # ------------------------------------------------------------------

    async def incremental_update(
        self,
        user_id: str,
        new_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Incrementally update a user's profile with new memories.

        If no profile exists yet, triggers a full build instead.

        Args:
            user_id: User identifier.
            new_memories: List of newly-stored memory dicts.

        Returns:
            Updated profile document, or None on failure.
        """
        if not self._user_profiles_enabled or not self._incremental_enabled:
            return None

        if not new_memories:
            return None

        user_id_str = str(user_id)
        now = datetime.now(timezone.utc)

        # Get existing profile
        existing = await self.get_user_profile(user_id_str)

        if not existing:
            # No existing profile — do a full build
            logger.info(f"[Profile] No existing profile for {user_id_str}, triggering full build")
            return await self.build_user_profile(user_id_str)

        if not self._llm_service:
            logger.debug("[Profile] LLM service unavailable, skipping incremental update")
            return existing

        # Merge via LLM
        updated_data = await incremental_update_profile(
            existing_profile=existing,
            new_memories=new_memories,
            llm_service=self._llm_service,
            user_id=user_id_str,
            llm_model=self._llm_model,
        )

        # Persist the updated fields
        update_fields = {
            k: v
            for k, v in updated_data.items()
            if k in ("identity", "preferences", "relationships", "active_context", "safety", "narrative")
        }
        update_fields["updated_at"] = now
        update_fields["last_incremental"] = now
        # Increment version
        try:
            await self._user_profiles.update_one(
                {"user_id": user_id_str},
                {
                    "$set": update_fields,
                    "$inc": {"version": 1},
                },
            )
            logger.info(
                f"[Profile] Incremental update stored for user {user_id_str}: "
                f"{len(new_memories)} new memories merged"
            )
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Profile] Failed to store incremental update: {e}")
            return existing

        # Return the merged doc
        existing.update(update_fields)
        return existing

    # ------------------------------------------------------------------
    # Community Profile
    # ------------------------------------------------------------------

    async def get_community_profile(self) -> dict[str, Any] | None:
        """Retrieve the community profile for this app.

        Returns:
            Community profile document or None.
        """
        if not self._community_enabled or not self._community_profiles:
            return None

        try:
            doc = await self._community_profiles.find_one({"app_slug": self._app_slug})
            return doc
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Community] Failed to retrieve profile: {e}")
            return None

    async def get_community_profile_text(self) -> str:
        """Get community profile formatted for prompt injection."""
        profile = await self.get_community_profile()
        if profile:
            return format_community_for_prompt(profile)
        return ""

    async def build_community_profile(self) -> dict[str, Any] | None:
        """Full rebuild of the community profile.

        Uses MongoDB aggregation — no LLM calls.

        Returns:
            Community profile document or None on failure.
        """
        if not self._community_enabled or not self._community_profiles:
            return None

        if not self._memory_collection:
            logger.warning("[Community] Memory collection not available, cannot build")
            return None

        try:
            profile = await build_community_profile(
                memory_collection=self._memory_collection,
                graph_collection=self._graph_collection,
                app_slug=self._app_slug,
                min_users=self._community_min_users,
            )

            # Persist
            await self._community_profiles.update_one(
                {"app_slug": self._app_slug},
                {"$set": profile},
                upsert=True,
            )

            logger.info(
                f"[Community] Profile stored for '{self._app_slug}': "
                f"{profile.get('population', {}).get('total_users', 0)} users"
            )
            return profile

        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Community] Build failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_user_profile(self, user_id: str) -> bool:
        """Delete a user's profile (e.g., for GDPR compliance).

        Args:
            user_id: User identifier.

        Returns:
            True if deleted, False otherwise.
        """
        try:
            result = await self._user_profiles.delete_one({"user_id": str(user_id)})
            deleted = result.deleted_count > 0
            if deleted:
                logger.info(f"[Profile] Deleted profile for user {user_id}")
            return deleted
        except (TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"[Profile] Failed to delete profile: {e}")
            return False
