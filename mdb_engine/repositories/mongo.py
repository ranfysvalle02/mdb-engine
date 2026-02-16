"""
MongoDB Repository Implementation

Implements the Repository interface using MongoDB through ScopedCollectionWrapper.
This provides automatic app scoping and security features.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

from bson import ObjectId

from .base import Entity, Repository

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Entity)


class MongoRepository(Repository[T], Generic[T]):
    """
    MongoDB implementation of the Repository interface.

    Uses ScopedCollectionWrapper for automatic app_id scoping
    and security features.

    Example:
        # Direct usage
        users_collection = db.users  # ScopedCollectionWrapper
        user_repo = MongoRepository(users_collection, User)

        # Find users
        users = await user_repo.find({"role": "admin"})

        # Add new user
        user = User(email="john@example.com", name="John")
        user_id = await user_repo.add(user)
    """

    def __init__(
        self,
        collection: ScopedCollectionWrapper,
        entity_class: type[T],
    ):
        """
        Initialize the MongoDB repository.

        Args:
            collection: ScopedCollectionWrapper for the collection
            entity_class: Entity subclass for this repository
        """
        self._collection = collection
        self._entity_class = entity_class

    def _to_entity(self, doc: dict[str, Any] | None) -> T | None:
        """Convert a MongoDB document to an entity."""
        if doc is None:
            return None
        return self._entity_class.from_dict(doc)

    def _to_document(self, entity: T, include_id: bool = False) -> dict[str, Any]:
        """Convert an entity to a MongoDB document."""
        doc = entity.to_dict()
        if not include_id and "_id" in doc:
            del doc["_id"]
        return doc

    async def get(self, id: str) -> T | None:
        """Get entity by ID."""
        try:
            object_id = ObjectId(id) if ObjectId.is_valid(id) else id
        except (TypeError, ValueError):
            return None

        doc = await self._collection.find_one({"_id": object_id})
        return self._to_entity(doc)

    async def find(
        self,
        filter: dict[str, Any] | None = None,
        skip: int = 0,
        limit: int = 100,
        sort: list[tuple] | None = None,
    ) -> list[T]:
        """Find entities matching a filter."""
        cursor = self._collection.find(filter or {})

        if skip > 0:
            cursor = cursor.skip(skip)
        if limit > 0:
            cursor = cursor.limit(limit)
        if sort:
            cursor = cursor.sort(sort)

        docs = await cursor.to_list(length=limit)
        return [self._to_entity(doc) for doc in docs]

    async def find_one(self, filter: dict[str, Any]) -> T | None:
        """Find a single entity matching a filter."""
        doc = await self._collection.find_one(filter)
        return self._to_entity(doc)

    async def add(self, entity: T) -> str:
        """Add a new entity and return its ID."""
        entity.created_at = datetime.now(timezone.utc)
        doc = self._to_document(entity)

        result = await self._collection.insert_one(doc)
        entity.id = str(result.inserted_id)

        logger.debug(f"Added {self._entity_class.__name__} with id={entity.id}")
        return entity.id

    async def add_many(self, entities: list[T]) -> list[str]:
        """Add multiple entities and return their IDs."""
        now = datetime.now(timezone.utc)
        docs = []

        for entity in entities:
            entity.created_at = now
            docs.append(self._to_document(entity))

        result = await self._collection.insert_many(docs)
        ids = [str(id) for id in result.inserted_ids]

        for entity, id in zip(entities, ids, strict=False):
            entity.id = id

        logger.debug(f"Added {len(ids)} {self._entity_class.__name__} entities")
        return ids

    async def update(self, id: str, entity: T) -> bool:
        """Update an entity by ID."""
        try:
            object_id = ObjectId(id) if ObjectId.is_valid(id) else id
        except (TypeError, ValueError):
            return False

        entity.updated_at = datetime.now(timezone.utc)
        doc = self._to_document(entity)

        result = await self._collection.update_one({"_id": object_id}, {"$set": doc})

        return result.modified_count > 0

    async def update_fields(self, id: str, fields: dict[str, Any]) -> bool:
        """Update specific fields of an entity."""
        try:
            object_id = ObjectId(id) if ObjectId.is_valid(id) else id
        except (TypeError, ValueError):
            return False

        fields["updated_at"] = datetime.now(timezone.utc)

        result = await self._collection.update_one({"_id": object_id}, {"$set": fields})

        return result.modified_count > 0

    async def delete(self, id: str) -> bool:
        """Delete an entity by ID."""
        try:
            object_id = ObjectId(id) if ObjectId.is_valid(id) else id
        except (TypeError, ValueError):
            return False

        result = await self._collection.delete_one({"_id": object_id})
        return result.deleted_count > 0

    async def count(self, filter: dict[str, Any] | None = None) -> int:
        """Count entities matching a filter."""
        return await self._collection.count_documents(filter or {})

    async def exists(self, id: str) -> bool:
        """Check if an entity exists."""
        try:
            object_id = ObjectId(id) if ObjectId.is_valid(id) else id
        except (TypeError, ValueError):
            return False

        doc = await self._collection.find_one({"_id": object_id}, projection={"_id": 1})
        return doc is not None

    # Additional MongoDB-specific methods

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Run an aggregation pipeline.

        Args:
            pipeline: MongoDB aggregation pipeline

        Returns:
            List of result documents
        """
        cursor = self._collection.aggregate(pipeline)
        return await cursor.to_list(length=None)

    async def update_many(
        self,
        filter: dict[str, Any],
        update: dict[str, Any],
    ) -> int:
        """
        Update multiple documents matching a filter.

        Args:
            filter: MongoDB filter
            update: Update operations

        Returns:
            Number of modified documents
        """
        if "$set" not in update:
            update = {"$set": update}

        update["$set"]["updated_at"] = datetime.now(timezone.utc)

        result = await self._collection.update_many(filter, update)
        return result.modified_count

    async def delete_many(self, filter: dict[str, Any]) -> int:
        """
        Delete multiple documents matching a filter.

        Args:
            filter: MongoDB filter

        Returns:
            Number of deleted documents
        """
        result = await self._collection.delete_many(filter)
        return result.deleted_count
