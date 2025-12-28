"""
App Database Wrapper

A MongoDB-style database abstraction layer for apps.
Follows MongoDB API conventions for familiarity and ease of use.

This module provides an easy-to-use API that matches MongoDB's API closely,
so apps can use familiar MongoDB methods. All operations automatically
handle app scoping and indexing behind the scenes.

This module is part of MDB_ENGINE - MongoDB Engine.

Usage:
    from mdb_engine.database import AppDB, Collection

    # In FastAPI route
    @bp.get("/")
    async def my_route(db: AppDB = Depends(get_app_db)):
        # MongoDB-style operations - familiar API!
        doc = await db.my_collection.find_one({"_id": "doc_123"})
        docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
        await db.my_collection.insert_one({"name": "Test"})
        count = await db.my_collection.count_documents({})
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from ..exceptions import MongoDBEngineError
from .scoped_wrapper import ScopedMongoWrapper

try:
    from pymongo.errors import (AutoReconnect, ConnectionFailure,
                                InvalidOperation, OperationFailure,
                                ServerSelectionTimeoutError)
except ImportError:
    OperationFailure = Exception
    AutoReconnect = Exception
    ConnectionFailure = Exception
    ServerSelectionTimeoutError = Exception

try:
    from motor.motor_asyncio import AsyncIOMotorCursor
    from pymongo.results import (DeleteResult, InsertManyResult,
                                 InsertOneResult, UpdateResult)
except ImportError:
    AsyncIOMotorCursor = None
    InsertOneResult = None
    InsertManyResult = None
    UpdateResult = None
    DeleteResult = None
    logging.warning("Failed to import Motor types. Type hints may not work correctly.")

logger = logging.getLogger(__name__)


class Collection:
    """
    A MongoDB collection wrapper that follows MongoDB API conventions.

    This class wraps a ScopedCollectionWrapper and provides MongoDB-style methods
    that match the familiar Motor/pymongo API. All operations automatically handle
    app scoping and indexing.

    Example:
        collection = Collection(scoped_wrapper.my_collection)
        doc = await collection.find_one({"_id": "doc_123"})
        docs = await collection.find({"status": "active"}).to_list(length=10)
        await collection.insert_one({"name": "Test"})
        count = await collection.count_documents({})
    """

    def __init__(self, scoped_collection):
        """
        Initialize a Collection wrapper around a ScopedCollectionWrapper.

        Args:
            scoped_collection: A ScopedCollectionWrapper instance
        """
        self._collection = scoped_collection

    async def find_one(
        self, filter: Optional[Dict[str, Any]] = None, *args, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Find a single document matching the filter.

        This matches MongoDB's find_one() API exactly.

        Args:
            filter: Optional dict of field/value pairs to filter by
                   Example: {"_id": "doc_123"}, {"status": "active"}
            *args, **kwargs: Additional arguments passed to find_one()
                            (e.g., projection, sort, etc.)

        Returns:
            The document as a dict, or None if not found

        Example:
            doc = await collection.find_one({"_id": "doc_123"})
            doc = await collection.find_one({"status": "active"})
        """
        try:
            return await self._collection.find_one(filter or {}, *args, **kwargs)
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
            logger.exception("Database operation failed in find_one")
            return None
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in find_one")
            raise MongoDBEngineError(
                "Error retrieving document",
                context={"operation": "find_one"},
            ) from e

    def find(
        self, filter: Optional[Dict[str, Any]] = None, *args, **kwargs
    ) -> AsyncIOMotorCursor:
        """
        Find documents matching the filter.

        This matches MongoDB's find() API exactly. Returns a cursor
        that you can iterate or call .to_list() on.

        Args:
            filter: Optional dict of field/value pairs to filter by
                   Example: {"status": "active"}, {"age": {"$gte": 18}}
            *args, **kwargs: Additional arguments passed to find()
                            (e.g., projection, sort, limit, skip, etc.)

        Returns:
            AsyncIOMotorCursor that can be iterated or converted to list

        Example:
            cursor = collection.find({"status": "active"})
            docs = await cursor.to_list(length=10)

            # Or with sort, limit
            cursor = collection.find({"status": "active"}).sort("created_at", -1).limit(10)
            docs = await cursor.to_list(length=None)
        """
        return self._collection.find(filter or {}, *args, **kwargs)

    async def insert_one(
        self, document: Dict[str, Any], *args, **kwargs
    ) -> InsertOneResult:
        """
        Insert a single document.

        This matches MongoDB's insert_one() API exactly.

        Args:
            document: The document to insert
            *args, **kwargs: Additional arguments passed to insert_one()

        Returns:
            InsertOneResult with inserted_id

        Example:
            result = await collection.insert_one({"name": "Test"})
            print(result.inserted_id)
        """
        try:
            return await self._collection.insert_one(document, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in insert_one")
            raise MongoDBEngineError(
                "Failed to insert document", context={"operation": "insert_one"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in insert_one")
            raise MongoDBEngineError(
                "Error inserting document",
                context={"operation": "insert_one"},
            ) from e

    async def insert_many(
        self, documents: List[Dict[str, Any]], *args, **kwargs
    ) -> InsertManyResult:
        """
        Insert multiple documents at once.

        This matches MongoDB's insert_many() API exactly.

        Args:
            documents: List of documents to insert
            *args, **kwargs: Additional arguments passed to insert_many()
                            (e.g., ordered=True/False)

        Returns:
            InsertManyResult with inserted_ids

        Example:
            result = await collection.insert_many([{"name": "A"}, {"name": "B"}])
            print(result.inserted_ids)
        """
        try:
            return await self._collection.insert_many(documents, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in insert_many")
            raise MongoDBEngineError(
                "Failed to insert documents",
                context={"operation": "insert_many", "count": len(documents)},
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in insert_many")
            raise MongoDBEngineError(
                "Error inserting documents",
                context={"operation": "insert_many", "count": len(documents)},
            ) from e

    async def update_one(
        self, filter: Dict[str, Any], update: Dict[str, Any], *args, **kwargs
    ) -> UpdateResult:
        """
        Update a single document matching the filter.

        This matches MongoDB's update_one() API exactly.

        Args:
            filter: Dict of field/value pairs to match documents
                   Example: {"_id": "doc_123"}
            update: Update operations (e.g., {"$set": {...}}, {"$inc": {...}})
            *args, **kwargs: Additional arguments passed to update_one()
                            (e.g., upsert=True/False)

        Returns:
            UpdateResult with modified_count and upserted_id

        Example:
            result = await collection.update_one(
                {"_id": "doc_123"},
                {"$set": {"status": "active"}}
            )
        """
        try:
            return await self._collection.update_one(filter, update, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in update_one")
            raise MongoDBEngineError(
                "Failed to update document", context={"operation": "update_one"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in update_one")
            raise MongoDBEngineError(
                "Error updating document",
                context={"operation": "update_one"},
            ) from e

    async def update_many(
        self, filter: Dict[str, Any], update: Dict[str, Any], *args, **kwargs
    ) -> UpdateResult:
        """
        Update multiple documents matching the filter.

        This matches MongoDB's update_many() API exactly.

        Args:
            filter: Dict of field/value pairs to match documents
            update: Update operations (e.g., {"$set": {...}}, {"$inc": {...}})
            *args, **kwargs: Additional arguments passed to update_many()
                            (e.g., upsert=True/False)

        Returns:
            UpdateResult with modified_count

        Example:
            result = await collection.update_many(
                {"status": "pending"},
                {"$set": {"status": "active"}}
            )
        """
        try:
            return await self._collection.update_many(filter, update, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in update_many")
            raise MongoDBEngineError(
                "Failed to update documents", context={"operation": "update_many"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in update_many")
            raise MongoDBEngineError(
                "Error updating documents",
                context={"operation": "update_many"},
            ) from e

    async def replace_one(
        self, filter: Dict[str, Any], replacement: Dict[str, Any], *args, **kwargs
    ) -> UpdateResult:
        """
        Replace a single document matching the filter.

        This matches MongoDB's replace_one() API exactly.
        Replaces the entire document with the replacement document.

        Args:
            filter: Dict of field/value pairs to match documents
                   Example: {"_id": "doc_123"}
            replacement: The replacement document (entire document, not update operators)
            *args, **kwargs: Additional arguments passed to replace_one()
                            (e.g., upsert=True/False)

        Returns:
            UpdateResult with modified_count and upserted_id

        Example:
            result = await collection.replace_one(
                {"_id": "doc_123"},
                {"_id": "doc_123", "name": "New Name", "status": "active"}
            )
        """
        try:
            # The underlying _collection is a ScopedCollectionWrapper
            # It doesn't have replace_one, so we use delete + insert for true replacement
            # This ensures proper app_id scoping
            upsert = kwargs.get("upsert", False)

            # Try to delete first (if document exists)
            delete_result = await self._collection.delete_one(filter)

            # If document was deleted or upsert is True, insert the replacement
            if delete_result.deleted_count > 0 or upsert:
                # Insert the replacement document (app_id will be auto-injected)
                insert_result = await self._collection.insert_one(replacement)
                # Return an UpdateResult-like object
                from pymongo.results import UpdateResult

                # Create UpdateResult with proper structure
                # modified_count = 1 if we deleted and inserted, 0 if we only inserted (upsert)
                modified_count = 1 if delete_result.deleted_count > 0 else 0
                upserted_id = (
                    insert_result.inserted_id
                    if upsert and delete_result.deleted_count == 0
                    else None
                )

                # Create a proper UpdateResult
                # UpdateResult expects raw_result dict with specific keys
                raw_result = {
                    "ok": 1.0,
                    "n": modified_count,
                    "nModified": modified_count,
                }
                if upserted_id:
                    raw_result["upserted"] = upserted_id

                return UpdateResult(raw_result, acknowledged=True)
            else:
                # Document not found and upsert=False
                from pymongo.results import UpdateResult

                return UpdateResult(
                    raw_result={"ok": 1.0, "n": 0, "nModified": 0}, acknowledged=True
                )
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in replace_one")
            raise MongoDBEngineError(
                "Failed to replace document", context={"operation": "replace_one"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in replace_one")
            raise MongoDBEngineError(
                "Error replacing document",
                context={"operation": "replace_one"},
            ) from e

    async def delete_one(self, filter: Dict[str, Any], *args, **kwargs) -> DeleteResult:
        """
        Delete a single document matching the filter.

        This matches MongoDB's delete_one() API exactly.

        Args:
            filter: Dict of field/value pairs to match documents
                   Example: {"_id": "doc_123"}
            *args, **kwargs: Additional arguments passed to delete_one()

        Returns:
            DeleteResult with deleted_count

        Example:
            result = await collection.delete_one({"_id": "doc_123"})
            print(result.deleted_count)
        """
        try:
            return await self._collection.delete_one(filter, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in delete_one")
            raise MongoDBEngineError(
                "Failed to delete document", context={"operation": "delete_one"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in delete_one")
            raise MongoDBEngineError(
                "Error deleting document",
                context={"operation": "delete_one"},
            ) from e

    async def delete_many(
        self, filter: Optional[Dict[str, Any]] = None, *args, **kwargs
    ) -> DeleteResult:
        """
        Delete multiple documents matching the filter.

        This matches MongoDB's delete_many() API exactly.

        Args:
            filter: Optional dict of field/value pairs to match documents
            *args, **kwargs: Additional arguments passed to delete_many()

        Returns:
            DeleteResult with deleted_count

        Example:
            result = await collection.delete_many({"status": "deleted"})
            print(result.deleted_count)
        """
        try:
            return await self._collection.delete_many(filter or {}, *args, **kwargs)
        except (OperationFailure, AutoReconnect) as e:
            logger.exception("Database operation failed in delete_many")
            raise MongoDBEngineError(
                "Failed to delete documents", context={"operation": "delete_many"}
            ) from e
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in delete_many")
            raise MongoDBEngineError(
                "Error deleting documents",
                context={"operation": "delete_many"},
            ) from e

    async def count_documents(
        self, filter: Optional[Dict[str, Any]] = None, *args, **kwargs
    ) -> int:
        """
        Count documents matching the filter.

        This matches MongoDB's count_documents() API exactly.

        Args:
            filter: Optional dict of field/value pairs to filter by
            *args, **kwargs: Additional arguments passed to count_documents()
                            (e.g., limit, skip)

        Returns:
            Number of matching documents

        Example:
            count = await collection.count_documents({"status": "active"})
            count = await collection.count_documents({})  # Count all
        """
        try:
            return await self._collection.count_documents(filter or {}, *args, **kwargs)
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
            logger.exception("Database operation failed in count_documents")
            return 0
        except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
            logger.exception("Error in count_documents")
            raise MongoDBEngineError(
                "Error counting documents",
                context={"operation": "count_documents"},
            ) from e

    def aggregate(
        self, pipeline: List[Dict[str, Any]], *args, **kwargs
    ) -> AsyncIOMotorCursor:
        """
        Perform aggregation pipeline.

        This matches MongoDB's aggregate() API exactly.

        Args:
            pipeline: List of aggregation stages
            *args, **kwargs: Additional arguments passed to aggregate()

        Returns:
            AsyncIOMotorCursor for iterating results

        Example:
            pipeline = [
                {"$match": {"status": "active"}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}}
            ]
            cursor = collection.aggregate(pipeline)
            results = await cursor.to_list(length=None)
        """
        return self._collection.aggregate(pipeline, *args, **kwargs)


class AppDB:
    """
    A MongoDB-style database interface for apps.

    This class wraps ScopedMongoWrapper and provides MongoDB-style methods
    that match the familiar Motor/pymongo API. All operations automatically
    handle app scoping and indexing.

    Example:
        from mdb_engine.database import AppDB
        from mdb_engine.database import get_app_db

        @bp.get("/")
        async def my_route(db: AppDB = Depends(get_app_db)):
            # MongoDB-style operations - familiar API!
            doc = await db.users.find_one({"_id": "user_123"})
            docs = await db.users.find({"status": "active"}).to_list(length=10)
            await db.users.insert_one({"name": "John", "email": "john@example.com"})
            count = await db.users.count_documents({})
    """

    def __init__(self, scoped_wrapper: ScopedMongoWrapper):
        """
        Initialize AppDB with a ScopedMongoWrapper.

        Args:
            scoped_wrapper: A ScopedMongoWrapper instance (typically from application layer)
        """
        if not ScopedMongoWrapper:
            raise RuntimeError("ScopedMongoWrapper is not available. Check imports.")

        self._wrapper = scoped_wrapper
        self._collection_cache: Dict[str, Collection] = {}

    def collection(self, name: str) -> Collection:
        """
        Get a Collection wrapper for a collection by name.

        Args:
            name: The collection name (base name, without app prefix)
                 Example: "users", "products", "orders"

        Returns:
            A Collection instance for easy database operations

        Example:
            users = db.collection("users")
            doc = await users.get("user_123")
        """
        if name in self._collection_cache:
            return self._collection_cache[name]

        # Get the scoped collection from wrapper
        scoped_collection = getattr(self._wrapper, name)

        # Create and cache Collection wrapper
        collection = Collection(scoped_collection)
        self._collection_cache[name] = collection

        return collection

    def __getattr__(self, name: str) -> Collection:
        """
        Allow direct access to collections as attributes.

        Example:
            db.users.get("user_123")  # Instead of db.collection("users").get("user_123")
        """
        # Only proxy collection names, not internal attributes
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return self.collection(name)

    @property
    def raw(self) -> ScopedMongoWrapper:
        """
        Access the underlying ScopedMongoWrapper for advanced operations.

        Use this if you need to access MongoDB-specific features that aren't
        covered by the simple API. For most cases, you won't need this.

        Example:
            # Advanced aggregation
            pipeline = [{"$match": {...}}, {"$group": {...}}]
            results = await db.raw.my_collection.aggregate(pipeline).to_list(None)
        """
        return self._wrapper

    @property
    def database(self):
        """
        Access the underlying AsyncIOMotorDatabase (unscoped).

        This is useful for advanced operations that need direct access to the
        real database without scoping, such as index management or administrative
        operations.

        Returns:
            The underlying AsyncIOMotorDatabase instance

        Example:
            # Access underlying database for index management
            real_db = db.database
            collection = real_db["my_collection"]
            index_manager = AsyncAtlasIndexManager(collection)
        """
        return self._wrapper.database


# FastAPI dependency helper
async def get_app_db(request, get_scoped_db_func: Callable) -> AppDB:
    """
    FastAPI Dependency: Provides an AppDB instance.

    This is a convenience wrapper around get_scoped_db that returns
    an AppDB instance instead of ScopedMongoWrapper.

    Args:
        request: FastAPI Request object
        get_scoped_db_func: Required callable that takes a request and returns ScopedMongoWrapper.

    Usage:
        from mdb_engine.database import get_app_db
        from my_app import get_scoped_db  # Your application layer

        @bp.get("/")
        async def my_route(
            request: Request,
            db: AppDB = Depends(lambda r: get_app_db(r, get_scoped_db_func=get_scoped_db))
        ):
            doc = await db.users.get("user_123")
    """
    if not get_scoped_db_func:
        raise ValueError(
            "get_app_db requires get_scoped_db_func parameter. "
            "Provide a callable that takes a Request and returns ScopedMongoWrapper."
        )

    scoped_db = await get_scoped_db_func(request)
    return AppDB(scoped_db)


# ============================================================================
# Database Factory - Convenient factory for creating scoped database interfaces
# ============================================================================
