"""
Unit tests for Memory Service update functionality.

Tests the update method implementation including:
- Mem0's built-in update method usage
- Fallback to direct MongoDB updates
- Content and metadata updates
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
    return mock_memory


@pytest.fixture
def memory_service(mock_mem0_memory):
    """Create a Mem0MemoryService instance with mocked Mem0."""
    with (
        patch("mdb_engine.memory.service._check_mem0_available", return_value=True),
        patch("mdb_engine.memory.service.Memory", return_value=mock_mem0_memory),
    ):
        service = Mem0MemoryService(
            mongo_uri="mongodb://localhost:27017/",
            db_name="test_db",
            app_slug="test_app",
            config={"collection_name": "test_memories"},
        )
        service.memory = mock_mem0_memory
        return service


@pytest.fixture
def existing_memory():
    """Create a sample existing memory document."""
    return {
        "id": "memory_123",
        "memory": "I like Python",
        "text": "I like Python",
        "user_id": "user_123",
        "metadata": {"source": "chat", "category": "programming"},
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 1, 1),
        "embedding": [0.1] * 1536,
    }


class TestMemoryServiceUpdate:
    """Test memory service update functionality."""

    def test_update_with_mem0_method_success(self, memory_service, existing_memory):
        """Test successful update using Mem0's built-in update method."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(
            return_value={
                "id": "memory_123",
                "memory": "I love Python programming",
                "text": "I love Python programming",
                "user_id": "user_123",
                "metadata": {"source": "chat", "category": "programming", "updated": True},
            }
        )

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
        memory_service.memory.update.assert_called_once()
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["memory_id"] == "memory_123"
        assert call_kwargs["text"] == "I love Python programming"
        assert call_kwargs["metadata"] == {"updated": True}
        assert call_kwargs["user_id"] == "user_123"

    def test_update_content_only(self, memory_service, existing_memory):
        """Test updating only content (no metadata)."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(
            return_value={
                "id": "memory_123",
                "memory": "Updated content",
                "text": "Updated content",
            }
        )

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated content",
        )

        assert result is not None
        assert result["memory"] == "Updated content"
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["text"] == "Updated content"
        assert "metadata" not in call_kwargs or call_kwargs["metadata"] is None

    def test_update_metadata_only(self, memory_service, existing_memory):
        """Test updating only metadata (no content change)."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            metadata={"category": "updated"},
        )

        assert result is not None
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["metadata"] == {"category": "updated"}
        assert "text" not in call_kwargs or call_kwargs["text"] is None

    def test_update_memory_not_found(self, memory_service):
        """Test update when memory doesn't exist."""
        memory_service.get = MagicMock(return_value=None)

        result = memory_service.update(
            memory_id="nonexistent",
            user_id="user_123",
            memory="New content",
        )

        assert result is None
        memory_service.memory.update.assert_not_called()

    def test_update_invalid_memory_id(self, memory_service):
        """Test update with invalid memory_id."""
        with pytest.raises(ValueError, match="memory_id is required"):
            memory_service.update(memory_id="", user_id="user_123", memory="content")

        with pytest.raises(ValueError, match="memory_id is required"):
            memory_service.update(memory_id=None, user_id="user_123", memory="content")  # type: ignore

    def test_update_with_data_parameter(self, memory_service, existing_memory):
        """Test update using 'data' parameter for backward compatibility."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            data="Updated via data parameter",
        )

        assert result is not None
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["text"] == "Updated via data parameter"

    def test_update_with_messages_parameter(self, memory_service, existing_memory):
        """Test update using 'messages' parameter."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            messages=[{"role": "user", "content": "Updated via messages"}],
        )

        assert result is not None
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["text"] == "Updated via messages"

    def test_update_mem0_method_fails_raises_error(self, memory_service, existing_memory):
        """Test that update raises error when Mem0's update fails."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(side_effect=AttributeError("Method not available"))

        # Should raise Mem0MemoryServiceError when Mem0 update fails
        with pytest.raises(Mem0MemoryServiceError, match="Update failed"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory="Updated content",
            )

    def test_update_mem0_returns_none_returns_none(self, memory_service, existing_memory):
        """Test that update returns None when Mem0's update returns None."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=None)

        # Should return None when Mem0 update returns None (no fallback)
        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated content",
        )

        assert result is None

    def test_update_normalizes_content_input(self, memory_service, existing_memory):
        """Test that content input is properly normalized."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        # Test with whitespace
        memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="  Updated content  ",
        )

        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["text"] == "Updated content"

    def test_update_metadata_merging(self, memory_service, existing_memory):
        """Test that metadata is properly merged (not replaced)."""
        memory_service.get = MagicMock(return_value=existing_memory)
        updated_memory = existing_memory.copy()
        updated_memory["metadata"] = {
            "source": "chat",
            "category": "programming",
            "updated": True,
        }
        memory_service.memory.update = MagicMock(return_value=updated_memory)

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            metadata={"updated": True},
        )

        assert result is not None
        # Mem0 handles merging, so we just verify it was called with the new metadata
        call_kwargs = memory_service.memory.update.call_args[1]
        assert call_kwargs["metadata"] == {"updated": True}


class TestMemoryServiceUpdateErrorHandling:
    """Test error handling in update method."""

    def test_update_type_error(self, memory_service, existing_memory):
        """Test update handles TypeError."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(side_effect=TypeError("Invalid type"))

        # TypeError should be caught and wrapped in Mem0MemoryServiceError
        with pytest.raises(Mem0MemoryServiceError):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory="Content",
            )

    def test_update_invalid_content_type(self, memory_service, existing_memory):
        """Test update validates content input types."""
        memory_service.get = MagicMock(return_value=existing_memory)

        # The error is wrapped in Mem0MemoryServiceError, but the original TypeError is preserved
        with pytest.raises(Mem0MemoryServiceError, match="memory parameter must be a string"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                memory=123,  # Invalid type
            )

    def test_update_invalid_metadata_type(self, memory_service, existing_memory):
        """Test update validates metadata input types."""
        memory_service.get = MagicMock(return_value=existing_memory)

        # The error is wrapped in Mem0MemoryServiceError, but the original TypeError is preserved
        with pytest.raises(Mem0MemoryServiceError, match="metadata must be a dict or None"):
            memory_service.update(
                memory_id="memory_123",
                user_id="user_123",
                metadata="not a dict",  # Invalid type
            )


