"""
mdb_engine.realtime — Change Stream → WebSocket realtime subscriptions.

Public API:
    ChangeStreamWatcher  — background task that watches a MongoDB database
    RealtimeManager      — subscription tracking + event dispatch
    create_realtime_endpoint — builds the ``/ws/realtime`` handler
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from .manager import RealtimeManager
from .protocol import (
    MSG_SUBSCRIBE,
    MSG_UNSUBSCRIBE,
    error_msg,
    parse_client_message,
    subscribed_msg,
    unsubscribed_msg,
)
from .watcher import ChangeStreamWatcher, WatcherState

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

__all__ = [
    "ChangeStreamWatcher",
    "RealtimeManager",
    "WatcherState",
    "create_realtime_endpoint",
    "register_realtime",
]

REALTIME_WS_PATH = "/ws/realtime"


# ── WebSocket endpoint factory ───────────────────────────────────────


def create_realtime_endpoint(
    manager: RealtimeManager,
    app_slug: str,
) -> Any:
    """Return an async WebSocket handler for realtime subscriptions.

    The handler:
    1. Authenticates via the existing ticket-based WebSocket auth.
    2. Accepts the connection.
    3. Listens for ``subscribe`` / ``unsubscribe`` messages.
    4. Pushes change events via :class:`RealtimeManager`.
    """

    async def realtime_ws(websocket: WebSocket) -> None:
        # Ticket-based auth (reuse engine infrastructure)
        user_id: str | None = None
        user_email: str | None = None
        try:
            from ..routing.websockets import authenticate_websocket

            user_id, user_email = await authenticate_websocket(websocket, require_auth=True)
        except (ValueError, KeyError, RuntimeError, OSError):
            try:
                await websocket.close(code=4401, reason="Authentication required")
            except (OSError, RuntimeError):
                pass
            return

        if not user_id:
            try:
                await websocket.close(code=4401, reason="Authentication required")
            except (OSError, RuntimeError):
                pass
            return

        await websocket.accept()
        logger.info("Realtime WS connected: user=%s app=%s", user_email or user_id, app_slug)

        try:
            while True:
                raw_text = await websocket.receive_text()
                try:
                    data = json.loads(raw_text)
                except (json.JSONDecodeError, TypeError):
                    await websocket.send_json(error_msg("Invalid JSON"))
                    continue

                msg_type, collection = parse_client_message(data)

                if msg_type is None:
                    await websocket.send_json(error_msg("Unknown message type"))
                    continue

                if collection is None:
                    await websocket.send_json(error_msg("Invalid or missing collection name"))
                    continue

                if msg_type == MSG_SUBSCRIBE:
                    if not manager.is_realtime_collection(app_slug, collection):
                        await websocket.send_json(error_msg(f"Collection '{collection}' does not support realtime"))
                        continue
                    ok = manager.subscribe(websocket, app_slug, collection)
                    if ok:
                        await websocket.send_json(subscribed_msg(collection))
                    else:
                        await websocket.send_json(error_msg("Subscription limit reached"))

                elif msg_type == MSG_UNSUBSCRIBE:
                    manager.unsubscribe(websocket, app_slug, collection)
                    await websocket.send_json(unsubscribed_msg(collection))

        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Realtime WS error for user=%s", user_id)
        finally:
            manager.disconnect(websocket)
            logger.debug("Realtime WS disconnected: user=%s", user_id)

    return realtime_ws


# ── Convenience: wire everything from a manifest ─────────────────────


def register_realtime(
    app: FastAPI,
    slug: str,
    collections_cfg: dict[str, dict[str, Any]],
    manager: RealtimeManager,
) -> None:
    """Register the ``/ws/realtime`` endpoint on *app* if there are realtime collections."""
    realtime_names = [name for name, cfg in collections_cfg.items() if cfg.get("realtime", False)]
    if not realtime_names:
        return

    handler = create_realtime_endpoint(manager, slug)
    app.websocket(REALTIME_WS_PATH)(handler)
    logger.info(
        "Registered realtime WebSocket at %s for %d collection(s): %s",
        REALTIME_WS_PATH,
        len(realtime_names),
        ", ".join(realtime_names),
    )
