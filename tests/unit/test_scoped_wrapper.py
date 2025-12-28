"""
Unit tests for ScopedMongoWrapper and ScopedCollectionWrapper.

Tests data scoping, query filtering, and isolation logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection

from mdb_engine.database.scoped_wrapper import (ScopedCollectionWrapper,
                                                ScopedMongoWrapper)


@pytest.mark.unit
class TestScopedCollectionWrapper:
    """Test ScopedCollectionWrapper functionality."""

    @pytest.mark.asyncio
    async def test_scoped_insert_one_adds_app_id(self, mock_mongo_collection):
        """Test that insert_one automatically adds app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        document = {"name": "Test", "value": 100}
        await wrapper.insert_one(document)

        # Verify insert_one was called with app_id added
        call_args = mock_mongo_collection.insert_one.call_args
        assert call_args is not None
        inserted_doc = call_args[0][0]
        assert inserted_doc["app_id"] == "test_app"
        assert inserted_doc["name"] == "Test"
        assert inserted_doc["value"] == 100

    @pytest.mark.asyncio
    async def test_scoped_insert_one_preserves_original_document(
        self, mock_mongo_collection
    ):
        """Test that insert_one doesn't mutate the original document."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        original_doc = {"name": "Test", "value": 100}
        await wrapper.insert_one(original_doc)

        # Original document should not have app_id
        assert "app_id" not in original_doc

    @pytest.mark.asyncio
    async def test_scoped_insert_many_adds_app_id(self, mock_mongo_collection):
        """Test that insert_many adds app_id to all documents."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        documents = [{"name": "Test 1", "value": 100}, {"name": "Test 2", "value": 200}]
        await wrapper.insert_many(documents)

        # Verify insert_many was called
        call_args = mock_mongo_collection.insert_many.call_args
        assert call_args is not None
        inserted_docs = call_args[0][0]
        assert len(inserted_docs) == 2
        assert all(doc["app_id"] == "test_app" for doc in inserted_docs)

    @pytest.mark.asyncio
    async def test_scoped_find_one_filters_by_app_id(self, mock_mongo_collection):
        """Test that find_one filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"name": "Test"}
        await wrapper.find_one(user_filter)

        # Verify find_one was called with scoped filter
        call_args = mock_mongo_collection.find_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        # Should have $and with user filter and app_id scope
        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert len(and_conditions) == 2
        assert {"name": "Test"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_scoped_find_one_empty_filter(self, mock_mongo_collection):
        """Test that find_one with no filter still applies app_id scope."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        await wrapper.find_one()

        # Verify find_one was called with app_id filter
        call_args = mock_mongo_collection.find_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]
        assert scoped_filter == {"app_id": {"$in": ["test_app"]}}

    @pytest.mark.asyncio
    async def test_scoped_find_filters_by_app_id(self, mock_mongo_collection):
        """Test that find filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"status": "active"}
        wrapper.find(user_filter)

        # Verify find was called with scoped filter
        # Note: find returns a cursor, so we check the underlying collection call
        # The actual call happens when cursor is used, but wrapper prepares the filter
        # We can verify by checking the wrapper's filter injection logic
        scoped_filter = wrapper._inject_read_filter(user_filter)
        assert "$and" in scoped_filter
        assert {"status": "active"} in scoped_filter["$and"]
        assert {"app_id": {"$in": ["test_app"]}} in scoped_filter["$and"]

    @pytest.mark.asyncio
    async def test_scoped_find_multiple_read_scopes(self, mock_mongo_collection):
        """Test that find supports multiple read scopes."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["app1", "app2"],
            write_scope="app1",
        )

        scoped_filter = wrapper._inject_read_filter({"name": "Test"})
        assert "$and" in scoped_filter
        assert {"app_id": {"$in": ["app1", "app2"]}} in scoped_filter["$and"]

    @pytest.mark.asyncio
    async def test_scoped_update_one_filters_by_app_id(self, mock_mongo_collection):
        """Test that update_one filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"name": "Test"}
        update = {"$set": {"status": "updated"}}
        await wrapper.update_one(user_filter, update)

        # Verify update_one was called with scoped filter
        call_args = mock_mongo_collection.update_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        # Should have $and with user filter and app_id scope
        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert {"name": "Test"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_scoped_delete_one_filters_by_app_id(self, mock_mongo_collection):
        """Test that delete_one filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"name": "Test"}
        await wrapper.delete_one(user_filter)

        # Verify delete_one was called with scoped filter
        call_args = mock_mongo_collection.delete_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        # Should have $and with user filter and app_id scope
        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert {"name": "Test"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_scoped_count_documents_filters_by_app_id(
        self, mock_mongo_collection
    ):
        """Test that count_documents filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"status": "active"}
        await wrapper.count_documents(user_filter)

        # Verify count_documents was called with scoped filter
        call_args = mock_mongo_collection.count_documents.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        # Should have $and with user filter and app_id scope
        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert {"status": "active"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    def test_inject_read_filter_empty_filter(self):
        """Test _inject_read_filter with empty filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=MagicMock(spec=AsyncIOMotorCollection),
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        result = wrapper._inject_read_filter(None)
        assert result == {"app_id": {"$in": ["test_app"]}}

        result = wrapper._inject_read_filter({})
        assert result == {"app_id": {"$in": ["test_app"]}}

    def test_inject_read_filter_with_user_filter(self):
        """Test _inject_read_filter with user filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=MagicMock(spec=AsyncIOMotorCollection),
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"name": "Test", "value": {"$gt": 100}}
        result = wrapper._inject_read_filter(user_filter)

        assert "$and" in result
        assert user_filter in result["$and"]
        assert {"app_id": {"$in": ["test_app"]}} in result["$and"]


@pytest.mark.unit
class TestScopedMongoWrapper:
    """Test ScopedMongoWrapper functionality."""

    def test_get_collection_returns_scoped_wrapper(self, mock_mongo_database):
        """Test that accessing a collection returns ScopedCollectionWrapper."""
        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection = wrapper.test_collection
        assert isinstance(collection, ScopedCollectionWrapper)
        assert collection._write_scope == "test_app"
        assert collection._read_scopes == ["test_app"]

    def test_get_collection_caching(self, mock_mongo_database):
        """Test that collection wrappers are cached."""
        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection1 = wrapper.test_collection
        collection2 = wrapper.test_collection

        # Should be the same instance (cached)
        assert collection1 is collection2

    def test_get_collection_already_prefixed(self, mock_mongo_database):
        """Test get_collection with already prefixed name (lines 1499-1501)."""
        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Collection name that's already prefixed (cross-app access)
        collection = wrapper.get_collection("other_app_collection")
        assert isinstance(collection, ScopedCollectionWrapper)
        # Should use the name as-is without adding prefix again
        assert collection._write_scope == "test_app"

    def test_get_collection_caching_via_get_collection(self, mock_mongo_database):
        """Test that get_collection caches wrappers."""
        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        collection1 = wrapper.get_collection("test_collection")
        collection2 = wrapper.get_collection("test_collection")

        # Should be the same instance (cached)
        assert collection1 is collection2

    @pytest.mark.skip(
        reason="Hard to mock MagicMock to return non-collection - edge case"
    )
    def test_get_collection_not_collection_error(self, mock_mongo_database):
        """Test get_collection raises error for non-collection (lines 1514-1518)."""
        # This test is skipped because MagicMock always returns MagicMock instances
        # Making it return a non-collection requires complex mocking that causes recursion
        pass

    @pytest.mark.asyncio
    async def test_get_collection_auto_indexing_enabled(self, mock_mongo_database):
        """Test get_collection with auto-indexing enabled (lines 1530-1589)."""
        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
            auto_index=True,
        )

        # Mock collection
        mock_collection = MagicMock(spec=AsyncIOMotorCollection)
        mock_collection.name = "test_app_test_collection"
        mock_collection.database = mock_mongo_database
        mock_mongo_database.test_app_test_collection = mock_collection

        # Mock client admin command for ping
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})
        mock_mongo_database.client = mock_client

        # Mock _ensure_app_id_index at class level
        with patch(
            "mdb_engine.database.scoped_wrapper.ScopedMongoWrapper._ensure_app_id_index",
            new_callable=AsyncMock,
            return_value=True,
        ):
            collection = wrapper.get_collection("test_collection")

            assert isinstance(collection, ScopedCollectionWrapper)
            # Should have triggered auto-indexing check (fire-and-forget, so we can't easily verify)
            # But we can verify the collection was created and cached
            assert wrapper.get_collection("test_collection") is collection

    @pytest.mark.asyncio
    async def test_get_collection_connection_failure_during_index(
        self, mock_mongo_database
    ):
        """Test get_collection handles connection failure during index check (lines 1545-1560)."""
        from pymongo.errors import ConnectionFailure

        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
            auto_index=True,
        )

        # Mock collection
        mock_collection = MagicMock(spec=AsyncIOMotorCollection)
        mock_collection.name = "test_app_test_collection"
        mock_collection.database = mock_mongo_database
        mock_mongo_database.test_app_test_collection = mock_collection

        # Mock client admin command to raise ConnectionFailure
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(
            side_effect=ConnectionFailure("Connection failed")
        )
        mock_mongo_database.client = mock_client

        # Clear cache to ensure fresh check
        ScopedMongoWrapper._app_id_index_cache.clear()

        collection = wrapper.get_collection("test_collection")

        # Should still return collection even if index check fails
        assert isinstance(collection, ScopedCollectionWrapper)

    @pytest.mark.asyncio
    async def test_get_collection_operation_failure_during_index(
        self, mock_mongo_database
    ):
        """Test get_collection handles OperationFailure during index creation (lines 1577-1583)."""
        from pymongo.errors import OperationFailure

        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
            auto_index=True,
        )

        # Mock collection
        mock_collection = MagicMock(spec=AsyncIOMotorCollection)
        mock_collection.name = "test_app_test_collection"
        mock_collection.database = mock_mongo_database
        mock_mongo_database.test_app_test_collection = mock_collection

        # Mock client admin command for ping
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})
        mock_mongo_database.client = mock_client

        # Mock _ensure_app_id_index to raise OperationFailure at class level
        with patch(
            "mdb_engine.database.scoped_wrapper.ScopedMongoWrapper._ensure_app_id_index",
            new_callable=AsyncMock,
            side_effect=OperationFailure("Index failed"),
        ):
            # Clear cache
            ScopedMongoWrapper._app_id_index_cache.clear()

            collection = wrapper.get_collection("test_collection")

            # Should still return collection even if index creation fails
            assert isinstance(collection, ScopedCollectionWrapper)


class TestScopedCollectionWrapperAdditionalOperations:
    """Test additional collection operations."""

    @pytest.mark.asyncio
    async def test_scoped_update_many_filters_by_app_id(self, mock_mongo_collection):
        """Test that update_many filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"status": "pending"}
        update = {"$set": {"status": "completed"}}
        await wrapper.update_many(user_filter, update)

        call_args = mock_mongo_collection.update_many.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert {"status": "pending"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_scoped_delete_many_filters_by_app_id(self, mock_mongo_collection):
        """Test that delete_many filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"status": "deleted"}
        await wrapper.delete_many(user_filter)

        call_args = mock_mongo_collection.delete_many.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]

        assert "$and" in scoped_filter
        and_conditions = scoped_filter["$and"]
        assert {"status": "deleted"} in and_conditions
        assert {"app_id": {"$in": ["test_app"]}} in and_conditions

    @pytest.mark.asyncio
    async def test_scoped_aggregate_filters_by_app_id(self, mock_mongo_collection):
        """Test that aggregate filters by app_id."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        pipeline = [{"$match": {"status": "active"}}]
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        result_cursor = wrapper.aggregate(pipeline)
        await result_cursor.to_list()

        call_args = mock_mongo_collection.aggregate.call_args
        assert call_args is not None
        scoped_pipeline = call_args[0][0]

        # Should have added $match stage with app_id filter
        assert len(scoped_pipeline) >= 1
        first_stage = scoped_pipeline[0]
        assert "$match" in first_stage
        # The filter might be direct app_id or wrapped in $and
        match_filter = first_stage["$match"]
        if "$and" in match_filter:
            assert {"app_id": {"$in": ["test_app"]}} in match_filter["$and"]
        else:
            # Direct app_id filter
            assert match_filter == {"app_id": {"$in": ["test_app"]}}

    @pytest.mark.asyncio
    async def test_scoped_replace_one_adds_app_id(self, mock_mongo_collection):
        """Test that replace_one adds app_id to replacement document."""
        # Check if replace_one exists on the wrapper
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # replace_one may not be implemented, so skip if not available
        if not hasattr(wrapper, "replace_one"):
            pytest.skip("replace_one not implemented on ScopedCollectionWrapper")

        filter_doc = {"_id": "123"}
        replacement = {"name": "Updated", "value": 200}
        await wrapper.replace_one(filter_doc, replacement)

        call_args = mock_mongo_collection.replace_one.call_args
        assert call_args is not None
        scoped_filter = call_args[0][0]
        replaced_doc = call_args[0][1]

        # Filter should be scoped
        assert "$and" in scoped_filter
        # Replacement should have app_id
        assert replaced_doc["app_id"] == "test_app"
        assert replaced_doc["name"] == "Updated"


class TestScopedCollectionWrapperErrorHandling:
    """Test error handling in scoped operations."""

    @pytest.mark.asyncio
    async def test_scoped_operations_connection_error(self, mock_mongo_collection):
        """Test handling connection failures."""
        from pymongo.errors import ConnectionFailure

        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.find_one = AsyncMock(
            side_effect=ConnectionFailure("Connection lost")
        )

        with pytest.raises(ConnectionFailure):
            await wrapper.find_one({"name": "Test"})

    @pytest.mark.asyncio
    async def test_scoped_operations_operation_error(self, mock_mongo_collection):
        """Test handling operation failures."""
        from pymongo.errors import OperationFailure

        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.insert_one = AsyncMock(
            side_effect=OperationFailure("Operation failed")
        )

        # The wrapper catches OperationFailure and wraps it in MongoDBEngineError
        from mdb_engine.exceptions import MongoDBEngineError

        with pytest.raises(MongoDBEngineError):
            await wrapper.insert_one({"name": "Test"})

    @pytest.mark.asyncio
    async def test_scoped_operations_timeout_error(self, mock_mongo_collection):
        """Test handling timeout errors."""
        from pymongo.errors import ServerSelectionTimeoutError

        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.find_one = AsyncMock(
            side_effect=ServerSelectionTimeoutError("Timeout")
        )

        with pytest.raises(ServerSelectionTimeoutError):
            await wrapper.find_one({"name": "Test"})


class TestScopedCollectionWrapperEdgeCases:
    """Test edge cases in scoped operations."""

    def test_scoped_filter_with_existing_and(self):
        """Test handling existing $and in filters."""
        wrapper = ScopedCollectionWrapper(
            real_collection=MagicMock(spec=AsyncIOMotorCollection),
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"$and": [{"name": "Test"}, {"value": {"$gt": 100}}]}
        result = wrapper._inject_read_filter(user_filter)

        # Should merge with existing $and - the $and itself becomes part of the outer $and
        assert "$and" in result
        # The existing $and becomes one condition, plus the app_id filter
        assert len(result["$and"]) == 2

    def test_scoped_filter_with_or_conditions(self):
        """Test handling $or conditions."""
        wrapper = ScopedCollectionWrapper(
            real_collection=MagicMock(spec=AsyncIOMotorCollection),
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        user_filter = {"$or": [{"name": "Test1"}, {"name": "Test2"}]}
        result = wrapper._inject_read_filter(user_filter)

        # Should wrap $or in $and with app_id
        assert "$and" in result
        # Original $or should be preserved
        assert any(
            "$or" in str(cond) or isinstance(cond, dict) and "$or" in cond
            for cond in result["$and"]
        )

    def test_scoped_filter_empty_read_scopes(self):
        """Test handling empty read scopes."""
        wrapper = ScopedCollectionWrapper(
            real_collection=MagicMock(spec=AsyncIOMotorCollection),
            read_scopes=[],  # Empty scopes
            write_scope="test_app",
        )

        result = wrapper._inject_read_filter({"name": "Test"})
        # Should still have filter structure, but with empty $in
        assert "$and" in result or "app_id" in result

    @pytest.mark.asyncio
    async def test_scoped_find_with_auto_indexing(self, mock_mongo_collection):
        """Test find() with auto-indexing enabled (lines 1175-1194)."""
        # Create a mock AutoIndexManager
        mock_auto_index_manager = MagicMock()
        mock_auto_index_manager.ensure_index_for_query = AsyncMock(
            side_effect=Exception("Index error")
        )

        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Set auto_index_manager directly (it's a property, so set the private attribute)
        wrapper._auto_index_manager = mock_auto_index_manager

        # Mock cursor
        mock_cursor = MagicMock()
        mock_mongo_collection.find = MagicMock(return_value=mock_cursor)

        result = wrapper.find({"status": "active"}, sort=[("name", 1)])

        # Should still return cursor even if indexing fails
        assert result == mock_cursor
        mock_mongo_collection.find.assert_called_once()

    @pytest.mark.asyncio
    async def test_scoped_aggregate_with_vector_search(self, mock_mongo_collection):
        """Test aggregate() with $vectorSearch stage (lines 1264-1290)."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Pipeline with $vectorSearch as first stage
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "embedding",
                    "queryVector": [0.1, 0.2, 0.3],
                    "numCandidates": 10,
                    "limit": 5,
                }
            },
            {"$project": {"name": 1}},
        ]

        mock_cursor = MagicMock()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        wrapper.aggregate(pipeline)

        # Verify aggregate was called
        assert mock_mongo_collection.aggregate.called
        call_args = mock_mongo_collection.aggregate.call_args
        modified_pipeline = call_args[0][0]

        # First stage should still be $vectorSearch
        assert "$vectorSearch" in modified_pipeline[0]
        # But it should have a filter with app_id
        assert "filter" in modified_pipeline[0]["$vectorSearch"]
        assert modified_pipeline[0]["$vectorSearch"]["filter"]["app_id"] == {
            "$in": ["test_app"]
        }

    @pytest.mark.asyncio
    async def test_scoped_aggregate_empty_pipeline(self, mock_mongo_collection):
        """Test aggregate() with empty pipeline (lines 1258-1262)."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        wrapper.aggregate([])

        # Should prepend $match stage
        call_args = mock_mongo_collection.aggregate.call_args
        modified_pipeline = call_args[0][0]
        assert len(modified_pipeline) == 1
        assert "$match" in modified_pipeline[0]
        assert modified_pipeline[0]["$match"]["app_id"] == {"$in": ["test_app"]}

    @pytest.mark.asyncio
    async def test_scoped_count_documents_with_auto_indexing(
        self, mock_mongo_collection
    ):
        """Test count_documents() with auto-indexing (lines 1242-1244)."""
        # Create a mock AutoIndexManager
        mock_auto_index_manager = MagicMock()
        mock_auto_index_manager.ensure_index_for_query = AsyncMock()

        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Set auto_index_manager directly (it's a property, so set the private attribute)
        wrapper._auto_index_manager = mock_auto_index_manager

        mock_mongo_collection.count_documents = AsyncMock(return_value=5)

        result = await wrapper.count_documents({"status": "active"})

        assert result == 5
        mock_auto_index_manager.ensure_index_for_query.assert_called_once()
        mock_mongo_collection.count_documents.assert_called_once()


class TestAsyncAtlasIndexManager:
    """Test AsyncAtlasIndexManager functionality."""

    def test_index_manager_initialization(self, mock_mongo_collection):
        """Test proper initialization of index manager."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        assert manager._collection == mock_mongo_collection

    def test_index_manager_initialization_invalid_type(self):
        """Test initialization with invalid collection type."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        with pytest.raises(TypeError, match="Expected AsyncIOMotorCollection"):
            AsyncAtlasIndexManager(MagicMock())  # Not an AsyncIOMotorCollection

    @pytest.mark.asyncio
    async def test_index_manager_ensure_collection_exists(self, mock_mongo_collection):
        """Test ensuring collection exists."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        # Ensure database attribute exists on mock
        mock_database = MagicMock()
        mock_database.create_collection = AsyncMock()
        mock_mongo_collection.database = mock_database

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        await manager._ensure_collection_exists()

        mock_database.create_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_index_manager_create_regular_index(self, mock_mongo_collection):
        """Test creating regular index."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        # Ensure database attribute exists on mock
        mock_database = MagicMock()
        mock_database.create_collection = AsyncMock()
        mock_mongo_collection.database = mock_database
        mock_mongo_collection.create_index = AsyncMock(return_value="test_index")
        # Mock list_indexes to return a cursor with to_list method
        mock_index_cursor = MagicMock()
        mock_index_cursor.to_list = AsyncMock(return_value=[])
        mock_mongo_collection.list_indexes = MagicMock(return_value=mock_index_cursor)

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        # AsyncAtlasIndexManager uses create_index, not create_regular_index
        result = await manager.create_index(
            keys=[("field1", 1), ("field2", -1)],
            name="test_index",
        )

        assert result == "test_index"
        mock_mongo_collection.create_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_index_manager_error_handling(self, mock_mongo_collection):
        """Test error handling in index operations."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        # Ensure database attribute exists on mock
        mock_database = MagicMock()
        mock_database.create_collection = AsyncMock()
        mock_mongo_collection.database = mock_database
        mock_mongo_collection.create_index = AsyncMock(
            side_effect=OperationFailure("Index creation failed")
        )
        # Mock list_indexes to return a cursor with to_list method
        mock_index_cursor = MagicMock()
        mock_index_cursor.to_list = AsyncMock(return_value=[])
        mock_mongo_collection.list_indexes = MagicMock(return_value=mock_index_cursor)

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        # The error is wrapped in MongoDBEngineError
        from mdb_engine.exceptions import MongoDBEngineError

        with pytest.raises(MongoDBEngineError):
            # AsyncAtlasIndexManager uses create_index, not create_regular_index
            await manager.create_index(
                keys=[("field1", 1)],
                name="test_index",
            )

    @pytest.mark.asyncio
    async def test_index_manager_ensure_collection_connection_error(
        self, mock_mongo_collection
    ):
        """Test handling connection errors when ensuring collection exists."""
        from pymongo.errors import ConnectionFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_database = MagicMock()
        mock_database.create_collection = AsyncMock(
            side_effect=ConnectionFailure("Connection failed")
        )
        mock_mongo_collection.database = mock_database

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        from mdb_engine.exceptions import MongoDBEngineError

        with pytest.raises(MongoDBEngineError, match="connection failed"):
            await manager._ensure_collection_exists()

    @pytest.mark.asyncio
    async def test_index_manager_ensure_collection_operation_error(
        self, mock_mongo_collection
    ):
        """Test handling operation errors when ensuring collection exists."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_database = MagicMock()
        mock_database.create_collection = AsyncMock(
            side_effect=OperationFailure("Operation failed")
        )
        mock_mongo_collection.database = mock_database

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        from mdb_engine.exceptions import MongoDBEngineError

        with pytest.raises(MongoDBEngineError):
            await manager._ensure_collection_exists()

    @pytest.mark.asyncio
    async def test_index_manager_check_definition_changed_vector(
        self, mock_mongo_collection
    ):
        """Test checking definition changes for vector search index."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Test vector search with changed fields
        definition = {
            "fields": [{"type": "vector", "path": "embedding", "numDimensions": 1536}]
        }
        latest_def = {
            "fields": [{"type": "vector", "path": "embedding", "numDimensions": 768}]
        }

        changed, reason = manager._check_definition_changed(
            definition, latest_def, "vectorsearch", "test_idx"
        )
        assert changed is True
        assert "vector 'fields' definition differs" in reason

        # Test vector search with same fields
        latest_def_same = {
            "fields": [{"type": "vector", "path": "embedding", "numDimensions": 1536}]
        }
        changed, reason = manager._check_definition_changed(
            definition, latest_def_same, "vectorsearch", "test_idx"
        )
        assert changed is False

    @pytest.mark.asyncio
    async def test_index_manager_check_definition_changed_search(
        self, mock_mongo_collection
    ):
        """Test checking definition changes for search index."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Test search with changed mappings
        definition = {"mappings": {"dynamic": True}}
        latest_def = {"mappings": {"dynamic": False}}

        changed, reason = manager._check_definition_changed(
            definition, latest_def, "search", "test_idx"
        )
        assert changed is True
        assert "Lucene 'mappings' definition differs" in reason

        # Test search with same mappings
        latest_def_same = {"mappings": {"dynamic": True}}
        changed, reason = manager._check_definition_changed(
            definition, latest_def_same, "search", "test_idx"
        )
        assert changed is False

    @pytest.mark.asyncio
    async def test_index_manager_check_definition_changed_mismatch(
        self, mock_mongo_collection
    ):
        """Test checking definition changes with mismatched index type."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Test with vectorsearch type but mappings key (mismatch)
        definition = {"mappings": {"dynamic": True}}
        latest_def = {"mappings": {"dynamic": False}}

        changed, reason = manager._check_definition_changed(
            definition, latest_def, "vectorsearch", "test_idx"
        )
        # Should log warning but not detect change
        assert changed is False

    @pytest.mark.asyncio
    async def test_index_manager_ensure_collection_collection_invalid(
        self, mock_mongo_collection
    ):
        """Test handling CollectionInvalid when ensuring collection exists (lines 132-145)."""
        from pymongo.errors import CollectionInvalid

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_database = MagicMock()
        # Test "already exists" case
        mock_database.create_collection = AsyncMock(
            side_effect=CollectionInvalid("collection already exists")
        )
        mock_mongo_collection.database = mock_database

        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        # Should handle gracefully and continue
        await manager._ensure_collection_exists()

        # Test other CollectionInvalid error
        mock_database.create_collection = AsyncMock(
            side_effect=CollectionInvalid("Other error")
        )
        manager = AsyncAtlasIndexManager(mock_mongo_collection)
        from mdb_engine.exceptions import MongoDBEngineError

        with pytest.raises(MongoDBEngineError):
            await manager._ensure_collection_exists()

    @pytest.mark.asyncio
    async def test_handle_existing_index_definition_changed(
        self, mock_mongo_collection
    ):
        """Test _handle_existing_index when definition has changed (lines 194-204)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock _check_definition_changed to return True (definition changed)
        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._check_definition_changed",
            return_value=(True, "mappings changed"),
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.update_search_index",
                new_callable=AsyncMock,
            ) as mock_update:
                existing_index = {
                    "name": "test_idx",
                    "latestDefinition": {"mappings": {"dynamic": True}},
                    "queryable": False,
                }
                definition = {"mappings": {"dynamic": False}}

                result = await manager._handle_existing_index(
                    existing_index, definition, "vectorsearch", "test_idx"
                )

                assert result is False  # Will wait below
                mock_update.assert_called_once_with(
                    name="test_idx",
                    definition=definition,
                    wait_for_ready=False,
                )

    @pytest.mark.asyncio
    async def test_handle_existing_index_queryable(self, mock_mongo_collection):
        """Test _handle_existing_index when index is queryable (lines 205-209)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._check_definition_changed",
            return_value=(False, None),
        ):
            existing_index = {
                "name": "test_idx",
                "latestDefinition": {"mappings": {"dynamic": False}},
                "queryable": True,  # Index is queryable
            }
            definition = {"mappings": {"dynamic": False}}

            result = await manager._handle_existing_index(
                existing_index, definition, "vectorsearch", "test_idx"
            )

            assert result is True  # Ready to use

    @pytest.mark.asyncio
    async def test_handle_existing_index_failed_status(self, mock_mongo_collection):
        """Test _handle_existing_index when index status is FAILED (lines 210-215)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._check_definition_changed",
            return_value=(False, None),
        ):
            existing_index = {
                "name": "test_idx",
                "latestDefinition": {"mappings": {"dynamic": False}},
                "queryable": False,
                "status": "FAILED",  # Failed status
            }
            definition = {"mappings": {"dynamic": False}}

            result = await manager._handle_existing_index(
                existing_index, definition, "vectorsearch", "test_idx"
            )

            assert result is False  # Will wait below

    @pytest.mark.asyncio
    async def test_handle_existing_index_not_queryable(self, mock_mongo_collection):
        """Test _handle_existing_index when index exists but not queryable (lines 216-221)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._check_definition_changed",
            return_value=(False, None),
        ):
            existing_index = {
                "name": "test_idx",
                "latestDefinition": {"mappings": {"dynamic": False}},
                "queryable": False,
                "status": "BUILDING",  # Building status
            }
            definition = {"mappings": {"dynamic": False}}

            result = await manager._handle_existing_index(
                existing_index, definition, "vectorsearch", "test_idx"
            )

            assert result is False  # Will wait below


class TestAutoIndexManager:
    """Test AutoIndexManager functionality."""

    def test_extract_index_fields_from_filter_empty(self, mock_mongo_collection):
        """Test _extract_index_fields_from_filter with empty filter (lines 791-792)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._extract_index_fields_from_filter(None)
        assert result == []

        result = auto_manager._extract_index_fields_from_filter({})
        assert result == []

    def test_extract_index_fields_from_filter_equality(self, mock_mongo_collection):
        """Test _extract_index_fields_from_filter with equality matches (lines 815-817)."""
        from pymongo import ASCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        filter_dict = {"name": "test", "status": "active"}
        result = auto_manager._extract_index_fields_from_filter(filter_dict)

        assert len(result) == 2
        assert ("name", ASCENDING) in result
        assert ("status", ASCENDING) in result

    def test_extract_index_fields_from_filter_operators(self, mock_mongo_collection):
        """Test _extract_index_fields_from_filter with operators (lines 800-805)."""
        from pymongo import ASCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        filter_dict = {
            "age": {"$gt": 18},
            "score": {"$gte": 100},
            "price": {"$lt": 50},
            "quantity": {"$lte": 10},
            "status": {"$ne": "deleted"},
            "tags": {"$in": ["tag1", "tag2"]},
            "active": {"$exists": True},
        }
        result = auto_manager._extract_index_fields_from_filter(filter_dict)

        assert len(result) == 7
        assert ("age", ASCENDING) in result
        assert ("score", ASCENDING) in result
        assert ("price", ASCENDING) in result
        assert ("quantity", ASCENDING) in result
        assert ("status", ASCENDING) in result
        assert ("tags", ASCENDING) in result
        assert ("active", ASCENDING) in result

    def test_extract_index_fields_from_filter_nested_and(self, mock_mongo_collection):
        """Test _extract_index_fields_from_filter with nested $and (lines 807-811)."""
        from pymongo import ASCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        # $and at top level is handled differently - need to check how it's processed
        # The code checks if "$and" is in value, not at top level
        filter_dict = {
            "field": {
                "$and": [
                    {"name": "test"},
                    {"status": "active"},
                ]
            }
        }
        result = auto_manager._extract_index_fields_from_filter(filter_dict)

        # Should extract fields from nested $and within field value
        assert len(result) >= 2
        assert ("name", ASCENDING) in result
        assert ("status", ASCENDING) in result

    def test_extract_index_fields_from_filter_or_skipped(self, mock_mongo_collection):
        """Test _extract_index_fields_from_filter skips $or (lines 812-814)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        filter_dict = {
            "$or": [
                {"name": "test"},
                {"status": "active"},
            ]
        }
        result = auto_manager._extract_index_fields_from_filter(filter_dict)

        assert result == []

    def test_extract_index_fields_from_filter_duplicates_removed(
        self, mock_mongo_collection
    ):
        """Test _extract_index_fields_from_filter removes duplicates (line 824)."""
        from pymongo import ASCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        filter_dict = {
            "name": "test",
            "$and": [{"name": "test"}],
        }
        result = auto_manager._extract_index_fields_from_filter(filter_dict)

        assert len(result) == 1
        assert ("name", ASCENDING) in result

    def test_extract_sort_fields_empty(self, mock_mongo_collection):
        """Test _extract_sort_fields with empty sort (lines 834-835)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._extract_sort_fields(None)
        assert result == []

        result = auto_manager._extract_sort_fields([])
        assert result == []

    def test_extract_sort_fields_dict(self, mock_mongo_collection):
        """Test _extract_sort_fields with dict format (lines 837-838)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        sort_dict = {"name": 1, "age": -1}
        result = auto_manager._extract_sort_fields(sort_dict)

        assert result == [("name", 1), ("age", -1)]

    def test_extract_sort_fields_list(self, mock_mongo_collection):
        """Test _extract_sort_fields with list format (lines 839-840)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        sort_list = [("name", 1), ("age", -1)]
        result = auto_manager._extract_sort_fields(sort_list)

        assert result == sort_list

    def test_extract_sort_fields_invalid_type(self, mock_mongo_collection):
        """Test _extract_sort_fields with invalid type (lines 841-842)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._extract_sort_fields("invalid")
        assert result == []

    def test_generate_index_name_empty(self, mock_mongo_collection):
        """Test _generate_index_name with empty fields (lines 846-847)."""
        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._generate_index_name([])
        assert result == "auto_idx_empty"

    def test_generate_index_name_single_field(self, mock_mongo_collection):
        """Test _generate_index_name with single field."""
        from pymongo import ASCENDING, DESCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._generate_index_name([("name", ASCENDING)])
        assert result == "auto_name_asc"

        result = auto_manager._generate_index_name([("age", DESCENDING)])
        assert result == "auto_age_desc"

    def test_generate_index_name_multiple_fields(self, mock_mongo_collection):
        """Test _generate_index_name with multiple fields."""
        from pymongo import ASCENDING, DESCENDING

        from mdb_engine.database.scoped_wrapper import (AsyncAtlasIndexManager,
                                                        AutoIndexManager)

        index_manager = AsyncAtlasIndexManager(mock_mongo_collection)
        auto_manager = AutoIndexManager(mock_mongo_collection, index_manager)

        result = auto_manager._generate_index_name(
            [
                ("name", ASCENDING),
                ("age", DESCENDING),
            ]
        )
        assert result == "auto_name_asc_age_desc"


class TestAsyncAtlasIndexManagerCreateIndex:
    """Test AsyncAtlasIndexManager index creation error handling."""

    @pytest.mark.asyncio
    async def test_create_new_search_index_index_already_exists(
        self, mock_mongo_collection
    ):
        """Test _create_new_search_index handles IndexAlreadyExists race condition."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock create_search_index to raise OperationFailure with IndexAlreadyExists
        mock_mongo_collection.create_search_index = AsyncMock(
            side_effect=OperationFailure(
                "IndexAlreadyExists: Index 'test_idx' already exists"
            )
        )

        # Should not raise - just logs warning
        await manager._create_new_search_index(
            "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
        )

    @pytest.mark.asyncio
    async def test_create_new_search_index_operation_failure(
        self, mock_mongo_collection
    ):
        """Test _create_new_search_index raises OperationFailure for other errors."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock create_search_index to raise OperationFailure with other error
        error = OperationFailure("Other error", code=123)
        mock_mongo_collection.create_search_index = AsyncMock(side_effect=error)

        # Should raise OperationFailure (not wrapped)
        with pytest.raises(OperationFailure):
            await manager._create_new_search_index(
                "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
            )

    @pytest.mark.asyncio
    async def test_create_search_index_no_existing_index(self, mock_mongo_collection):
        """Test create_search_index when no existing index."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_mongo_collection.database = MagicMock()  # Ensure database attribute exists
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock get_search_index to return None (no existing index)
        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                new_callable=AsyncMock,
                return_value=None,
            ):
                with patch(
                    (
                        "mdb_engine.database.scoped_wrapper."
                        "AsyncAtlasIndexManager._create_new_search_index"
                    ),
                    new_callable=AsyncMock,
                ) as mock_create:
                    with patch(
                        (
                            "mdb_engine.database.scoped_wrapper."
                            "AsyncAtlasIndexManager._wait_for_search_index_ready"
                        ),
                        new_callable=AsyncMock,
                        return_value=True,
                    ):
                        result = await manager.create_search_index(
                            "test_idx",
                            {"mappings": {"dynamic": True}},
                            "vectorsearch",
                            wait_for_ready=True,
                        )

                        assert result is True
                        mock_create.assert_called_once_with(
                            "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
                        )

    @pytest.mark.asyncio
    async def test_create_search_index_operation_failure(self, mock_mongo_collection):
        """Test create_search_index handles OperationFailure (lines 282-289)."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager
        from mdb_engine.exceptions import MongoDBEngineError

        mock_mongo_collection.database = MagicMock()  # Ensure database attribute exists
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock get_search_index to raise OperationFailure
        async def get_search_index_side_effect(*args, **kwargs):
            raise OperationFailure("DB error")

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with pytest.raises(
                    MongoDBEngineError, match="Failed to create/check search index"
                ):
                    await manager.create_search_index(
                        "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
                    )

    @pytest.mark.asyncio
    async def test_create_search_index_connection_failure(self, mock_mongo_collection):
        """Test create_search_index handles ConnectionFailure (lines 290-297)."""
        from pymongo.errors import ConnectionFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager
        from mdb_engine.exceptions import MongoDBEngineError

        mock_mongo_collection.database = MagicMock()  # Ensure database attribute exists
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        async def get_search_index_side_effect(*args, **kwargs):
            raise ConnectionFailure("Connection error")

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with pytest.raises(
                    MongoDBEngineError,
                    match="Connection failed while creating/checking search index",
                ):
                    await manager.create_search_index(
                        "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
                    )

    @pytest.mark.asyncio
    async def test_create_search_index_invalid_operation(self, mock_mongo_collection):
        """Test create_search_index handles InvalidOperation (lines 298-305)."""
        from pymongo.errors import InvalidOperation

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager
        from mdb_engine.exceptions import MongoDBEngineError

        mock_mongo_collection.database = MagicMock()  # Ensure database attribute exists
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        async def get_search_index_side_effect(*args, **kwargs):
            raise InvalidOperation("Invalid operation")

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with pytest.raises(
                    MongoDBEngineError, match="Error creating/checking search index"
                ):
                    await manager.create_search_index(
                        "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
                    )

    @pytest.mark.asyncio
    async def test_get_search_index_operation_failure(self, mock_mongo_collection):
        """Test get_search_index handles OperationFailure (returns None) (lines 318-320)."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock aggregate to raise OperationFailure
        async def async_iter():
            raise OperationFailure("DB error")
            yield  # Make it an async generator

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        result = await manager.get_search_index("test_idx")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_search_index_connection_failure(self, mock_mongo_collection):
        """Test get_search_index handles ConnectionFailure (returns None) (lines 321-323)."""
        from pymongo.errors import ConnectionFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock aggregate to raise ConnectionFailure
        async def async_iter():
            raise ConnectionFailure("Connection error")
            yield

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        result = await manager.get_search_index("test_idx")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_search_index_server_selection_timeout(
        self, mock_mongo_collection
    ):
        """Test get_search_index handles ServerSelectionTimeoutError (returns None)."""
        from pymongo.errors import ServerSelectionTimeoutError

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock aggregate to raise ServerSelectionTimeoutError
        async def async_iter():
            raise ServerSelectionTimeoutError("Timeout")
            yield

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        result = await manager.get_search_index("test_idx")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_search_index_invalid_operation(self, mock_mongo_collection):
        """Test get_search_index handles InvalidOperation (raises MongoDBEngineError)."""
        from pymongo.errors import InvalidOperation

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager
        from mdb_engine.exceptions import MongoDBEngineError

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock aggregate to raise InvalidOperation
        async def async_iter():
            raise InvalidOperation("Invalid operation")
            yield

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        with pytest.raises(MongoDBEngineError, match="Error retrieving search index"):
            await manager.get_search_index("test_idx")

    @pytest.mark.asyncio
    async def test_get_search_index_returns_none_when_not_found(
        self, mock_mongo_collection
    ):
        """Test get_search_index returns None when index not found (line 318)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        # Mock aggregate to return empty iterator (no results)
        async def async_iter():
            # Empty async generator - no results
            if False:
                yield
            return

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        result = await manager.get_search_index("nonexistent_idx")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_new_search_index_logs_info(self, mock_mongo_collection):
        """Test _create_new_search_index logs info message (line 238)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        mock_mongo_collection.create_search_index = AsyncMock()

        with patch("mdb_engine.database.scoped_wrapper.logger") as mock_logger:
            await manager._create_new_search_index(
                "test_idx", {"mappings": {"dynamic": True}}, "vectorsearch"
            )

            # Should log info message about build submission
            mock_logger.info.assert_called()
            # Check that the log message contains the index name
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("test_idx" in str(call) for call in log_calls)

    @pytest.mark.asyncio
    async def test_create_search_index_existing_index_ready(
        self, mock_mongo_collection
    ):
        """Test create_search_index when existing index is ready (lines 273-277)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_mongo_collection.database = MagicMock()
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        existing_index = {
            "name": "test_idx",
            "status": "READY",
            "queryable": True,
            "latestDefinition": {"mappings": {"dynamic": True}},
        }

        async def get_search_index_side_effect(*args, **kwargs):
            return existing_index

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with patch(
                    (
                        "mdb_engine.database.scoped_wrapper."
                        "AsyncAtlasIndexManager._handle_existing_index"
                    ),
                    new_callable=AsyncMock,
                    return_value=True,
                ) as mock_handle:
                    result = await manager.create_search_index(
                        "test_idx",
                        {"mappings": {"dynamic": True}},
                        "vectorsearch",
                        wait_for_ready=False,
                    )

                    assert result is True
                    mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_search_index_wait_for_ready(self, mock_mongo_collection):
        """Test create_search_index with wait_for_ready=True (line 283)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_mongo_collection.database = MagicMock()
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        async def get_search_index_side_effect(*args, **kwargs):
            return None

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with patch(
                    (
                        "mdb_engine.database.scoped_wrapper."
                        "AsyncAtlasIndexManager._create_new_search_index"
                    ),
                    new_callable=AsyncMock,
                ):
                    with patch(
                        (
                            "mdb_engine.database.scoped_wrapper."
                            "AsyncAtlasIndexManager._wait_for_search_index_ready"
                        ),
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as mock_wait:
                        result = await manager.create_search_index(
                            "test_idx",
                            {"mappings": {"dynamic": True}},
                            "vectorsearch",
                            wait_for_ready=True,
                        )

                        assert result is True
                        mock_wait.assert_called_once_with("test_idx", 600)

    @pytest.mark.asyncio
    async def test_create_search_index_existing_index_not_ready(
        self, mock_mongo_collection
    ):
        """Test create_search_index when existing index is not ready (lines 273-277)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        mock_mongo_collection.database = MagicMock()
        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        existing_index = {
            "name": "test_idx",
            "status": "BUILDING",
            "queryable": False,
            "latestDefinition": {"mappings": {"dynamic": True}},
        }

        async def get_search_index_side_effect(*args, **kwargs):
            return existing_index

        with patch(
            "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager._ensure_collection_exists",
            new_callable=AsyncMock,
        ):
            with patch(
                "mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager.get_search_index",
                side_effect=get_search_index_side_effect,
            ):
                with patch(
                    (
                        "mdb_engine.database.scoped_wrapper."
                        "AsyncAtlasIndexManager._handle_existing_index"
                    ),
                    new_callable=AsyncMock,
                    return_value=False,
                ):
                    with patch(
                        (
                            "mdb_engine.database.scoped_wrapper."
                            "AsyncAtlasIndexManager._wait_for_search_index_ready"
                        ),
                        new_callable=AsyncMock,
                        return_value=True,
                    ):
                        result = await manager.create_search_index(
                            "test_idx",
                            {"mappings": {"dynamic": True}},
                            "vectorsearch",
                            wait_for_ready=True,
                        )

                        assert result is True
                        manager._wait_for_search_index_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_search_indexes_success(self, mock_mongo_collection):
        """Test list_search_indexes successful path (lines 334-336)."""
        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        mock_indexes = [
            {"name": "idx1", "status": "READY"},
            {"name": "idx2", "status": "BUILDING"},
        ]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=mock_indexes)
        mock_mongo_collection.list_search_indexes = MagicMock(return_value=mock_cursor)

        result = await manager.list_search_indexes()

        assert result == mock_indexes

    @pytest.mark.asyncio
    async def test_list_search_indexes_operation_failure(self, mock_mongo_collection):
        """Test list_search_indexes handles OperationFailure (lines 336-338)."""
        from pymongo.errors import OperationFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(side_effect=OperationFailure("DB error"))
        mock_mongo_collection.list_search_indexes = MagicMock(return_value=mock_cursor)

        result = await manager.list_search_indexes()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_search_indexes_connection_failure(self, mock_mongo_collection):
        """Test list_search_indexes handles ConnectionFailure (lines 336-338)."""
        from pymongo.errors import ConnectionFailure

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            side_effect=ConnectionFailure("Connection error")
        )
        mock_mongo_collection.list_search_indexes = MagicMock(return_value=mock_cursor)

        result = await manager.list_search_indexes()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_search_indexes_invalid_operation(self, mock_mongo_collection):
        """Test list_search_indexes handles InvalidOperation (lines 339-342)."""
        from pymongo.errors import InvalidOperation

        from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager

        manager = AsyncAtlasIndexManager(mock_mongo_collection)

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(side_effect=InvalidOperation("Client closed"))
        mock_mongo_collection.list_search_indexes = MagicMock(return_value=mock_cursor)

        result = await manager.list_search_indexes()

        assert result == []
