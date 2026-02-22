"""
Unit tests for index type mismatch detection, FAILED auto-recovery,
AutoIndexManager key pattern checks, and query_counts eviction.

Covers:
- _handle_existing_index type mismatch (drop + recreate)
- _handle_existing_index FAILED auto-recovery
- _create_index_safely key pattern overlap detection
- _create_index_safely no deprecated background option
- _query_counts eviction on creation and size cap
- IndexOptionsConflict (code 85) handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import OperationFailure

from mdb_engine.database.scoped_wrapper import AsyncAtlasIndexManager


class TestHandleExistingIndexTypeMismatch:
    """Tests for type mismatch detection in _handle_existing_index."""

    @pytest.mark.asyncio
    async def test_type_mismatch_drops_and_recreates(self):
        """When existing index type differs from expected, drop and recreate."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            with (
                patch.object(AsyncAtlasIndexManager, "drop_search_index", new_callable=AsyncMock) as mock_drop,
                patch.object(AsyncAtlasIndexManager, "_create_new_search_index", new_callable=AsyncMock) as mock_create,
            ):
                manager = AsyncAtlasIndexManager(mock_coll)

                existing = {"type": "search", "latestDefinition": {"mappings": {"dynamic": True}}}
                definition = {"fields": [{"type": "vector", "path": "embedding"}]}

                result = await manager._handle_existing_index(  # noqa: SLF001
                    existing, definition, "vectorSearch", "my_index"
                )

                assert result is False
                mock_drop.assert_awaited_once_with(name="my_index", wait_for_drop=True)
                mock_create.assert_awaited_once_with("my_index", definition, "vectorSearch")

    @pytest.mark.asyncio
    async def test_matching_type_no_drop(self):
        """When types match, proceed normally (no drop)."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            with patch.object(AsyncAtlasIndexManager, "update_search_index", new_callable=AsyncMock):
                manager = AsyncAtlasIndexManager(mock_coll)

                existing = {
                    "type": "vectorSearch",
                    "queryable": True,
                    "latestDefinition": {"fields": [{"type": "vector", "path": "embedding"}]},
                }
                definition = {"fields": [{"type": "vector", "path": "embedding"}]}

                result = await manager._handle_existing_index(  # noqa: SLF001
                    existing, definition, "vectorSearch", "my_index"
                )

                assert result is True

    @pytest.mark.asyncio
    async def test_empty_type_field_no_crash(self):
        """If existing index has no 'type' field, skip the type check."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            manager = AsyncAtlasIndexManager(mock_coll)

            existing = {
                "queryable": True,
                "latestDefinition": {"mappings": {"dynamic": True}},
            }
            definition = {"mappings": {"dynamic": True}}

            result = await manager._handle_existing_index(  # noqa: SLF001
                existing, definition, "search", "my_index"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_failed_index_auto_recovery(self):
        """FAILED index triggers drop + recreate via _attempt_failed_index_recovery."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            with (
                patch.object(AsyncAtlasIndexManager, "drop_search_index", new_callable=AsyncMock) as mock_drop,
                patch.object(AsyncAtlasIndexManager, "_create_new_search_index", new_callable=AsyncMock) as mock_create,
            ):
                manager = AsyncAtlasIndexManager(mock_coll)

                existing = {
                    "type": "vectorSearch",
                    "status": "FAILED",
                    "queryable": False,
                    "latestDefinition": {"fields": [{"type": "vector", "path": "embedding"}]},
                }
                definition = {"fields": [{"type": "vector", "path": "embedding"}]}

                result = await manager._handle_existing_index(  # noqa: SLF001
                    existing, definition, "vectorSearch", "my_index"
                )

                assert result is False
                mock_drop.assert_awaited_once_with(name="my_index", wait_for_drop=True)
                mock_create.assert_awaited_once_with("my_index", definition, "vectorSearch")

    @pytest.mark.asyncio
    async def test_failed_recovery_fallback_on_error(self):
        """If auto-recovery itself fails, return False without propagating."""
        mock_coll = MagicMock()

        with patch("mdb_engine.database.scoped_wrapper.AsyncIOMotorCollection", MagicMock):
            with patch.object(
                AsyncAtlasIndexManager,
                "drop_search_index",
                new_callable=AsyncMock,
                side_effect=OperationFailure("drop failed"),
            ):
                manager = AsyncAtlasIndexManager(mock_coll)

                existing = {
                    "type": "vectorSearch",
                    "status": "FAILED",
                    "queryable": False,
                    "latestDefinition": {"fields": []},
                }

                result = await manager._handle_existing_index(  # noqa: SLF001
                    existing, {"fields": []}, "vectorSearch", "my_index"
                )

                assert result is False


class TestCreateIndexSafelyKeyPattern:
    """Tests for _create_index_safely key pattern overlap detection."""

    @pytest.fixture
    def auto_index_manager(self):
        from mdb_engine.database.scoped_wrapper import AutoIndexManager

        mock_collection = MagicMock()
        mock_collection.name = "test_collection"
        mock_index_manager = AsyncMock()
        return AutoIndexManager(mock_collection, mock_index_manager)

    @pytest.mark.asyncio
    async def test_skips_when_same_key_pattern_exists(self, auto_index_manager):
        """Skip creation if an index on the same keys exists under a different name."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(
            return_value=[
                {"name": "user_id_1", "key": {"user_id": 1}},
            ]
        )

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        auto_index_manager._index_manager.create_index.assert_not_called()
        assert auto_index_manager._creation_cache.get("auto_user_id_asc") is True

    @pytest.mark.asyncio
    async def test_skips_when_exact_name_exists(self, auto_index_manager):
        """Skip creation if index with exact name already exists."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(
            return_value=[
                {"name": "auto_user_id_asc", "key": {"user_id": 1}},
            ]
        )

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        auto_index_manager._index_manager.create_index.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_when_no_overlap(self, auto_index_manager):
        """Create index when no existing index covers the same keys. No deprecated 'background' option."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(
            return_value=[
                {"name": "status_1", "key": {"status": 1}},
            ]
        )
        auto_index_manager._index_manager.create_index = AsyncMock()

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        auto_index_manager._index_manager.create_index.assert_called_once()
        call_kwargs = auto_index_manager._index_manager.create_index.call_args
        # Verify deprecated 'background' option is not passed
        assert "background" not in call_kwargs.kwargs

    @pytest.mark.asyncio
    async def test_catches_index_options_conflict(self, auto_index_manager):
        """IndexOptionsConflict (code 85) is treated as success."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(return_value=[])
        auto_index_manager._index_manager.create_index = AsyncMock(
            side_effect=OperationFailure("IndexOptionsConflict", code=85)
        )

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        assert auto_index_manager._creation_cache.get("auto_user_id_asc") is True

    @pytest.mark.asyncio
    async def test_query_counts_evicted_on_creation(self, auto_index_manager):
        """After successful index creation, the pattern is removed from _query_counts."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(return_value=[])
        auto_index_manager._index_manager.create_index = AsyncMock()
        auto_index_manager._query_counts["auto_user_id_asc"] = 5  # noqa: SLF001

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        assert "auto_user_id_asc" not in auto_index_manager._query_counts

    @pytest.mark.asyncio
    async def test_query_counts_evicted_on_key_pattern_hit(self, auto_index_manager):
        """After cache hit via key pattern, the pattern is removed from _query_counts."""
        auto_index_manager._index_manager.list_indexes = AsyncMock(
            return_value=[{"name": "user_id_1", "key": {"user_id": 1}}]
        )
        auto_index_manager._query_counts["auto_user_id_asc"] = 5  # noqa: SLF001

        await auto_index_manager._create_index_safely(  # noqa: SLF001
            "auto_user_id_asc", [("user_id", 1)]
        )

        assert "auto_user_id_asc" not in auto_index_manager._query_counts


class TestQueryCountsEviction:
    """Tests for AutoIndexManager query count bounding."""

    @pytest.mark.asyncio
    async def test_query_counts_capped_at_max(self):
        """_query_counts is capped at MAX_AUTO_INDEX_PATTERNS."""
        from mdb_engine.constants import MAX_AUTO_INDEX_PATTERNS
        from mdb_engine.database.scoped_wrapper import AutoIndexManager

        mock_collection = MagicMock()
        mock_collection.name = "test_collection"
        mock_index_manager = AsyncMock()
        mgr = AutoIndexManager(mock_collection, mock_index_manager)

        # Fill _query_counts beyond the cap
        for i in range(MAX_AUTO_INDEX_PATTERNS + 100):
            mgr._query_counts[f"pattern_{i}"] = i  # noqa: SLF001

        assert len(mgr._query_counts) > MAX_AUTO_INDEX_PATTERNS

        # Call ensure_index_for_query to trigger eviction
        await mgr.ensure_index_for_query(filter={"new_field": "value"}, hint_threshold=999)

        assert len(mgr._query_counts) <= MAX_AUTO_INDEX_PATTERNS + 1
