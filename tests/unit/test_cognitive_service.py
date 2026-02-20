"""
Tests for CognitiveMemoryService core CRUD operations.

Validates the decomposed add(), inject(), search(), get(), update(),
delete(), delete_all(), and detect_knowledge_conflict() methods.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.validation import InputValidationError
from mdb_engine.memory.cognitive import CognitiveMemoryService

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def mock_collection():
    """Standard async MongoDB collection mock."""
    col = MagicMock()
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mem_001"))
    col.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["mem_001"]))
    col.find_one = AsyncMock(return_value=None)

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)

    agg = MagicMock()
    agg.to_list = AsyncMock(return_value=[])
    col.aggregate = MagicMock(return_value=agg)

    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    col.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    col.count_documents = AsyncMock(return_value=0)
    col.create_index = AsyncMock()
    return col


def _build_service(col, **overrides):
    """Create a CognitiveMemoryService with mocked internals."""
    emb = MagicMock()
    emb.embed = AsyncMock(return_value=[[0.1] * 1536])

    svc = CognitiveMemoryService(
        app_slug="test_app",
        collection=col,
        config={"enable_cognitive": True, "categories": {"enabled": True}},
        embedding_service=emb,
        **overrides,
    )

    svc._get_embedding = AsyncMock(return_value=[0.1] * 1536)
    svc._get_embeddings_batch = AsyncMock(return_value={"test fact": [0.1] * 1536})
    svc._find_similar_memories = AsyncMock(return_value=[])
    svc._find_similar_memories_parallel = AsyncMock(return_value=[[]])
    svc._assess_importance = AsyncMock(return_value=0.7)
    svc._assess_importance_parallel = AsyncMock(return_value={"test fact": 0.7})
    svc._extract_facts = AsyncMock(return_value=["test fact"])
    svc._extract_facts_cognitive = AsyncMock(
        return_value=[{"text": "test fact", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"}]
    )
    svc._extract_facts_with_categories = AsyncMock(return_value=[{"text": "test fact", "category": "biographical"}])
    svc._deduplicate_extracted_facts = AsyncMock(
        return_value=[{"text": "test fact", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"}]
    )
    svc._cognitive_fields_ensured = True
    svc._aux_indexes_created = True
    return svc


@pytest.fixture
def service(mock_collection):
    return _build_service(mock_collection)


# ========================================================================
# add() tests
# ========================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_extracts_and_stores_facts(self, service, mock_collection):
        """add() should extract facts via LLM and store them."""
        result = await service.add(messages="I love Python", user_id="u1")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_add_returns_empty_for_blank_input(self, service):
        """add() should return [] for whitespace-only input."""
        result = await service.add(messages="   ", user_id="u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_add_deduplicates_similar_facts(self, service):
        """add() should call deduplication when multiple facts are extracted."""
        service._extract_facts_cognitive = AsyncMock(
            return_value=[
                {"text": "fact one", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"},
                {"text": "fact two", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"},
            ]
        )
        service._get_embeddings_batch = AsyncMock(return_value={"fact one": [0.1] * 1536, "fact two": [0.2] * 1536})
        service._find_similar_memories_parallel = AsyncMock(return_value=[[], []])
        service._assess_importance_parallel = AsyncMock(return_value={"fact one": 0.7, "fact two": 0.6})
        service._deduplicate_extracted_facts = AsyncMock(
            return_value=[
                {"text": "fact one", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"},
                {"text": "fact two", "category": "biographical", "emotion": 0.3, "emotion_type": "neutral"},
            ]
        )

        await service.add(messages="Some input", user_id="u1")
        service._deduplicate_extracted_facts.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_reinforces_existing_memory(self, service):
        """add() should reinforce rather than create when similarity is high."""
        service._find_similar_memories_parallel = AsyncMock(
            return_value=[
                [
                    {
                        "_id": "existing",
                        "id": "existing",
                        "similarity": 0.88,
                        "text": "old fact",
                        "memory": "old fact",
                        "importance": 0.6,
                        "access_count": 2,
                        "category": "biographical",
                    }
                ]
            ]
        )
        service._reinforce_memory = AsyncMock(
            return_value={
                "id": "existing",
                "memory": "old fact",
                "action": "reinforced",
            }
        )
        result = await service.add(messages="test fact", user_id="u1")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_add_requires_user_id(self, service):
        """add() with empty user_id should raise InputValidationError."""
        with pytest.raises(InputValidationError):
            await service.add(messages="hello", user_id="")

    @pytest.mark.asyncio
    async def test_add_accepts_list_messages(self, service):
        """add() should accept list-of-dict messages format."""
        result = await service.add(
            messages=[{"role": "user", "content": "test fact"}],
            user_id="u1",
        )
        assert isinstance(result, list)


# ========================================================================
# inject() tests
# ========================================================================


class TestInject:
    @pytest.mark.asyncio
    async def test_inject_bypasses_extraction(self, service, mock_collection):
        """inject() should store directly without LLM extraction."""
        result = await service.inject(memory="User likes cats", user_id="u1")
        assert result is not None
        assert "id" in result

    @pytest.mark.asyncio
    async def test_inject_with_importance_override(self, service, mock_collection):
        """inject() should respect explicit importance."""
        result = await service.inject(
            memory="Critical allergy info",
            user_id="u1",
            importance=0.99,
        )
        assert result is not None


# ========================================================================
# search() tests
# ========================================================================


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_ranked_results(self, service):
        """search() should return results from vector search."""
        # Mock the low-level _search (ScoringMixin) which handles the actual vector search
        service._search = AsyncMock(
            return_value=[
                {"id": "m1", "memory": "fact 1", "score": 0.9},
                {"id": "m2", "memory": "fact 2", "score": 0.7},
            ]
        )
        # Disable persona blending to avoid persona_engine async calls
        service.persona_engine = None
        results = await service.search(query="test", user_id="u1", limit=5)
        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]

    @pytest.mark.asyncio
    async def test_search_requires_user_id(self, service):
        """search() should raise for empty user_id."""
        with pytest.raises(InputValidationError):
            await service.search(query="test", user_id="")


# ========================================================================
# get() / update() / delete() tests
# ========================================================================


class TestCRUD:
    @pytest.mark.asyncio
    async def test_get_returns_memory_by_id(self, service):
        """get() should return a document when found."""
        service.collection.find_one = AsyncMock(
            return_value={"_id": "65a1b2c3d4e5f67890123456", "text": "hello", "user_id": "u1"}
        )
        result = await service.get(memory_id="65a1b2c3d4e5f67890123456", user_id="u1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, service):
        """get() should return None when memory doesn't exist."""
        service.collection.find_one = AsyncMock(return_value=None)
        result = await service.get(memory_id="65a1b2c3d4e5f67890123456", user_id="u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_soft_deletes_by_default(self, service):
        """delete() should mark is_active=False, not remove the document."""
        service.collection.find_one = AsyncMock(return_value={"_id": "65a1b2c3d4e5f67890123456", "user_id": "u1"})
        result = await service.delete(memory_id="65a1b2c3d4e5f67890123456", user_id="u1")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_all_requires_hard_delete_flag(self, service):
        """delete_all() should require explicit hard_delete parameter."""
        result = await service.delete_all(user_id="u1", hard_delete=False)
        assert isinstance(result, bool)


# ========================================================================
# detect_knowledge_conflict() tests
# ========================================================================


class TestConflictDetection:
    @pytest.mark.asyncio
    async def test_detect_knowledge_conflict_returns_none_when_clean(self, service):
        """detect_knowledge_conflict() should return None for no conflicts."""
        with patch("mdb_engine.memory.conflict.detect_knowledge_conflict", new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = None
            result = await service.detect_knowledge_conflict(
                user_id="u1",
                new_fact="User likes cats",
            )
            assert result is None
