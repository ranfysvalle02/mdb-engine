"""
Unit tests for the ReflectionService.

Tests cover:
- Initialization and configuration
- Reflection trigger logic
- Memory consolidation flow
- Pruning behavior
- Stats and reflection retrieval
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.memory.reflection import (
    ReflectionService,
    create_reflection_service,
)


class TestReflectionServiceInit:
    """Test ReflectionService initialization."""

    def test_init_default_config(self):
        """Test initialization with default configuration."""
        mock_memories = MagicMock()
        mock_reflections = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
        )

        assert service.enabled is False  # Disabled by default
        assert service.interval_hours == 24
        assert service.message_threshold == 50
        assert service.min_salience_to_keep == 0.4
        assert service.store_reflections is True

    def test_init_enabled(self):
        """Test initialization with service enabled."""
        mock_memories = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={"enabled": True},
        )

        assert service.enabled is True

    def test_init_custom_config(self):
        """Test initialization with custom configuration."""
        mock_memories = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={
                "enabled": True,
                "interval_hours": 12,
                "message_threshold": 25,
                "min_salience_to_keep": 0.3,
                "store_reflections": False,
            },
        )

        assert service.interval_hours == 12
        assert service.message_threshold == 25
        assert service.min_salience_to_keep == 0.3
        assert service.store_reflections is False

    def test_factory_function(self):
        """Test factory function creates service correctly."""
        mock_memories = MagicMock()

        service = create_reflection_service(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={"enabled": True},
        )

        assert isinstance(service, ReflectionService)
        assert service.enabled is True


class TestShouldReflect:
    """Test reflection trigger logic."""

    @pytest.mark.asyncio
    async def test_should_reflect_disabled(self):
        """Test should_reflect returns False when disabled."""
        mock_memories = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={"enabled": False},
        )

        should, reason = await service.should_reflect("user123")

        assert should is False
        assert "disabled" in reason.lower()

    @pytest.mark.asyncio
    async def test_should_reflect_count_threshold(self):
        """Test should_reflect triggers on count threshold."""
        mock_memories = MagicMock()
        mock_memories.count_documents = AsyncMock(return_value=60)  # > 50 threshold

        mock_reflections = MagicMock()
        mock_reflections.find_one = AsyncMock(return_value=None)  # No previous reflection

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True, "message_threshold": 50},
        )

        should, reason = await service.should_reflect("user123")

        assert should is True
        assert "exceeds threshold" in reason.lower() or "accumulated" in reason.lower()

    @pytest.mark.asyncio
    async def test_should_reflect_time_based(self):
        """Test should_reflect triggers on time interval."""
        mock_memories = MagicMock()
        mock_memories.count_documents = AsyncMock(return_value=10)  # Below threshold

        mock_reflections = MagicMock()
        # Last reflection was 25 hours ago (> 24h default)
        last_time = datetime.now(timezone.utc) - timedelta(hours=25)
        mock_reflections.find_one = AsyncMock(return_value={"created_at": last_time})

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True, "interval_hours": 24},
        )

        should, reason = await service.should_reflect("user123")

        assert should is True
        assert "time" in reason.lower() or "hours" in reason.lower()

    @pytest.mark.asyncio
    async def test_should_reflect_recent_reflection(self):
        """Test should_reflect returns False if recent reflection exists."""
        mock_memories = MagicMock()
        mock_memories.count_documents = AsyncMock(return_value=10)  # Below threshold

        mock_reflections = MagicMock()
        # Last reflection was 1 hour ago (< 24h)
        last_time = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_reflections.find_one = AsyncMock(return_value={"created_at": last_time})

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True},
        )

        should, reason = await service.should_reflect("user123")

        assert should is False
        assert "ago" in reason.lower()


class TestRunReflection:
    """Test reflection execution."""

    @pytest.mark.asyncio
    async def test_run_reflection_disabled(self):
        """Test run_reflection returns early when disabled."""
        mock_memories = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={"enabled": False},
        )

        result = await service.run_reflection("user123")

        assert result["success"] is False
        assert "disabled" in result["reason"].lower()
        assert result["memories_processed"] == 0

    @pytest.mark.asyncio
    async def test_run_reflection_force(self):
        """Test run_reflection with force flag."""
        mock_memories = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])  # No memories
        mock_memories.find = MagicMock(return_value=mock_cursor)
        mock_memories.count_documents = AsyncMock(return_value=0)

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={"enabled": False},  # Disabled but force=True
        )

        result = await service.run_reflection("user123", force=True)

        # Should attempt even though disabled
        assert result["success"] is True
        assert result["memories_processed"] == 0

    @pytest.mark.asyncio
    async def test_run_reflection_no_memories(self):
        """Test run_reflection with no memories to process."""
        mock_memories = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_memories.find = MagicMock(return_value=mock_cursor)
        mock_memories.count_documents = AsyncMock(return_value=0)

        mock_reflections = MagicMock()
        mock_reflections.find_one = AsyncMock(return_value=None)

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True},
        )

        result = await service.run_reflection("user123", force=True)

        assert result["success"] is True
        assert "no memories" in result["reason"].lower() or "period" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_run_reflection_with_memories(self):
        """Test run_reflection processes memories."""
        # Setup mock memories
        mock_memories = MagicMock()
        test_memories = [
            {
                "_id": "mem1",
                "text": "User likes Python",
                "importance": 0.8,
                "category": "preferences",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": "mem2",
                "text": "User works at Tech Corp",
                "importance": 0.7,
                "category": "biographical",
                "created_at": datetime.now(timezone.utc),
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=test_memories)
        mock_memories.find = MagicMock(return_value=mock_cursor)
        mock_memories.count_documents = AsyncMock(return_value=60)

        mock_reflections = MagicMock()
        mock_reflections.find_one = AsyncMock(return_value=None)
        mock_reflections.insert_one = AsyncMock(return_value=MagicMock(inserted_id="ref123"))

        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="The user is a Python developer at Tech Corp.")

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={
                "enabled": True,
                "min_salience_to_keep": 0,  # Don't prune
            },
            llm_service=mock_llm_service,
        )

        result = await service.run_reflection("user123", force=True)

        assert result["success"] is True
        assert result["memories_processed"] == 2
        assert "Python developer" in result["reflection_content"]


class TestPruning:
    """Test memory pruning functionality (Perfect Recall: no pruning)."""

    def test_pruning_removed_perfect_recall(self):
        """Test that pruning method was removed (Perfect Recall preserves all memories)."""
        mock_memories = MagicMock()

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            config={
                "enabled": True,
                "min_salience_to_keep": 0.5,
            },
        )

        # _prune_low_salience_memories was removed in favor of Perfect Recall
        assert not hasattr(service, "_prune_low_salience_memories")

    @pytest.mark.asyncio
    async def test_run_reflection_preserves_all_memories(self):
        """Test that reflection does not delete any memories (Perfect Recall)."""
        mock_memories = MagicMock()
        test_memories = [
            {
                "_id": "mem1",
                "text": "Low importance memory",
                "importance": 0.2,
                "category": "preferences",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": "mem2",
                "text": "High importance memory",
                "importance": 0.9,
                "category": "biographical",
                "created_at": datetime.now(timezone.utc),
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=test_memories)
        mock_memories.find = MagicMock(return_value=mock_cursor)
        mock_memories.count_documents = AsyncMock(return_value=60)
        mock_memories.delete_many = AsyncMock()  # Should NOT be called

        mock_reflections = MagicMock()
        mock_reflections.find_one = AsyncMock(return_value=None)
        mock_reflections.insert_one = AsyncMock(return_value=MagicMock(inserted_id="ref123"))

        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="Summary of all memories.")

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True, "min_salience_to_keep": 0.5},
            llm_service=mock_llm_service,
        )

        result = await service.run_reflection("user123", force=True)

        assert result["success"] is True
        assert result["memories_processed"] == 2
        # Perfect Recall: no memories should be deleted
        mock_memories.delete_many.assert_not_called()


class TestGetRecentReflections:
    """Test retrieving recent reflections."""

    @pytest.mark.asyncio
    async def test_get_recent_reflections(self):
        """Test getting recent reflections."""
        mock_reflections = MagicMock()
        reflections_data = [
            {
                "_id": "ref1",
                "content": "Summary 1",
                "type": "periodic_summary",
                "memories_consolidated": 10,
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": "ref2",
                "content": "Summary 2",
                "type": "periodic_summary",
                "memories_consolidated": 15,
                "created_at": datetime.now(timezone.utc) - timedelta(days=1),
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=reflections_data)
        mock_reflections.find = MagicMock(return_value=mock_cursor)

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=MagicMock(),
            reflections_collection=mock_reflections,
            config={"enabled": True},
        )

        reflections = await service.get_recent_reflections("user123", limit=5)

        assert len(reflections) == 2
        assert reflections[0]["content"] == "Summary 1"

    @pytest.mark.asyncio
    async def test_get_recent_reflections_no_collection(self):
        """Test getting reflections when no reflections collection available."""
        mock_memories = MagicMock()
        # When reflections_collection=None, the init falls back to
        # memories_collection.database.get_collection(). Force it to None
        # after construction to test the guard clause.
        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=None,
            config={"enabled": True},
        )
        # Override after init to test the None guard in get_recent_reflections
        service.reflections_collection = None

        reflections = await service.get_recent_reflections("user123")

        assert reflections == []


class TestStats:
    """Test statistics retrieval."""

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test getting service statistics."""
        mock_memories = MagicMock()
        mock_memories.count_documents = AsyncMock(return_value=10)

        mock_reflections = MagicMock()
        mock_reflections.count_documents = AsyncMock(return_value=5)
        last_time = datetime.now(timezone.utc) - timedelta(hours=12)
        mock_reflections.find_one = AsyncMock(
            return_value={
                "created_at": last_time,
                "memories_consolidated": 20,
            }
        )

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True},
        )

        stats = await service.get_stats("user123")

        assert stats["enabled"] is True
        assert stats["total_reflections"] == 5
        assert stats["last_memories_consolidated"] == 20

    @pytest.mark.asyncio
    async def test_get_stats_no_reflections(self):
        """Test stats when no reflections exist."""
        mock_memories = MagicMock()
        mock_memories.count_documents = AsyncMock(return_value=10)

        mock_reflections = MagicMock()
        mock_reflections.count_documents = AsyncMock(return_value=0)
        mock_reflections.find_one = AsyncMock(return_value=None)

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=mock_memories,
            reflections_collection=mock_reflections,
            config={"enabled": True},
        )

        stats = await service.get_stats("user123")

        assert stats["total_reflections"] == 0
        assert "last_reflection" not in stats


class TestGenerateReflection:
    """Test reflection generation."""

    @pytest.mark.asyncio
    async def test_generate_reflection_no_llm(self):
        """Test reflection generation fails gracefully without LLM."""
        service = ReflectionService(
            app_slug="test_app",
            memories_collection=MagicMock(),
            config={"enabled": True},
            llm_service=None,  # No LLM service
        )

        result = await service._generate_reflection([], "user123")  # noqa: SLF001

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_reflection_success(self):
        """Test successful reflection generation."""
        mock_llm_service = MagicMock()
        mock_llm_service.chat_completion = AsyncMock(return_value="User summary here.")

        service = ReflectionService(
            app_slug="test_app",
            memories_collection=MagicMock(),
            config={"enabled": True},
            llm_service=mock_llm_service,
        )

        memories = [
            {"text": "User likes coding", "importance": 0.8, "category": "preferences"},
        ]

        result = await service._generate_reflection(memories, "user123")  # noqa: SLF001

        assert result == "User summary here."
        assert mock_llm_service.chat_completion.called
