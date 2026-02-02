"""
Unit tests for Memory Service update functionality.

Tests the hybrid update pattern implementation including:
- Content updates via Mem0 (for re-embedding)
- Metadata updates via direct PyMongo
- Final result fetched from MongoDB
- Error handling and edge cases
- Input validation
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mdb_engine.memory.service import Mem0MemoryService, Mem0MemoryServiceError


@pytest.fixture
def mock_mem0_memory():
    """Create a mock Mem0 Memory instance."""
    mock_memory = MagicMock()
    mock_memory.add = MagicMock(return_value=[{"id": "test_id", "memory": "test"}])
    mock_memory.get = MagicMock(return_value={"id": "test_id", "memory": "test"})
    mock_memory.delete = MagicMock(return_value=True)
    mock_memory.get_all = MagicMock(return_value=[])
    mock_memory.search = MagicMock(return_value=[])
    mock_memory.update = MagicMock(return_value=None)  # Mem0 update returns None/ignored
    return mock_memory


@pytest.fixture
def mock_mongo_collection():
    """Create a mock MongoDB collection."""
    mock_collection = MagicMock()
    return mock_collection


@pytest.fixture
def memory_service(mock_mem0_memory, mock_mongo_collection):
    """Create a Mem0MemoryService instance with mocked Mem0 and MongoDB."""
    with (
        patch("mdb_engine.memory.service._check_mem0_available", return_value=True),
        patch("mdb_engine.memory.service.Memory", return_value=mock_mem0_memory),
        patch("mdb_engine.memory.service.MongoClient") as mock_client,
    ):
        # Setup MongoDB client mock
        mock_client_instance = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_mongo_collection)
        mock_client_instance.__getitem__ = MagicMock(return_value=mock_db)
        mock_client.return_value = mock_client_instance

        service = Mem0MemoryService(
            mongo_uri="mongodb://localhost:27017/",
            db_name="test_db",
            app_slug="test_app",
            config={"collection_name": "test_memories"},
        )
        service.memory = mock_mem0_memory
        service.memories_collection = mock_mongo_collection
        return service


@pytest.fixture
def existing_memory():
    """Create a sample existing memory document in Mem0 format."""
    # Mem0 stores memories as: {_id: "...", embedding: [...], payload: {...}}
    return {
        "_id": "memory_123",
        "embedding": [0.1] * 1536,
        "payload": {
            "memory": "I like Python",
            "text": "I like Python",
            "user_id": "user_123",
            "metadata": {"source": "chat", "category": "programming"},
            "created_at": datetime(2024, 1, 1),
            "updated_at": datetime(2024, 1, 1),
        },
    }


@pytest.fixture
def normalized_memory():
    """Create a normalized memory document (what get() returns)."""
    return {
        "id": "memory_123",
        "memory": "I like Python",
        "text": "I like Python",
        "user_id": "user_123",
        "metadata": {"source": "chat", "category": "programming"},
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 1, 1),
    }


class TestMemoryServiceUpdate:
    """Test memory service update functionality."""

    def test_update_with_mem0_method_success(
        self, memory_service, existing_memory, normalized_memory
    ):
        """Test successful hybrid update: content via Mem0, metadata via MongoDB."""
        # Mock MongoDB collection responses (Mem0 format)
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "I love Python programming"
        updated_raw["payload"]["text"] = "I love Python programming"
        updated_raw["payload"]["metadata"]["updated"] = True
        updated_raw["payload"]["updated_at"] = "2024-01-02T00:00:00"

        updated_normalized = normalized_memory.copy()
        updated_normalized["memory"] = "I love Python programming"
        updated_normalized["text"] = "I love Python programming"
        updated_normalized["metadata"]["updated"] = True
        updated_normalized["updated_at"] = "2024-01-02T00:00:00"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},  # Existence check
                updated_raw,  # Final fetch (raw Mem0 format)
            ]
        )
        memory_service.memories_collection.update_one = MagicMock()

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="I love Python programming",
            metadata={"updated": True},
        )

        assert result is not None
        assert result["id"] == "memory_123"  # ID preserved
        assert result["memory"] == "I love Python programming"
        assert result["metadata"]["updated"] is True

        # Verify Mem0 was called for content update
        memory_service.memory.update.assert_called_once_with(
            memory_id="memory_123", data="I love Python programming"
        )

        # Verify MongoDB was called for metadata update
        memory_service.memories_collection.update_one.assert_called_once()
        update_call = memory_service.memories_collection.update_one.call_args
        assert update_call[0][0] == {"_id": "memory_123"}  # Query uses _id
        assert "$set" in update_call[0][1]  # Update operation
        assert "payload.metadata.updated" in update_call[0][1]["$set"]
        assert "payload.updated_at" in update_call[0][1]["$set"]

    def test_update_content_only(self, memory_service, existing_memory, normalized_memory):
        """Test updating only content (no metadata) - Mem0 only."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated content"
        updated_raw["payload"]["text"] = "Updated content"

        updated_normalized = normalized_memory.copy()
        updated_normalized["memory"] = "Updated content"
        updated_normalized["text"] = "Updated content"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},  # Existence check
                updated_raw,  # Final fetch (raw Mem0 format)
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated content",
        )

        assert result is not None
        assert result["memory"] == "Updated content"
        # Mem0 should be called for content
        memory_service.memory.update.assert_called_once_with(
            memory_id="memory_123", data="Updated content"
        )
        # MongoDB should NOT be called for update_one (no metadata)
        memory_service.memories_collection.update_one.assert_not_called()

    def test_update_metadata_only(self, memory_service, existing_memory, normalized_memory):
        """Test updating only metadata (no content change) - MongoDB only."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["metadata"]["category"] = "updated"

        updated_normalized = normalized_memory.copy()
        updated_normalized["metadata"]["category"] = "updated"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},  # Existence check
                updated_raw,  # Final fetch (raw Mem0 format)
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            metadata={"category": "updated"},
        )

        assert result is not None
        # Mem0 should NOT be called (no content update)
        memory_service.memory.update.assert_not_called()
        # MongoDB should be called for metadata
        memory_service.memories_collection.update_one.assert_called_once()
        update_call = memory_service.memories_collection.update_one.call_args
        assert update_call[0][0] == {"_id": "memory_123"}  # Query uses _id
        assert "payload.metadata.category" in update_call[0][1]["$set"]

    def test_update_memory_not_found(self, memory_service):
        """Test update when memory doesn't exist."""
        memory_service.memories_collection.find_one = MagicMock(return_value=None)

        result = memory_service.update(
            memory_id="nonexistent",
            user_id="user_123",
            memory="New content",
        )

        assert result is None
        memory_service.memory.update.assert_not_called()
        memory_service.memories_collection.update_one.assert_not_called()

    def test_update_invalid_memory_id(self, memory_service):
        """Test update with invalid memory_id."""
        with pytest.raises(ValueError, match="memory_id is required"):
            memory_service.update(memory_id="", user_id="user_123", memory="content")

        with pytest.raises(ValueError, match="memory_id is required"):
            memory_service.update(memory_id=None, user_id="user_123", memory="content")  # type: ignore

    def test_update_with_data_parameter(self, memory_service, existing_memory):
        """Test update using 'data' parameter as alternative to 'memory' parameter."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated via data parameter"
        updated_raw["payload"]["text"] = "Updated via data parameter"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            data="Updated via data parameter",
        )

        assert result is not None
        memory_service.memory.update.assert_called_once_with(
            memory_id="memory_123", data="Updated via data parameter"
        )

    def test_update_with_messages_parameter(self, memory_service, existing_memory):
        """Test update using 'messages' parameter."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated via messages"
        updated_raw["payload"]["text"] = "Updated via messages"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            messages=[{"role": "user", "content": "Updated via messages"}],
        )

        assert result is not None
        memory_service.memory.update.assert_called_once_with(
            memory_id="memory_123", data="Updated via messages"
        )

    def test_update_mem0_method_fails_raises_error(self, memory_service, existing_memory):
        """Test that update raises error when Mem0's update fails."""
        memory_service.memories_collection.find_one = MagicMock(
            return_value={"_id": "memory_123", "payload": {"user_id": "user_123"}}
        )
        memory_service.memory.update = MagicMock(side_effect=Exception("Method not available"))

        # Should raise Mem0MemoryServiceError when Mem0 update fails
        with pytest.raises(Mem0MemoryServiceError, match="Content update failed"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory="Updated content",
            )

    def test_update_fetches_final_result_from_mongodb(
        self, memory_service, existing_memory, normalized_memory
    ):
        """Test that update always fetches final result from MongoDB."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated content"
        updated_raw["payload"]["text"] = "Updated content"

        updated_normalized = normalized_memory.copy()
        updated_normalized["memory"] = "Updated content"
        updated_normalized["text"] = "Updated content"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},  # Existence check
                updated_raw,  # Final fetch (raw Mem0 format)
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated content",
        )

        # Should return normalized result from MongoDB (final fetch)
        assert result is not None
        assert result["id"] == "memory_123"
        assert result["memory"] == "Updated content"
        # Verify MongoDB find_one was called twice: existence check + final fetch
        assert memory_service.memories_collection.find_one.call_count == 2

    def test_update_normalizes_content_input(self, memory_service, existing_memory):
        """Test that content input is properly normalized."""
        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                existing_memory,
            ]
        )

        memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="  Updated content  ",
        )

        # Verify normalized content was passed to Mem0
        memory_service.memory.update.assert_called_once()
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["data"] == "Updated content"  # Whitespace normalized

    def test_update_metadata_updates_via_mongodb(self, memory_service, existing_memory):
        """Test that metadata updates go through MongoDB, not Mem0."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["metadata"]["updated"] = True

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            metadata={"updated": True},
        )

        assert result is not None
        # Mem0 should NOT be called (no content update)
        memory_service.memory.update.assert_not_called()
        # MongoDB should be called for metadata
        memory_service.memories_collection.update_one.assert_called_once()
        update_call = memory_service.memories_collection.update_one.call_args
        assert update_call[0][0] == {"_id": "memory_123"}  # Query uses _id
        assert "payload.metadata.updated" in update_call[0][1]["$set"]

    def test_update_without_user_id(self, memory_service, existing_memory):
        """Test update when user_id is None (non-SSO use case)."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated content"
        updated_raw["payload"]["text"] = "Updated content"
        updated_raw["payload"]["metadata"]["category"] = "test"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {}},  # No user_id check
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id=None,  # No user_id provided (non-SSO use case)
            memory="Updated content",
            metadata={"category": "test"},
        )

        assert result is not None
        # Mem0 should be called for content
        memory_service.memory.update.assert_called_once()
        # MongoDB should be called for metadata
        memory_service.memories_collection.update_one.assert_called_once()

    def test_update_without_user_id_no_metadata(self, memory_service, existing_memory):
        """Test update when user_id is None and no metadata provided (non-SSO use case)."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated content"
        updated_raw["payload"]["text"] = "Updated content"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id=None,  # No user_id provided (non-SSO use case)
            memory="Updated content",
        )

        assert result is not None
        # Mem0 should be called for content
        memory_service.memory.update.assert_called_once()
        # MongoDB should NOT be called (no metadata)
        memory_service.memories_collection.update_one.assert_not_called()


