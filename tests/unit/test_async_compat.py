"""
Unit tests for memory async compatibility helpers.
"""

import pytest

from mdb_engine.memory._async_compat import cursor_to_list, maybe_await


class TestMaybeAwait:
    """Tests for maybe_await()."""

    @pytest.mark.asyncio
    async def test_awaits_coroutine(self):
        async def coro():
            return 42

        result = await maybe_await(coro())
        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_plain_value(self):
        result = await maybe_await(42)
        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_none(self):
        result = await maybe_await(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_list(self):
        result = await maybe_await([1, 2, 3])
        assert result == [1, 2, 3]


class TestCursorToList:
    """Tests for cursor_to_list() sync/async handling."""

    @pytest.mark.asyncio
    async def test_async_cursor(self):
        """Motor async cursor: to_list returns a coroutine."""

        class AsyncCursor:
            async def to_list(self, length=None):
                return [{"a": 1}, {"a": 2}]

        result = await cursor_to_list(AsyncCursor(), limit=10)
        assert result == [{"a": 1}, {"a": 2}]

    @pytest.mark.asyncio
    async def test_sync_cursor(self):
        """PyMongo sync cursor: to_list returns a plain list."""

        class SyncCursor:
            def to_list(self, length=None):
                return [{"a": 1}, {"a": 2}]

        result = await cursor_to_list(SyncCursor(), limit=10)
        assert result == [{"a": 1}, {"a": 2}]

    @pytest.mark.asyncio
    async def test_plain_iterable(self):
        """Fallback: plain list without to_list method."""
        result = await cursor_to_list([1, 2, 3])
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_empty_async_cursor(self):
        class AsyncCursor:
            async def to_list(self, length=None):
                return []

        result = await cursor_to_list(AsyncCursor())
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_passed_through(self):
        """Verify limit argument is forwarded to to_list."""
        received_length = None

        class TrackingCursor:
            async def to_list(self, length=None):
                nonlocal received_length
                received_length = length
                return []

        await cursor_to_list(TrackingCursor(), limit=25)
        assert received_length == 25

    @pytest.mark.asyncio
    async def test_limit_none_by_default(self):
        received_length = "NOT_CALLED"

        class TrackingCursor:
            async def to_list(self, length=None):
                nonlocal received_length
                received_length = length
                return []

        await cursor_to_list(TrackingCursor())
        assert received_length is None
