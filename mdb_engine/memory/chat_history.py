"""
Chat History Service
Manages Short-Term Memory (STM) - the active context window.

All methods are async-native, operating directly on the Motor
AsyncIOMotorCollection without thread-pool wrappers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


class ChatHistoryService:
    """
    Manages Short-Term Memory (The active context window).

    Stores raw messages in MongoDB: {session_id, role, content, created_at}
    This is for immediate context - "what did I just say 5 seconds ago?"

    All public methods are ``async`` -- they operate directly on a Motor
    ``AsyncIOMotorCollection`` without thread-pool overhead.
    """

    def __init__(self, collection: ScopedCollectionWrapper, collection_name: str = "chat_history"):
        """
        Initialize Chat History Service.

        Args:
            collection: Motor AsyncIOMotorCollection instance (REQUIRED - must be from
                       MDB-Engine connection manager)
            collection_name: Name of the collection for chat history (for logging)
        """
        if collection is None:
            raise ValueError(
                "Collection is REQUIRED. ChatHistoryService must use MDB-Engine's connection pool. "
                "Pass a Motor AsyncIOMotorCollection obtained from MDB-Engine's connection manager."
            )

        # Reject synchronous PyMongo collections and plain strings.
        # A sync pymongo.Collection.create_index() returns a string (index name),
        # so ``await collection.create_index(...)`` would become ``await "string"``
        # and raise ``TypeError: object str can't be used in 'await' expression``.
        if isinstance(collection, str):
            raise TypeError(
                f"collection must be an async Motor collection or ScopedCollectionWrapper, "
                f"not a string ('{collection}'). Did you mean collection_name='{collection}'?"
            )
        try:
            from pymongo.collection import Collection as SyncCollection

            if isinstance(collection, SyncCollection):
                raise TypeError(
                    "collection must be an ASYNC Motor collection (AsyncIOMotorCollection) "
                    "or ScopedCollectionWrapper, not a synchronous pymongo.Collection. "
                    "Use engine.get_scoped_db(slug) to get async collections."
                )
        except ImportError:
            pass

        self.collection = collection
        self._collection_name = collection_name
        self._indexes_ensured = False
        # Guards the lazy index-creation path so concurrent first-callers don't
        # all race into create_index() (idempotent in Mongo, but wasteful).
        self._ready_lock = asyncio.Lock()
        logger.info(f"Chat History Service using MDB-Engine collection: {collection_name}")

    async def _ensure_ready(self) -> None:
        """Lazily create indexes on first operation (not in __init__)."""
        if self._indexes_ensured:
            return
        async with self._ready_lock:
            # Double-checked: another coroutine may have finished while we waited.
            if self._indexes_ensured:
                return
            try:
                await self.collection.create_index([("session_id", ASCENDING), ("created_at", DESCENDING)])
                await self.collection.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])

                # TTL index for working memory (24 hours default)
                try:
                    ttl_seconds = 24 * 3600
                    await self.collection.create_index(
                        [("created_at", ASCENDING)],
                        name="working_memory_ttl_idx",
                        expireAfterSeconds=ttl_seconds,
                    )
                except (PyMongoError, AttributeError, TypeError) as e:
                    logger.debug(f"TTL index creation: {e}")

                logger.info(f"Chat history indexes created for {self._collection_name}")
            except PyMongoError as e:
                logger.warning(f"Failed to create chat history indexes: {e}")
            finally:
                self._indexes_ensured = True

    # --- STM Summary Cache ---

    async def get_cached_summary(self, session_id: str, user_id: str | None = None) -> tuple[str, int] | None:
        """
        Look up a cached STM summary for a session.

        Args:
            session_id: Session identifier
            user_id: Optional user ID. When provided, the lookup is scoped to that
                user so a summary can never leak across users sharing a session_id.

        Returns:
            Tuple of (summary_text, message_count_at_time_of_summary) or None
        """
        await self._ensure_ready()
        query: dict[str, Any] = {"session_id": session_id, "type": "stm_summary"}
        if user_id:
            query["user_id"] = str(user_id)
        doc = await self.collection.find_one(
            query,
            sort=[("created_at", DESCENDING)],
        )
        if doc and doc.get("summary"):
            return (doc["summary"], doc.get("message_count", 0))
        return None

    async def store_cached_summary(
        self,
        session_id: str,
        summary_text: str,
        message_count: int,
        user_id: str | None = None,
    ) -> None:
        """
        Store (upsert) a cached STM summary for a session.

        Args:
            session_id: Session identifier
            summary_text: The generated summary
            message_count: Number of messages at time of summary generation
            user_id: Optional user ID. When provided, the cache key is scoped to
                the user so each user keeps an independent summary per session.
        """
        await self._ensure_ready()
        doc = {
            "session_id": session_id,
            "type": "stm_summary",
            "summary": summary_text,
            "message_count": message_count,
            "created_at": datetime.now(timezone.utc),
        }
        # Keep the upsert key aligned with get_cached_summary's scoping.
        cache_key: dict[str, Any] = {"session_id": session_id, "type": "stm_summary"}
        if user_id:
            doc["user_id"] = str(user_id)
            cache_key["user_id"] = str(user_id)

        await self.collection.update_one(
            cache_key,
            {"$set": doc},
            upsert=True,
        )
        logger.debug(f"Cached STM summary for session {session_id} ({message_count} messages)")

    async def get_message_count(self, session_id: str, user_id: str | None = None) -> int:
        """
        Get the count of messages for a session (excludes cache docs).

        Args:
            session_id: Session identifier
            user_id: Optional user ID for filtering

        Returns:
            Number of messages in the session
        """
        await self._ensure_ready()
        query: dict[str, Any] = {"session_id": session_id, "type": {"$ne": "stm_summary"}}
        if user_id:
            query["user_id"] = str(user_id)
        return await self.collection.count_documents(query)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Adds a message to the chat history.

        Args:
            session_id: Unique session identifier
            role: Message role ("user", "assistant", "system")
            content: Message content
            user_id: Optional user ID for filtering
            metadata: Optional metadata to store with the message
        """
        await self._ensure_ready()
        doc = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc),
            "memory_type": "working",
        }

        if user_id:
            doc["user_id"] = str(user_id)

        if metadata:
            doc["metadata"] = metadata

        await self.collection.insert_one(doc)
        logger.debug(f"Added {role} message to session {session_id}")

    async def get_context(
        self,
        session_id: str,
        limit: int = 10,
        user_id: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Retrieves the most recent messages (Short-Term Memory).

        Returns them in chronological order (Oldest -> Newest) for the LLM.
        Excludes cache documents (type='stm_summary').

        Args:
            session_id: Session identifier
            limit: Maximum number of messages to retrieve
            user_id: Optional user ID for filtering

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        await self._ensure_ready()
        query: dict[str, Any] = {"session_id": session_id, "type": {"$ne": "stm_summary"}}
        if user_id:
            query["user_id"] = str(user_id)

        # Sort DESCENDING + limit to select the NEWEST messages, then reverse
        # so the LLM receives them in chronological order (oldest -> newest).
        # Sorting ASCENDING before limit would return the OLDEST messages and
        # drop recent context once a session exceeds ``limit`` messages.
        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(limit)
        history = await cursor.to_list(length=limit)
        history.reverse()

        return [{"role": h["role"], "content": h["content"]} for h in history]

    async def get_recent_messages(
        self,
        session_id: str,
        limit: int = 10,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieves the most recent messages in reverse chronological order.

        Useful for displaying recent messages in UI.
        Excludes cache documents (type='stm_summary').

        Args:
            session_id: Session identifier
            limit: Maximum number of messages to retrieve
            user_id: Optional user ID for filtering

        Returns:
            List of full message documents
        """
        await self._ensure_ready()
        query: dict[str, Any] = {"session_id": session_id, "type": {"$ne": "stm_summary"}}
        if user_id:
            query["user_id"] = str(user_id)

        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_session_count(self, session_id: str, user_id: str | None = None) -> int:
        """Get the number of messages in a session (excludes cache docs).

        Alias for :meth:`get_message_count`; kept for backwards compatibility.
        """
        return await self.get_message_count(session_id, user_id)

    async def clear_session(self, session_id: str, user_id: str | None = None) -> None:
        """
        Wipes Short-Term memory for a session.

        Args:
            session_id: Session identifier
            user_id: Optional user ID for security filtering
        """
        await self._ensure_ready()
        query: dict[str, Any] = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)
        result = await self.collection.delete_many(query)
        logger.info(f"Cleared {result.deleted_count} messages from session {session_id}")

    async def delete_old_messages(
        self,
        session_id: str,
        keep_count: int = 10,
        user_id: str | None = None,
    ) -> int:
        """
        Deletes old messages from a session, keeping only the most recent ones.

        Useful for managing context window size.

        Args:
            session_id: Session identifier
            keep_count: Number of recent messages to keep
            user_id: Optional user ID for security filtering

        Returns:
            Number of messages deleted
        """
        await self._ensure_ready()
        # Never let the stm_summary cache doc occupy a "keep" slot or be deleted
        # as if it were a real message.
        query: dict[str, Any] = {"session_id": session_id, "type": {"$ne": "stm_summary"}}
        if user_id:
            query["user_id"] = str(user_id)

        # Get IDs of messages to keep
        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(keep_count)
        keep_messages = await cursor.to_list(length=keep_count)
        keep_ids = [msg["_id"] for msg in keep_messages]

        # Delete all others (excluding the summary cache doc)
        delete_query: dict[str, Any] = {
            "session_id": session_id,
            "type": {"$ne": "stm_summary"},
            "_id": {"$nin": keep_ids},
        }
        if user_id:
            delete_query["user_id"] = str(user_id)

        result = await self.collection.delete_many(delete_query)
        logger.info(f"Deleted {result.deleted_count} old messages from session {session_id}")
        return result.deleted_count
