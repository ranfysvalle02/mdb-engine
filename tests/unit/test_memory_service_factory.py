"""
Tests for get_memory_service() factory function.

Validates provider resolution, collection requirement, and strategy passthrough.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.memory.base import MemoryServiceError
from mdb_engine.memory.service import get_memory_service

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def mock_collection():
    col = MagicMock()
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="id1"))
    col.find_one = AsyncMock(return_value=None)
    col.create_index = AsyncMock()

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)

    agg = MagicMock()
    agg.to_list = AsyncMock(return_value=[])
    col.aggregate = MagicMock(return_value=agg)

    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    col.update_many = AsyncMock()
    col.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    col.delete_many = AsyncMock()
    col.count_documents = AsyncMock(return_value=0)
    return col


@pytest.fixture
def mock_embedding():
    emb = MagicMock()
    emb.embed = AsyncMock(return_value=[[0.1] * 1536])
    return emb


# ========================================================================
# Tests
# ========================================================================


class TestGetMemoryService:
    def test_creates_cognitive_service_by_default(self, mock_collection, mock_embedding):
        """Factory should return a CognitiveMemoryService when provider is 'cognitive'."""
        svc = get_memory_service(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
        )
        assert svc is not None
        assert hasattr(svc, "add")
        assert hasattr(svc, "search")

    def test_raises_for_unknown_provider(self, mock_collection, mock_embedding):
        """Factory should raise for unsupported providers."""
        with pytest.raises((ValueError, MemoryServiceError)):
            get_memory_service(
                app_slug="test",
                provider="unknown_provider",
                collection=mock_collection,
                embedding_service=mock_embedding,
            )

    def test_collection_required(self, mock_embedding):
        """Factory should raise when no collection is provided."""
        with pytest.raises((ValueError, TypeError)):
            get_memory_service(
                app_slug="test",
                collection=None,
                embedding_service=mock_embedding,
            )

    def test_passes_strategies_to_builder(self, mock_collection, mock_embedding):
        """Factory should forward strategy kwargs to the builder."""
        from mdb_engine.memory.strategies import NoDecay

        svc = get_memory_service(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
            decay_strategy=NoDecay(),
        )
        assert svc is not None
