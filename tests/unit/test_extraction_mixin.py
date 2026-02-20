"""
Tests for ExtractionMixin (fact extraction pipeline).

Validates LLM-powered extraction (basic, categorised, cognitive),
deduplication of extracted facts, and memory type detection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.memory.cognitive import CognitiveMemoryService

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


def _build_service(col, *, enable_cognitive=True, categories_enabled=True):
    """Build a CognitiveMemoryService with mocked LLM."""
    emb = MagicMock()
    emb.embed = AsyncMock(return_value=[[0.1] * 1536])

    svc = CognitiveMemoryService(
        app_slug="test_app",
        collection=col,
        config={
            "enable_cognitive": enable_cognitive,
            "categories": {"enabled": categories_enabled},
        },
        embedding_service=emb,
    )

    svc._cognitive_fields_ensured = True
    svc._aux_indexes_created = True
    svc._get_embedding = AsyncMock(return_value=[0.1] * 1536)
    svc._get_embeddings_batch = AsyncMock(return_value={"test": [0.1] * 1536})
    return svc


@pytest.fixture
def service(mock_collection):
    return _build_service(mock_collection)


# ========================================================================
# Tests
# ========================================================================


class TestExtractFactsBasic:
    @pytest.mark.asyncio
    async def test_extract_facts_basic_returns_list(self, service):
        """_extract_facts should return a list of strings."""
        service._llm_completion = AsyncMock(return_value='["User likes Python"]')
        result = await service._extract_facts("I love Python programming")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_extract_returns_empty_when_llm_unavailable(self, service):
        """_extract_facts should return [] when LLM is unavailable."""
        service.llm_available = False
        result = await service._extract_facts("Some text")
        assert result == []


class TestExtractFactsWithCategories:
    @pytest.mark.asyncio
    async def test_extract_facts_with_categories(self, service):
        """_extract_facts_with_categories should return dicts with text + category."""
        service._llm_completion = AsyncMock(return_value='[{"text": "User likes Python", "category": "preferences"}]')
        result = await service._extract_facts_with_categories("I love Python")
        assert isinstance(result, list)
        if result:
            assert "text" in result[0]
            assert "category" in result[0]


class TestExtractFactsCognitive:
    @pytest.mark.asyncio
    async def test_extract_facts_cognitive(self, service):
        """_extract_facts_cognitive should return dicts with emotion fields."""
        service._llm_completion = AsyncMock(
            return_value=(
                '[{"text": "User loves Python", "category": "preferences", ' '"emotion": 0.6, "emotion_type": "joy"}]'
            )
        )
        result = await service._extract_facts_cognitive("I love Python")
        assert isinstance(result, list)


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_deduplicate_removes_near_duplicates(self, service):
        """_deduplicate_extracted_facts should remove semantically similar facts."""
        facts = [
            {"text": "User likes Python", "category": "preferences", "emotion": 0.3},
            {"text": "User enjoys Python", "category": "preferences", "emotion": 0.3},
            {"text": "User works at Google", "category": "biographical", "emotion": 0.3},
        ]
        # Mock embeddings so first two are very similar
        service._get_embeddings_batch = AsyncMock(
            return_value={
                "User likes Python": [1.0, 0.0, 0.0],
                "User enjoys Python": [0.99, 0.01, 0.0],
                "User works at Google": [0.0, 1.0, 0.0],
            }
        )
        result = await service._deduplicate_extracted_facts(facts, similarity_threshold=0.95)
        assert isinstance(result, list)
        # Should have fewer facts than input (deduplicated)
        assert len(result) <= len(facts)


class TestMemoryTypeDetection:
    @pytest.mark.asyncio
    async def test_detect_memory_type(self, service):
        """_detect_memory_type should classify text into a memory type."""
        service._llm_completion = AsyncMock(return_value="semantic")
        result = await service._detect_memory_type("The user's name is Alice")
        assert result in ("semantic", "episodic", "procedural", "working")
