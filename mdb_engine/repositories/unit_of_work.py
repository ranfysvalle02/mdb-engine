"""
Unit of Work Pattern

Manages repository access and provides a clean interface for data operations.
The UnitOfWork acts as a factory for repositories and manages their lifecycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

from .base import Entity, Repository
from .mongo import MongoRepository

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Entity)


class UnitOfWork:
    """
    Unit of Work for managing repository access.

    Provides a clean interface for accessing repositories through
    attribute access (e.g., uow.users, uow.orders).

    The UnitOfWork is request-scoped - one instance per HTTP request.
    Repositories are created lazily and cached for the duration of the request.

    Usage:
        # In a route handler
        @app.get("/users/{user_id}")
        async def get_user(user_id: str, ctx: RequestContext = Depends(get_request_context)):
            # Access repository through UnitOfWork
            user = await ctx.uow.users.get(user_id)
            return user

        # With explicit repository method
        @app.get("/orders")
        async def list_orders(ctx: RequestContext = Depends(get_request_context)):
            repo = ctx.uow.repository("orders", Order)
            return await repo.find({"status": "pending"})

    Repository Naming Convention:
        - Attribute access uses collection name: uow.users -> users collection
        - The entity class defaults to Entity if not registered
        - Register entity classes for type-safe repositories
    """

    def __init__(
        self,
        db: Any,  # ScopedMongoWrapper - avoid import cycle
        entity_registry: dict[str, type[Entity]] | None = None,
    ):
        """
        Initialize the Unit of Work.

        Args:
            db: ScopedMongoWrapper for database access
            entity_registry: Optional mapping of collection names to entity classes
        """
        self._db = db
        self._repositories: dict[str, Repository] = {}
        self._entity_registry: dict[str, type[Entity]] = entity_registry or {}

    def register_entity(self, collection_name: str, entity_class: type[Entity]) -> None:
        """
        Register an entity class for a collection.

        This enables type-safe repository access.

        Args:
            collection_name: Name of the collection
            entity_class: Entity subclass for this collection
        """
        self._entity_registry[collection_name] = entity_class

    def repository(
        self,
        name: str,
        entity_class: type[T] | None = None,
    ) -> Repository[T]:
        """
        Get or create a repository for a collection.

        Args:
            name: Collection name
            entity_class: Optional entity class override

        Returns:
            Repository instance for the collection
        """
        if name in self._repositories:
            return self._repositories[name]

        # Determine entity class
        if entity_class is None:
            entity_class = self._entity_registry.get(name, Entity)

        # Get collection from db wrapper
        collection = getattr(self._db, name)

        # Create repository
        repo = MongoRepository(collection, entity_class)
        self._repositories[name] = repo

        logger.debug(f"Created repository for '{name}' with entity {entity_class.__name__}")
        return repo

    def __getattr__(self, name: str) -> Repository:
        """
        Access repositories via attribute syntax.

        Example:
            uow.users  # Returns Repository for 'users' collection
            uow.orders  # Returns Repository for 'orders' collection
        """
        # Prevent recursion on private attributes
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        return self.repository(name)

    @property
    def db(self) -> ScopedMongoWrapper:
        """
        Direct access to the underlying ScopedMongoWrapper.

        Use this for operations not covered by the Repository interface,
        like complex aggregations or raw queries.

        Example:
            # Complex aggregation
            pipeline = [{"$match": {...}}, {"$group": {...}}]
            results = await ctx.uow.db.users.aggregate(pipeline).to_list(None)
        """
        return self._db

    def dispose(self) -> None:
        """
        Dispose of the UnitOfWork and clear cached repositories.

        Called automatically at the end of a request scope.
        """
        self._repositories.clear()
        logger.debug("UnitOfWork disposed")


class TypedUnitOfWork(UnitOfWork, Generic[T]):
    """
    Generic typed UnitOfWork for better IDE support.

    This is a convenience class that provides type hints for specific
    repository types.

    Usage:
        class MyUnitOfWork(TypedUnitOfWork):
            @property
            def users(self) -> Repository[User]:
                return self.repository("users", User)

            @property
            def orders(self) -> Repository[Order]:
                return self.repository("orders", Order)
    """

    pass
