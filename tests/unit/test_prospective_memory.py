"""
Unit tests for ProspectiveMemory.

Tests the intention-based trigger system including:
- Setting triggers with condition embeddings
- Checking triggers against context via vector similarity
- Marking triggers as fired (one-shot and recurring)
- Active trigger listing
- Trigger deactivation
- Fallback similarity computation
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.memory.prospective import (
    ProspectiveMemory,
    ProspectiveMemoryError,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_collection():
    """Create a mock async MongoDB collection."""
    collection = AsyncMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.update_one = AsyncMock()
    collection.find = MagicMock()
    collection.aggregate = MagicMock()
    return collection


@pytest.fixture
def prospective(mock_collection):
    """Create a ProspectiveMemory instance with mocked collection."""
    return ProspectiveMemory(
        collection=mock_collection,
        embedding_model="text-embedding-3-small",
        embedding_dims=1536,
    )


# ============================================================================
# Set Trigger Tests
# ============================================================================


class TestSetTrigger:
    """Tests for setting prospective memory triggers."""

    @pytest.mark.asyncio
    async def test_set_trigger_success(self, prospective, mock_collection):
        """Should store trigger with condition embedding."""
        mock_result = MagicMock()
        mock_result.inserted_id = "trigger_123"
        mock_collection.insert_one = AsyncMock(return_value=mock_result)

        with patch.object(prospective, "_get_embedding", return_value=[0.1] * 1536):
            trigger_id = await prospective.set_trigger(
                condition="user mentions deadline",
                action="Remind about risk assessment",
                user_id="user123",
            )

        assert trigger_id == "trigger_123"
        mock_collection.insert_one.assert_called_once()

        # Verify the stored document
        stored_doc = mock_collection.insert_one.call_args[0][0]
        assert stored_doc["condition"] == "user mentions deadline"
        assert stored_doc["action"] == "Remind about risk assessment"
        assert stored_doc["user_id"] == "user123"
        assert stored_doc["triggered"] is False
        assert stored_doc["is_active"] is True
        assert stored_doc["one_shot"] is True
        assert len(stored_doc["condition_embedding"]) == 1536

    @pytest.mark.asyncio
    async def test_set_trigger_empty_condition_raises(self, prospective):
        """Empty condition should raise error."""
        with pytest.raises(ProspectiveMemoryError, match="Condition cannot be empty"):
            await prospective.set_trigger(condition="", action="do something", user_id="user123")

    @pytest.mark.asyncio
    async def test_set_trigger_empty_action_raises(self, prospective):
        """Empty action should raise error."""
        with pytest.raises(ProspectiveMemoryError, match="Action cannot be empty"):
            await prospective.set_trigger(condition="something happens", action="", user_id="user123")

    @pytest.mark.asyncio
    async def test_set_trigger_embedding_failure_raises(self, prospective):
        """Embedding failure should raise error."""
        with patch.object(prospective, "_get_embedding", return_value=[]):
            with pytest.raises(ProspectiveMemoryError, match="Failed to generate"):
                await prospective.set_trigger(condition="test", action="test", user_id="user123")

    @pytest.mark.asyncio
    async def test_set_trigger_recurring(self, prospective, mock_collection):
        """Should support recurring triggers (one_shot=False)."""
        mock_result = MagicMock()
        mock_result.inserted_id = "trigger_456"
        mock_collection.insert_one = AsyncMock(return_value=mock_result)

        with patch.object(prospective, "_get_embedding", return_value=[0.1] * 1536):
            await prospective.set_trigger(
                condition="test",
                action="test",
                user_id="user123",
                one_shot=False,
            )

        stored_doc = mock_collection.insert_one.call_args[0][0]
        assert stored_doc["one_shot"] is False

    @pytest.mark.asyncio
    async def test_set_trigger_with_metadata(self, prospective, mock_collection):
        """Should store optional metadata."""
        mock_result = MagicMock()
        mock_result.inserted_id = "trigger_789"
        mock_collection.insert_one = AsyncMock(return_value=mock_result)

        with patch.object(prospective, "_get_embedding", return_value=[0.1] * 1536):
            await prospective.set_trigger(
                condition="test",
                action="test",
                user_id="user123",
                metadata={"source": "manual", "priority": "high"},
            )

        stored_doc = mock_collection.insert_one.call_args[0][0]
        assert stored_doc["metadata"]["source"] == "manual"
        assert stored_doc["metadata"]["priority"] == "high"


# ============================================================================
# Check Triggers Tests
# ============================================================================


class TestCheckTriggers:
    """Tests for checking triggers against current context."""

    @pytest.mark.asyncio
    async def test_check_triggers_empty_context(self, prospective):
        """Empty context should return no triggers."""
        result = await prospective.check_triggers(current_context="", user_id="user123")
        assert result == []

    @pytest.mark.asyncio
    async def test_check_triggers_no_embedding(self, prospective):
        """Should return empty if embedding generation fails."""
        with patch.object(prospective, "_get_embedding", return_value=[]):
            result = await prospective.check_triggers(current_context="test query", user_id="user123")
        assert result == []

    @pytest.mark.asyncio
    async def test_check_triggers_with_results(self, prospective, mock_collection):
        """Should return matching triggers from vector search."""
        mock_doc = {
            "_id": "trigger_123",
            "action": "Remind about risk assessment",
            "condition": "user mentions deadline",
            "similarity": 0.92,
            "metadata": {},
            "created_at": datetime.now(timezone.utc),
        }

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[mock_doc])
        mock_collection.aggregate = MagicMock(return_value=mock_cursor)

        with patch.object(prospective, "_get_embedding", return_value=[0.1] * 1536):
            result = await prospective.check_triggers(
                current_context="When is the project deadline?",
                user_id="user123",
            )

        assert len(result) == 1
        assert result[0]["trigger_id"] == "trigger_123"
        assert result[0]["action"] == "Remind about risk assessment"
        assert result[0]["similarity"] == 0.92


# ============================================================================
# Fallback Check Tests
# ============================================================================


class TestFallbackCheck:
    """Tests for manual cosine similarity fallback."""

    @pytest.mark.asyncio
    async def test_fallback_cosine_similarity(self, prospective, mock_collection):
        """Fallback should compute cosine similarity manually."""
        # Create triggers with known embeddings
        mock_trigger = {
            "_id": "trigger_1",
            "action": "Test action",
            "condition": "Test condition",
            "condition_embedding": [1.0, 0.0, 0.0],
            "user_id": "user123",
            "is_active": True,
            "triggered": False,
        }

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[mock_trigger])
        mock_collection.find = MagicMock(return_value=mock_cursor)

        # Query with identical embedding should match
        result = await prospective._fallback_check(
            context_embedding=[1.0, 0.0, 0.0],
            user_id="user123",
            threshold=0.9,
            limit=5,
        )

        assert len(result) == 1
        assert result[0]["similarity"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fallback_below_threshold(self, prospective, mock_collection):
        """Triggers below threshold should not be returned."""
        mock_trigger = {
            "_id": "trigger_1",
            "action": "Test",
            "condition": "Test",
            "condition_embedding": [1.0, 0.0, 0.0],
            "user_id": "user123",
            "is_active": True,
            "triggered": False,
        }

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[mock_trigger])
        mock_collection.find = MagicMock(return_value=mock_cursor)

        # Orthogonal vector = 0 similarity, below any threshold
        result = await prospective._fallback_check(
            context_embedding=[0.0, 1.0, 0.0],
            user_id="user123",
            threshold=0.5,
            limit=5,
        )

        assert len(result) == 0


# ============================================================================
# Mark Triggered Tests
# ============================================================================


class TestMarkTriggered:
    """Tests for marking triggers as fired."""

    @pytest.mark.asyncio
    async def test_mark_one_shot_trigger(self, prospective, mock_collection):
        """One-shot trigger should be deactivated when marked."""
        valid_id = "507f1f77bcf86cd799439011"
        mock_collection.find_one = AsyncMock(return_value={"_id": valid_id, "one_shot": True})
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_collection.update_one = AsyncMock(return_value=mock_result)

        result = await prospective.mark_triggered(valid_id)
        assert result is True

        update_doc = mock_collection.update_one.call_args[0][1]
        assert update_doc["$set"]["triggered"] is True
        assert update_doc["$set"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_mark_recurring_trigger(self, prospective, mock_collection):
        """Recurring trigger should stay active after firing."""
        valid_id = "507f1f77bcf86cd799439012"
        mock_collection.find_one = AsyncMock(return_value={"_id": valid_id, "one_shot": False})
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_collection.update_one = AsyncMock(return_value=mock_result)

        result = await prospective.mark_triggered(valid_id)
        assert result is True

        update_doc = mock_collection.update_one.call_args[0][1]
        # Should NOT have triggered=True or is_active=False
        assert "triggered" not in update_doc.get("$set", {})
        assert "is_active" not in update_doc.get("$set", {})
        # Should increment trigger_count
        assert update_doc["$inc"]["trigger_count"] == 1

    @pytest.mark.asyncio
    async def test_mark_nonexistent_trigger(self, prospective, mock_collection):
        """Should return False for non-existent trigger."""
        valid_id = "507f1f77bcf86cd799439013"
        mock_collection.find_one = AsyncMock(return_value=None)

        result = await prospective.mark_triggered(valid_id)
        assert result is False


# ============================================================================
# Active Triggers Tests
# ============================================================================


class TestActiveTriggers:
    """Tests for listing active triggers."""

    @pytest.mark.asyncio
    async def test_get_active_triggers(self, prospective, mock_collection):
        """Should return formatted active triggers."""
        mock_triggers = [
            {
                "_id": "trigger_1",
                "condition": "user asks about pricing",
                "action": "Suggest enterprise plan",
                "one_shot": True,
                "trigger_count": 0,
                "created_at": datetime.now(timezone.utc),
                "metadata": {},
            }
        ]

        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=mock_triggers)
        mock_collection.find = MagicMock(return_value=mock_cursor)

        result = await prospective.get_active_triggers(user_id="user123")

        assert len(result) == 1
        assert result[0]["trigger_id"] == "trigger_1"
        assert result[0]["condition"] == "user asks about pricing"
        assert result[0]["action"] == "Suggest enterprise plan"


# ============================================================================
# Deactivate Trigger Tests
# ============================================================================


class TestDeactivateTrigger:
    """Tests for manually deactivating triggers."""

    @pytest.mark.asyncio
    async def test_deactivate_success(self, prospective, mock_collection):
        """Should deactivate trigger successfully."""
        valid_id = "507f1f77bcf86cd799439014"
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_collection.update_one = AsyncMock(return_value=mock_result)

        result = await prospective.deactivate_trigger(valid_id)
        assert result is True

        update_doc = mock_collection.update_one.call_args[0][1]
        assert update_doc["$set"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_deactivate_nonexistent(self, prospective, mock_collection):
        """Should return False for non-existent trigger."""
        valid_id = "507f1f77bcf86cd799439015"
        mock_result = MagicMock()
        mock_result.modified_count = 0
        mock_collection.update_one = AsyncMock(return_value=mock_result)

        result = await prospective.deactivate_trigger(valid_id)
        assert result is False
