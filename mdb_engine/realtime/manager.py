"""
RealtimeManager — subscription tracking and Change Stream event dispatch.

Maps physical MongoDB collection names (e.g. ``my_app_tasks``) back to
``(app_slug, logical_name)`` and routes change events to the correct
WebSocket subscribers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from ..routing._serialization import serialize_doc
from .protocol import change_msg

logger = logging.getLogger(__name__)

DEFAULT_MAX_SUBS_PER_CONN = 20

# Fields stripped from outgoing documents (internal to the engine).
_STRIP_FIELDS = frozenset({"app_id"})


class RealtimeManager:
    """Track subscriptions and dispatch Change Stream events to WebSocket clients."""

    def __init__(
        self,
        collection_map: dict[str, tuple[str, str]],
        *,
        max_subs_per_conn: int = DEFAULT_MAX_SUBS_PER_CONN,
    ) -> None:
        """
        Args:
            collection_map: ``{physical_name: (app_slug, logical_name)}``.
            max_subs_per_conn: Max collections a single connection may subscribe to.
        """
        self._collection_map = dict(collection_map)
        self._max_subs = max_subs_per_conn

        # (app_slug, logical_name) -> set of WebSocket connections
        self._subscriptions: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)
        # WebSocket -> set of (app_slug, logical_name) keys
        self._conn_subs: dict[int, set[tuple[str, str]]] = defaultdict(set)

    # ── Subscription management ──────────────────────────────────────

    def _ws_key(self, ws: WebSocket) -> int:
        return id(ws)

    @property
    def realtime_collections(self) -> set[str]:
        """Logical collection names that have realtime enabled."""
        return {logical for _, logical in self._collection_map.values()}

    def is_realtime_collection(self, app_slug: str, collection: str) -> bool:
        physical = f"{app_slug}_{collection}"
        return physical in self._collection_map

    def subscribe(self, ws: WebSocket, app_slug: str, collection: str) -> bool:
        """Subscribe *ws* to changes on *collection* within *app_slug*.

        Returns ``False`` if the connection has hit its subscription limit.
        """
        key = (app_slug, collection)
        ws_id = self._ws_key(ws)

        if key in self._conn_subs.get(ws_id, set()):
            return True  # already subscribed

        if len(self._conn_subs[ws_id]) >= self._max_subs:
            return False

        self._subscriptions[key].add(ws)
        self._conn_subs[ws_id].add(key)
        logger.debug("WS %s subscribed to %s/%s", ws_id, app_slug, collection)
        return True

    def unsubscribe(self, ws: WebSocket, app_slug: str, collection: str) -> None:
        key = (app_slug, collection)
        ws_id = self._ws_key(ws)
        self._subscriptions[key].discard(ws)
        self._conn_subs.get(ws_id, set()).discard(key)

    def disconnect(self, ws: WebSocket) -> None:
        """Remove all subscriptions for a disconnected client."""
        ws_id = self._ws_key(ws)
        for key in list(self._conn_subs.pop(ws_id, set())):
            self._subscriptions[key].discard(ws)

    # ── Event dispatch ───────────────────────────────────────────────

    async def dispatch(self, event: dict[str, Any]) -> int:
        """Route a raw Change Stream event to matching subscribers.

        Returns the number of clients that received the message.
        """
        ns = event.get("ns", {})
        physical = ns.get("coll")
        if not physical or physical not in self._collection_map:
            return 0

        app_slug, logical = self._collection_map[physical]
        key = (app_slug, logical)
        subscribers = self._subscriptions.get(key)
        if not subscribers:
            return 0

        operation = event.get("operationType", "unknown")
        document_id, document = self._extract_document(event, operation)

        msg = change_msg(
            collection=logical,
            operation=operation,
            document_id=document_id,
            document=document,
        )

        sent = 0
        dead: list[WebSocket] = []
        for ws in list(subscribers):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(msg)
                    sent += 1
                else:
                    dead.append(ws)
            except (OSError, RuntimeError):
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        return sent

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_document(event: dict[str, Any], operation: str) -> tuple[str, dict[str, Any] | None]:
        """Pull ``document_id`` and ``document`` from a Change Stream event."""
        doc_key = event.get("documentKey", {})
        document_id = str(doc_key.get("_id", ""))

        if operation in ("insert", "replace"):
            raw = event.get("fullDocument") or {}
        elif operation == "update":
            ud = event.get("updateDescription", {})
            raw = ud.get("updatedFields") or {}
        else:
            raw = None

        if raw is not None:
            cleaned = {k: v for k, v in raw.items() if k not in _STRIP_FIELDS}
            return document_id, serialize_doc(cleaned)
        return document_id, None

    @property
    def subscription_count(self) -> int:
        return sum(len(subs) for subs in self._subscriptions.values())
