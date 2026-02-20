"""
Unit tests for GraphService (standalone Graph Service module)

Tests the knowledge graph functionality including:
- Node operations (upsert, get, delete, list)
- Edge operations (add, remove, update, deactivate)
- Graph traversal ($graphLookup)
- Hybrid search (vector + graph)
- GraphRAG search methods (local_search, global_search, drift_search, classify_query)
- LLM-based graph extraction
- Context formatting
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the module under test
from mdb_engine.graph import (
    BaseGraphService,
    GraphService,
    GraphServiceError,
    get_graph_service,
)

# ============================================================================
# Fixtures
# ============================================================================


def _make_async_cursor(return_value=None):
    """Helper to create a mock async cursor with to_list and async-for support."""
    items = return_value or []
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)

    # Support ``async for doc in cursor``
    async def _aiter():
        for item in items:
            yield item

    cursor.__aiter__ = lambda self_: _aiter()
    return cursor


@pytest.fixture
def mock_collection():
    """Create a mock MongoDB collection."""
    collection = MagicMock()
    collection.name = "test_kg"
    collection.create_index = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.update_one = AsyncMock()
    collection.delete_one = AsyncMock()
    collection.delete_many = AsyncMock()
    collection.update_many = AsyncMock()
    collection.count_documents = AsyncMock(return_value=0)
    # find() and aggregate() return cursors synchronously; cursor.to_list() is async
    collection.find = MagicMock(return_value=_make_async_cursor())
    collection.aggregate = MagicMock(return_value=_make_async_cursor())
    return collection


@pytest.fixture
def mock_llm_service():
    """Create a mock LLM service."""
    service = MagicMock()
    service.chat_completion = AsyncMock(return_value='{"nodes": [], "edges": []}')
    return service


@pytest.fixture
def mock_embedding_service():
    """Create a mock embedding service."""
    service = MagicMock()
    # Return a simple mock embedding
    service.embed = AsyncMock(return_value=[[0.1] * 1536])
    return service


@pytest.fixture
def graph_service(mock_collection, mock_llm_service, mock_embedding_service):
    """Create a GraphService instance with mocked dependencies."""
    return GraphService(
        app_slug="test_app",
        collection=mock_collection,
        config={"enabled": True, "auto_extract": False},
        llm_service=mock_llm_service,
        embedding_service=mock_embedding_service,
    )


@pytest.fixture
def graph_service_disabled(mock_collection, mock_llm_service, mock_embedding_service):
    """Create a disabled GraphService."""
    return GraphService(
        app_slug="test_app",
        collection=mock_collection,
        config={"enabled": False},
        llm_service=mock_llm_service,
        embedding_service=mock_embedding_service,
    )


@pytest.fixture
def sample_node():
    """Sample node document."""
    return {
        "_id": "person:alex",
        "type": "person",
        "name": "Alex",
        "properties": {"occupation": "Engineer"},
        "edges": [
            {
                "relation": "likes",
                "target": "interest:golf",
                "weight": 0.9,
                "active": True,
                "created_at": datetime.now(timezone.utc),
            }
        ],
        "app_slug": "test_app",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


# ============================================================================
# Base Class Tests
# ============================================================================


class TestBaseGraphService:
    """Test BaseGraphService abstract interface."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseGraphService cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseGraphService()  # type: ignore

    def test_defines_required_methods(self):
        """Test that BaseGraphService defines all required abstract methods."""
        required_methods = [
            "upsert_node",
            "get_node",
            "delete_node",
            "list_nodes",
            "add_edge",
            "remove_edge",
            "update_edge",
            "deactivate_edge",
            "traverse",
            "get_neighbors",
            "hybrid_search",
            "extract_graph_from_text",
            "format_graph_context",
            "get_stats",
        ]
        for method in required_methods:
            assert hasattr(BaseGraphService, method)


# ============================================================================
# Initialization Tests
# ============================================================================


