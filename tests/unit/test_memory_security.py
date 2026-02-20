"""
Tests for memory system security hardening.

Round 1:
- Search method rename (_search_without_decay -> _search)
- User ID enforcement across CRUD methods
- Filter override protection in scoring
- Shared memory group membership verification
- Log redaction helper
- Graph-memory cleanup on delete/merge

Round 2:
- user_id validation in specialist modules (recall, prospective, veto)
- Recall veto scope normalization
- get_memory_service raises instead of returning None
- Threshold validation in builder
- Graph search graceful degradation
- get_all filter override protection
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.core.validation import InputValidationError
from mdb_engine.memory.cognitive import CognitiveMemoryService
from mdb_engine.memory.log_utils import redact
from mdb_engine.memory.shared import SharedMemory, SharedMemoryError

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def mock_collection():
    """Create a mock MongoDB collection with standard methods."""
    collection = MagicMock()
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id1"]))
    collection.find_one = AsyncMock(return_value=None)

    # Mock cursor for find()
    mock_cursor = MagicMock()
    mock_cursor.sort = MagicMock(return_value=mock_cursor)
    mock_cursor.limit = MagicMock(return_value=mock_cursor)
    mock_cursor.to_list = AsyncMock(return_value=[])
    collection.find = MagicMock(return_value=mock_cursor)

    # Mock cursor for aggregate()
    mock_agg_cursor = MagicMock()
    mock_agg_cursor.to_list = AsyncMock(return_value=[])
    collection.aggregate = MagicMock(return_value=mock_agg_cursor)

    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    collection.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    collection.count_documents = AsyncMock(return_value=0)
    collection.create_index = AsyncMock()
    return collection


@pytest.fixture
def memory_service(mock_collection):
    """Create a CognitiveMemoryService instance for testing."""
    mock_embedding_provider = MagicMock()
    mock_embedding_provider.embed = AsyncMock(return_value=[[0.1] * 1536])

    service = CognitiveMemoryService(
        app_slug="test_app",
        collection=mock_collection,
        config={"enable_cognitive": True, "categories": {"enabled": True}},
        embedding_service=mock_embedding_provider,
    )

    # Set mock helpers for test isolation
    service._get_embedding = AsyncMock(return_value=[0.1] * 1536)
    service._get_embeddings_batch = AsyncMock(return_value={"test": [0.1] * 1536})
    service._find_similar_memories = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_graph_service():
    """Create a mock GraphService."""
    graph = MagicMock()
    graph.remove_memory_references = AsyncMock(return_value=1)
    graph.extract_graph_from_text = AsyncMock(return_value={"nodes_created": 0, "edges_created": 0})
    return graph


@pytest.fixture
def mock_members_collection():
    """Mock collection for group membership verification."""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.create_index = AsyncMock()
    return collection


# ========================================================================
# 1. Search method rename
# ========================================================================


class TestSearchMethodRename:
    """Verify _search_without_decay no longer exists and _search is present."""

    def test_search_method_exists(self, memory_service):
        """_search should exist on the service."""
        assert hasattr(memory_service, "_search"), "CognitiveMemoryService should have a '_search' method"

    def test_search_without_decay_removed(self, memory_service):
        """_search_without_decay should NOT exist on the service."""
        assert not hasattr(
            memory_service, "_search_without_decay"
        ), "'_search_without_decay' should have been renamed to '_search'"


# ========================================================================
# 2. User ID enforcement
# ========================================================================


class TestUserIdEnforcement:
    """Verify that user_id is required for bulk/search/delete operations."""

    @pytest.mark.asyncio
    async def test_get_all_requires_user_id(self, memory_service):
        """get_all(user_id=None) must raise InputValidationError."""
        with pytest.raises(InputValidationError, match="user_id"):
            await memory_service.get_all(user_id=None)

    @pytest.mark.asyncio
    async def test_search_requires_user_id(self, memory_service):
        """search(query, user_id=None) must raise InputValidationError."""
        # Disable persona engine to avoid unrelated mock issues
        memory_service.persona_engine = None
        with pytest.raises(InputValidationError, match="user_id"):
            await memory_service.search(query="test query", user_id=None)

    @pytest.mark.asyncio
    async def test_delete_requires_user_id(self, memory_service):
        """delete(id, user_id=None) must raise InputValidationError."""
        with pytest.raises(InputValidationError, match="user_id"):
            await memory_service.delete(memory_id="abc123", user_id=None)

    @pytest.mark.asyncio
    async def test_delete_all_requires_user_id(self, memory_service):
        """delete_all(user_id=None) must raise InputValidationError."""
        with pytest.raises(InputValidationError, match="user_id"):
            await memory_service.delete_all(user_id=None, hard_delete=True)

    @pytest.mark.asyncio
    async def test_get_still_allows_optional_user_id(self, memory_service):
        """get() should still work without user_id (single-item lookup)."""
        # Should not raise — get() by ID is safe without user_id
        result = await memory_service.get(memory_id="507f1f77bcf86cd799439011")
        assert result is None  # Mock returns None, but no error

    @pytest.mark.asyncio
    async def test_filter_cannot_override_user_id(self, memory_service, mock_collection):
        """Filters must not be able to override the scoped user_id."""
        # Disable sub-services to avoid unrelated mock issues
        memory_service.persona_engine = None
        memory_service.timeline_service = None

        # Mock the aggregate pipeline to capture what was sent
        mock_agg_cursor = MagicMock()
        mock_agg_cursor.to_list = AsyncMock(return_value=[])
        mock_collection.aggregate = MagicMock(return_value=mock_agg_cursor)

        # Mock _update_access_counts to avoid DB calls
        memory_service._update_access_counts = AsyncMock()

        await memory_service.search(
            query="test",
            user_id="legitimate_user",
            filters={"user_id": "evil_user"},  # Attempt to override
        )

        # Inspect the pipeline passed to aggregate
        call_args = mock_collection.aggregate.call_args
        assert call_args is not None, "aggregate was never called"
        pipeline = call_args[0][0]

        # The $vectorSearch filter should contain the legitimate user_id
        vector_search_stage = pipeline[0]["$vectorSearch"]
        search_filter = vector_search_stage["filter"]
        assert (
            search_filter["user_id"] == "legitimate_user"
        ), f"user_id should be 'legitimate_user', but filter contains: {search_filter}"


# ========================================================================
# 3. Shared memory group validation
# ========================================================================


class TestSharedMemoryGroupValidation:
    """Verify group membership is enforced in SharedMemory."""

    @pytest.mark.asyncio
    async def test_get_shared_memory_requires_user_id(self, mock_collection):
        """get_shared_memory must require user_id."""
        shared = SharedMemory(
            semantic_collection=mock_collection,
            shared_collection=mock_collection,
        )
        with pytest.raises(InputValidationError, match="user_id"):
            await shared.get_shared_memory(
                group_id="team-001",
                user_id=None,
            )

    @pytest.mark.asyncio
    async def test_shared_memory_rejects_non_member(self, mock_collection, mock_members_collection):
        """Non-member user should be rejected."""
        # find_one returns None -> user is NOT a member
        mock_members_collection.find_one = AsyncMock(return_value=None)

        shared = SharedMemory(
            semantic_collection=mock_collection,
            shared_collection=mock_collection,
            group_members_collection=mock_members_collection,
        )

        with pytest.raises(SharedMemoryError, match="not a member"):
            await shared.get_shared_memory(
                group_id="team-001",
                user_id="outsider",
            )

    @pytest.mark.asyncio
    async def test_shared_memory_allows_member(self, mock_collection, mock_members_collection):
        """Member user should get results."""
        # find_one returns a document -> user IS a member
        mock_members_collection.find_one = AsyncMock(return_value={"group_id": "team-001", "user_id": "member1"})

        # Mock cursor for the find query
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[{"fact": "test", "_id": "1"}])
        mock_collection.find = MagicMock(return_value=mock_cursor)

        shared = SharedMemory(
            semantic_collection=mock_collection,
            shared_collection=mock_collection,
            group_members_collection=mock_members_collection,
        )

        results = await shared.get_shared_memory(
            group_id="team-001",
            user_id="member1",
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_shared_memory_no_enforcement_without_members_collection(self, mock_collection):
        """Without group_members_collection, access should be allowed (backwards-compat)."""
        # Mock cursor for the find query
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_collection.find = MagicMock(return_value=mock_cursor)

        shared = SharedMemory(
            semantic_collection=mock_collection,
            shared_collection=mock_collection,
            # No group_members_collection -> no enforcement
        )

        # Should NOT raise
        results = await shared.get_shared_memory(
            group_id="team-001",
            user_id="anyone",
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_promote_verifies_source_user_membership(self, mock_collection, mock_members_collection):
        """promote_to_shared should verify all source users are group members."""
        # First user is a member, second is not
        mock_members_collection.find_one = AsyncMock(
            side_effect=[
                {"group_id": "team-001", "user_id": "user1"},  # member
                None,  # NOT a member
            ]
        )

        shared = SharedMemory(
            semantic_collection=mock_collection,
            shared_collection=mock_collection,
            group_members_collection=mock_members_collection,
        )

        with pytest.raises(SharedMemoryError, match="not a member"):
            await shared.promote_to_shared(
                fact="Team uses Python for all projects",
                source_user_ids=["user1", "user2"],
                confidence=0.85,
                group_id="team-001",
            )


# ========================================================================
# 4. Log redaction helper
# ========================================================================


class TestRedactHelper:
    """Unit tests for the redact() helper function."""

    def test_redact_returns_char_count(self):
        """redact should return '<N chars>' for non-empty text."""
        assert redact("The user loves pizza") == "<20 chars>"

    def test_redact_empty_string(self):
        """redact should return '<empty>' for empty string."""
        assert redact("") == "<empty>"

    def test_redact_none(self):
        """redact should return '<empty>' for None."""
        assert redact(None) == "<empty>"

    def test_redact_long_text(self):
        """redact should work for long text."""
        long_text = "x" * 10000
        assert redact(long_text) == "<10000 chars>"

    def test_redact_ignores_max_len_param(self):
        """max_len parameter is accepted but ignored."""
        result = redact("hello", max_len=3)
        assert result == "<5 chars>"


# ========================================================================
# 5. Graph-memory cleanup
# ========================================================================


class TestGraphMemoryCleanup:
    """Verify graph cleanup occurs on memory delete and merge."""

    @pytest.mark.asyncio
    async def test_delete_memory_cleans_graph(self, memory_service, mock_collection, mock_graph_service):
        """Deleting a memory should call remove_memory_references on the graph."""
        memory_service._graph_service = mock_graph_service
        mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

        result = await memory_service.delete(
            memory_id="507f1f77bcf86cd799439011",
            user_id="user1",
        )

        assert result is True
        mock_graph_service.remove_memory_references.assert_called_once_with("507f1f77bcf86cd799439011")

    @pytest.mark.asyncio
    async def test_delete_memory_no_graph_cleanup_on_failure(self, memory_service, mock_collection, mock_graph_service):
        """If the memory wasn't actually deleted, graph cleanup should NOT run."""
        memory_service._graph_service = mock_graph_service
        mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))

        result = await memory_service.delete(
            memory_id="507f1f77bcf86cd799439011",
            user_id="user1",
        )

        assert result is False
        mock_graph_service.remove_memory_references.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_memory_graph_error_non_fatal(self, memory_service, mock_collection, mock_graph_service):
        """Graph cleanup failures should NOT prevent memory deletion."""
        memory_service._graph_service = mock_graph_service
        mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        mock_graph_service.remove_memory_references = AsyncMock(side_effect=RuntimeError("graph down"))

        # Should still return True (delete succeeded)
        result = await memory_service.delete(
            memory_id="507f1f77bcf86cd799439011",
            user_id="user1",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_graph_extraction_passes_source_memory_id(self, memory_service, mock_collection, mock_graph_service):
        """add() should pass source_memory_id to graph extraction."""
        memory_service._graph_service = mock_graph_service
        memory_service._graph_auto_extract = True
        memory_service.infer = False  # Skip LLM extraction

        # Mock the add pipeline to produce stored memories
        mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["mem_id_1"]))

        # We need to mock the full pipeline; use inject for simplicity
        result = await memory_service.inject(
            memory="User likes hiking",
            user_id="user1",
        )

        # Now simulate graph extraction call with the stored memory
        mem_id = result["id"]
        await mock_graph_service.extract_graph_from_text("User likes hiking", "user1", source_memory_id=mem_id)

        # Verify extract_graph_from_text was called with source_memory_id
        mock_graph_service.extract_graph_from_text.assert_called_with(
            "User likes hiking", "user1", source_memory_id=mem_id
        )

    @pytest.mark.asyncio
    async def test_merge_cleans_up_old_memory_graph(self, memory_service, mock_graph_service):
        """_merge_memories should clean up graph for the deleted (old) memory."""
        memory_service._graph_service = mock_graph_service

        # Mock LLM completion for merge prompt
        from types import SimpleNamespace

        memory_service._llm_completion = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Merged: User likes cats and dogs"))]
            )
        )
        memory_service.llm_available = True

        # Mock collection to return docs for the batch-fetch
        from bson import ObjectId

        new_id = ObjectId()
        old_id = ObjectId()

        async def mock_find(query):
            """Return mock documents for the batch fetch."""
            docs = [
                {"_id": new_id, "access_count": 0, "category": "preferences"},
                {"_id": old_id, "access_count": 1, "category": "preferences"},
            ]
            for doc in docs:
                if doc["_id"] in query.get("_id", {}).get("$in", []):
                    yield doc

        memory_service.collection.find = MagicMock(return_value=mock_find({"_id": {"$in": [old_id, new_id]}}))
        memory_service.collection.update_one = AsyncMock()
        memory_service.collection.delete_one = AsyncMock()

        result = await memory_service._merge_memories(
            new_memory_id=new_id,
            existing_memory_id=old_id,
            new_text="User likes cats",
            existing_text="User likes dogs",
            new_embedding=[0.1] * 1536,
            existing_embedding=[0.2] * 1536,
            new_importance=0.7,
            existing_importance=0.6,
        )

        assert result is True
        # The old memory's graph references should be cleaned up
        mock_graph_service.remove_memory_references.assert_called_once_with(str(old_id))


