"""
Procedural Memory System
Stores executable "recipes," tool definitions, and task workflows.

Procedural memory contains skills and procedures that the agent can execute:
- Tool definitions (JSON Schema)
- Successful code snippets
- Task workflows and sequences
- "Golden Examples" of successful operations

This memory type enables the agent to get faster and more reliable over time
by reusing proven procedures rather than re-reasoning the same problems.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ._async_compat import cursor_to_list as _cursor_to_list
from ._async_compat import maybe_await as _maybe_await

try:
    from pymongo.errors import OperationFailure, PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    raise ImportError("pip install pymongo") from None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


class ProceduralMemoryError(MemoryServiceError):
    """Base exception for Procedural Memory errors."""

    pass


async def retrieve_procedural_memory(
    collection: Any,
    task_description: str,
    embedding_service: Any = None,
    embed_model: str = "text-embedding-3-small",
    limit: int = 1,
) -> dict[str, Any] | None:
    """
    Search for procedural memory (skills/tools) for a specific task.

    Uses vector search to find the most relevant procedural memory based on
    task description similarity.

    Args:
        collection: MongoDB collection for procedural memory
        task_description: Description of the task to find procedure for
        embedding_service: EmbeddingService instance for generating embeddings
        embed_model: Embedding model to use (default: "text-embedding-3-small")
        limit: Maximum number of results to return (default: 1)

    Returns:
        Most relevant procedural memory document, or None if not found

    Example:
        ```python
        from mdb_engine.memory.procedural import retrieve_procedural_memory

        procedure = await retrieve_procedural_memory(
            collection=procedural_collection,
            task_description="Deploy Docker container",
            embedding_service=embedding_service,
            limit=1
        )
        if procedure:
            print(f"Found procedure: {procedure['name']}")
            print(f"Steps: {procedure['steps']}")
        ```
    """
    if embedding_service is None:
        raise ProceduralMemoryError("embedding_service is required. Pass an EmbeddingService instance.")

    try:
        # Generate query vector
        vectors = await embedding_service.embed([task_description])
        if not vectors:
            raise ProceduralMemoryError("Empty embedding response")
        query_vector = vectors[0]

        # Use MongoDB Atlas Vector Search if available
        try:
            results = collection.aggregate(
                [
                    {
                        "$vectorSearch": {
                            "index": "proc_vector_index",
                            "path": "vector",
                            "queryVector": query_vector,
                            "numCandidates": limit * 10,
                            "limit": limit,
                        }
                    },
                    {
                        "$match": {
                            "is_active": True,  # Only active procedures
                            "success_rate": {"$gte": 0.7},  # Only successful procedures
                        }
                    },
                ]
            )
            procedures = await _cursor_to_list(results, limit)
            return procedures[0] if procedures else None
        except (PyMongoError, OperationFailure):
            # Fallback: simple text search if vector index not available
            logger.debug("Vector search index not available, using text search")
            cursor = (
                collection.find(
                    {
                        "is_active": True,
                        "success_rate": {"$gte": 0.7},
                        "$text": {"$search": task_description},
                    },
                    {"score": {"$meta": "textScore"}},
                )
                .sort([("score", {"$meta": "textScore"})])
                .limit(limit)
            )
            results = await _cursor_to_list(cursor, limit)
            return results[0] if results else None

    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as e:
        logger.error(f"Procedural memory retrieval failed: {e}", exc_info=True)
        raise ProceduralMemoryError(f"Failed to retrieve procedural memory: {e}") from e


class ProceduralMemory:
    """
    Manager for procedural memory (skills, tools, workflows).

    Procedural memory stores executable knowledge:
    - Tool definitions (JSON Schema)
    - Successful code snippets
    - Task workflows
    - "Golden Examples" tagged with is_successful_procedure: true

    Example:
        ```python
        from mdb_engine.memory.procedural import ProceduralMemory

        proc_memory = ProceduralMemory(collection=procedural_collection)

        # Store a successful procedure
        await proc_memory.store_procedure(
            name="Docker Deployment Workflow",
            task_type="deployment",
            steps=["docker build", "docker push", "docker deploy"],
            code_snippet="docker build -t app . && docker push app && docker deploy",
            success_rate=1.0,
            metadata={"environment": "production"}
        )

        # Retrieve a procedure
        procedure = await proc_memory.get_procedure("Docker Deployment Workflow")
        ```
    """

    def __init__(
        self,
        collection: Any,
        embedding_service: Any = None,
        embed_model: str = "text-embedding-3-small",
    ):
        """
        Initialize ProceduralMemory manager.

        Args:
            collection: MongoDB collection for procedural memory
            embedding_service: EmbeddingService instance for generating embeddings
            embed_model: Embedding model for vector generation
        """
        if embedding_service is None:
            raise ProceduralMemoryError("embedding_service is required. Pass an EmbeddingService instance.")

        self.collection = collection
        self._embedding_service = embedding_service
        self.embed_model = embed_model
        self._indexes_ensured = False

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first operation."""
        if self._indexes_ensured:
            return
        await self._ensure_indexes()
        self._indexes_ensured = True

    async def _ensure_indexes(self):
        """Create necessary indexes for procedural memory."""
        try:
            await _maybe_await(self.collection.create_index([("task_type", 1), ("success_rate", -1)]))
            await _maybe_await(self.collection.create_index([("name", 1)], unique=True))
            await _maybe_await(self.collection.create_index("is_active"))
            logger.info("Procedural memory indexes created")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    async def _get_vector(self, text: str) -> list[float]:
        """Generate embedding vector for text."""
        try:
            vectors = await self._embedding_service.embed([text])
            if not vectors:
                raise ProceduralMemoryError("Empty embedding response")
            return vectors[0]
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise ProceduralMemoryError(f"Failed to generate embedding: {e}") from e

    async def store_procedure(
        self,
        name: str,
        task_type: str,
        steps: list[str] | None = None,
        code_snippet: str | None = None,
        tool_schema: dict[str, Any] | None = None,
        success_rate: float = 1.0,
        metadata: dict[str, Any] | None = None,
        is_successful_procedure: bool = True,
    ) -> dict[str, Any]:
        """
        Store a procedural memory (skill/tool/workflow).

        Args:
            name: Name/identifier of the procedure
            task_type: Type of task (e.g., "deployment", "debugging", "data_processing")
            steps: List of step descriptions (optional)
            code_snippet: Executable code snippet (optional)
            tool_schema: JSON Schema for tool definition (optional)
            success_rate: Success rate (0.0 to 1.0, default: 1.0)
            metadata: Optional metadata dictionary
            is_successful_procedure: Mark as successful procedure (default: True)

        Returns:
            Created procedure document

        Example:
            ```python
            await proc_memory.store_procedure(
                name="Clear Cache Workflow",
                task_type="maintenance",
                steps=["Run --force flag", "Verify cache cleared"],
                code_snippet="cache --force",
                success_rate=0.95,
                metadata={"command": "cache --force"}
            )
            ```
        """
        await self._ensure_ready()

        try:
            # Generate vector from combined content
            content_parts = []
            if steps:
                content_parts.extend(steps)
            if code_snippet:
                content_parts.append(code_snippet)
            if tool_schema:
                content_parts.append(json.dumps(tool_schema))

            content_text = " ".join(content_parts) if content_parts else name
            vector = await self._get_vector(content_text)

            procedure_doc = {
                "name": name,
                "task_type": task_type,
                "vector": vector,
                "success_rate": success_rate,
                "is_active": True,
                "is_successful_procedure": is_successful_procedure,
                "created_at": datetime.now(timezone.utc),
                "last_used": datetime.now(timezone.utc),
                "usage_count": 0,
            }

            if steps:
                procedure_doc["steps"] = steps
            if code_snippet:
                procedure_doc["code_snippet"] = code_snippet
            if tool_schema:
                procedure_doc["tool_schema"] = tool_schema
            if metadata:
                procedure_doc["metadata"] = metadata

            # Upsert (update if exists, insert if new)
            await _maybe_await(
                self.collection.update_one(
                    {"name": name},
                    {
                        "$set": procedure_doc,
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )
            )

            logger.info(f"Procedure stored: {name} (task_type: {task_type})")
            return procedure_doc

        except (PyMongoError, OperationFailure, ProceduralMemoryError) as e:
            logger.error(f"Failed to store procedure: {e}", exc_info=True)
            raise ProceduralMemoryError(f"Failed to store procedure: {e}") from e

    async def get_procedure(self, name: str) -> dict[str, Any] | None:
        """
        Retrieve a procedure by name.

        Args:
            name: Procedure name

        Returns:
            Procedure document or None if not found
        """
        await self._ensure_ready()

        try:
            return await _maybe_await(self.collection.find_one({"name": name, "is_active": True}))
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get procedure: {e}")
            return None

    async def search_procedures(
        self,
        task_description: str,
        task_type: str | None = None,
        min_success_rate: float = 0.7,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search for procedures using vector similarity.

        Args:
            task_description: Description of the task
            task_type: Optional filter by task type
            min_success_rate: Minimum success rate threshold (default: 0.7)
            limit: Maximum number of results

        Returns:
            List of matching procedure documents
        """
        await self._ensure_ready()

        try:
            query_vector = await self._get_vector(task_description)

            # Build match filter
            match_filter = {
                "is_active": True,
                "success_rate": {"$gte": min_success_rate},
            }
            if task_type:
                match_filter["task_type"] = task_type

            # Use MongoDB Atlas Vector Search if available
            try:
                pipeline = [
                    {
                        "$vectorSearch": {
                            "index": "proc_vector_index",
                            "path": "vector",
                            "queryVector": query_vector,
                            "numCandidates": limit * 10,
                            "limit": limit,
                        }
                    },
                    {"$match": match_filter},
                ]
                results = self.collection.aggregate(pipeline)
                return await _cursor_to_list(results, limit)
            except (PyMongoError, OperationFailure):
                # Fallback: simple text search
                logger.debug("Vector search index not available, using text search")
                text_filter = {"$text": {"$search": task_description}}
                text_filter.update(match_filter)
                cursor = (
                    self.collection.find(text_filter, {"score": {"$meta": "textScore"}})
                    .sort([("score", {"$meta": "textScore"})])
                    .limit(limit)
                )
                return await _cursor_to_list(cursor, limit)

        except (PyMongoError, OperationFailure, ProceduralMemoryError) as e:
            logger.warning(f"Procedure search failed: {e}")
            return []

    async def mark_procedure_used(self, name: str, success: bool = True) -> None:
        """
        Mark a procedure as used and update success rate.

        Args:
            name: Procedure name
            success: Whether the usage was successful
        """
        await self._ensure_ready()

        try:
            # Update usage count and success rate
            procedure = await _maybe_await(self.collection.find_one({"name": name}))
            if not procedure:
                logger.warning(f"Procedure not found: {name}")
                return

            usage_count = procedure.get("usage_count", 0) + 1
            current_success_rate = procedure.get("success_rate", 1.0)

            # Update success rate (moving average)
            if success:
                new_success_rate = (current_success_rate * (usage_count - 1) + 1.0) / usage_count
            else:
                new_success_rate = (current_success_rate * (usage_count - 1) + 0.0) / usage_count

            await _maybe_await(
                self.collection.update_one(
                    {"name": name},
                    {
                        "$set": {
                            "success_rate": new_success_rate,
                            "last_used": datetime.now(timezone.utc),
                        },
                        "$inc": {"usage_count": 1},
                    },
                )
            )
            logger.debug(f"Procedure usage updated: {name} (success_rate: {new_success_rate:.2f})")

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to mark procedure used: {e}")

    @staticmethod
    def format_for_prompt(procedures: list[dict[str, Any]]) -> str:
        """
        Format a list of procedures as a context string for injection into the
        system prompt.

        Args:
            procedures: List of procedure documents from ``search_procedures()``.

        Returns:
            Formatted string suitable for inclusion in the system prompt.
            Returns empty string if *procedures* is empty.

        Example output::

            [AVAILABLE SKILLS]
            Skill: "Clear Cache Workflow" (success_rate: 0.95, used 47 times)
              Type: maintenance
              Steps: 1. Run --force flag  2. Verify cache cleared
              Code: cache --force
        """
        if not procedures:
            return ""

        lines: list[str] = ["[AVAILABLE SKILLS]"]
        for proc in procedures:
            name = proc.get("name", "unnamed")
            rate = proc.get("success_rate", 0.0)
            uses = proc.get("usage_count", 0)
            task_type = proc.get("task_type", "general")
            lines.append(f'Skill: "{name}" (success_rate: {rate:.2f}, used {uses} times)')
            lines.append(f"  Type: {task_type}")

            steps = proc.get("steps")
            if steps:
                numbered = "  ".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
                lines.append(f"  Steps: {numbered}")

            snippet = proc.get("code_snippet")
            if snippet:
                lines.append(f"  Code: {snippet}")

            description = proc.get("description") or proc.get("metadata", {}).get("description")
            if description:
                lines.append(f"  Description: {description}")

            lines.append("")  # blank line between skills

        return "\n".join(lines)

    async def deactivate_below_threshold(
        self,
        min_success_rate: float = 0.5,
        min_usage_count: int = 5,
    ) -> int:
        """
        Deactivate (prune) procedures that have fallen below quality thresholds.

        Only deactivates procedures that have been used enough times to have
        statistically meaningful success rates.

        Args:
            min_success_rate: Procedures below this rate are deactivated.
            min_usage_count: Minimum usage count before pruning can trigger
                (prevents pruning skills that haven't been tried enough).

        Returns:
            Number of procedures deactivated.
        """
        await self._ensure_ready()

        try:
            result = await _maybe_await(
                self.collection.update_many(
                    {
                        "is_active": True,
                        "success_rate": {"$lt": min_success_rate},
                        "usage_count": {"$gte": min_usage_count},
                    },
                    {
                        "$set": {
                            "is_active": False,
                            "deactivated_at": datetime.now(timezone.utc),
                            "deactivation_reason": (
                                f"success_rate below {min_success_rate} " f"after {min_usage_count}+ uses"
                            ),
                        },
                    },
                )
            )
            count = result.modified_count if hasattr(result, "modified_count") else 0
            if count:
                logger.info(
                    f"Pruned {count} low-performing procedures "
                    f"(success_rate < {min_success_rate}, uses >= {min_usage_count})"
                )
            return count
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to prune procedures: {e}")
            return 0