class TestGraphServiceInit:
    """Test GraphService initialization."""

    @pytest.mark.asyncio
    async def test_init_creates_indexes(self, mock_collection, mock_llm_service, mock_embedding_service):
        """Test that indexes are created lazily on first async call."""
        service = GraphService(
            app_slug="test_app",
            collection=mock_collection,
            config={"enabled": True},
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        # Indexes are lazy — not created in __init__
        assert not mock_collection.create_index.called
        assert service.app_slug == "test_app"

        # Trigger lazy init via get_stats()
        stats = await service.get_stats()
        assert stats["enabled"] is True
        # Now indexes should have been created
        assert mock_collection.create_index.called

    @pytest.mark.asyncio
    async def test_init_enabled_by_default(self, mock_collection, mock_llm_service, mock_embedding_service):
        """Test that graph service is enabled by default (empty config)."""
        service = GraphService(
            app_slug="test_app",
            collection=mock_collection,
            config={},
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        # Verify enabled state via public API (enabled by default)
        stats = await service.get_stats()
        assert stats["enabled"] is True

    @pytest.mark.asyncio
    async def test_init_explicitly_disabled(self, mock_collection, mock_llm_service, mock_embedding_service):
        """Test that graph service can be explicitly disabled."""
        service = GraphService(
            app_slug="test_app",
            collection=mock_collection,
            config={"enabled": False},
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        # Verify disabled state via public API
        stats = await service.get_stats()
        assert stats["enabled"] is False

    @pytest.mark.asyncio
    async def test_init_with_config(self, mock_collection, mock_llm_service, mock_embedding_service):
        """Test initialization with custom config."""
        config = {
            "enabled": True,
            "auto_extract": False,
            "llm_model": "openai/gpt-4",
            "default_max_depth": 3,
            "node_types": ["person", "place"],
        }

        service = GraphService(
            app_slug="test_app",
            collection=mock_collection,
            config=config,
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        # Verify config applied via public properties
        stats = await service.get_stats()
        assert stats["enabled"] is True
        assert service.auto_extract is False
        assert service.llm_model == "openai/gpt-4"
        assert service.default_max_depth == 3
        assert "person" in service.node_types
        assert "place" in service.node_types

    def test_init_requires_collection(self, mock_llm_service, mock_embedding_service):
        """Test that collection is required."""
        with pytest.raises(GraphServiceError, match="Collection is REQUIRED"):
            GraphService(
                app_slug="test_app",
                collection=None,
                config={"enabled": True},
                llm_service=mock_llm_service,
                embedding_service=mock_embedding_service,
            )


class TestFactoryFunction:
    """Test the get_graph_service factory function."""

    def test_graph_service_mro_resolves_all_abstract_methods(self):
        """Verify GraphService MRO correctly resolves all abstract methods."""
        assert len(GraphService.__abstractmethods__) == 0

    def test_get_graph_service(self, mock_collection, mock_llm_service, mock_embedding_service):
        """Test factory function creates GraphService."""
        service = get_graph_service(
            app_slug="test_app",
            collection=mock_collection,
            config={"enabled": True},
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        assert isinstance(service, GraphService)
        assert service.app_slug == "test_app"


# ============================================================================
# Node Operations Tests
# ============================================================================


class TestNodeOperations:
    """Test node CRUD operations."""

    @pytest.mark.asyncio
    async def test_upsert_node_creates_new(self, graph_service, mock_collection):
        """Test upserting a new node."""
        mock_collection.update_one.return_value = MagicMock(
            upserted_id="person:alex",
            modified_count=0,
        )
        mock_collection.find_one.return_value = {
            "_id": "person:alex",
            "type": "person",
            "name": "Alex",
        }

        result = await graph_service.upsert_node(
            node_id="person:alex",
            node_type="person",
            name="Alex",
            properties={"occupation": "Engineer"},
            user_id="user123",
        )

        assert mock_collection.update_one.called
        assert result["_id"] == "person:alex"

    @pytest.mark.asyncio
    async def test_upsert_node_disabled(self, graph_service_disabled, mock_collection):
        """Test that upsert returns empty when disabled."""
        result = await graph_service_disabled.upsert_node(
            node_id="person:alex",
            node_type="person",
            name="Alex",
        )

        assert result == {}
        assert not mock_collection.update_one.called

    @pytest.mark.asyncio
    async def test_get_node_found(self, graph_service, mock_collection, sample_node):
        """Test getting an existing node."""
        mock_collection.find_one.return_value = sample_node

        result = await graph_service.get_node("person:alex")

        assert result is not None
        assert result["_id"] == "person:alex"
        assert result["type"] == "person"

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, graph_service, mock_collection):
        """Test getting a non-existent node."""
        mock_collection.find_one.return_value = None

        result = await graph_service.get_node("person:nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_node(self, graph_service, mock_collection):
        """Test deleting a node."""
        mock_collection.delete_one.return_value = MagicMock(deleted_count=1)
        mock_collection.update_many.return_value = MagicMock(modified_count=2)

        result = await graph_service.delete_node("person:alex")

        assert result is True
        # Should also remove edges pointing to this node
        assert mock_collection.update_many.called

    @pytest.mark.asyncio
    async def test_delete_node_not_found(self, graph_service, mock_collection):
        """Test deleting a non-existent node."""
        mock_collection.delete_one.return_value = MagicMock(deleted_count=0)

        result = await graph_service.delete_node("person:nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_list_nodes(self, graph_service, mock_collection, sample_node):
        """Test listing nodes."""
        mock_cursor = _make_async_cursor([sample_node])
        mock_collection.find.return_value = mock_cursor

        result = await graph_service.list_nodes(node_type="person", limit=10)

        assert len(result) == 1
        mock_collection.find.assert_called_once()


# ============================================================================
# Edge Operations Tests
# ============================================================================


class TestEdgeOperations:
    """Test edge CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_edge_new(self, graph_service, mock_collection):
        """Test adding a new edge."""
        # First update (existing edge check) returns no match
        mock_collection.update_one.side_effect = [
            MagicMock(modified_count=0),  # No existing edge
            MagicMock(modified_count=1),  # Edge added
        ]

        result = await graph_service.add_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:golf",
            properties={"since": "2020"},
            weight=0.9,
        )

        assert result is True
        assert mock_collection.update_one.call_count == 2

    @pytest.mark.asyncio
    async def test_add_edge_updates_existing(self, graph_service, mock_collection):
        """Test that adding an existing edge updates it."""
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = await graph_service.add_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:golf",
            weight=0.95,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_add_edge_disabled(self, graph_service_disabled, mock_collection):
        """Test that add_edge returns False when disabled."""
        result = await graph_service_disabled.add_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:golf",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_remove_edge(self, graph_service, mock_collection):
        """Test removing an edge."""
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = await graph_service.remove_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:golf",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_remove_edge_not_found(self, graph_service, mock_collection):
        """Test removing a non-existent edge."""
        mock_collection.update_one.return_value = MagicMock(modified_count=0)

        result = await graph_service.remove_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:nonexistent",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_edge(self, graph_service, mock_collection):
        """Test updating an edge."""
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = await graph_service.update_edge(
            source_id="person:alex",
            relation="likes",
            target_id="interest:golf",
            updates={"weight": 0.95, "active": True},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_deactivate_edge(self, graph_service, mock_collection):
        """Test deactivating an edge (soft delete)."""
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = await graph_service.deactivate_edge(
            source_id="person:alex",
            relation="works_at",
            target_id="organization:oldcorp",
        )

        assert result is True


# ============================================================================
# Graph Traversal Tests
# ============================================================================


class TestGraphTraversal:
    """Test graph traversal operations."""

    @pytest.mark.asyncio
    async def test_traverse_basic(self, graph_service, mock_collection, sample_node):
        """Test basic graph traversal."""
        # Mock aggregation result via async cursor
        traverse_data = [
            {"node": sample_node, "hop_distance": 0},
            {
                "node": {
                    "_id": "interest:golf",
                    "type": "interest",
                    "name": "Golf",
                    "edges": [],
                },
                "hop_distance": 1,
            },
        ]
        mock_collection.aggregate.return_value = _make_async_cursor(traverse_data)

        results = await graph_service.traverse("person:alex", max_depth=2)

        assert len(results) == 2
        assert mock_collection.aggregate.called

    @pytest.mark.asyncio
    async def test_traverse_disabled(self, graph_service_disabled, mock_collection):
        """Test that traverse returns empty when disabled."""
        results = await graph_service_disabled.traverse("person:alex")

        assert results == []

    @pytest.mark.asyncio
    async def test_traverse_with_depth_limit(self, graph_service, mock_collection):
        """Test traversal respects depth limit."""
        mock_collection.aggregate.return_value = _make_async_cursor([])

        await graph_service.traverse("person:alex", max_depth=1)

        # Check that the pipeline includes maxDepth
        call_args = mock_collection.aggregate.call_args
        pipeline = call_args[0][0]

        # Find $graphLookup stage and verify maxDepth
        for stage in pipeline:
            if "$graphLookup" in stage:
                assert stage["$graphLookup"]["maxDepth"] == 0  # maxDepth is 0-indexed

    @pytest.mark.asyncio
    async def test_get_neighbors(self, graph_service, mock_collection, sample_node):
        """Test getting immediate neighbors."""
        # get_node returns the sample_node (has one "likes" edge to interest:golf)
        mock_collection.find_one.return_value = sample_node
        # get_neighbors then batch-fetches target nodes via collection.find()
        mock_collection.find.return_value = _make_async_cursor(
            [
                {
                    "_id": "interest:golf",
                    "type": "interest",
                    "name": "Golf",
                    "app_slug": "test_app",
                },
            ]
        )

        neighbors = await graph_service.get_neighbors("person:alex")

        assert len(neighbors) == 1
        assert neighbors[0]["relation"] == "likes"

    @pytest.mark.asyncio
    async def test_get_neighbors_with_filter(self, graph_service, mock_collection, sample_node):
        """Test getting neighbors with relation filter."""
        sample_node["edges"].append(
            {
                "relation": "works_at",
                "target": "organization:corp",
                "active": True,
            }
        )
        mock_collection.find_one.return_value = sample_node

        # Filter for only "likes" relations
        neighbors = await graph_service.get_neighbors("person:alex", relation="likes")

        # Only the "likes" edge should be returned
        assert all(n["relation"] == "likes" for n in neighbors)


# ============================================================================
# Hybrid Search Tests
# ============================================================================


class TestHybridSearch:
    """Test hybrid search (vector + graph)."""

    @pytest.mark.asyncio
    async def test_hybrid_search_disabled(self, graph_service_disabled, mock_collection):
        """Test hybrid search when disabled."""
        result = await graph_service_disabled.hybrid_search(
            query="What does Alex like?",
            user_id="user123",
        )

        assert result["entry_nodes"] == []
        assert result["graph_context"] == []
        assert result["total_nodes"] == 0

    @pytest.mark.asyncio
    async def test_hybrid_search_with_embedding(self, graph_service, mock_collection, sample_node):
        """Test hybrid search with embedding service."""
        # Mock vector search results via async cursors
        mock_collection.aggregate.side_effect = [
            # First call: vector search
            _make_async_cursor(
                [
                    {
                        "_id": "person:alex",
                        "type": "person",
                        "name": "Alex",
                        "edges": [],
                        "similarity": 0.92,
                    }
                ]
            ),
            # Second call: traverse
            _make_async_cursor([]),
        ]

        result = await graph_service.hybrid_search(
            query="What does Alex like?",
            user_id="user123",
            max_depth=2,
        )

        assert len(result["entry_nodes"]) == 1
        assert result["entry_nodes"][0]["_id"] == "person:alex"


# ============================================================================
# Graph Extraction Tests
# ============================================================================


class TestGraphExtraction:
    """Test LLM-based graph extraction."""

    @pytest.mark.asyncio
    async def test_extract_disabled_when_auto_extract_false(self, graph_service, mock_collection):
        """Test extraction is skipped when auto_extract is False."""
        result = await graph_service.extract_graph_from_text(
            text="Alex loves golf",
            user_id="user123",
        )

        assert result["nodes_created"] == 0
        assert result["edges_created"] == 0

    @pytest.mark.asyncio
    async def test_extract_disabled_when_graph_disabled(
        self, mock_collection, mock_llm_service, mock_embedding_service
    ):
        """Test extraction is skipped when graph is disabled."""
        service = GraphService(
            app_slug="test_app",
            collection=mock_collection,
            config={"enabled": False, "auto_extract": True},
            llm_service=mock_llm_service,
            embedding_service=mock_embedding_service,
        )

        result = await service.extract_graph_from_text(
            text="Alex loves golf",
            user_id="user123",
        )

        assert result["nodes_created"] == 0
        assert result["edges_created"] == 0


# ============================================================================
# Context Formatting Tests
# ============================================================================


class TestContextFormatting:
    """Test graph context formatting."""

    def test_format_empty_results(self, graph_service):
        """Test formatting empty results."""
        result = graph_service.format_graph_context(
            hybrid_results={"entry_nodes": [], "graph_context": []},
        )

        assert result == ""

    def test_format_with_nodes(self, graph_service):
        """Test formatting results with nodes."""
        hybrid_results = {
            "entry_nodes": [
                {
                    "_id": "person:alex",
                    "name": "Alex",
                    "type": "person",
                    "properties": {"occupation": "Engineer"},
                }
            ],
            "graph_context": [],
        }

        result = graph_service.format_graph_context(
            hybrid_results=hybrid_results,
            max_nodes=10,
            include_edges=True,
        )

        assert "KNOWLEDGE GRAPH CONTEXT:" in result
        assert "Alex" in result
        assert "person" in result


# ============================================================================
# Statistics Tests
# ============================================================================


class TestStatistics:
    """Test graph statistics."""

    @pytest.mark.asyncio
    async def test_get_stats_disabled(self, graph_service_disabled):
        """Test stats when disabled."""
        stats = await graph_service_disabled.get_stats()

        assert stats["enabled"] is False
        assert stats["total_nodes"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_enabled(self, graph_service, mock_collection):
        """Test getting stats when enabled."""
        mock_collection.aggregate.return_value = _make_async_cursor([])

        stats = await graph_service.get_stats()

        assert stats["enabled"] is True
        assert stats["app_slug"] == "test_app"


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Test error handling."""

    def test_graph_service_error_inherits_from_exception(self):
        """Test GraphServiceError is a proper exception."""
        error = GraphServiceError("Test error")
        assert isinstance(error, Exception)
        assert str(error) == "Test error"

    @pytest.mark.asyncio
    async def test_node_operations_handle_mongo_errors(self, graph_service, mock_collection):
        """Test that node operations handle MongoDB errors gracefully."""
        from pymongo.errors import PyMongoError

        mock_collection.find_one.side_effect = PyMongoError("MongoDB error")

        result = await graph_service.get_node("person:alex")

        # Should return None on error (get_node catches PyMongoError)
        assert result is None