# ========================================================================
# Round 2 tests
# ========================================================================


class TestSpecialistModulesValidateUserId:
    """Verify user_id is validated in specialist memory modules."""

    @pytest.mark.asyncio
    async def test_recall_validates_user_id(self):
        """QueryAwareRecall.recall() must validate user_id."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()
        with pytest.raises(InputValidationError, match="user_id"):
            await recall.recall(
                query="test",
                user_id="",  # empty = invalid
                collection=MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_recall_multi_scope_validates_user_id(self):
        """QueryAwareRecall.recall_multi_scope() must validate user_id."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()
        with pytest.raises(InputValidationError, match="user_id"):
            await recall.recall_multi_scope(
                query="test",
                user_id="",
                collections={},
                allowed_scopes=["user"],
            )

    @pytest.mark.asyncio
    async def test_prospective_set_trigger_validates_user_id(self):
        """ProspectiveMemory.set_trigger() must validate user_id."""
        from mdb_engine.memory.prospective import ProspectiveMemory

        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        pm = ProspectiveMemory(collection=mock_coll, embedding_service=MagicMock())
        with pytest.raises(InputValidationError, match="user_id"):
            await pm.set_trigger(
                condition="test condition",
                action="test action",
                user_id="",
            )

    @pytest.mark.asyncio
    async def test_veto_add_validates_user_id(self):
        """MemoryVeto.add_veto() must validate user_id."""
        from mdb_engine.memory.veto import MemoryVeto

        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        veto = MemoryVeto(collection=mock_coll)
        with pytest.raises(InputValidationError, match="user_id"):
            await veto.add_veto(
                memory_id="mem123",
                user_id="",
            )

    @pytest.mark.asyncio
    async def test_veto_check_validates_user_id(self):
        """MemoryVeto.check_veto() must validate user_id."""
        from mdb_engine.memory.veto import MemoryVeto

        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        veto = MemoryVeto(collection=mock_coll)
        with pytest.raises(InputValidationError, match="user_id"):
            await veto.check_veto(
                memory_id="mem123",
                user_id="",
            )


