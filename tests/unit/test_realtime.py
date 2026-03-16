"""
Unit tests for the realtime module (Change Stream → WebSocket).

Covers:
- Protocol: message parsing, builders, collection name validation
- RealtimeManager: subscribe, unsubscribe, disconnect, dispatch, limits,
  cross-app isolation, app_id stripping
- ChangeStreamWatcher: start/stop lifecycle, replica-set detection,
  dispatch delegation, resume token tracking
- Health check: check_realtime_health for each watcher state
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from mdb_engine.realtime.manager import RealtimeManager
from mdb_engine.realtime.protocol import (
    MSG_CHANGE,
    MSG_ERROR,
    MSG_SUBSCRIBE,
    MSG_SUBSCRIBED,
    MSG_UNSUBSCRIBE,
    MSG_UNSUBSCRIBED,
    change_msg,
    error_msg,
    parse_client_message,
    subscribed_msg,
    unsubscribed_msg,
)
from mdb_engine.realtime.watcher import ChangeStreamWatcher, WatcherState

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_ws(*, connected: bool = True) -> MagicMock:
    """Build a fake WebSocket with send_json and client_state."""
    from starlette.websockets import WebSocketState

    ws = MagicMock()
    ws.send_json = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
    return ws


def _insert_event(
    physical_coll: str,
    doc: dict[str, Any] | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal Change Stream insert event."""
    oid = doc_id or str(ObjectId())
    return {
        "operationType": "insert",
        "ns": {"coll": physical_coll, "db": "test"},
        "documentKey": {"_id": ObjectId(oid) if len(oid) == 24 else oid},
        "fullDocument": doc or {"_id": oid, "title": "Hello", "app_id": "my_app"},
    }


def _update_event(physical_coll: str, doc_id: str | None = None) -> dict[str, Any]:
    oid = doc_id or str(ObjectId())
    return {
        "operationType": "update",
        "ns": {"coll": physical_coll, "db": "test"},
        "documentKey": {"_id": ObjectId(oid) if len(oid) == 24 else oid},
        "updateDescription": {
            "updatedFields": {"title": "Updated", "app_id": "my_app"},
            "removedFields": [],
        },
    }


def _delete_event(physical_coll: str, doc_id: str | None = None) -> dict[str, Any]:
    oid = doc_id or str(ObjectId())
    return {
        "operationType": "delete",
        "ns": {"coll": physical_coll, "db": "test"},
        "documentKey": {"_id": ObjectId(oid) if len(oid) == 24 else oid},
    }


