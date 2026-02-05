"""
Perception Engine - User and Self-Perception Management

This module implements the "Perception Layer" of the cognitive architecture,
tracking the robot's view of users (user perceptions) and itself (self-perceptions).

Perceptions are stored in a hybrid system:
- Separate collection for long-term user/self perceptions
- Embedded in memory documents for memory-specific perceptions
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# LiteLLM import no longer needed - using LLMService instead


class PerceptionEngine:
    """
    Manages user and self-perceptions for the cognitive memory system.

    Perceptions track:
    - User View: How the robot perceives the user (competence, emotion, relationship)
    - Self View: How the robot perceives itself (confidence, efficacy, role alignment)
    """

    def __init__(
        self,
        app_slug: str,
        collection: Any,
        embedding_service: Any,
        llm_service: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Perception Engine.

        Args:
            app_slug: Application slug
            collection: MongoDB collection (for reference to database)
            embedding_service: Embedding service for perception vectors
            llm_service: Optional LLM service for perception analysis
            config: Perception configuration dictionary
        """
        self.app_slug = app_slug
        self.collection = collection
        self.embedding_service = embedding_service
        self.llm_service = llm_service
        self.config = config or {}

        # Perceptions collection name
        self.perceptions_collection_name = f"{app_slug}_perceptions"
        self.perceptions_collection = self.collection.database[self.perceptions_collection_name]

        # Configuration
        self.enabled = self.config.get("enabled", True)
        self.auto_analyze = self.config.get("auto_analyze", True)
        self.update_frequency = self.config.get("update_frequency", "per_interaction")

        # LLM availability - only available if llm_service is provided
        self.llm_available = llm_service is not None

        if self.enabled:
            logger.info(f"✅ Perception Engine initialized for {app_slug}")

    async def analyze_interaction(
        self,
        user_input: str,
        robot_response: str,
        user_id: str,
        persona_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Analyze an interaction to extract perceptions.

        Uses LLM to analyze user input and robot response, extracting:
        - User emotion, skill level, communication style
        - Robot efficacy, confidence, role alignment

        Args:
            user_input: User's message
            robot_response: Robot's response
            user_id: User ID
            persona_context: Current persona role (for context)

        Returns:
            Dictionary with user_view and self_view perceptions
        """
        if not self.enabled or not self.auto_analyze:
            return {}

        if not self.llm_available:
            logger.debug("LLM not available for perception analysis")
            return {}

        try:
            persona_text = f"Current persona: {persona_context}" if persona_context else ""

            analysis_prompt = f"""Analyze this interaction between a user and an AI assistant.

{persona_text}

USER INPUT: {user_input}

ROBOT RESPONSE: {robot_response}

Extract perceptions in JSON format:
{{
    "user_view": {{
        "perceived_emotion": "frustrated|curious|happy|neutral|excited|confused",
        "skill_level_estimate": "beginner|intermediate|expert",
        "communication_style": "direct|polite|casual|formal",
        "engagement_level": 0.0-1.0
    }},
    "self_view": {{
        "status": "helpful_assistant|confused|confident|uncertain",
        "internal_state": "high_confidence|medium_confidence|low_confidence",
        "action_taken": "provided_answer|asked_clarification|offered_encouragement",
        "efficacy_score": 0.0-1.0
    }}
}}

Return ONLY valid JSON, no additional text."""

            # Use LLM service if available
            if not self.llm_service:
                logger.debug("LLM service not available for perception analysis")
                return {}

            try:
                result_text = await self.llm_service.chat_completion(
                    messages=[{"role": "user", "content": analysis_prompt}],
                    model=None,  # Use LLM service default
                    temperature=0.7,
                )
            except (
                AttributeError,
                RuntimeError,
                ConnectionError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"LLM call failed for perception analysis: {e}")
                return {}

            # Parse JSON response
            try:
                # Extract JSON from response (handle markdown code blocks)
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()

                perceptions = json.loads(result_text)
                return perceptions
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse perception analysis JSON: {e}")
                logger.debug(f"Response was: {result_text}")
                return {}

        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logger.warning(f"Perception analysis failed: {e}", exc_info=True)
            return {}

    def update_user_perception(
        self,
        user_id: str,
        attributes: dict[str, Any],
        justification: str | None = None,
        persona_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Update user perception (long-term view).

        Args:
            user_id: User ID
            attributes: Perception attributes (e.g., technical_competence, relationship_warmth)
            justification: Reason for this perception
            persona_context: Persona role when perception was formed

        Returns:
            Updated perception document
        """
        if not self.enabled:
            return {}

        # Get or create user perception document
        existing = self.perceptions_collection.find_one(
            {
                "app_slug": self.app_slug,
                "user_id": str(user_id),
                "perception_type": "user_view",
            }
        )

        # Generate embedding for perception
        perception_text = f"User perception: {justification or str(attributes)}"
        embedding = None
        try:
            if self.embedding_service:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self.embedding_service.embed(perception_text), loop
                    )
                    embeddings = future.result(timeout=30)
                else:
                    embeddings = asyncio.run(self.embedding_service.embed(perception_text))

                if embeddings and len(embeddings) > 0:
                    embedding = embeddings[0]
        except (
            RuntimeError,
            AttributeError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.warning(f"Failed to generate perception embedding: {e}")

        # Update or create perception
        update_doc: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc),
            "$inc": {"interaction_count": 1},
        }

        if attributes:
            update_doc["$set"] = {
                "attributes": attributes,
                "last_updated": datetime.now(timezone.utc),
            }
            if justification:
                update_doc["$set"]["justification"] = justification
            if persona_context:
                update_doc["$set"]["persona_context"] = persona_context
            if embedding:
                update_doc["$set"]["embedding"] = embedding

        if existing:
            # Update existing
            self.perceptions_collection.update_one(
                {
                    "app_slug": self.app_slug,
                    "user_id": str(user_id),
                    "perception_type": "user_view",
                },
                update_doc,
            )
        else:
            # Create new
            new_doc = {
                "app_slug": self.app_slug,
                "user_id": str(user_id),
                "perception_type": "user_view",
                "subject": str(user_id),
                "attributes": attributes,
                "justification": justification or "",
                "persona_context": persona_context,
                "embedding": embedding,
                "interaction_count": 1,
                "created_at": datetime.now(timezone.utc),
                "last_updated": datetime.now(timezone.utc),
            }
            self.perceptions_collection.insert_one(new_doc)

        return self.get_user_perception(user_id)

    def update_self_perception(
        self,
        attributes: dict[str, Any],
        justification: str | None = None,
        persona_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Update self-perception (robot's view of itself).

        Args:
            attributes: Self-perception attributes (e.g., confidence, efficacy)
            justification: Reason for this perception
            persona_context: Persona role when perception was formed

        Returns:
            Updated perception document
        """
        if not self.enabled:
            return {}

        # Get or create self-perception document
        existing = self.perceptions_collection.find_one(
            {
                "app_slug": self.app_slug,
                "perception_type": "self_view",
                "subject": "system",
            }
        )

        # Generate embedding
        perception_text = f"Self-perception: {justification or str(attributes)}"
        embedding = None
        try:
            if self.embedding_service:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self.embedding_service.embed(perception_text), loop
                    )
                    embeddings = future.result(timeout=30)
                else:
                    embeddings = asyncio.run(self.embedding_service.embed(perception_text))

                if embeddings and len(embeddings) > 0:
                    embedding = embeddings[0]
        except (
            RuntimeError,
            AttributeError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.warning(f"Failed to generate self-perception embedding: {e}")

        # Update or create
        update_doc: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc),
            "$inc": {"interaction_count": 1},
        }

        if attributes:
            update_doc["$set"] = {
                "attributes": attributes,
                "last_updated": datetime.now(timezone.utc),
            }
            if justification:
                update_doc["$set"]["justification"] = justification
            if persona_context:
                update_doc["$set"]["persona_context"] = persona_context
            if embedding:
                update_doc["$set"]["embedding"] = embedding

        if existing:
            self.perceptions_collection.update_one(
                {
                    "app_slug": self.app_slug,
                    "perception_type": "self_view",
                    "subject": "system",
                },
                update_doc,
            )
        else:
            new_doc = {
                "app_slug": self.app_slug,
                "user_id": None,  # Self-perception is app-level
                "perception_type": "self_view",
                "subject": "system",
                "attributes": attributes,
                "justification": justification or "",
                "persona_context": persona_context,
                "embedding": embedding,
                "interaction_count": 1,
                "created_at": datetime.now(timezone.utc),
                "last_updated": datetime.now(timezone.utc),
            }
            self.perceptions_collection.insert_one(new_doc)

        return self.get_self_perception()

    def get_user_perception(self, user_id: str) -> dict[str, Any] | None:
        """
        Get user perception for a specific user.

        Args:
            user_id: User ID

        Returns:
            User perception document or None
        """
        if not self.enabled:
            return None

        perception = self.perceptions_collection.find_one(
            {
                "app_slug": self.app_slug,
                "user_id": str(user_id),
                "perception_type": "user_view",
            }
        )

        if perception:
            perception["_id"] = str(perception["_id"])

        return perception

    def get_self_perception(self) -> dict[str, Any] | None:
        """
        Get self-perception (robot's view of itself).

        Returns:
            Self-perception document or None
        """
        if not self.enabled:
            return None

        perception = self.perceptions_collection.find_one(
            {
                "app_slug": self.app_slug,
                "perception_type": "self_view",
                "subject": "system",
            }
        )

        if perception:
            perception["_id"] = str(perception["_id"])

        return perception

    def get_perception_history(
        self,
        user_id: str | None = None,
        perception_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get perception history (for temporal analysis).

        Args:
            user_id: Optional user ID filter
            perception_type: Optional type filter ("user_view" or "self_view")
            limit: Maximum results

        Returns:
            List of perception documents
        """
        if not self.enabled:
            return []

        query: dict[str, Any] = {"app_slug": self.app_slug}

        if user_id:
            query["user_id"] = str(user_id)
        if perception_type:
            query["perception_type"] = perception_type

        perceptions = list(
            self.perceptions_collection.find(query).sort("last_updated", -1).limit(limit)
        )

        for p in perceptions:
            p["_id"] = str(p["_id"])

        return perceptions
