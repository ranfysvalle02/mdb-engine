"""
Procedural Memory Service - Cognitive Blueprint v2.0

Manages procedural memory: how-to workflows, code snippets, and step-by-step procedures.
Procedural memories are permanent and track success counts for optimization.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

try:
    from pymongo.errors import PyMongoError
except ImportError:
    PyMongoError = Exception  # Fallback if pymongo not available

logger = logging.getLogger(__name__)


class ProceduralMemoryService:
    """
    Service for managing procedural memories (workflows, code, procedures).

    Procedural memories store:
    - Task type (e.g., "Data_Cleaning", "API_Integration")
    - Step-by-step instructions
    - Associated tools/libraries
    - Success count (for optimization)
    """

    def __init__(
        self,
        app_slug: str,
        collection: Any,
        llm_service: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize Procedural Memory Service.

        Args:
            app_slug: Application slug
            collection: MongoDB collection (for reference to database)
            llm_service: Optional LLM service for procedural extraction
            config: Procedural memory configuration
        """
        self.app_slug = app_slug
        self.collection = collection
        self.llm_service = llm_service
        self.config = config or {}

        # Configuration
        self.enabled = self.config.get("enabled", True)
        self.auto_extract = self.config.get("auto_extract", True)
        self.detect_code = self.config.get("detect_code", True)
        self.detect_workflows = self.config.get("detect_workflows", True)

        # LLM availability - only available if llm_service is provided
        self.llm_available = llm_service is not None

        if self.enabled:
            logger.info(f"✅ Procedural Memory Service initialized for {app_slug}")

    def detect_procedural_content(self, text: str) -> dict[str, Any] | None:
        """
        Detect if text contains procedural content (code, workflows, procedures).

        Args:
            text: Text to analyze

        Returns:
            Dictionary with procedural information or None if not procedural
        """
        if not self.enabled or not self.auto_extract:
            return None

        if not self.llm_available:
            # Simple heuristic fallback
            code_indicators = ["def ", "import ", "function", "procedure", "step", "how to"]
            if any(indicator in text.lower() for indicator in code_indicators):
                return {
                    "task_type": "General",
                    "steps": [text],
                    "associated_tools": [],
                }
            return None

        try:
            prompt = f"""Analyze this text and determine if it contains procedural content:
- Code snippets or programming instructions
- Step-by-step workflows or procedures
- How-to instructions

Text: {text}

If procedural, return JSON:
{{
    "is_procedural": true,
    "task_type": "Task category (e.g., Data_Cleaning, API_Integration)",
    "steps": ["step 1", "step 2", ...],
    "associated_tools": ["tool1", "tool2", ...]
}}

If NOT procedural, return:
{{"is_procedural": false}}

Return ONLY valid JSON."""

            # Use LLM service if available
            if not self.llm_service:
                logger.debug("LLM service not available for procedural detection")
                return None

            try:
                # Call async chat_completion from sync context
                # Check if we're in an async context
                try:
                    asyncio.get_running_loop()
                    # We're in an async context, need to use a different approach
                    # For now, return None and let caller handle async version
                    logger.debug("Cannot call async LLM from sync method in async context")
                    return None
                except RuntimeError:
                    # No running loop, safe to use asyncio.run()
                    result_text = asyncio.run(
                        self.llm_service.chat_completion(
                            messages=[{"role": "user", "content": prompt}],
                            model=None,  # Use LLM service default
                            temperature=0.3,
                        )
                    )
            except (
                AttributeError,
                RuntimeError,
                ConnectionError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as e:
                logger.warning(f"LLM call failed for procedural detection: {e}")
                return None

            # Parse JSON response
            import json

            try:
                # Extract JSON from response
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0].strip()

                result = json.loads(result_text)

                if result.get("is_procedural"):
                    return {
                        "task_type": result.get("task_type", "General"),
                        "steps": result.get("steps", []),
                        "associated_tools": result.get("associated_tools", []),
                    }
                return None
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse procedural detection JSON: {e}")
                return None

        except (
            AttributeError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logger.warning(f"Procedural detection failed: {e}", exc_info=True)
            return None

    def create_procedural_memory(
        self,
        text: str,
        user_id: str,
        task_type: str | None = None,
        steps: list[str] | None = None,
        associated_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a procedural memory document.

        Args:
            text: The procedural content text
            user_id: User ID
            task_type: Task type (auto-detected if not provided)
            steps: Step-by-step instructions (auto-extracted if not provided)
            associated_tools: Associated tools/libraries (auto-extracted if not provided)

        Returns:
            Procedural memory document
        """
        if not self.enabled:
            return {}

        # Auto-detect if not provided
        if not task_type or not steps:
            detected = self.detect_procedural_content(text)
            if detected:
                task_type = task_type or detected.get("task_type", "General")
                steps = steps or detected.get("steps", [text])
                associated_tools = associated_tools or detected.get("associated_tools", [])
            else:
                # Fallback
                task_type = task_type or "General"
                steps = steps or [text]
                associated_tools = associated_tools or []

        procedural_doc = {
            "memory_type": "procedural",
            "text": text,
            "task_type": task_type,
            "steps": steps,
            "associated_tools": associated_tools,
            "success_count": 0,
            "user_id": str(user_id),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        return procedural_doc

    def increment_success(self, memory_id: str, user_id: str) -> bool:
        """
        Increment success count for a procedural memory.

        Args:
            memory_id: Memory document ID
            user_id: User ID for security

        Returns:
            True if successful
        """
        if not self.enabled:
            return False

        try:
            from bson import ObjectId

            result = self.collection.update_one(
                {
                    "_id": ObjectId(memory_id),
                    "user_id": str(user_id),
                    "memory_type": "procedural",
                },
                {
                    "$inc": {"success_count": 1},
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                },
            )

            return result.modified_count > 0
        except (PyMongoError, AttributeError, TypeError) as e:
            logger.warning(f"Failed to increment success count: {e}")
            return False

    def get_by_task_type(
        self, task_type: str, user_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Get procedural memories by task type.

        Args:
            task_type: Task type to search for
            user_id: User ID
            limit: Maximum results

        Returns:
            List of procedural memory documents
        """
        if not self.enabled:
            return []

        try:
            memories = list(
                self.collection.find(
                    {
                        "user_id": str(user_id),
                        "memory_type": "procedural",
                        "task_type": task_type,
                        "is_active": True,
                    }
                )
                .sort("success_count", -1)  # Sort by success count (most successful first)
                .limit(limit)
            )

            for m in memories:
                m["_id"] = str(m["_id"])

            return memories
        except (PyMongoError, AttributeError, TypeError) as e:
            logger.warning(f"Failed to get procedural memories: {e}")
            return []
