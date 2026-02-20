"""Tests for mdb_engine.routing.websockets — RoomManager and authenticated_websocket."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRoomManager:
    """Tests for the RoomManager class."""

    def test_initial_state(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        assert rm.room_names == []
        assert rm.connection_count() == 0

    async def test_connect_and_disconnect(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        ws = AsyncMock()
        await rm.connect(ws, "room1")
        ws.accept.assert_called_once()
        assert rm.connection_count("room1") == 1

        rm.disconnect(ws, "room1")
        assert rm.connection_count("room1") == 0
        assert "room1" not in rm.room_names

    async def test_multiple_connections_in_room(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await rm.connect(ws1, "room1")
        await rm.connect(ws2, "room1")
        assert rm.connection_count("room1") == 2

    async def test_broadcast_to_room(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await rm.connect(ws1, "room1")
        await rm.connect(ws2, "room1")

        sent = await rm.broadcast("room1", {"type": "msg", "text": "hello"})
        assert sent == 2
        ws1.send_json.assert_called_once_with({"type": "msg", "text": "hello"})
        ws2.send_json.assert_called_once_with({"type": "msg", "text": "hello"})

    async def test_broadcast_to_empty_room(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        sent = await rm.broadcast("nonexistent", {"data": 1})
        assert sent == 0

    async def test_broadcast_removes_dead_connections(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        ws_good = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json.side_effect = RuntimeError("connection closed")

        await rm.connect(ws_good, "room1")
        await rm.connect(ws_dead, "room1")

        sent = await rm.broadcast("room1", {"ping": True})
        assert sent == 1
        assert rm.connection_count("room1") == 1

    def test_total_connection_count(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        rm._rooms["a"] = {MagicMock(), MagicMock()}
        rm._rooms["b"] = {MagicMock()}
        assert rm.connection_count() == 3

    async def test_multiple_rooms(self):
        from mdb_engine.routing.websockets import RoomManager

        rm = RoomManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await rm.connect(ws1, "room-a")
        await rm.connect(ws2, "room-b")

        assert set(rm.room_names) == {"room-a", "room-b"}
        assert rm.connection_count("room-a") == 1
        assert rm.connection_count("room-b") == 1


class TestAuthenticatedWebsocket:
    """Tests for the authenticated_websocket decorator."""

    async def test_closes_on_missing_cookie(self):
        from mdb_engine.routing.websockets import authenticated_websocket

        @authenticated_websocket
        async def handler(ws, user):
            pass

        ws = AsyncMock()
        ws.cookies = {}
        await handler(ws)
        ws.close.assert_called_once()
        assert ws.close.call_args[1].get("code", ws.close.call_args[0][0] if ws.close.call_args[0] else None) == 4001

    async def test_closes_on_no_pool(self):
        from mdb_engine.routing.websockets import authenticated_websocket

        @authenticated_websocket
        async def handler(ws, user):
            pass

        ws = AsyncMock()
        ws.cookies = {"mdb_auth_token": "a.b.c"}
        ws.app = MagicMock()
        ws.app.state = MagicMock(spec=[])

        await handler(ws)
        ws.close.assert_called_once()

    async def test_closes_on_invalid_token(self):
        from mdb_engine.routing.websockets import authenticated_websocket

        @authenticated_websocket
        async def handler(ws, user):
            pass

        ws = AsyncMock()
        ws.cookies = {"mdb_auth_token": "a.b.c"}
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value=None)
        ws.app = MagicMock()
        ws.app.state.user_pool = pool

        await handler(ws)
        ws.close.assert_called_once()

    async def test_calls_handler_on_valid_token(self):
        from mdb_engine.routing.websockets import authenticated_websocket

        called_with = {}

        @authenticated_websocket
        async def handler(ws, user):
            called_with["user"] = user

        ws = AsyncMock()
        ws.cookies = {"mdb_auth_token": "a.b.c"}
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value={"email": "u@t.com", "_id": "u1"})
        ws.app = MagicMock()
        ws.app.state.user_pool = pool

        await handler(ws)
        assert called_with["user"]["email"] == "u@t.com"


class TestWebSocketOps:
    """Tests for WebSocketMixin internals."""

    def _make_mixin(self, ticket_store=None, session_manager=None, reg_manager=None, svc_init=None):
        from mdb_engine.core.websocket_ops import WebSocketMixin

        obj = object.__new__(WebSocketMixin)
        obj._websocket_ticket_store = ticket_store
        obj._websocket_session_manager = session_manager
        obj._app_registration_manager = reg_manager
        obj._service_initializer = svc_init
        return obj

    def test_register_routes_raises_without_ticket_store(self):
        """RuntimeError when ticket store is missing."""
        import pytest

        mixin = self._make_mixin(ticket_store=None)
        app = MagicMock()
        with pytest.raises(RuntimeError, match="websocket_ticket_store is not available"):
            mixin.register_websocket_routes(app, "my_app")

    def test_auth_fallback_to_app_level_auth_policy(self):
        """When endpoint has no auth config, falls back to manifest auth_policy."""
        svc_init = MagicMock()
        svc_init.get_websocket_config.return_value = {"chat": {"path": "/ws/chat"}}
        reg_manager = MagicMock()
        reg_manager.get_app.return_value = {"auth_policy": {"required": False}}
        ticket_store = MagicMock()
        mixin = self._make_mixin(
            ticket_store=ticket_store,
            reg_manager=reg_manager,
            svc_init=svc_init,
        )
        app = MagicMock()
        app.state = MagicMock(spec=[])
        app.state.websocket_ticket_store = None

        mixin.register_websocket_routes(app, "my_app")
        reg_manager.get_app.assert_called_with("my_app")

    @pytest.mark.asyncio
    async def test_ticket_ttl_configured_from_manifest(self):
        """Ticket TTL is taken from manifest websockets config."""
        mixin = self._make_mixin(ticket_store=MagicMock())
        mixin._websocket_ticket_store.ticket_ttl = 60

        app = MagicMock()
        manifest = {
            "websockets": {
                "chat": {"path": "/ws/chat", "ticket_ttl_seconds": 30},
                "feed": {"path": "/ws/feed", "ticket_ttl_seconds": 45},
            }
        }
        with patch("mdb_engine.auth.websocket_tickets.WebSocketTicketStore") as MockStore:
            MockStore.return_value = MagicMock(ticket_ttl=30)
            await mixin._configure_websocket_ticket_ttl(app, manifest, "my_app")
            MockStore.assert_called_once_with(ticket_ttl_seconds=30)
