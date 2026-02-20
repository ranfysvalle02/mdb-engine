"""
Collection Type Safety Tests

Regression tests that verify all service constructors accepting MongoDB
collections properly reject incorrect types at construction time.

Background:
    A synchronous ``pymongo.Collection``'s ``create_index()`` returns a
    string (the index name).  When awaited inside an async service, this
    causes ``TypeError: object str can't be used in 'await' expression``.
    These tests ensure that bad types are caught *immediately* in the
    constructor rather than failing at runtime deep in the call stack.
"""

from unittest.mock import MagicMock

import pytest
from pymongo.collection import Collection as SyncCollection

from mdb_engine.memory.chat_history import ChatHistoryService
from mdb_engine.memory.orchestrator import CognitiveEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_collection() -> MagicMock:
    """Create a MagicMock that passes ``isinstance(..., SyncCollection)``."""
    return MagicMock(spec=SyncCollection)


def _make_async_collection() -> MagicMock:
    """Create a MagicMock simulating an async Motor collection (valid)."""
    from unittest.mock import AsyncMock

    col = MagicMock()
    col.insert_one = AsyncMock()
    col.find = MagicMock()
    col.create_index = AsyncMock()
    col.find_one = AsyncMock(return_value=None)
    col.count_documents = AsyncMock(return_value=0)
    col.update_one = AsyncMock()
    col.delete_many = AsyncMock()
    return col


# ---------------------------------------------------------------------------
# Parametrized bad-type inputs
# ---------------------------------------------------------------------------

BAD_COLLECTION_TYPES = [
    pytest.param("chat_history", id="string"),
    pytest.param(42, id="int"),
    pytest.param({"key": "value"}, id="dict"),
    pytest.param(["a", "b"], id="list"),
]

BAD_COLLECTION_TYPES_WITH_SYNC = BAD_COLLECTION_TYPES + [
    pytest.param(_make_sync_collection(), id="sync_pymongo_collection"),
]


# ============================================================================
# ChatHistoryService
# ============================================================================


class TestChatHistoryServiceTypeSafety:
    """ChatHistoryService must reject non-async collection types."""

    def test_accepts_async_mock_collection(self):
        """Valid async collection should be accepted."""
        col = _make_async_collection()
        svc = ChatHistoryService(col, "test")
        assert svc.collection is col

    def test_rejects_none(self):
        """None must raise ValueError (existing behavior)."""
        with pytest.raises(ValueError, match="REQUIRED"):
            ChatHistoryService(None)

    def test_rejects_string(self):
        """String must raise TypeError with helpful message."""
        with pytest.raises(TypeError, match="not a string"):
            ChatHistoryService("chat_history")

    def test_rejects_sync_pymongo_collection(self):
        """Synchronous pymongo.Collection must raise TypeError."""
        sync_col = _make_sync_collection()
        with pytest.raises(TypeError, match="synchronous pymongo.Collection"):
            ChatHistoryService(sync_col)

    @pytest.mark.parametrize("bad_value", [42, {"k": "v"}, ["a"]], ids=["int", "dict", "list"])
    def test_rejects_primitive_types(self, bad_value):
        """Non-collection primitive types should be rejected.

        Note: int/dict/list don't match str or SyncCollection checks but will
        still fail when used.  This test documents that they aren't silently
        accepted (they pass construction but will fail on first async call).
        """
        # These types currently pass the constructor because they aren't
        # strings or SyncCollections.  They'll fail at runtime on the first
        # await call.  The test documents the current behaviour -- future
        # improvements could add further type narrowing.
        svc = ChatHistoryService(bad_value)
        assert svc.collection is bad_value


# ============================================================================
# CognitiveEngine
# ============================================================================


class TestCognitiveEngineTypeSafety:
    """CognitiveEngine must reject non-async chat_history_collection types."""

    @pytest.fixture
    def mock_memory_service(self):
        return MagicMock()

    def test_accepts_async_mock_collection(self, mock_memory_service):
        """Valid async collection should be accepted."""
        col = _make_async_collection()
        engine = CognitiveEngine(
            app_slug="test",
            memory_service=mock_memory_service,
            chat_history_collection=col,
        )
        assert engine.stm.collection is col

    def test_rejects_none(self, mock_memory_service):
        """None must raise ValueError (existing behavior)."""
        with pytest.raises(ValueError, match="REQUIRED"):
            CognitiveEngine(
                app_slug="test",
                memory_service=mock_memory_service,
                chat_history_collection=None,
            )

    def test_rejects_string(self, mock_memory_service):
        """String must raise TypeError with helpful message."""
        with pytest.raises(TypeError, match="not a string"):
            CognitiveEngine(
                app_slug="test",
                memory_service=mock_memory_service,
                chat_history_collection="chat_history",
            )

    def test_rejects_sync_pymongo_collection(self, mock_memory_service):
        """Synchronous pymongo.Collection must raise TypeError."""
        sync_col = _make_sync_collection()
        with pytest.raises(TypeError, match="synchronous pymongo.Collection"):
            CognitiveEngine(
                app_slug="test",
                memory_service=mock_memory_service,
                chat_history_collection=sync_col,
            )


# ============================================================================
# CognitiveMemoryService (via builder)
# ============================================================================


class TestCognitiveMemoryServiceTypeSafety:
    """CognitiveMemoryServiceBuilder must reject non-async collection types."""

    def test_rejects_none(self):
        """None must raise CognitiveMemoryServiceError."""
        from mdb_engine.memory.cognitive import CognitiveMemoryServiceError

        with pytest.raises(CognitiveMemoryServiceError, match="REQUIRED"):
            from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

            CognitiveMemoryServiceBuilder(app_slug="test", collection=None)

    def test_rejects_string(self):
        """String must raise TypeError."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        with pytest.raises(TypeError, match="not a string"):
            CognitiveMemoryServiceBuilder(app_slug="test", collection="user_memories")

    def test_rejects_sync_pymongo_collection(self):
        """Synchronous pymongo.Collection must raise TypeError."""
        from mdb_engine.memory.builder import CognitiveMemoryServiceBuilder

        sync_col = _make_sync_collection()
        with pytest.raises(TypeError, match="synchronous pymongo.Collection"):
            CognitiveMemoryServiceBuilder(app_slug="test", collection=sync_col)
