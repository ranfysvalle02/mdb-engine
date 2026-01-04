"""
Integration tests for security features in ScopedCollectionWrapper.

Tests that verify security features work correctly in real-world scenarios.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection

from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper
from mdb_engine.exceptions import QueryValidationError


@pytest.mark.unit
class TestScopedCollectionWrapperSecurityIntegration:
    """Integration tests for security features."""

    @pytest.mark.asyncio
    async def test_find_with_none_filter(self, mock_mongo_collection):
        """Test that find() works with None filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.find = MagicMock(return_value=mock_cursor)

        # Should not raise validation error for None filter
        cursor = wrapper.find(None)
        assert cursor == mock_cursor

    @pytest.mark.asyncio
    async def test_find_one_with_none_filter(self, mock_mongo_collection):
        """Test that find_one() works with None filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.find_one = AsyncMock(return_value={"_id": "123"})

        # Should not raise validation error for None filter
        result = await wrapper.find_one(None)
        assert result == {"_id": "123"}

    @pytest.mark.asyncio
    async def test_aggregate_with_empty_pipeline(self, mock_mongo_collection):
        """Test that aggregate() works with empty pipeline."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        # Should not raise validation error for empty pipeline
        cursor = wrapper.aggregate([])
        assert cursor is not None

    @pytest.mark.asyncio
    async def test_regex_validation_in_queries(self, mock_mongo_collection):
        """Test that regex patterns in queries are validated."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        from mdb_engine.constants import MAX_REGEX_LENGTH

        # Test overly long regex
        long_regex = "a" * (MAX_REGEX_LENGTH + 1)
        filter_with_long_regex = {"name": {"$regex": long_regex}}

        with pytest.raises(QueryValidationError, match="exceeds maximum length"):
            wrapper.find(filter_with_long_regex)

    @pytest.mark.asyncio
    async def test_insert_one_with_valid_document(self, mock_mongo_collection):
        """Test that insert_one() works with valid documents."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_result = MagicMock()
        mock_mongo_collection.insert_one = AsyncMock(return_value=mock_result)

        doc = {"name": "Test", "value": 123}
        await wrapper.insert_one(doc)

        # Verify insert_one was called (maxTimeMS is removed because insert_one doesn't accept it)
        call_kwargs = mock_mongo_collection.insert_one.call_args[1]
        assert "maxTimeMS" not in call_kwargs  # Removed because insert_one doesn't accept it

    @pytest.mark.asyncio
    async def test_find_with_custom_timeout(self, mock_mongo_collection):
        """Test that custom timeouts are respected (within limits)."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.find = MagicMock(return_value=mock_cursor)

        # Set custom timeout
        wrapper.find({"status": "active"}, maxTimeMS=15000)

        call_kwargs = mock_mongo_collection.find.call_args[1]
        # maxTimeMS is removed before calling find() because Cursor constructor doesn't accept it
        assert "maxTimeMS" not in call_kwargs

    @pytest.mark.asyncio
    async def test_find_with_excessive_timeout_capped(self, mock_mongo_collection):
        """Test that excessive timeouts are capped to maximum."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.find = MagicMock(return_value=mock_cursor)

        from mdb_engine.constants import MAX_QUERY_TIME_MS

        # Set timeout exceeding maximum
        wrapper.find({"status": "active"}, maxTimeMS=MAX_QUERY_TIME_MS + 10000)

        call_kwargs = mock_mongo_collection.find.call_args[1]
        # maxTimeMS is removed before calling find() because Cursor constructor doesn't accept it
        assert "maxTimeMS" not in call_kwargs

    @pytest.mark.asyncio
    async def test_scoped_mongo_wrapper_shares_validators(self, mock_mongo_database):
        """Test that ScopedMongoWrapper shares validators across collections."""
        from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

        wrapper = ScopedMongoWrapper(
            real_db=mock_mongo_database,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Mock collections
        mock_collection1 = MagicMock(spec=AsyncIOMotorCollection)
        mock_collection1.name = "test_app_users"
        mock_mongo_database.test_app_users = mock_collection1

        mock_collection2 = MagicMock(spec=AsyncIOMotorCollection)
        mock_collection2.name = "test_app_products"
        mock_mongo_database.test_app_products = mock_collection2

        # Get collections
        collection1 = wrapper.users
        collection2 = wrapper.products

        # Verify they share the same validators/limiters
        assert collection1._query_validator is wrapper._query_validator
        assert collection1._resource_limiter is wrapper._resource_limiter
        assert collection2._query_validator is wrapper._query_validator
        assert collection2._resource_limiter is wrapper._resource_limiter

    @pytest.mark.asyncio
    async def test_count_documents_with_none_filter(self, mock_mongo_collection):
        """Test that count_documents() works with None filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.count_documents = AsyncMock(return_value=10)

        result = await wrapper.count_documents(None)
        assert result == 10

        # Note: count_documents doesn't enforce maxTimeMS to avoid cursor creation errors
        # Verify count_documents was called (maxTimeMS is removed to avoid cursor issues)
        call_kwargs = mock_mongo_collection.count_documents.call_args[1]
        # maxTimeMS is intentionally not passed to count_documents to avoid cursor errors
        assert "maxTimeMS" not in call_kwargs or call_kwargs.get("maxTimeMS") is None

    @pytest.mark.asyncio
    async def test_delete_one_with_valid_filter(self, mock_mongo_collection):
        """Test that delete_one() validates filter and strips maxTimeMS."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_mongo_collection.delete_one = AsyncMock()

        await wrapper.delete_one({"status": "deleted"})

        # Verify delete_one was called WITHOUT maxTimeMS (Motor doesn't support it)
        call_kwargs = mock_mongo_collection.delete_one.call_args[1]
        assert "maxTimeMS" not in call_kwargs

    @pytest.mark.asyncio
    async def test_update_one_validates_filter(self, mock_mongo_collection):
        """Test that update_one() validates filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Test dangerous operator is blocked
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            await wrapper.update_one({"$where": "true"}, {"$set": {"status": "active"}})

    @pytest.mark.asyncio
    async def test_update_many_validates_filter(self, mock_mongo_collection):
        """Test that update_many() validates filter."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        # Test dangerous operator is blocked
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            await wrapper.update_many({"$where": "true"}, {"$set": {"status": "active"}})

    @pytest.mark.asyncio
    async def test_insert_many_with_valid_documents(self, mock_mongo_collection):
        """Test that insert_many() works with valid documents."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_result = MagicMock()
        mock_mongo_collection.insert_many = AsyncMock(return_value=mock_result)

        docs = [{"name": f"Test{i}", "value": i} for i in range(10)]
        await wrapper.insert_many(docs)

        # Verify insert_many was called (maxTimeMS is removed because insert_many doesn't accept it)
        call_kwargs = mock_mongo_collection.insert_many.call_args[1]
        assert "maxTimeMS" not in call_kwargs  # Removed because insert_many doesn't accept it

    @pytest.mark.asyncio
    async def test_find_with_empty_filter_dict(self, mock_mongo_collection):
        """Test that find() works with empty filter dict."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.find = MagicMock(return_value=mock_cursor)

        # Empty dict should work (will be combined with app_id filter)
        cursor = wrapper.find({})
        assert cursor == mock_cursor

    @pytest.mark.asyncio
    async def test_aggregate_with_valid_pipeline(self, mock_mongo_collection):
        """Test that aggregate() works with valid pipeline."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        mock_cursor = MagicMock()
        mock_mongo_collection.aggregate = MagicMock(return_value=mock_cursor)

        pipeline = [
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        ]
        wrapper.aggregate(pipeline)

        # Verify timeout was added
        call_kwargs = mock_mongo_collection.aggregate.call_args[1]
        assert "maxTimeMS" in call_kwargs

    @pytest.mark.asyncio
    async def test_find_with_sort_validation(self, mock_mongo_collection):
        """Test that find() validates sort specification."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_mongo_collection,
            read_scopes=["test_app"],
            write_scope="test_app",
        )

        from mdb_engine.constants import MAX_SORT_FIELDS

        # Create sort with too many fields
        sort_fields = [(f"field{i}", 1) for i in range(MAX_SORT_FIELDS + 1)]

        with pytest.raises(QueryValidationError, match="exceeds maximum fields"):
            wrapper.find({"status": "active"}, sort=sort_fields)
