"""Node operations mixin for GraphService."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pymongo import DESCENDING
from pymongo.errors import OperationFailure, PyMongoError

from ..core.validation import validate_node_id, validate_user_id
from .base import GraphServiceError

logger = logging.getLogger(__name__)


class NodeOperationsMixin:
    """Mixin providing node CRUD operations and embedding backfill.

    Expects the composing class to provide:
        self.collection, self._app_slug, self._enabled,
        self.embedding_service, self._get_embedding()
    """

    async def upsert_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None,
        embedding: list[float] | None = None,
        source_memory_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create or update a node in the graph."""
        if not self._enabled:
            logger.debug("GraphService disabled, skipping upsert_node")
            return {}

        validate_node_id(node_id)
        validate_user_id(user_id, allow_none=True)

        now = datetime.now(timezone.utc)

        # Build update document
        update_doc: dict[str, Any] = {
            "$set": {
                "type": node_type,
                "name": name,
                "app_slug": self._app_slug,
                "updated_at": now,
            },
            "$setOnInsert": {
                "_id": node_id,
                "edges": [],
                "created_at": now,
            },
        }

        # Track which memories produced this node (append, not replace)
        if source_memory_ids:
            update_doc.setdefault("$addToSet", {})
            update_doc["$addToSet"]["source_memory_ids"] = {"$each": source_memory_ids}

        if properties:
            update_doc["$set"]["properties"] = properties

        if user_id:
            update_doc["$set"]["user_id"] = str(user_id)

        if embedding:
            update_doc["$set"]["embedding"] = embedding
        elif self.embedding_service and not embedding:
            # Generate embedding from name + properties
            embed_text = f"{name} {node_type}"
            if properties:
                embed_text += " " + " ".join(str(v) for v in properties.values())
            generated_embedding = await self._get_embedding(embed_text)
            if generated_embedding:
                update_doc["$set"]["embedding"] = generated_embedding

        try:
            result = await self.collection.update_one(
                {"_id": node_id, "app_slug": self._app_slug},
                update_doc,
                upsert=True,
            )

            if result.upserted_id:
                logger.info(f"Created node: {node_id} ({node_type})")
            else:
                logger.debug(f"Updated node: {node_id}")

            # Return the node
            return await self.get_node(node_id) or {}

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to upsert node {node_id}: {e}")
            raise GraphServiceError(f"Failed to upsert node: {e}") from e

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""
        validate_node_id(node_id)
        try:
            return await self.collection.find_one({"_id": node_id, "app_slug": self._app_slug})
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to get node {node_id}: {e}")
            return None

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node and all its edges."""
        if not self._enabled:
            return False
        validate_node_id(node_id)

        try:
            # Delete the node
            result = await self.collection.delete_one({"_id": node_id, "app_slug": self._app_slug})

            if result.deleted_count > 0:
                # Also remove edges pointing to this node from other nodes
                await self.collection.update_many(
                    {"app_slug": self._app_slug, "edges.target": node_id},
                    {"$pull": {"edges": {"target": node_id}}},
                )
                logger.info(f"Deleted node: {node_id}")
                return True

            return False

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to delete node {node_id}: {e}")
            return False

    async def remove_memory_references(self, memory_id: str) -> int:
        """
        Remove a memory reference from all graph nodes for this app.

        1. ``$pull`` the *memory_id* from ``source_memory_ids`` on every node.
        2. Delete any node whose ``source_memory_ids`` is now empty (orphaned).
        3. Clean up edges pointing to deleted orphan nodes.

        Args:
            memory_id: The memory ID to unlink.

        Returns:
            Number of orphan nodes deleted.
        """
        if not self._enabled:
            return 0

        try:
            app_filter = {"app_slug": self._app_slug}

            # Step 1: Remove the memory ID from all nodes
            await self.collection.update_many(
                {**app_filter, "source_memory_ids": memory_id},
                {"$pull": {"source_memory_ids": memory_id}},
            )

            # Step 2: Find and delete orphan nodes (empty source_memory_ids)
            orphan_filter = {
                **app_filter,
                "source_memory_ids": {"$exists": True, "$size": 0},
            }
            orphan_cursor = self.collection.find(orphan_filter, {"_id": 1})
            orphan_ids = [doc["_id"] async for doc in orphan_cursor]

            if not orphan_ids:
                return 0

            # Step 3: Remove edges pointing to orphan nodes from surviving nodes
            await self.collection.update_many(
                {**app_filter, "edges.target": {"$in": orphan_ids}},
                {"$pull": {"edges": {"target": {"$in": orphan_ids}}}},
            )

            # Step 4: Delete the orphan nodes themselves
            result = await self.collection.delete_many({"_id": {"$in": orphan_ids}, **app_filter})

            deleted = result.deleted_count
            if deleted:
                logger.info(f"[Graph Cleanup] Removed memory {memory_id}: " f"deleted {deleted} orphan node(s)")
            return deleted

        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"[Graph Cleanup] Failed to remove memory references: {e}")
            return 0

    async def list_nodes(
        self,
        node_type: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List nodes with optional filtering."""
        query: dict[str, Any] = {"app_slug": self._app_slug}

        if node_type:
            query["type"] = node_type
        if user_id:
            query["user_id"] = str(user_id)

        try:
            cursor = self.collection.find(query).sort("updated_at", DESCENDING).limit(limit)
            return await cursor.to_list(length=None)
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Failed to list nodes: {e}")
            return []

    async def backfill_embeddings(
        self,
        user_id: str | None = None,
        batch_size: int = 50,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Backfill embeddings for existing nodes that don't have them.

        This is useful when nodes were created before embeddings were enabled,
        or when the embedding service wasn't available during node creation.

        Args:
            user_id: Optional user ID to filter nodes (if None, processes all users)
            batch_size: Number of nodes to process in each batch
            limit: Maximum number of nodes to process (None = all)

        Returns:
            Dict with:
                - processed: Number of nodes processed
                - updated: Number of nodes successfully updated with embeddings
                - failed: Number of nodes that failed to get embeddings
                - skipped: Number of nodes that already had embeddings
        """
        if not self._enabled:
            return {
                "processed": 0,
                "updated": 0,
                "failed": 0,
                "skipped": 0,
                "error": "GraphService disabled",
            }

        if not self.embedding_service:
            return {
                "processed": 0,
                "updated": 0,
                "failed": 0,
                "skipped": 0,
                "error": "Embedding service not available",
            }

        # Find nodes without embeddings
        query: dict[str, Any] = {
            "app_slug": self._app_slug,
            "$or": [
                {"embedding": {"$exists": False}},
                {"embedding": None},
                {"embedding": []},
            ],
        }

        if user_id:
            query["user_id"] = str(user_id)

        try:
            # Get all nodes without embeddings
            cursor = self.collection.find(query)
            if limit:
                cursor = cursor.limit(limit)

            nodes_without_embeddings = await cursor.to_list(length=None)

            if not nodes_without_embeddings:
                logger.info("No nodes found without embeddings")
                return {
                    "processed": 0,
                    "updated": 0,
                    "failed": 0,
                    "skipped": len(nodes_without_embeddings),
                }

            logger.info(f"Found {len(nodes_without_embeddings)} nodes without embeddings, processing...")

            updated = 0
            failed = 0
            skipped = 0

            # Process in batches
            for i in range(0, len(nodes_without_embeddings), batch_size):
                batch = nodes_without_embeddings[i : i + batch_size]

                # Prepare texts for batch embedding
                texts_to_embed = []
                node_ids = []

                for node in batch:
                    node_id = node["_id"]
                    name = node.get("name", "")
                    node_type = node.get("type", "")
                    properties = node.get("properties", {})

                    # Build embedding text (same logic as upsert_node)
                    embed_text = f"{name} {node_type}"
                    if properties:
                        embed_text += " " + " ".join(str(v) for v in properties.values())

                    texts_to_embed.append(embed_text)
                    node_ids.append(node_id)

                # Generate embeddings in batch
                try:
                    embeddings = await self.embedding_service.embed(texts_to_embed)

                    if not embeddings or len(embeddings) != len(batch):
                        logger.warning(
                            f"Batch embedding returned "
                            f"{len(embeddings) if embeddings else 0} embeddings for {len(batch)} nodes"
                        )
                        failed += len(batch)
                        continue

                    # Update nodes with embeddings
                    for node_id, embedding in zip(node_ids, embeddings, strict=False):
                        if not embedding:
                            failed += 1
                            logger.warning(f"Failed to generate embedding for node {node_id}")
                            continue

                        try:
                            result = await self.collection.update_one(
                                {"_id": node_id, "app_slug": self._app_slug},
                                {
                                    "$set": {
                                        "embedding": embedding,
                                        "updated_at": datetime.now(timezone.utc),
                                    }
                                },
                            )

                            if result.modified_count > 0:
                                updated += 1
                                logger.debug(f"Added embedding to node {node_id}")
                            else:
                                skipped += 1
                        except (PyMongoError, OperationFailure) as e:
                            failed += 1
                            logger.warning(f"Failed to update node {node_id} with embedding: {e}")

                except (ConnectionError, TimeoutError, ValueError) as e:
                    logger.exception(f"Failed to generate embeddings for batch: {e}")
                    failed += len(batch)

            logger.info(
                f"Backfill complete: {updated} updated, {failed} failed, {skipped} skipped "
                f"(out of {len(nodes_without_embeddings)} nodes)"
            )

            return {
                "processed": len(nodes_without_embeddings),
                "updated": updated,
                "failed": failed,
                "skipped": skipped,
            }

        except (PyMongoError, OperationFailure) as e:
            logger.exception(f"Failed to backfill embeddings: {e}")
            return {"processed": 0, "updated": 0, "failed": 0, "skipped": 0, "error": str(e)}
