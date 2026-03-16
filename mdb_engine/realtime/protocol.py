"""
Realtime subscription protocol — message types and builders.

Client -> Server:
    {"type": "subscribe",   "collection": "tasks"}
    {"type": "unsubscribe", "collection": "tasks"}

Server -> Client:
    {"type": "subscribed",   "collection": "tasks"}
    {"type": "unsubscribed", "collection": "tasks"}
    {"type": "change", "collection": "tasks", "operation": "insert", ...}
    {"type": "error",  "message": "..."}
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ── Client message types ────────────────────────────────────────────────

MSG_SUBSCRIBE = "subscribe"
MSG_UNSUBSCRIBE = "unsubscribe"

CLIENT_MESSAGE_TYPES = frozenset({MSG_SUBSCRIBE, MSG_UNSUBSCRIBE})

# ── Server message types ────────────────────────────────────────────────

MSG_SUBSCRIBED = "subscribed"
MSG_UNSUBSCRIBED = "unsubscribed"
MSG_CHANGE = "change"
MSG_ERROR = "error"


# ── Builders ────────────────────────────────────────────────────────────


def subscribed_msg(collection: str) -> dict[str, Any]:
    return {"type": MSG_SUBSCRIBED, "collection": collection}


def unsubscribed_msg(collection: str) -> dict[str, Any]:
    return {"type": MSG_UNSUBSCRIBED, "collection": collection}


def error_msg(message: str) -> dict[str, Any]:
    return {"type": MSG_ERROR, "message": message}


def change_msg(
    *,
    collection: str,
    operation: str,
    document_id: str,
    document: dict[str, Any] | None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build a ``change`` event to push to subscribers."""
    return {
        "type": MSG_CHANGE,
        "collection": collection,
        "operation": operation,
        "document_id": document_id,
        "document": document,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


# ── Parsing helpers ─────────────────────────────────────────────────────

_COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def parse_client_message(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """Parse a client WebSocket message.

    Returns:
        ``(message_type, collection)`` or ``(None, None)`` if invalid.
    """
    msg_type = raw.get("type")
    if msg_type not in CLIENT_MESSAGE_TYPES:
        return None, None
    collection = raw.get("collection")
    if not isinstance(collection, str) or not _COLLECTION_NAME_RE.match(collection):
        return msg_type, None
    return msg_type, collection