class TestMemoryServiceUpdateErrorHandling:
    """Test error handling in update method."""

    def test_update_type_error(self, memory_service, existing_memory):
        """Test update handles TypeError from Mem0."""
        memory_service.memories_collection.find_one = MagicMock(
            return_value={"_id": "memory_123", "payload": {"user_id": "user_123"}}
        )
        memory_service.memory.update = MagicMock(side_effect=TypeError("Invalid type"))

        # TypeError should be wrapped in Mem0MemoryServiceError
        with pytest.raises(Mem0MemoryServiceError, match="Content update failed"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory="Content",
            )

    def test_update_invalid_content_type(self, memory_service, existing_memory):
        """Test update validates content input types."""
        memory_service.memories_collection.find_one = MagicMock(
            return_value={"_id": "memory_123", "payload": {"user_id": "user_123"}}
        )

        # The error should be raised during normalization
        with pytest.raises(TypeError, match="memory parameter must be a string"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory=123,  # Invalid type
            )

    def test_update_invalid_metadata_type(self, memory_service, existing_memory):
        """Test update validates metadata input types."""
        memory_service.memories_collection.find_one = MagicMock(
            return_value={"_id": "memory_123", "payload": {"user_id": "user_123"}}
        )

        # The error should be raised during normalization
        with pytest.raises(TypeError, match="metadata parameter must be a dict"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                metadata="not a dict",  # Invalid type
            )


