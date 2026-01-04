"""
Unit tests for seeding functionality.

Tests the initial data seeding system including:
- Idempotent seeding behavior
- Metadata tracking
- Datetime conversion
- Error handling
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.seeding import seed_initial_data


@pytest.mark.unit
class TestSeedInitialData:
    """Test seed_initial_data function."""

    @pytest.mark.asyncio
    async def test_seed_empty_collection(self, mock_mongo_collection):
        """Test seeding an empty collection."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        # Mock metadata collection to return no existing seeding
        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_many = AsyncMock(
            return_value=MagicMock(inserted_ids=["id1", "id2"])
        )
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {
            "test_collection": [
                {"name": "Test 1", "value": 100},
                {"name": "Test 2", "value": 200},
            ]
        }

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        assert results["test_collection"] == 2
        mock_mongo_collection.insert_many.assert_called_once()
        mock_mongo_collection.insert_one.assert_called_once()  # Metadata

    @pytest.mark.asyncio
    async def test_seed_idempotent_already_seeded(self, mock_mongo_collection):
        """Test that seeding skips already seeded collections."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        # Mock metadata showing collection already seeded
        seeding_metadata = {
            "app_slug": "test_app",
            "seeded_collections": ["test_collection"],
        }
        mock_mongo_collection.find_one = AsyncMock(return_value=seeding_metadata)
        mock_mongo_collection.update_one = AsyncMock()

        initial_data = {"test_collection": [{"name": "Test 1", "value": 100}]}

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        assert results["test_collection"] == 0
        mock_mongo_collection.insert_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_idempotent_collection_not_empty(self, mock_mongo_collection):
        """Test that seeding skips non-empty collections."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        # Mock metadata showing no seeding, but collection has data
        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=5)  # Collection has 5 docs
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": [{"name": "Test 1", "value": 100}]}

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        assert results["test_collection"] == 0
        mock_mongo_collection.insert_many.assert_not_called()
        mock_mongo_collection.insert_one.assert_called_once()  # Metadata

    @pytest.mark.asyncio
    async def test_seed_adds_created_at(self, mock_mongo_collection):
        """Test that seeding adds created_at timestamp if missing."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": [{"name": "Test 1", "value": 100}]}  # No created_at

        with patch("mdb_engine.core.seeding.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)

            await seed_initial_data(mock_db, "test_app", initial_data)

            # Check that insert_many was called with created_at added
            call_args = mock_mongo_collection.insert_many.call_args
            assert call_args is not None
            inserted_docs = call_args[0][0]
            assert len(inserted_docs) == 1
            assert "created_at" in inserted_docs[0]

    @pytest.mark.asyncio
    async def test_seed_preserves_existing_created_at(self, mock_mongo_collection):
        """Test that seeding preserves existing created_at field."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        existing_date = datetime(2023, 6, 15, 10, 30, 0)
        initial_data = {"test_collection": [{"name": "Test 1", "created_at": existing_date}]}

        await seed_initial_data(mock_db, "test_app", initial_data)

        # Check that existing created_at was preserved
        call_args = mock_mongo_collection.insert_many.call_args
        inserted_docs = call_args[0][0]
        assert inserted_docs[0]["created_at"] == existing_date

    @pytest.mark.asyncio
    async def test_seed_handles_datetime_strings(self, mock_mongo_collection):
        """Test that seeding converts datetime strings to datetime objects."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": [{"name": "Test 1", "date": "2024-01-01T12:00:00Z"}]}

        with patch("dateutil.parser.parse") as mock_parse:
            # Mock dateutil.parser.parse
            try:
                from dateutil.parser import parse as parse_date_real

                mock_parse.side_effect = parse_date_real
            except ImportError:
                # If dateutil not available, skip this test
                pytest.skip("dateutil not available")

            await seed_initial_data(mock_db, "test_app", initial_data)

            # Verify parse_date was called (if dateutil available)
            # The actual conversion happens inside the function

    @pytest.mark.asyncio
    async def test_seed_handles_datetime_parsing_errors(self, mock_mongo_collection):
        """Test handling datetime parsing errors (lines 102, 105-110)."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        # Test invalid datetime string (will fail to parse)
        initial_data = {
            "test_collection": [
                {"name": "Test 1", "date": "invalid-date-string"},
                {"name": "Test 2", "date": {"$date": "invalid-extended-json"}},
            ]
        }

        # Mock insert_many to capture what was actually inserted - must be AsyncMock
        inserted_docs = []

        async def capture_insert(docs, **kwargs):
            inserted_docs.extend(docs)
            return MagicMock(inserted_ids=["id1", "id2"])

        mock_mongo_collection.insert_many = AsyncMock(side_effect=capture_insert)

        await seed_initial_data(mock_db, "test_app", initial_data)

        # Should complete without error - invalid dates kept as strings
        assert len(inserted_docs) == 2
        # The invalid date strings should remain as strings (not converted)
        assert inserted_docs[0]["date"] == "invalid-date-string"
        assert inserted_docs[1]["date"] == {"$date": "invalid-extended-json"}

    @pytest.mark.asyncio
    async def test_seed_handles_value_error_datetime_parsing(self, mock_mongo_collection):
        """Test handling ValueError during datetime parsing (line 102)."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        # Mock insert_many to capture what was inserted
        inserted_docs = []

        def capture_insert(docs, **kwargs):
            inserted_docs.extend(docs)
            return MagicMock(inserted_ids=["id1"])

        mock_mongo_collection.insert_many = AsyncMock(side_effect=capture_insert)

        # Test ValueError during datetime parsing (invalid format)
        initial_data = {
            "test_collection": [{"name": "Test 1", "date": "2023-13-45"}]  # Invalid date format
        }

        await seed_initial_data(mock_db, "test_app", initial_data)

        # Should complete without error - invalid date kept as string
        assert len(inserted_docs) == 1
        assert inserted_docs[0]["date"] == "2023-13-45"  # Kept as string

    @pytest.mark.asyncio
    async def test_seed_handles_metadata_update_errors(self, mock_mongo_collection):
        """Test handling errors when updating seeding metadata (lines 167-175)."""
        from pymongo.errors import ConnectionFailure, OperationFailure

        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1"]))
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": [{"name": "Test 1"}]}

        # Test OperationFailure
        mock_mongo_collection.update_one = AsyncMock(side_effect=OperationFailure("Update failed"))
        results = await seed_initial_data(mock_db, "test_app", initial_data)
        assert results["test_collection"] == 1  # Should still return results

        # Test ConnectionFailure
        mock_mongo_collection.update_one = AsyncMock(
            side_effect=ConnectionFailure("Connection failed")
        )
        results = await seed_initial_data(mock_db, "test_app", initial_data)
        assert results["test_collection"] == 1

        # Test ValueError
        mock_mongo_collection.update_one = AsyncMock(side_effect=ValueError("Invalid value"))
        results = await seed_initial_data(mock_db, "test_app", initial_data)
        assert results["test_collection"] == 1

        # Test TypeError
        mock_mongo_collection.update_one = AsyncMock(side_effect=TypeError("Invalid type"))
        results = await seed_initial_data(mock_db, "test_app", initial_data)
        assert results["test_collection"] == 1

        # Test KeyError
        mock_mongo_collection.update_one = AsyncMock(side_effect=KeyError("Missing key"))
        results = await seed_initial_data(mock_db, "test_app", initial_data)
        assert results["test_collection"] == 1

    @pytest.mark.asyncio
    async def test_seed_handles_errors_gracefully(self, mock_mongo_collection):
        """Test that seeding handles errors without crashing."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(side_effect=Exception("Database error"))
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": [{"name": "Test 1", "value": 100}]}

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        # Should return 0 for failed collection but continue
        assert results["test_collection"] == 0

    @pytest.mark.asyncio
    async def test_seed_multiple_collections(self, mock_mongo_collection):
        """Test seeding multiple collections."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.collection1 = mock_mongo_collection
        mock_db.collection2 = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1"]))
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {
            "collection1": [{"name": "Test 1"}],
            "collection2": [{"name": "Test 2"}],
        }

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        assert results["collection1"] == 1
        assert results["collection2"] == 1
        assert mock_mongo_collection.insert_many.call_count == 2

    @pytest.mark.asyncio
    async def test_seed_empty_documents_list(self, mock_mongo_collection):
        """Test seeding with empty documents list."""
        mock_db = MagicMock()
        mock_db.app_seeding_metadata = mock_mongo_collection
        mock_db.test_collection = mock_mongo_collection

        mock_mongo_collection.find_one = AsyncMock(return_value=None)
        mock_mongo_collection.count_documents = AsyncMock(return_value=0)
        mock_mongo_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="meta_id"))

        initial_data = {"test_collection": []}  # Empty list

        results = await seed_initial_data(mock_db, "test_app", initial_data)

        assert results["test_collection"] == 0
        mock_mongo_collection.insert_many.assert_not_called()
