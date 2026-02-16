"""
Persona Engine - App-level Identity Filter

Manages app-level persona that filters memory retrieval and interpretation.
Extracted from cognitive.py for modularity.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .base import MemoryServiceError

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

logger = logging.getLogger(__name__)


class PersonaEngine:
    """
    Manages app-level persona that filters memory retrieval and interpretation.

    Persona acts as a "filter" through which all data passes, determining:
    - Salience: What is worth remembering?
    - Tone & Synthesis: How is a memory retrieved?
    - Consistency: Ensures memories align with agent's established "self"
    """

    def __init__(
        self,
        app_slug: str,
        collection: ScopedCollectionWrapper,
        embedding_service: Any,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Persona Engine.

        Args:
            app_slug: Application slug
            collection: MongoDB collection for persona storage
            embedding_service: Embedding service for persona vector generation
            config: Persona configuration dictionary
        """
        self.app_slug = app_slug
        self.collection = collection
        self.embedding_service = embedding_service
        self.config = config or {}

        # Persona collection name
        self.persona_collection_name = f"{app_slug}_persona"
        # Access persona collection through scoped database wrapper
        # collection.database returns ScopedMongoWrapper, ensuring all collections are scoped
        self.persona_collection: ScopedCollectionWrapper = self.collection.database.get_collection(
            self.persona_collection_name
        )

        # Default persona configuration
        self.enabled = self.config.get("enabled", True)
        self.default_role = self.config.get("default_role", "Helpful Assistant")
        self.default_description = self.config.get(
            "default_description",
            "A helpful AI assistant focused on providing accurate and useful information.",
        )
        self.default_traits = self.config.get(
            "default_traits",
            {
                "technical_focus": 0.5,
                "humor": 0.2,
                "formality": 0.7,
                "empathy": 0.6,
                "creativity": 0.4,
            },
        )

        # Initialize persona if enabled (lazy initialization - will be called on first async use)
        # Note: _ensure_persona_exists() is now async, so we can't call it synchronously in __init__
        # It will be called lazily when get_persona() is first called
        self._persona_initialized = False

    async def _ensure_persona_exists(self):
        """Ensure persona document exists, create default if not."""
        existing = await self.persona_collection.find_one({"app_slug": self.app_slug})
        if not existing:
            # Create default persona
            persona_doc = {
                "app_slug": self.app_slug,
                "role": self.default_role,
                "description": self.default_description,
                "traits": self.default_traits,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }

            # Generate embedding for persona
            persona_text = f"{self.default_role}. {self.default_description}"
            try:
                if self.embedding_service:
                    embeddings = await self.embedding_service.embed(persona_text)
                    if embeddings and len(embeddings) > 0:
                        persona_doc["vector"] = embeddings[0]
            except (
                RuntimeError,
                AttributeError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"Failed to generate persona embedding: {e}")

            await self.persona_collection.insert_one(persona_doc)
            logger.info(f"Created default persona for {self.app_slug}: {self.default_role}")

    async def get_persona(self) -> dict[str, Any] | None:
        """
        Get current persona for the app.

        Returns:
            Persona document or None if not found
        """
        if not self.enabled:
            return None

        # Lazy initialization: ensure persona exists on first use
        if not self._persona_initialized:
            await self._ensure_persona_exists()
            self._persona_initialized = True

        persona = await self.persona_collection.find_one({"app_slug": self.app_slug})
        if persona:
            persona["_id"] = str(persona["_id"])
        return persona

    async def get_persona_vector(self) -> list[float] | None:
        """
        Get persona vector for similarity matching.

        Returns:
            Persona embedding vector or None
        """
        persona = await self.get_persona()
        if persona and "vector" in persona:
            return persona["vector"]
        return None

    async def update_persona(
        self,
        role: str | None = None,
        description: str | None = None,
        traits: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """
        Update persona configuration.

        Args:
            role: New persona role
            description: New persona description
            traits: New persona traits dictionary

        Returns:
            Updated persona document
        """
        if not self.enabled:
            raise MemoryServiceError("Persona feature is disabled")

        update_doc: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

        if role:
            update_doc["role"] = role
        if description:
            update_doc["description"] = description
        if traits:
            update_doc["traits"] = traits

        # Regenerate embedding if role or description changed
        if role or description:
            current = await self.get_persona()
            final_role = role or current.get("role", self.default_role) if current else self.default_role
            final_description = (
                description or current.get("description", self.default_description)
                if current
                else self.default_description
            )

            persona_text = f"{final_role}. {final_description}"
            try:
                if self.embedding_service:
                    embeddings = await self.embedding_service.embed(persona_text)
                    if embeddings and len(embeddings) > 0:
                        update_doc["vector"] = embeddings[0]
            except (
                RuntimeError,
                AttributeError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"Failed to regenerate persona embedding: {e}")

        await self.persona_collection.update_one({"app_slug": self.app_slug}, {"$set": update_doc}, upsert=True)

        logger.info(f"Updated persona for {self.app_slug}")
        return await self.get_persona() or {}