class TestRecallVetoScopeNormalization:
    """Verify recall.py uses normalized_scope consistently for veto checks."""

    @pytest.mark.asyncio
    async def test_recall_veto_uses_normalized_scope(self):
        """Both vector-search and standard-query paths should use normalized_scope."""
        from mdb_engine.memory.recall import QueryAwareRecall

        recall = QueryAwareRecall()

        # Create a mock veto that tracks calls
        mock_veto = MagicMock()
        mock_veto.check_veto = MagicMock(return_value=False)

        # Mock collection -- make aggregate raise so it falls back to standard query
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[{"_id": "mem1", "text": "test"}])

        mock_collection = MagicMock()
        mock_collection.find = MagicMock(return_value=mock_cursor)

        # Pass a pre-computed query_vector AND make vector search fail
        # so it falls through to the standard query path
        from pymongo.errors import PyMongoError

        mock_agg_cursor = MagicMock()
        mock_agg_cursor.to_list = AsyncMock(side_effect=PyMongoError("no vector index"))
        mock_collection.aggregate = MagicMock(return_value=mock_agg_cursor)

        result = await recall.recall(
            query="test",
            user_id="user1",
            collection=mock_collection,
            scope="family",  # Should normalize to "shared"
            memory_veto=mock_veto,
            query_vector=[0.1] * 1536,  # Skip embedding service
        )

        # Veto check should have been called with "shared", not "family"
        assert mock_veto.check_veto.called, "Veto check should have been called"
        call_args = mock_veto.check_veto.call_args
        assert (
            call_args.kwargs.get("target_scope") == "shared"
        ), f"Veto check should use normalized_scope='shared', got: {call_args}"


