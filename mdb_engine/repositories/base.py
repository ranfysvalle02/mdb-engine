"""
Abstract Repository Pattern

Defines the repository interface that abstracts data access operations.
This allows domain services to work with any data store implementation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from bson import ObjectId


@dataclass
class Entity:
    """
    Base class for domain entities.

    All entities have an ID and timestamps. Subclass this for your domain models.

    Example:
        @dataclass
        class User(Entity):
            email: str
            name: str
            role: str = "user"
    """

    id: str | None = None
    created_at: datetime | None = field(default=None)
    updated_at: datetime | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Convert entity to dictionary for storage."""
        data = {}
        for key, value in self.__dict__.items():
            if value is not None:
                if key == "id":
                    # Convert string ID to ObjectId for MongoDB
                    if value and ObjectId.is_valid(value):
                        data["_id"] = ObjectId(value)
                    else:
                        data["_id"] = value
                else:
                    data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        """Create entity from dictionary (e.g., from database)."""
        if data is None:
            return None

        # Convert _id to id
        if "_id" in data:
            data["id"] = str(data.pop("_id"))

        # Get field names from dataclass
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(cls)}

        # Filter to only known fields
        filtered_data = {k: v for k, v in data.items() if k in field_names}

        return cls(**filtered_data)


T = TypeVar("T", bound=Entity)


class Repository(ABC, Generic[T]):
    """
    Abstract repository interface for data access.

    This interface defines standard CRUD operations that can be
    implemented for any data store (MongoDB, PostgreSQL, in-memory, etc.)

    Type parameter T should be an Entity subclass.

    Example:
        class UserRepository(Repository[User]):
            async def find_by_email(self, email: str) -> Optional[User]:
                users = await self.find({"email": email}, limit=1)
                return users[0] if users else None
    """

    @abstractmethod
    async def get(self, id: str) -> T | None:
        """
        Get a single entity by ID.

        Args:
            id: Entity ID

        Returns:
            Entity if found, None otherwise
        """
        pass

    @abstractmethod
    async def find(
        self,
        filter: dict[str, Any] | None = None,
        skip: int = 0,
        limit: int = 100,
        sort: list[tuple] | None = None,
    ) -> list[T]:
        """
        Find entities matching a filter.

        Args:
            filter: MongoDB-style filter dictionary
            skip: Number of documents to skip
            limit: Maximum documents to return
            sort: List of (field, direction) tuples

        Returns:
            List of matching entities
        """
        pass

    @abstractmethod
    async def find_one(
        self,
        filter: dict[str, Any],
    ) -> T | None:
        """
        Find a single entity matching a filter.

        Args:
            filter: MongoDB-style filter dictionary

        Returns:
            First matching entity or None
        """
        pass

    @abstractmethod
    async def add(self, entity: T) -> str:
        """
        Add a new entity.

        Args:
            entity: Entity to add

        Returns:
            ID of the created entity
        """
        pass

    @abstractmethod
    async def add_many(self, entities: list[T]) -> list[str]:
        """
        Add multiple entities.

        Args:
            entities: List of entities to add

        Returns:
            List of created entity IDs
        """
        pass

    @abstractmethod
    async def update(self, id: str, entity: T) -> bool:
        """
        Update an existing entity.

        Args:
            id: Entity ID
            entity: Updated entity data

        Returns:
            True if entity was updated, False if not found
        """
        pass

    @abstractmethod
    async def update_fields(self, id: str, fields: dict[str, Any]) -> bool:
        """
        Update specific fields of an entity.

        Args:
            id: Entity ID
            fields: Dictionary of fields to update

        Returns:
            True if entity was updated, False if not found
        """
        pass

    @abstractmethod
    async def delete(self, id: str) -> bool:
        """
        Delete an entity by ID.

        Args:
            id: Entity ID

        Returns:
            True if entity was deleted, False if not found
        """
        pass

    @abstractmethod
    async def count(self, filter: dict[str, Any] | None = None) -> int:
        """
        Count entities matching a filter.

        Args:
            filter: MongoDB-style filter dictionary

        Returns:
            Count of matching entities
        """
        pass

    @abstractmethod
    async def exists(self, id: str) -> bool:
        """
        Check if an entity exists.

        Args:
            id: Entity ID

        Returns:
            True if entity exists
        """
        pass


class InMemoryRepository(Repository[T]):
    """
    In-memory repository implementation for testing.

    Stores entities in a dictionary, useful for unit tests
    without database dependencies.
    """

    def __init__(self, entity_class: type):
        self._entity_class = entity_class
        self._storage: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def get(self, id: str) -> T | None:
        data = self._storage.get(id)
        if data is None:
            return None
        return self._entity_class.from_dict(data)

    async def find(
        self,
        filter: dict[str, Any] | None = None,
        skip: int = 0,
        limit: int = 100,
        sort: list[tuple] | None = None,
    ) -> list[T]:
        results = []
        for data in self._storage.values():
            if filter is None or self._matches_filter(data, filter):
                results.append(self._entity_class.from_dict(data))

        # Apply skip and limit
        return results[skip : skip + limit]

    async def find_one(self, filter: dict[str, Any]) -> T | None:
        results = await self.find(filter, limit=1)
        return results[0] if results else None

    async def add(self, entity: T) -> str:
        self._counter += 1
        id = str(self._counter)
        entity.id = id
        entity.created_at = datetime.now(timezone.utc)
        self._storage[id] = entity.to_dict()
        return id

    async def add_many(self, entities: list[T]) -> list[str]:
        return [await self.add(e) for e in entities]

    async def update(self, id: str, entity: T) -> bool:
        if id not in self._storage:
            return False
        entity.id = id
        entity.updated_at = datetime.now(timezone.utc)
        self._storage[id] = entity.to_dict()
        return True

    async def update_fields(self, id: str, fields: dict[str, Any]) -> bool:
        if id not in self._storage:
            return False
        self._storage[id].update(fields)
        self._storage[id]["updated_at"] = datetime.now(timezone.utc)
        return True

    async def delete(self, id: str) -> bool:
        if id not in self._storage:
            return False
        del self._storage[id]
        return True

    async def count(self, filter: dict[str, Any] | None = None) -> int:
        if filter is None:
            return len(self._storage)
        return len(await self.find(filter, limit=999999))

    async def exists(self, id: str) -> bool:
        return id in self._storage

    def _matches_filter(self, data: dict[str, Any], filter: dict[str, Any]) -> bool:
        """Simple filter matching for testing."""
        for key, value in filter.items():
            if key not in data:
                return False
            if data[key] != value:
                return False
        return True

    def clear(self) -> None:
        """Clear all entities (useful for test setup)."""
        self._storage.clear()
        self._counter = 0