# ═══════════════════════════════════════════════════════════════════════
# Protocol
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolBuilders:
    def test_subscribed_msg(self):
        msg = subscribed_msg("tasks")
        assert msg == {"type": MSG_SUBSCRIBED, "collection": "tasks"}

    def test_unsubscribed_msg(self):
        msg = unsubscribed_msg("tasks")
        assert msg == {"type": MSG_UNSUBSCRIBED, "collection": "tasks"}

    def test_error_msg(self):
        msg = error_msg("oops")
        assert msg == {"type": MSG_ERROR, "message": "oops"}

    def test_change_msg(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        msg = change_msg(
            collection="tasks",
            operation="insert",
            document_id="abc",
            document={"title": "Hi"},
            timestamp=ts,
        )
        assert msg["type"] == MSG_CHANGE
        assert msg["collection"] == "tasks"
        assert msg["operation"] == "insert"
        assert msg["document_id"] == "abc"
        assert msg["document"] == {"title": "Hi"}
        assert "2026-01-01" in msg["timestamp"]

    def test_change_msg_auto_timestamp(self):
        msg = change_msg(
            collection="tasks",
            operation="delete",
            document_id="x",
            document=None,
        )
        assert msg["timestamp"]  # auto-generated


class TestProtocolParsing:
    def test_subscribe(self):
        t, c = parse_client_message({"type": "subscribe", "collection": "tasks"})
        assert t == MSG_SUBSCRIBE
        assert c == "tasks"

    def test_unsubscribe(self):
        t, c = parse_client_message({"type": "unsubscribe", "collection": "tasks"})
        assert t == MSG_UNSUBSCRIBE
        assert c == "tasks"

    def test_unknown_type(self):
        t, c = parse_client_message({"type": "bogus", "collection": "tasks"})
        assert t is None
        assert c is None

    def test_missing_collection(self):
        t, c = parse_client_message({"type": "subscribe"})
        assert t == MSG_SUBSCRIBE
        assert c is None

    def test_invalid_collection_name(self):
        t, c = parse_client_message({"type": "subscribe", "collection": "$bad"})
        assert t == MSG_SUBSCRIBE
        assert c is None

    def test_collection_with_underscore(self):
        t, c = parse_client_message({"type": "subscribe", "collection": "user_tasks"})
        assert c == "user_tasks"

    def test_collection_starting_with_number_rejected(self):
        t, c = parse_client_message({"type": "subscribe", "collection": "1tasks"})
        assert c is None

    def test_empty_dict(self):
        t, c = parse_client_message({})
        assert t is None


# ═══════════════════════════════════════════════════════════════════════
# RealtimeManager
# ═══════════════════════════════════════════════════════════════════════


class TestRealtimeManagerSubscriptions:
    def _manager(self, **extra_map):
        cmap = {"my_app_tasks": ("my_app", "tasks"), "my_app_comments": ("my_app", "comments")}
        cmap.update(extra_map)
        return RealtimeManager(cmap)

    def test_subscribe_and_count(self):
        m = self._manager()
        ws = _make_ws()
        assert m.subscribe(ws, "my_app", "tasks") is True
        assert m.subscription_count == 1

    def test_subscribe_idempotent(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")
        m.subscribe(ws, "my_app", "tasks")
        assert m.subscription_count == 1

    def test_unsubscribe(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")
        m.unsubscribe(ws, "my_app", "tasks")
        assert m.subscription_count == 0

    def test_disconnect_removes_all(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")
        m.subscribe(ws, "my_app", "comments")
        assert m.subscription_count == 2
        m.disconnect(ws)
        assert m.subscription_count == 0

    def test_max_subscriptions_enforced(self):
        m = RealtimeManager(
            {f"my_app_c{i}": ("my_app", f"c{i}") for i in range(5)},
            max_subs_per_conn=3,
        )
        ws = _make_ws()
        assert m.subscribe(ws, "my_app", "c0") is True
        assert m.subscribe(ws, "my_app", "c1") is True
        assert m.subscribe(ws, "my_app", "c2") is True
        assert m.subscribe(ws, "my_app", "c3") is False
        assert m.subscription_count == 3

    def test_is_realtime_collection(self):
        m = self._manager()
        assert m.is_realtime_collection("my_app", "tasks") is True
        assert m.is_realtime_collection("my_app", "unknown") is False
        assert m.is_realtime_collection("other_app", "tasks") is False

    def test_cross_app_isolation(self):
        m = RealtimeManager(
            {
                "app1_tasks": ("app1", "tasks"),
                "app2_tasks": ("app2", "tasks"),
            }
        )
        ws1 = _make_ws()
        ws2 = _make_ws()
        m.subscribe(ws1, "app1", "tasks")
        m.subscribe(ws2, "app2", "tasks")
        assert m.subscription_count == 2


class TestRealtimeManagerDispatch:
    def _manager(self):
        return RealtimeManager(
            {
                "my_app_tasks": ("my_app", "tasks"),
                "my_app_comments": ("my_app", "comments"),
            }
        )

    @pytest.mark.asyncio
    async def test_dispatch_insert_to_subscriber(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")

        event = _insert_event("my_app_tasks")
        sent = await m.dispatch(event)

        assert sent == 1
        ws.send_json.assert_called_once()
        msg = ws.send_json.call_args[0][0]
        assert msg["type"] == MSG_CHANGE
        assert msg["collection"] == "tasks"
        assert msg["operation"] == "insert"
        assert "app_id" not in msg["document"]

    @pytest.mark.asyncio
    async def test_dispatch_update_strips_app_id(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")

        event = _update_event("my_app_tasks")
        await m.dispatch(event)

        msg = ws.send_json.call_args[0][0]
        assert msg["operation"] == "update"
        assert "app_id" not in (msg["document"] or {})

    @pytest.mark.asyncio
    async def test_dispatch_delete_has_null_document(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")

        event = _delete_event("my_app_tasks")
        await m.dispatch(event)

        msg = ws.send_json.call_args[0][0]
        assert msg["operation"] == "delete"
        assert msg["document"] is None

    @pytest.mark.asyncio
    async def test_dispatch_to_correct_collection_only(self):
        m = self._manager()
        ws_tasks = _make_ws()
        ws_comments = _make_ws()
        m.subscribe(ws_tasks, "my_app", "tasks")
        m.subscribe(ws_comments, "my_app", "comments")

        event = _insert_event("my_app_tasks")
        await m.dispatch(event)

        ws_tasks.send_json.assert_called_once()
        ws_comments.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_ignores_unknown_collection(self):
        m = self._manager()
        ws = _make_ws()
        m.subscribe(ws, "my_app", "tasks")

        event = _insert_event("other_app_logs")
        sent = await m.dispatch(event)

        assert sent == 0
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_removes_dead_connections(self):
        m = self._manager()
        ws = _make_ws(connected=False)
        m.subscribe(ws, "my_app", "tasks")

        event = _insert_event("my_app_tasks")
        sent = await m.dispatch(event)

        assert sent == 0
        assert m.subscription_count == 0

    @pytest.mark.asyncio
    async def test_dispatch_to_multiple_subscribers(self):
        m = self._manager()
        ws1 = _make_ws()
        ws2 = _make_ws()
        m.subscribe(ws1, "my_app", "tasks")
        m.subscribe(ws2, "my_app", "tasks")

        event = _insert_event("my_app_tasks")
        sent = await m.dispatch(event)

        assert sent == 2

    @pytest.mark.asyncio
    async def test_cross_app_dispatch_isolation(self):
        m = RealtimeManager(
            {
                "app1_tasks": ("app1", "tasks"),
                "app2_tasks": ("app2", "tasks"),
            }
        )
        ws1 = _make_ws()
        ws2 = _make_ws()
        m.subscribe(ws1, "app1", "tasks")
        m.subscribe(ws2, "app2", "tasks")

        event = _insert_event("app1_tasks")
        await m.dispatch(event)

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# ChangeStreamWatcher
# ═══════════════════════════════════════════════════════════════════════


class _FakeChangeStream:
    """Async context manager that yields a list of events then stops."""

    def __init__(self, events: list[dict], resume_token: str = "tok123"):
        self._events = list(events)
        self.resume_token = resume_token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class TestChangeStreamWatcherLifecycle:
    def _make_watcher(self, db_mock=None, manager_mock=None) -> ChangeStreamWatcher:
        db = db_mock or MagicMock()
        manager = manager_mock or MagicMock()
        manager.dispatch = AsyncMock()
        return ChangeStreamWatcher(
            db=db,
            manager=manager,
            watched_collections={"my_app": {"my_app_tasks"}},
        )

    def test_initial_state_is_idle(self):
        w = self._make_watcher()
        assert w.state == WatcherState.IDLE
        assert w.running is False

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        w = self._make_watcher()
        db_mock = w._db

        events = [_insert_event("my_app_tasks")]
        db_mock.watch = MagicMock(return_value=_FakeChangeStream(events))

        await w.start()
        assert w._task is not None

        await asyncio.sleep(0.05)
        await w.stop()

        assert w.state == WatcherState.STOPPED
        assert w.running is False

    @pytest.mark.asyncio
    async def test_dispatches_events(self):
        manager = MagicMock()
        manager.dispatch = AsyncMock()
        db = MagicMock()

        events = [_insert_event("my_app_tasks"), _insert_event("my_app_tasks")]
        db.watch = MagicMock(return_value=_FakeChangeStream(events))

        w = ChangeStreamWatcher(
            db=db,
            manager=manager,
            watched_collections={"my_app": {"my_app_tasks"}},
        )
        await w.start()
        await asyncio.sleep(0.05)
        await w.stop()

        assert manager.dispatch.call_count == 2

    @pytest.mark.asyncio
    async def test_replica_set_error_marks_failed(self):
        from pymongo.errors import OperationFailure

        db = MagicMock()
        db.watch = MagicMock(side_effect=OperationFailure("The $changeStream stage is only supported on replica sets"))

        w = self._make_watcher(db_mock=db)
        await w.start()
        await asyncio.sleep(0.05)

        assert w.state == WatcherState.FAILED
        assert w.running is False

    @pytest.mark.asyncio
    async def test_stop_on_idle_is_noop(self):
        w = self._make_watcher()
        await w.stop()
        assert w.state == WatcherState.STOPPED


# ═══════════════════════════════════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════════════════════════════════


class TestRealtimeHealthCheck:
    @pytest.mark.asyncio
    async def test_none_watcher(self):
        from mdb_engine.observability.health import HealthStatus, check_realtime_health

        result = await check_realtime_health(None)
        assert result.status is HealthStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_running_watcher(self):
        from mdb_engine.observability.health import HealthStatus, check_realtime_health

        w = MagicMock()
        w.state = WatcherState.RUNNING
        w._manager = MagicMock()
        w._manager.subscription_count = 5

        result = await check_realtime_health(w)
        assert result.status is HealthStatus.HEALTHY
        assert "5 subscription" in result.message

    @pytest.mark.asyncio
    async def test_reconnecting_watcher(self):
        from mdb_engine.observability.health import HealthStatus, check_realtime_health

        w = MagicMock()
        w.state = WatcherState.RECONNECTING
        w._manager = MagicMock()
        w._manager.subscription_count = 0

        result = await check_realtime_health(w)
        assert result.status is HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_failed_watcher(self):
        from mdb_engine.observability.health import HealthStatus, check_realtime_health

        w = MagicMock()
        w.state = WatcherState.FAILED
        w._manager = MagicMock()
        w._manager.subscription_count = 0

        result = await check_realtime_health(w)
        assert result.status is HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_stopped_watcher(self):
        from mdb_engine.observability.health import HealthStatus, check_realtime_health

        w = MagicMock()
        w.state = WatcherState.STOPPED
        w._manager = MagicMock()
        w._manager.subscription_count = 0

        result = await check_realtime_health(w)
        assert result.status is HealthStatus.UNKNOWN