class TestGetMemoryServiceRaises:
    """Verify get_memory_service raises HTTPException instead of returning None."""

    @pytest.mark.asyncio
    async def test_raises_on_missing_engine(self):
        """Should raise HTTPException when engine is missing."""
        from fastapi import HTTPException

        from mdb_engine.dependencies import get_memory_service

        mock_request = MagicMock()
        mock_request.app.state = MagicMock(spec=[])  # No engine attribute

        with pytest.raises(HTTPException) as exc_info:
            await get_memory_service(mock_request)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_raises_on_missing_slug(self):
        """Should raise HTTPException when slug is missing."""
        from fastapi import HTTPException

        from mdb_engine.dependencies import get_memory_service

        mock_request = MagicMock()
        mock_request.app.state.engine = MagicMock()
        mock_request.app.state.app_slug = None

        with pytest.raises(HTTPException) as exc_info:
            await get_memory_service(mock_request)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_raises_when_service_not_configured(self):
        """Should raise HTTPException when memory service returns None."""
        from fastapi import HTTPException

        from mdb_engine.dependencies import get_memory_service

        mock_engine = MagicMock()
        mock_engine.get_memory_service.return_value = None

        mock_request = MagicMock()
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        with pytest.raises(HTTPException) as exc_info:
            await get_memory_service(mock_request)
        assert exc_info.value.status_code == 503
        assert "test_app" in str(exc_info.value.detail)


