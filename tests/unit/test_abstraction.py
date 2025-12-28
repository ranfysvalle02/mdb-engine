"""
Unit tests for database abstraction layer.

Tests Collection wrapper class methods.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import (AutoReconnect, ConnectionFailure, InvalidOperation,
                            OperationFailure)

from mdb_engine.database.abstraction import Collection
from mdb_engine.exceptions import MongoDBEngineError


@pytest.fixture
def mock_scoped_collection():
    """Create a mock ScopedCollectionWrapper."""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "test_id", "name": "Test"})
    collection.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[]))
    )
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    collection.insert_many = AsyncMock(
        return_value=MagicMock(inserted_ids=["id1", "id2"])
    )
    collection.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1, upserted_id=None)
    )
    collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    collection.count_documents = AsyncMock(return_value=5)
    collection.aggregate = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[]))
    )
    return collection


class TestCollectionFindOne:
    """Test Collection.find_one method."""

    @pytest.mark.asyncio
    async def test_find_one_success(self, mock_scoped_collection):
        """Test successful find_one operation."""
        collection = Collection(mock_scoped_collection)
        result = await collection.find_one({"_id": "test_id"})

        assert result == {"_id": "test_id", "name": "Test"}
        mock_scoped_collection.find_one.assert_called_once_with({"_id": "test_id"})

    @pytest.mark.asyncio
    async def test_find_one_none_filter(self, mock_scoped_collection):
        """Test find_one with None filter (lines 104)."""
        collection = Collection(mock_scoped_collection)
        result = await collection.find_one(None)

        assert result == {"_id": "test_id", "name": "Test"}
        mock_scoped_collection.find_one.assert_called_once_with({})

    @pytest.mark.asyncio
    async def test_find_one_operation_failure(self, mock_scoped_collection):
        """Test find_one handles OperationFailure (lines 105-107)."""
        mock_scoped_collection.find_one = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        result = await collection.find_one({"_id": "test_id"})

        assert result is None

    @pytest.mark.asyncio
    async def test_find_one_connection_failure(self, mock_scoped_collection):
        """Test find_one handles ConnectionFailure (lines 105-107)."""
        mock_scoped_collection.find_one = AsyncMock(
            side_effect=ConnectionFailure("Connection error")
        )
        collection = Collection(mock_scoped_collection)

        result = await collection.find_one({"_id": "test_id"})

        assert result is None

    @pytest.mark.asyncio
    async def test_find_one_invalid_operation_error(self, mock_scoped_collection):
        """Test find_one raises MongoDBEngineError for InvalidOperation (lines 108-113)."""
        mock_scoped_collection.find_one = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error retrieving document"):
            await collection.find_one({"_id": "test_id"})

    @pytest.mark.asyncio
    async def test_find_one_type_error(self, mock_scoped_collection):
        """Test find_one raises MongoDBEngineError for TypeError (lines 108-113)."""
        mock_scoped_collection.find_one = AsyncMock(side_effect=TypeError("Type error"))
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error retrieving document"):
            await collection.find_one({"_id": "test_id"})


class TestCollectionFind:
    """Test Collection.find method."""

    def test_find_success(self, mock_scoped_collection):
        """Test successful find operation."""
        collection = Collection(mock_scoped_collection)
        cursor = collection.find({"status": "active"})

        assert cursor is not None
        mock_scoped_collection.find.assert_called_once_with({"status": "active"})

    def test_find_none_filter(self, mock_scoped_collection):
        """Test find with None filter (line 141)."""
        collection = Collection(mock_scoped_collection)
        cursor = collection.find(None)

        assert cursor is not None
        mock_scoped_collection.find.assert_called_once_with({})


class TestCollectionInsertOne:
    """Test Collection.insert_one method."""

    @pytest.mark.asyncio
    async def test_insert_one_success(self, mock_scoped_collection):
        """Test successful insert_one operation."""
        collection = Collection(mock_scoped_collection)
        doc = {"name": "Test"}
        result = await collection.insert_one(doc)

        assert result.inserted_id == "test_id"
        mock_scoped_collection.insert_one.assert_called_once_with(doc)

    @pytest.mark.asyncio
    async def test_insert_one_operation_failure(self, mock_scoped_collection):
        """Test insert_one handles OperationFailure (lines 164-168)."""
        mock_scoped_collection.insert_one = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to insert document"):
            await collection.insert_one({"name": "Test"})

    @pytest.mark.asyncio
    async def test_insert_one_auto_reconnect(self, mock_scoped_collection):
        """Test insert_one handles AutoReconnect (lines 164-168)."""
        mock_scoped_collection.insert_one = AsyncMock(
            side_effect=AutoReconnect("Reconnect")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to insert document"):
            await collection.insert_one({"name": "Test"})

    @pytest.mark.asyncio
    async def test_insert_one_invalid_operation_error(self, mock_scoped_collection):
        """Test insert_one raises MongoDBEngineError for InvalidOperation (lines 169-174)."""
        mock_scoped_collection.insert_one = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error inserting document"):
            await collection.insert_one({"name": "Test"})


class TestCollectionInsertMany:
    """Test Collection.insert_many method."""

    @pytest.mark.asyncio
    async def test_insert_many_success(self, mock_scoped_collection):
        """Test successful insert_many operation."""
        collection = Collection(mock_scoped_collection)
        docs = [{"name": "Test1"}, {"name": "Test2"}]
        result = await collection.insert_many(docs)

        assert len(result.inserted_ids) == 2
        mock_scoped_collection.insert_many.assert_called_once_with(docs)

    @pytest.mark.asyncio
    async def test_insert_many_operation_failure(self, mock_scoped_collection):
        """Test insert_many handles OperationFailure (lines 198-203)."""
        mock_scoped_collection.insert_many = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to insert documents"):
            await collection.insert_many([{"name": "Test"}])

    @pytest.mark.asyncio
    async def test_insert_many_invalid_operation_error(self, mock_scoped_collection):
        """Test insert_many raises MongoDBEngineError for InvalidOperation (lines 204-209)."""
        mock_scoped_collection.insert_many = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error inserting documents"):
            await collection.insert_many([{"name": "Test"}])


class TestCollectionUpdateOne:
    """Test Collection.update_one method."""

    @pytest.mark.asyncio
    async def test_update_one_success(self, mock_scoped_collection):
        """Test successful update_one operation."""
        collection = Collection(mock_scoped_collection)
        filter_dict = {"_id": "test_id"}
        update = {"$set": {"status": "active"}}
        result = await collection.update_one(filter_dict, update)

        assert result.modified_count == 1
        mock_scoped_collection.update_one.assert_called_once_with(filter_dict, update)

    @pytest.mark.asyncio
    async def test_update_one_operation_failure(self, mock_scoped_collection):
        """Test update_one handles OperationFailure (lines 237-241)."""
        mock_scoped_collection.update_one = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to update document"):
            await collection.update_one(
                {"_id": "test_id"}, {"$set": {"status": "active"}}
            )

    @pytest.mark.asyncio
    async def test_update_one_invalid_operation_error(self, mock_scoped_collection):
        """Test update_one raises MongoDBEngineError for InvalidOperation (lines 242-247)."""
        mock_scoped_collection.update_one = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error updating document"):
            await collection.update_one(
                {"_id": "test_id"}, {"$set": {"status": "active"}}
            )


class TestCollectionUpdateMany:
    """Test Collection.update_many method."""

    @pytest.mark.asyncio
    async def test_update_many_success(self, mock_scoped_collection):
        """Test successful update_many operation."""
        collection = Collection(mock_scoped_collection)
        filter_dict = {"status": "pending"}
        update = {"$set": {"status": "active"}}
        result = await collection.update_many(filter_dict, update)

        assert result.modified_count == 2
        mock_scoped_collection.update_many.assert_called_once_with(filter_dict, update)

    @pytest.mark.asyncio
    async def test_update_many_operation_failure(self, mock_scoped_collection):
        """Test update_many handles OperationFailure (lines 274-278)."""
        mock_scoped_collection.update_many = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to update documents"):
            await collection.update_many(
                {"status": "pending"}, {"$set": {"status": "active"}}
            )

    @pytest.mark.asyncio
    async def test_update_many_invalid_operation_error(self, mock_scoped_collection):
        """Test update_many raises MongoDBEngineError for InvalidOperation (lines 279-284)."""
        mock_scoped_collection.update_many = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error updating documents"):
            await collection.update_many(
                {"status": "pending"}, {"$set": {"status": "active"}}
            )


class TestCollectionDeleteOne:
    """Test Collection.delete_one method."""

    @pytest.mark.asyncio
    async def test_delete_one_success(self, mock_scoped_collection):
        """Test successful delete_one operation."""
        collection = Collection(mock_scoped_collection)
        filter_dict = {"_id": "test_id"}
        result = await collection.delete_one(filter_dict)

        assert result.deleted_count == 1
        mock_scoped_collection.delete_one.assert_called_once_with(filter_dict)

    @pytest.mark.asyncio
    async def test_delete_one_operation_failure(self, mock_scoped_collection):
        """Test delete_one handles OperationFailure (lines 386-390)."""
        mock_scoped_collection.delete_one = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to delete document"):
            await collection.delete_one({"_id": "test_id"})

    @pytest.mark.asyncio
    async def test_delete_one_invalid_operation_error(self, mock_scoped_collection):
        """Test delete_one raises MongoDBEngineError for InvalidOperation (lines 391-396)."""
        mock_scoped_collection.delete_one = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error deleting document"):
            await collection.delete_one({"_id": "test_id"})


class TestCollectionDeleteMany:
    """Test Collection.delete_many method."""

    @pytest.mark.asyncio
    async def test_delete_many_success(self, mock_scoped_collection):
        """Test successful delete_many operation."""
        collection = Collection(mock_scoped_collection)
        filter_dict = {"status": "deleted"}
        result = await collection.delete_many(filter_dict)

        assert result.deleted_count == 2
        mock_scoped_collection.delete_many.assert_called_once_with(filter_dict)

    @pytest.mark.asyncio
    async def test_delete_many_none_filter(self, mock_scoped_collection):
        """Test delete_many with None filter (line 418)."""
        collection = Collection(mock_scoped_collection)
        result = await collection.delete_many(None)

        assert result.deleted_count == 2
        mock_scoped_collection.delete_many.assert_called_once_with({})

    @pytest.mark.asyncio
    async def test_delete_many_operation_failure(self, mock_scoped_collection):
        """Test delete_many handles OperationFailure (lines 419-423)."""
        mock_scoped_collection.delete_many = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to delete documents"):
            await collection.delete_many({"status": "deleted"})

    @pytest.mark.asyncio
    async def test_delete_many_invalid_operation_error(self, mock_scoped_collection):
        """Test delete_many raises MongoDBEngineError for InvalidOperation (lines 424-429)."""
        mock_scoped_collection.delete_many = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error deleting documents"):
            await collection.delete_many({"status": "deleted"})


class TestCollectionCountDocuments:
    """Test Collection.count_documents method."""

    @pytest.mark.asyncio
    async def test_count_documents_success(self, mock_scoped_collection):
        """Test successful count_documents operation."""
        collection = Collection(mock_scoped_collection)
        result = await collection.count_documents({"status": "active"})

        assert result == 5
        mock_scoped_collection.count_documents.assert_called_once_with(
            {"status": "active"}
        )

    @pytest.mark.asyncio
    async def test_count_documents_none_filter(self, mock_scoped_collection):
        """Test count_documents with None filter (line 452)."""
        collection = Collection(mock_scoped_collection)
        result = await collection.count_documents(None)

        assert result == 5
        mock_scoped_collection.count_documents.assert_called_once_with({})

    @pytest.mark.asyncio
    async def test_count_documents_operation_failure(self, mock_scoped_collection):
        """Test count_documents handles OperationFailure (lines 453-455)."""
        mock_scoped_collection.count_documents = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        result = await collection.count_documents({"status": "active"})

        assert result == 0

    @pytest.mark.asyncio
    async def test_count_documents_connection_failure(self, mock_scoped_collection):
        """Test count_documents handles ConnectionFailure (lines 453-455)."""
        mock_scoped_collection.count_documents = AsyncMock(
            side_effect=ConnectionFailure("Connection error")
        )
        collection = Collection(mock_scoped_collection)

        result = await collection.count_documents({"status": "active"})

        assert result == 0

    @pytest.mark.asyncio
    async def test_count_documents_invalid_operation_error(
        self, mock_scoped_collection
    ):
        """Test count_documents raises MongoDBEngineError for InvalidOperation (lines 456-461)."""
        mock_scoped_collection.count_documents = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error counting documents"):
            await collection.count_documents({"status": "active"})


class TestCollectionAggregate:
    """Test Collection.aggregate method."""

    def test_aggregate_success(self, mock_scoped_collection):
        """Test successful aggregate operation."""
        collection = Collection(mock_scoped_collection)
        pipeline = [{"$match": {"status": "active"}}]
        cursor = collection.aggregate(pipeline)

        assert cursor is not None
        mock_scoped_collection.aggregate.assert_called_once_with(pipeline)


class TestCollectionReplaceOne:
    """Test Collection.replace_one method."""

    @pytest.mark.asyncio
    async def test_replace_one_success(self, mock_scoped_collection):
        """Test successful replace_one operation (lines 311-346)."""
        mock_scoped_collection.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )
        mock_scoped_collection.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="new_id")
        )
        collection = Collection(mock_scoped_collection)

        filter_dict = {"_id": "test_id"}
        replacement = {"_id": "test_id", "name": "New Name"}
        result = await collection.replace_one(filter_dict, replacement)

        assert result.modified_count == 1
        mock_scoped_collection.delete_one.assert_called_once_with(filter_dict)
        mock_scoped_collection.insert_one.assert_called_once_with(replacement)

    @pytest.mark.asyncio
    async def test_replace_one_upsert(self, mock_scoped_collection):
        """Test replace_one with upsert=True (lines 315-346)."""
        mock_scoped_collection.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )
        mock_scoped_collection.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="new_id")
        )
        collection = Collection(mock_scoped_collection)

        filter_dict = {"_id": "test_id"}
        replacement = {"_id": "test_id", "name": "New Name"}
        result = await collection.replace_one(filter_dict, replacement, upsert=True)

        assert result.modified_count == 0
        assert result.upserted_id == "new_id"
        mock_scoped_collection.delete_one.assert_called_once_with(filter_dict)
        mock_scoped_collection.insert_one.assert_called_once_with(replacement)

    @pytest.mark.asyncio
    async def test_replace_one_not_found_no_upsert(self, mock_scoped_collection):
        """Test replace_one when document not found and upsert=False (lines 347-353)."""
        mock_scoped_collection.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )
        collection = Collection(mock_scoped_collection)

        filter_dict = {"_id": "test_id"}
        replacement = {"_id": "test_id", "name": "New Name"}
        result = await collection.replace_one(filter_dict, replacement, upsert=False)

        assert result.modified_count == 0
        assert result.upserted_id is None
        mock_scoped_collection.delete_one.assert_called_once_with(filter_dict)
        mock_scoped_collection.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_replace_one_operation_failure(self, mock_scoped_collection):
        """Test replace_one handles OperationFailure (lines 354-358)."""
        mock_scoped_collection.delete_one = AsyncMock(
            side_effect=OperationFailure("DB error")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Failed to replace document"):
            await collection.replace_one({"_id": "test_id"}, {"name": "New"})

    @pytest.mark.asyncio
    async def test_replace_one_invalid_operation_error(self, mock_scoped_collection):
        """Test replace_one raises MongoDBEngineError for InvalidOperation (lines 359-364)."""
        mock_scoped_collection.delete_one = AsyncMock(
            side_effect=InvalidOperation("Invalid")
        )
        collection = Collection(mock_scoped_collection)

        with pytest.raises(MongoDBEngineError, match="Error replacing document"):
            await collection.replace_one({"_id": "test_id"}, {"name": "New"})


class TestAppDB:
    """Test AppDB class."""

    def test_app_db_init(self, mock_scoped_collection):
        """Test AppDB initialization."""
        from mdb_engine.database.abstraction import AppDB
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        app_db = AppDB(mock_db)

        assert app_db._wrapper is mock_db
        assert app_db._collection_cache == {}

    def test_app_db_collection(self, mock_scoped_collection):
        """Test AppDB.collection method (lines 523-548)."""
        from mdb_engine.database.abstraction import AppDB, Collection
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        mock_db.users = mock_scoped_collection

        app_db = AppDB(mock_db)
        collection = app_db.collection("users")

        assert isinstance(collection, Collection)
        assert collection is app_db.collection("users")  # Should be cached

    def test_app_db_getattr(self, mock_scoped_collection):
        """Test AppDB.__getattr__ method (lines 550-562)."""
        from mdb_engine.database.abstraction import AppDB
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        mock_db.users = mock_scoped_collection

        app_db = AppDB(mock_db)
        collection = app_db.users

        assert isinstance(collection, Collection)

    def test_app_db_getattr_private_attribute(self, mock_scoped_collection):
        """Test AppDB.__getattr__ raises error for private attributes (lines 558-561)."""
        from mdb_engine.database.abstraction import AppDB
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        app_db = AppDB(mock_db)

        with pytest.raises(AttributeError):
            _ = app_db._private

    def test_app_db_raw_property(self, mock_scoped_collection):
        """Test AppDB.raw property (lines 564-577)."""
        from mdb_engine.database.abstraction import AppDB
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        app_db = AppDB(mock_db)

        assert app_db.raw is mock_db

    def test_app_db_database_property(self, mock_scoped_collection):
        """Test AppDB.database property (lines 579-597)."""
        from mdb_engine.database.abstraction import AppDB
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_db = MagicMock(spec=ScopedMongoWrapper)
        mock_db.database = MagicMock()
        app_db = AppDB(mock_db)

        assert app_db.database is mock_db.database

    def test_app_db_init_runtime_error(self):
        """Test AppDB.__init__ raises RuntimeError when ScopedMongoWrapper is falsy (line 518)."""
        from mdb_engine.database.abstraction import AppDB

        # Mock ScopedMongoWrapper to be falsy (None)
        with patch("mdb_engine.database.abstraction.ScopedMongoWrapper", None):
            with pytest.raises(
                RuntimeError, match="ScopedMongoWrapper is not available"
            ):
                AppDB(None)

    @pytest.mark.asyncio
    async def test_get_app_db(self, mock_scoped_collection):
        """Test get_app_db function (lines 601-630)."""
        from mdb_engine.database.abstraction import AppDB, get_app_db
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        mock_request = MagicMock()
        mock_db = MagicMock(spec=ScopedMongoWrapper)

        async def get_scoped_db_func(request):
            return mock_db

        app_db = await get_app_db(mock_request, get_scoped_db_func=get_scoped_db_func)

        assert isinstance(app_db, AppDB)
        assert app_db._wrapper is mock_db

    @pytest.mark.asyncio
    async def test_get_app_db_no_func(self):
        """Test get_app_db raises error when func is None (lines 623-627)."""
        from mdb_engine.database.abstraction import get_app_db

        mock_request = MagicMock()

        with pytest.raises(ValueError, match="get_app_db requires get_scoped_db_func"):
            await get_app_db(mock_request, get_scoped_db_func=None)
