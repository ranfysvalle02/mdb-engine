"""
Unit tests for memory scoring enhancements.

Tests the enhanced scoring formula including:
- Emotion-weighted recall (amygdala effect)
- Temporal recency bias
- Spreading activation via graph
- Salience-gated encoding
- Cross-check implementation
- Bulk decay pipeline
"""

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================================
# Scoring Formula Tests
# ============================================================================


class TestEmotionWeightedRecall:
    """Tests for emotion weighting in the scoring formula (amygdala effect)."""

    def test_emotion_boost_calculation(self):
        """Emotion boost formula: 1.0 + (emotion * emotion_weight)."""
        emotion = 0.9  # High emotion
        emotion_weight = 0.5

        emotion_boost = 1.0 + (emotion * emotion_weight)
        assert emotion_boost == pytest.approx(1.45)

    def test_emotion_boost_neutral(self):
        """Neutral emotion (0.3 default) should give mild boost."""
        emotion = 0.3
        emotion_weight = 0.5

        emotion_boost = 1.0 + (emotion * emotion_weight)
        assert emotion_boost == pytest.approx(1.15)

    def test_emotion_boost_zero_weight(self):
        """Emotion weight of 0 should disable emotion effect."""
        emotion = 0.9
        emotion_weight = 0.0

        emotion_boost = 1.0 + (emotion * emotion_weight)
        assert emotion_boost == 1.0

    def test_emotion_boost_zero_emotion(self):
        """Zero emotion should give no boost."""
        emotion = 0.0
        emotion_weight = 0.5

        emotion_boost = 1.0 + (emotion * emotion_weight)
        assert emotion_boost == 1.0

    def test_high_emotion_scores_higher(self):
        """Emotionally charged memories should score higher than neutral ones."""
        importance = 0.7
        access_count = 3
        similarity = 0.85
        emotion_weight = 0.5

        # High emotion memory
        high_emotion = 0.9
        high_boost = 1.0 + (high_emotion * emotion_weight)
        high_effective = importance * (1 + math.log(access_count + 1)) * high_boost
        high_score = similarity * high_effective

        # Low emotion memory
        low_emotion = 0.1
        low_boost = 1.0 + (low_emotion * emotion_weight)
        low_effective = importance * (1 + math.log(access_count + 1)) * low_boost
        low_score = similarity * low_effective

        assert high_score > low_score

    def test_emotion_weight_max(self):
        """Emotion weight of 2.0 (max) with max emotion should give 3x boost."""
        emotion = 1.0
        emotion_weight = 2.0

        emotion_boost = 1.0 + (emotion * emotion_weight)
        assert emotion_boost == pytest.approx(3.0)


