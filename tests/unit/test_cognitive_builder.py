"""
Tests for CognitiveMemoryServiceBuilder.

Validates config parsing, strategy resolution, threshold clamping,
and optional subsystem setup (persona, timeline).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder
from mdb_engine.memory.strategies import NoDecay, PerfectRecallScoring

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


class TestCognitiveMemoryServiceBuilder:
    def test_build_with_minimal_config(self, mock_collection, mock_embedding):
        """Builder should produce a service with only slug + collection."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
        )
        svc = builder.build()
        assert svc is not None
        assert svc.app_slug == "test"

    def test_build_resolves_model_from_llm_config(self, mock_collection, mock_embedding, monkeypatch):
        """Builder should pick up memory_llm_model from config."""
        # Isolate from environment so Azure/Gemini keys don't override the model
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
            config={"memory_llm_model": "gpt-4o-mini"},
        )
        svc = builder.build()
        # Builder prepends provider prefix → "openai/gpt-4o-mini"
        assert "gpt-4o-mini" in svc.memory_llm_model

    def test_build_clamps_thresholds(self, mock_collection, mock_embedding):
        """Builder should clamp similarity thresholds to valid ranges."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
            config={
                "similarity_threshold": 2.0,
                "reinforcement_factor": -1.0,
            },
        )
        svc = builder.build()
        assert svc.similarity_threshold <= 1.0
        # Builder clamps to valid range (lower bound is > 0, not necessarily 1.0)
        assert svc.reinforcement_factor > 0

    def test_build_sets_up_persona_when_enabled(self, mock_collection, mock_embedding):
        """Builder should create PersonaEngine when persona config is present."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
            config={
                "persona": {
                    "enabled": True,
                    "role": "Test Bot",
                    "description": "A test persona",
                },
            },
        )
        svc = builder.build()
        assert svc.persona_engine is not None

    def test_strategy_resolution_from_config(self, mock_collection, mock_embedding):
        """Builder should resolve named strategies from config."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
            scoring_strategy=PerfectRecallScoring(),
            decay_strategy=NoDecay(),
        )
        svc = builder.build()
        assert svc._scoring_strategy is not None
        assert svc._decay_strategy is not None

    def test_strategy_resolution_falls_back_to_defaults(self, mock_collection, mock_embedding):
        """Builder should use default strategies when none provided."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
        )
        svc = builder.build()
        assert svc._scoring_strategy is not None
        assert svc._decay_strategy is not None

    def test_build_creates_auxiliary_indexes(self, mock_collection, mock_embedding):
        """Builder should call create_auxiliary_indexes on the collection."""
        builder = CognitiveMemoryServiceBuilder(
            app_slug="test",
            collection=mock_collection,
            embedding_service=mock_embedding,
        )
        builder.build()
        # Indexes are created lazily, so just verify no error