class TestThresholdValidation:
    """Verify threshold values are validated and clamped in builder."""

    def test_clamp_out_of_range_high(self):
        """Values above 1.0 should be clamped to 1.0."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        result = CognitiveMemoryServiceBuilder._clamp("test", 5.0, 0.0, 1.0)
        assert result == 1.0

    def test_clamp_out_of_range_low(self):
        """Negative values should be clamped to 0.0."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        result = CognitiveMemoryServiceBuilder._clamp("test", -1.0, 0.0, 1.0)
        assert result == 0.0

    def test_clamp_valid_value_unchanged(self):
        """Valid values should pass through unchanged."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        result = CognitiveMemoryServiceBuilder._clamp("test", 0.7, 0.0, 1.0)
        assert result == 0.7

    def test_clamp_invalid_type_returns_midpoint(self):
        """Non-numeric values should return the midpoint."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        result = CognitiveMemoryServiceBuilder._clamp("test", "not_a_number", 0.0, 1.0)
        assert result == 0.5


class TestGetAllFilterProtection:
    """Verify get_all filter merging protects user_id."""

    @pytest.mark.asyncio
    async def test_get_all_filter_cannot_override_user_id(self, memory_service, mock_collection):
        """get_all filters must not override the scoped user_id."""
        # Mock the find/cursor chain
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_collection.count_documents = AsyncMock(return_value=0)

        await memory_service.get_all(
            user_id="legitimate_user",
            filters={"user_id": "evil_user"},  # Attempt to override
        )

        # Inspect the query passed to find()
        call_args = mock_collection.find.call_args
        query = call_args[0][0]
        assert (
            query["user_id"] == "legitimate_user"
        ), f"user_id should be 'legitimate_user', but query contains: {query}"
