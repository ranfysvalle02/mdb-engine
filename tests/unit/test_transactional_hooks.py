"""Tests for transactional hook mode (unit-level with mocks)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mdb_engine.routing._hooks import TransactionalHookExecutor


class FakeCollection:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake_id")


class FakeDB:
    def __init__(self):
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]


class TestTransactionalHookExecutor:
    @pytest.mark.asyncio
    async def test_runs_hooks_in_transaction(self):
        hooks = {
            "after_create": [
                {"action": "insert", "collection": "audit", "document": {"event": "created"}},
            ]
        }

        mock_session = AsyncMock()
        mock_session.start_transaction = MagicMock()
        mock_session.commit_transaction = AsyncMock()
        mock_session.abort_transaction = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.start_session = AsyncMock(return_value=mock_session)

        executor = TransactionalHookExecutor(hooks, mock_client)
        db = FakeDB()
        await executor.run("after_create", {"_id": "d1"}, None, db)

        mock_client.start_session.assert_called_once()
        mock_session.start_transaction.assert_called_once()
        mock_session.commit_transaction.assert_called_once()
        assert len(db["audit"].inserted) == 1

    @pytest.mark.asyncio
    async def test_no_actions_skips_transaction(self):
        hooks = {}
        mock_client = MagicMock()
        mock_client.start_session = AsyncMock()

        executor = TransactionalHookExecutor(hooks, mock_client)
        db = FakeDB()
        await executor.run("after_create", {"_id": "d1"}, None, db)

        mock_client.start_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_transaction_aborts_on_error(self):
        """On hook failure, transaction should abort."""
        hooks = {
            "after_create": [
                {"action": "insert", "collection": "audit", "document": {"event": "created"}},
            ]
        }

        mock_session = AsyncMock()
        mock_session.start_transaction = MagicMock()
        mock_session.commit_transaction = AsyncMock()
        mock_session.abort_transaction = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.start_session = AsyncMock(return_value=mock_session)

        executor = TransactionalHookExecutor(hooks, mock_client)
        db = FakeDB()

        async def failing_insert(doc):
            raise RuntimeError("Write failed")

        db["audit"].insert_one = failing_insert

        with pytest.raises(RuntimeError, match="Write failed"):
            await executor.run("after_create", {"_id": "d1"}, None, db)

        mock_session.abort_transaction.assert_called_once()
        mock_session.commit_transaction.assert_not_called()