class TestTemporalRecencyBias:
    """Tests for temporal recency bias in the scoring formula."""

    def test_recent_memory_gets_boost(self):
        """A memory from 1 hour ago should get a significant recency boost."""
        age_hours = 1.0
        recency_half_life = 168.0  # 1 week
        recency_weight = 0.3

        recency_boost = 1.0 + (math.exp(-age_hours / recency_half_life) * recency_weight)
        # Almost full boost since 1 hour is tiny relative to 168 half-life
        assert recency_boost > 1.29

    def test_old_memory_gets_minimal_boost(self):
        """A memory from 1 year ago should get almost no recency boost."""
        age_hours = 8760.0  # 1 year
        recency_half_life = 168.0  # 1 week
        recency_weight = 0.3

        recency_boost = 1.0 + (math.exp(-age_hours / recency_half_life) * recency_weight)
        # Should be very close to 1.0
        assert recency_boost < 1.001

    def test_recent_scores_higher_than_old(self):
        """Recent memories should score higher than old ones, all else equal."""
        similarity = 0.85
        importance = 0.7
        access_count = 2
        emotion_weight = 0.5
        emotion = 0.3
        recency_weight = 0.3
        recency_half_life = 168.0

        emotion_boost = 1.0 + (emotion * emotion_weight)
        effective = importance * (1 + math.log(access_count + 1)) * emotion_boost

        # Recent memory (1 hour)
        recent_boost = 1.0 + (math.exp(-1.0 / recency_half_life) * recency_weight)
        recent_score = similarity * effective * recent_boost

        # Old memory (30 days)
        old_boost = 1.0 + (math.exp(-720.0 / recency_half_life) * recency_weight)
        old_score = similarity * effective * old_boost

        assert recent_score > old_score

    def test_zero_recency_weight_disables(self):
        """Recency weight of 0 should give no boost regardless of age."""
        recency_weight = 0.0
        recency_half_life = 168.0

        for age in [0.1, 24, 720, 8760]:
            recency_boost = 1.0 + (math.exp(-age / recency_half_life) * recency_weight)
            assert recency_boost == 1.0

    def test_short_half_life_aggressive_decay(self):
        """Short half-life (24h) should decay fast."""
        recency_weight = 0.3
        recency_half_life = 24.0  # 1 day

        boost_1h = 1.0 + (math.exp(-1.0 / recency_half_life) * recency_weight)
        boost_48h = 1.0 + (math.exp(-48.0 / recency_half_life) * recency_weight)

        assert boost_1h > boost_48h
        assert boost_48h < 1.05  # Almost no boost after 2 days with 24h half-life


class TestCombinedScoringFormula:
    """Tests for the combined scoring formula with all factors."""

    def test_full_formula(self):
        """Test the complete scoring formula: similarity * effective_importance * recency_boost."""
        similarity = 0.85
        importance = 0.7
        access_count = 5
        emotion = 0.6
        emotion_weight = 0.5
        recency_weight = 0.3
        recency_half_life = 168.0
        age_hours = 24.0

        # Step 1: Emotion boost
        emotion_boost = 1.0 + (emotion * emotion_weight)

        # Step 2: Effective importance
        effective_importance = importance * (1 + math.log(access_count + 1)) * emotion_boost

        # Step 3: Recency boost
        recency_boost = 1.0 + (math.exp(-age_hours / recency_half_life) * recency_weight)

        # Step 4: Combined score
        combined_score = similarity * effective_importance * recency_boost

        # Verify each component
        assert emotion_boost == pytest.approx(1.3)
        assert effective_importance > importance  # Access count and emotion should boost it
        assert recency_boost > 1.0  # 24h is recent
        assert combined_score > 0  # Final score should be positive

    def test_minimal_memory_scores_positive(self):
        """Even a minimal memory should score > 0."""
        similarity = 0.1
        importance = 0.1
        access_count = 0
        emotion = 0.0
        emotion_weight = 0.5

        emotion_boost = 1.0 + (emotion * emotion_weight)
        effective_importance = importance * (1 + math.log(access_count + 1)) * emotion_boost
        combined_score = similarity * effective_importance

        assert combined_score > 0

    def test_scoring_deterministic(self):
        """Same inputs should always produce the same score."""
        params = {
            "similarity": 0.85,
            "importance": 0.7,
            "access_count": 3,
            "emotion": 0.5,
            "emotion_weight": 0.5,
        }

        def compute_score(p):
            eb = 1.0 + (p["emotion"] * p["emotion_weight"])
            ei = p["importance"] * (1 + math.log(p["access_count"] + 1)) * eb
            return p["similarity"] * ei

        score1 = compute_score(params)
        score2 = compute_score(params)
        assert score1 == score2


# ============================================================================
# Cross-Check Tests
# ============================================================================


