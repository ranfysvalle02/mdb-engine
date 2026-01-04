"""
Extended tests for scoped wrapper to cover security proxies and error paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo.errors import CollectionInvalid, ConnectionFailure, OperationFailure

from mdb_engine.database.scoped_wrapper import (
    AsyncAtlasIndexManager,
    _SecureCollectionProxy,
)
from mdb_engine.exceptions import MongoDBEngineError


class TestSecureCollectionProxy:
    """Test _SecureCollectionProxy security features."""

    def test_proxy_blocks_dangerous_attributes(self):
        """Test that dangerous attributes are blocked."""
        mock_coll = MagicMock(spec=AsyncIOMotorCollection)
        proxy = _SecureCollectionProxy(mock_coll)

        # Blocked attributes
        with pytest.raises(AttributeError, match="blocked for security"):
            _ = proxy.database

        with pytest.raises(AttributeError, match="blocked for security"):
            _ = proxy.client

        with pytest.raises(AttributeError, match="blocked for security"):
            _ = proxy.db

        # Allowed attributes
        proxy.find
        mock_coll.find.assert_not_called()  # accessing attribute doesn't call it

    def test_proxy_delegation(self):
        """Test that other attributes are delegated correctly."""
        mock_coll = MagicMock(spec=AsyncIOMotorCollection)
        mock_coll.find_one = AsyncMock(return_value={"id": 1})
        proxy = _SecureCollectionProxy(mock_coll)

        # Should be delegated
        assert proxy.find_one == mock_coll.find_one


class TestAsyncAtlasIndexManagerExtended:
    """Extended tests for AsyncAtlasIndexManager."""

    @pytest.mark.asyncio
    async def test_ensure_collection_exists_already_exists(self):
        """Test benign CollectionInvalid error (already exists)."""
        mock_coll = MagicMock()
        mock_coll.name = "test_coll"
        mock_coll.database.create_collection = AsyncMock(
            side_effect=CollectionInvalid("Collection already exists")
        )

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)
            # Should not raise
            await manager._ensure_collection_exists()

    @pytest.mark.asyncio
    async def test_ensure_collection_exists_failure(self):
        """Test fatal failures in _ensure_collection_exists."""
        mock_coll = MagicMock()
        mock_coll.name = "test_coll"

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)

            # Connection Failure
            mock_coll.database.create_collection = AsyncMock(
                side_effect=ConnectionFailure("Connection lost")
            )
            with pytest.raises(MongoDBEngineError, match="connection failed"):
                await manager._ensure_collection_exists()

            # Other Operation Failure
            mock_coll.database.create_collection = AsyncMock(
                side_effect=OperationFailure("Other error")
            )
            with pytest.raises(MongoDBEngineError, match="Error creating prerequisite collection"):
                await manager._ensure_collection_exists()

    @pytest.mark.asyncio
    async def test_handle_existing_index_failed_state(self):
        """Test handling of index in FAILED state."""
        mock_coll = MagicMock()
        mock_coll.update_search_index = AsyncMock()  # Required if definition check fails

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)

            # Provide matching definition to avoid update trigger
            existing = {
                "status": "FAILED",
                "queryable": False,
                "latestDefinition": {"mappings": {"dynamic": True}},
            }
            definition = {"mappings": {"dynamic": True}}

            # Should return False (not ready) and log error
            with patch("mdb_engine.database.scoped_wrapper.logger") as mock_logger:
                result = await manager._handle_existing_index(existing, definition, "search", "idx")
                assert result is False
                mock_logger.error.assert_called()
                assert "FAILED state" in mock_logger.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_existing_index_definition_changed(self):
        """Test detection of changed definition."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            # We need to patch the class method since instance has slots
            with patch.object(
                AsyncAtlasIndexManager, "update_search_index", new_callable=AsyncMock
            ) as mock_update:
                manager = AsyncAtlasIndexManager(mock_coll)

                existing = {
                    "latestDefinition": {"mappings": {"dynamic": False}},
                    "status": "READY",
                }
                new_definition = {"mappings": {"dynamic": True}}

                result = await manager._handle_existing_index(
                    existing, new_definition, "search", "idx"
                )

                assert result is False  # Will wait (mocked update)
                mock_update.assert_awaited_with(
                    name="idx", definition=new_definition, wait_for_ready=False
                )

    @pytest.mark.asyncio
    async def test_create_search_index_race_condition(self):
        """Test benign race condition (IndexAlreadyExists)."""
        mock_coll = MagicMock()
        mock_coll.create_search_index = AsyncMock(
            side_effect=OperationFailure("IndexAlreadyExists")
        )

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)

            # Mock dependencies on the class
            with patch.object(
                AsyncAtlasIndexManager,
                "_ensure_collection_exists",
                new_callable=AsyncMock,
            ), patch.object(
                AsyncAtlasIndexManager, "get_search_index", new_callable=AsyncMock
            ) as mock_get, patch.object(
                AsyncAtlasIndexManager,
                "_wait_for_search_index_ready",
                new_callable=AsyncMock,
            ) as mock_wait:
                mock_get.return_value = None
                mock_wait.return_value = True

                with patch("mdb_engine.database.scoped_wrapper.logger") as mock_logger:
                    result = await manager.create_search_index(
                        "idx", {"def": 1}, wait_for_ready=True
                    )

                    assert result is True
                    mock_logger.warning.assert_called()
                    assert "Race condition" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_search_index_errors(self):
        """Test error handling in create_search_index."""
        mock_coll = MagicMock()
        mock_coll.database.create_collection = (
            AsyncMock()
        )  # Defensive: allow real ensure_exists to pass if mock fails

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)

            with patch.object(
                AsyncAtlasIndexManager,
                "_ensure_collection_exists",
                new_callable=AsyncMock,
            ) as mock_ensure, patch.object(
                AsyncAtlasIndexManager, "get_search_index", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = None

                # Connection Failure
                mock_coll.create_search_index = AsyncMock(side_effect=ConnectionFailure("Fail"))
                with pytest.raises(MongoDBEngineError, match="Connection failed"):
                    await manager.create_search_index("idx", {})

                # Operation Failure (fatal)
                mock_coll.create_search_index = AsyncMock(
                    side_effect=OperationFailure("Fatal error")
                )
                with pytest.raises(MongoDBEngineError, match="Failed to create/check search index"):
                    await manager.create_search_index("idx", {})