class TestMemoryServiceUpdateEdgeCases:
    """Test edge cases in update functionality."""

    def test_update_empty_content_string(self, memory_service, existing_memory):
        """Test update with empty content string."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        # Empty string should be normalized to None
        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="   ",  # Whitespace only
        )

        # Should not update content if it's empty
        call_kwargs = memory_service.memory.update.call_args[1]
        assert "text" not in call_kwargs or call_kwargs["text"] is None

    def test_update_preserves_memory_id(self, memory_service, existing_memory):
        """Test that memory ID is always preserved."""
        memory_service.get = MagicMock(return_value=existing_memory)
        updated_memory = existing_memory.copy()
        updated_memory["memory"] = "Updated"
        memory_service.memory.update = MagicMock(return_value=updated_memory)

        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory="Updated",
        )

        assert result["id"] == "memory_123"  # ID preserved

    def test_update_with_none_values(self, memory_service, existing_memory):
        """Test update handles None values gracefully."""
        memory_service.get = MagicMock(return_value=existing_memory)
        memory_service.memory.update = MagicMock(return_value=existing_memory)

        # Should not raise error with None values
        result = memory_service.update(
            memory_id="memory_123",
            user_id="user_123",
            memory=None,
            metadata=None,
        )

        assert result is not None
        # Should only update timestamp
        call_kwargs = memory_service.memory.update.call_args[1]
        assert "text" not in call_kwargs or call_kwargs["text"] is None
        assert "metadata" not in call_kwargs or call_kwargs["metadata"] is None