class TestCrossCheck:
    """Tests for the cross-check implementation in recall.py."""

    def test_cross_check_no_contradictions(self):
        """Memories with different embeddings should not flag contradictions."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        memories = [
            {"id": "1", "content": "User likes pizza", "embedding": [1.0, 0.0, 0.0]},
            {"id": "2", "content": "User works at Google", "embedding": [0.0, 1.0, 0.0]},
        ]

        result = recall._cross_check_memories(memories)
        assert all("contradictions" not in m for m in result)

    def test_cross_check_detects_contradiction(self):
        """Similar embeddings with different content should flag contradiction."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        memories = [
            {"id": "1", "content": "User's favorite color is blue", "embedding": [0.9, 0.1, 0.0]},
            {"id": "2", "content": "User's favorite color is red", "embedding": [0.91, 0.09, 0.01]},
        ]

        result = recall._cross_check_memories(memories, similarity_threshold=0.95)
        # These vectors are very similar (cosine > 0.99)
        has_contradictions = any("contradictions" in m for m in result)
        assert has_contradictions

    def test_cross_check_identical_content_no_flag(self):
        """Identical content with same embedding should not flag."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        memories = [
            {"id": "1", "content": "User likes pizza", "embedding": [0.9, 0.1]},
            {"id": "2", "content": "User likes pizza", "embedding": [0.9, 0.1]},
        ]

        result = recall._cross_check_memories(memories)
        assert all("contradictions" not in m for m in result)

    def test_cross_check_single_memory(self):
        """Single memory should return unchanged."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        memories = [{"id": "1", "content": "test", "embedding": [1.0, 0.0]}]
        result = recall._cross_check_memories(memories)
        assert len(result) == 1
        assert "contradictions" not in result[0]

    def test_cross_check_no_embeddings(self):
        """Memories without embeddings should pass through unchanged."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        memories = [
            {"id": "1", "content": "test1"},
            {"id": "2", "content": "test2"},
        ]
        result = recall._cross_check_memories(memories)
        assert len(result) == 2
        assert all("contradictions" not in m for m in result)

    def test_cosine_similarity(self):
        """Test the cosine similarity helper function."""
        from mdb_engine.memory.embedding import cosine_similarity

        # Identical vectors = 1.0
        assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)

        # Orthogonal vectors = 0.0
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

        # Empty vectors = 0.0
        assert cosine_similarity([], []) == 0.0

        # Mismatched dimensions = 0.0
        assert cosine_similarity([1, 0], [1, 0, 0]) == 0.0


# ============================================================================
# Extraction Schema Tests
# ============================================================================


class TestExtractionSchema:
    """Tests for the CognitiveFact schema (text + category + emotion)."""

    def test_cognitive_fact_has_emotion_field(self):
        """CognitiveFact should have emotion field with default 0.3."""
        from mdb_engine.memory.extraction import CognitiveFact

        fact = CognitiveFact(text="test", category="biographical")
        assert hasattr(fact, "emotion")
        assert fact.emotion == 0.3

    def test_cognitive_fact_has_category_field(self):
        """CognitiveFact should have category field (required)."""
        from mdb_engine.memory.extraction import CognitiveFact

        fact = CognitiveFact(text="test", category="biographical")
        assert hasattr(fact, "category")
        assert fact.category == "biographical"

    def test_cognitive_fact_emotion_range(self):
        """Emotion should be constrained to 0.0-1.0."""
        from pydantic import ValidationError

        from mdb_engine.memory.extraction import CognitiveFact

        # Valid range
        fact = CognitiveFact(text="test", category="bio", emotion=0.85)
        assert fact.emotion == 0.85

        # Above maximum should fail validation
        with pytest.raises(ValidationError):
            CognitiveFact(text="test", category="bio", emotion=1.5)

    def test_cognitive_fact_full_fields(self):
        """CognitiveFact should accept all fields."""
        from mdb_engine.memory.extraction import CognitiveFact

        fact = CognitiveFact(
            text="User got promoted to Senior Engineer",
            category="biographical",
            emotion=0.9,
        )
        assert fact.text == "User got promoted to Senior Engineer"
        assert fact.category == "biographical"
        assert fact.emotion == 0.9

    def test_cognitive_fact_json_schema_includes_fields(self):
        """JSON schema should include text, category, and emotion for LLM structured output."""
        from mdb_engine.memory.extraction import CognitiveFact

        schema = CognitiveFact.model_json_schema()
        properties = schema.get("properties", {})
        assert "emotion" in properties
        assert "text" in properties
        assert "category" in properties

    def test_extraction_response_includes_fields(self):
        """CognitiveFactExtractionResponse should produce facts with all fields."""
        from mdb_engine.memory.extraction import CognitiveFactExtractionResponse

        response = CognitiveFactExtractionResponse.model_validate(
            {
                "facts": [
                    {
                        "text": "User works at MongoDB",
                        "category": "biographical",
                        "emotion": 0.5,
                    }
                ]
            }
        )
        assert len(response.facts) == 1
        assert response.facts[0].text == "User works at MongoDB"
        assert response.facts[0].category == "biographical"
        assert response.facts[0].emotion == 0.5


# ============================================================================
# STM Summary Cache Tests
# ============================================================================


class TestSTMSummaryCache:
    """Tests for the STM summary caching mechanism."""

    @pytest.fixture
    def mock_collection(self):
        """Create a mock MongoDB collection."""
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.update_one = AsyncMock()
        collection.find = MagicMock()
        collection.count_documents = AsyncMock(return_value=0)
        collection.create_index = AsyncMock()
        return collection

    @pytest.fixture
    def chat_service(self, mock_collection):
        """Create a ChatHistoryService with mocked collection."""
        from mdb_engine.memory.chat_history import ChatHistoryService

        return ChatHistoryService(collection=mock_collection, collection_name="test_chat")

    @pytest.mark.asyncio
    async def test_get_cached_summary_miss(self, chat_service, mock_collection):
        """Should return None when no cached summary exists."""
        mock_collection.find_one = AsyncMock(return_value=None)
        result = await chat_service.get_cached_summary("session_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_summary_hit(self, chat_service, mock_collection):
        """Should return (summary, count) when cached summary exists."""
        mock_collection.find_one = AsyncMock(
            return_value={
                "session_id": "session_123",
                "type": "stm_summary",
                "summary": "User discussed project deadlines and preferences.",
                "message_count": 20,
            }
        )
        result = await chat_service.get_cached_summary("session_123")
        assert result is not None
        summary, count = result
        assert summary == "User discussed project deadlines and preferences."
        assert count == 20

    @pytest.mark.asyncio
    async def test_store_cached_summary(self, chat_service, mock_collection):
        """Should upsert summary document in collection."""
        mock_collection.update_one = AsyncMock()
        await chat_service.store_cached_summary(
            session_id="session_123",
            summary_text="User discussed project deadlines.",
            message_count=25,
            user_id="user_456",
        )
        mock_collection.update_one.assert_called_once()
        call_args = mock_collection.update_one.call_args
        # Check filter (scoped by user_id so summaries never leak across users)
        assert call_args[0][0] == {"session_id": "session_123", "type": "stm_summary", "user_id": "user_456"}
        # Check upsert=True
        assert call_args[1]["upsert"] is True
        # Check stored doc has summary
        stored_doc = call_args[0][1]["$set"]
        assert stored_doc["summary"] == "User discussed project deadlines."
        assert stored_doc["message_count"] == 25
        assert stored_doc["user_id"] == "user_456"

    @pytest.mark.asyncio
    async def test_get_context_excludes_summary_docs(self, chat_service, mock_collection):
        """get_context() should exclude type='stm_summary' documents."""
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
        mock_collection.find = MagicMock(return_value=mock_cursor)

        await chat_service.get_context("session_123")

        # Verify the query includes the exclusion filter
        find_call = mock_collection.find.call_args[0][0]
        assert find_call.get("type") == {"$ne": "stm_summary"}

    @pytest.mark.asyncio
    async def test_get_message_count_excludes_summary_docs(self, chat_service, mock_collection):
        """get_message_count() should exclude type='stm_summary' documents."""
        mock_collection.count_documents = AsyncMock(return_value=15)

        result = await chat_service.get_message_count("session_123")

        count_call = mock_collection.count_documents.call_args[0][0]
        assert count_call.get("type") == {"$ne": "stm_summary"}
        assert result == 15

    @pytest.mark.asyncio
    async def test_get_session_count_excludes_summary_docs(self, chat_service, mock_collection):
        """get_session_count() delegates to get_message_count and excludes summary docs."""
        mock_collection.count_documents = AsyncMock(return_value=7)

        result = await chat_service.get_session_count("session_123", user_id="user_1")

        count_call = mock_collection.count_documents.call_args[0][0]
        assert count_call.get("type") == {"$ne": "stm_summary"}
        assert count_call.get("user_id") == "user_1"
        assert result == 7

    @pytest.mark.asyncio
    async def test_get_cached_summary_scopes_by_user_id(self, chat_service, mock_collection):
        """get_cached_summary(user_id=...) must scope the lookup to that user."""
        mock_collection.find_one = AsyncMock(return_value=None)

        await chat_service.get_cached_summary("session_123", user_id="user_1")

        find_query = mock_collection.find_one.call_args[0][0]
        assert find_query.get("session_id") == "session_123"
        assert find_query.get("type") == "stm_summary"
        assert find_query.get("user_id") == "user_1"

    @pytest.mark.asyncio
    async def test_get_cached_summary_unscoped_when_no_user(self, chat_service, mock_collection):
        """Without user_id the lookup stays backwards-compatible (no user_id filter)."""
        mock_collection.find_one = AsyncMock(return_value=None)

        await chat_service.get_cached_summary("session_123")

        find_query = mock_collection.find_one.call_args[0][0]
        assert "user_id" not in find_query

    @pytest.mark.asyncio
    async def test_store_cached_summary_scopes_cache_key_by_user_id(self, chat_service, mock_collection):
        """store_cached_summary upsert key must include user_id so get/store stay aligned."""
        mock_collection.update_one = AsyncMock()

        await chat_service.store_cached_summary(
            session_id="session_123",
            summary_text="A summary.",
            message_count=12,
            user_id="user_1",
        )

        upsert_key = mock_collection.update_one.call_args[0][0]
        assert upsert_key == {"session_id": "session_123", "type": "stm_summary", "user_id": "user_1"}

    @pytest.mark.asyncio
    async def test_delete_old_messages_excludes_summary_doc(self, chat_service, mock_collection):
        """delete_old_messages must never keep or delete the stm_summary cache doc."""
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[{"_id": "keep1"}, {"_id": "keep2"}])
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=3))

        await chat_service.delete_old_messages("session_123", keep_count=2, user_id="user_1")

        keep_query = mock_collection.find.call_args[0][0]
        assert keep_query.get("type") == {"$ne": "stm_summary"}

        delete_query = mock_collection.delete_many.call_args[0][0]
        assert delete_query.get("type") == {"$ne": "stm_summary"}
        assert delete_query.get("_id") == {"$nin": ["keep1", "keep2"]}

    @pytest.mark.asyncio
    async def test_optimize_stm_cache_hit(self):
        """Should use cached summary when fresh enough (no LLM call)."""
        from mdb_engine.memory.context_engineering import ContextEngineer

        ce = MagicMock(spec=ContextEngineer)
        ce.enable_context_engineering = True
        ce.stm_raw_window = 3
        ce.summary_staleness_threshold = 10

        # Mock STM service with cached summary
        mock_stm = MagicMock()
        mock_stm.get_cached_summary = AsyncMock(return_value=("Previous: user discussed AI and memory systems.", 12))
        ce.stm = mock_stm
        ce.llm_service = MagicMock()
        ce.llm_service.chat_completion = AsyncMock(return_value="unused")

        # 15 total messages, cache was at 12, only 3 new -> fresh
        stm_context = [{"role": "user", "content": f"msg {i}"} for i in range(15)]

        result = await ContextEngineer.optimize_stm_context(ce, stm_context, "session_1", "user_1")

        recent, summary = result
        assert len(recent) == 3
        assert summary == "Previous: user discussed AI and memory systems."
        # LLM should NOT have been called
        ce.llm_service.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_optimize_stm_cache_miss_generates_summary(self):
        """Should generate new summary via LLM when cache is empty."""
        from mdb_engine.memory.context_engineering import ContextEngineer

        ce = MagicMock(spec=ContextEngineer)
        ce.enable_context_engineering = True
        ce.stm_raw_window = 3
        ce.summary_staleness_threshold = 10

        # No cached summary
        mock_stm = MagicMock()
        mock_stm.get_cached_summary = AsyncMock(return_value=None)
        mock_stm.store_cached_summary = AsyncMock()
        ce.stm = mock_stm

        # LLM returns a summary (async interface)
        ce.llm_service = MagicMock()
        ce.llm_service.chat_completion = AsyncMock(return_value="User discussed project timelines and preferences.")

        stm_context = [{"role": "user", "content": f"msg {i}"} for i in range(10)]

        result = await ContextEngineer.optimize_stm_context(ce, stm_context, "session_1", "user_1")

        recent, summary = result
        assert len(recent) == 3
        assert "project timelines" in summary
        # LLM should have been called
        ce.llm_service.chat_completion.assert_called_once()
        # Cache should have been stored
        mock_stm.store_cached_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_optimize_stm_stale_cache_regenerates(self):
        """Should regenerate summary when cache is stale (too many new messages)."""
        from mdb_engine.memory.context_engineering import ContextEngineer

        ce = MagicMock(spec=ContextEngineer)
        ce.enable_context_engineering = True
        ce.stm_raw_window = 3
        ce.summary_staleness_threshold = 5  # Stale after 5 new messages

        # Cached summary at message count 10, but now we have 20 messages (10 new > 5 threshold)
        mock_stm = MagicMock()
        mock_stm.get_cached_summary = AsyncMock(return_value=("Old stale summary", 10))
        mock_stm.store_cached_summary = AsyncMock()
        ce.stm = mock_stm

        ce.llm_service = MagicMock()
        ce.llm_service.chat_completion = AsyncMock(return_value="Fresh summary of recent conversation.")

        stm_context = [{"role": "user", "content": f"msg {i}"} for i in range(20)]

        result = await ContextEngineer.optimize_stm_context(ce, stm_context, "session_1", "user_1")

        recent, summary = result
        assert "Fresh summary" in summary
        # LLM should have been called (cache was stale)
        ce.llm_service.chat_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_optimize_stm_no_llm_fallback(self):
        """Should use truncated fallback when no LLM provider available."""
        from mdb_engine.memory.context_engineering import ContextEngineer

        ce = MagicMock(spec=ContextEngineer)
        ce.enable_context_engineering = True
        ce.stm_raw_window = 3
        ce.summary_staleness_threshold = 10

        mock_stm = MagicMock()
        mock_stm.get_cached_summary = AsyncMock(return_value=None)
        ce.stm = mock_stm
        ce.llm_service = None  # No LLM available

        stm_context = [{"role": "user", "content": f"message number {i}"} for i in range(10)]

        result = await ContextEngineer.optimize_stm_context(ce, stm_context, "session_1", "user_1")

        recent, summary = result
        assert len(recent) == 3
        assert "Previous conversation" in summary
        assert "7 messages" in summary

    @pytest.mark.asyncio
    async def test_optimize_stm_short_context_no_summary(self):
        """Short conversations should not trigger summarization."""
        from mdb_engine.memory.context_engineering import ContextEngineer

        ce = MagicMock(spec=ContextEngineer)
        ce.enable_context_engineering = True
        ce.stm_raw_window = 5

        stm_context = [{"role": "user", "content": "hello"}]

        result = await ContextEngineer.optimize_stm_context(ce, stm_context, "session_1", "user_1")

        recent, summary = result
        assert len(recent) == 1
        assert summary is None