class TestMemoryServiceUpdateEdgeCases:
    """Test edge cases in update functionality."""

    def test_update_empty_content_string(self, memory_service, existing_memory):
        """Test update with empty content string."""
        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                existing_memory,
            ]
        )

        # Empty string should be normalized to None
        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="   ",  # Whitespace only
        )

        # Should not call Mem0 if content is empty
        memory_service.memory.update.assert_not_called()
        assert result is not None

    def test_update_preserves_memory_id(self, memory_service, existing_memory):
        """Test that memory ID is always preserved."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["memory"] = "Updated"
        updated_raw["payload"]["text"] = "Updated"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated",
        )

        assert result["id"] == "memory_123"  # ID preserved

    def test_update_with_none_values(self, memory_service, existing_memory):
        """Test update handles None values gracefully."""
        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                existing_memory,
            ]
        )

        # Should not raise error with None values
        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory=None,
            metadata=None,
        )

        assert result is not None
        # Should not call Mem0 (no content)
        memory_service.memory.update.assert_not_called()
        # Should not call MongoDB update_one (no metadata)
        memory_service.memories_collection.update_one.assert_not_called()

    def test_update_unauthorized_user_id_mismatch(self, memory_service, existing_memory):
        """Test update rejects unauthorized access when user_id doesn't match."""
        memory_service.memories_collection.find_one = MagicMock(
            return_value={"_id": "memory_123", "payload": {"user_id": "different_user"}}
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",  # Different from existing user_id
            memory="Updated content",
        )

        assert result is None
        # Should not proceed with update
        memory_service.memory.update.assert_not_called()
        memory_service.memories_collection.update_one.assert_not_called()

    def test_update_user_id_in_metadata(self, memory_service, existing_memory):
        """Test that user_id is added to metadata when provided."""
        updated_raw = existing_memory.copy()
        updated_raw["payload"]["metadata"]["category"] = "test"
        updated_raw["payload"]["user_id"] = "user_123"

        memory_service.memories_collection.find_one = MagicMock(
            side_effect=[
                {"_id": "memory_123", "payload": {"user_id": "user_123"}},
                updated_raw,
            ]
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            metadata={"category": "test"},
        )

        assert result is not None
        # Verify MongoDB update includes user_id in payload
        memory_service.memories_collection.update_one.assert_called_once()
        update_call = memory_service.memories_collection.update_one.call_args
        assert update_call[0][0] == {"_id": "memory_123"}  # Query uses _id
        assert "payload.user_id" in update_call[0][1]["$set"]
        assert update_call[0][1]["$set"]["payload.user_id"] == "user_123"
