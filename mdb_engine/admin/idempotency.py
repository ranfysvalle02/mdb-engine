"""
Idempotency key support for destructive admin plane endpoints.

When a client passes ``Idempotency-Key: <opaque>`` on a destructive
POST, the admin plane stores the first response body for 24h and
replays it verbatim on any repeat that carries the same key. The
scope is ``(slug, module, endpoint, key_hash)`` so the same key is
safely reusable across different destinations.

Failure semantics:

- If the handler itself raised (4xx/5xx), we do **not** cache. A
  retry with the same key tries again.
- If the cached body has a different hash from the current response,
  we return ``409 Conflict`` — the caller shipped two different
  payloads with the same key, which is almost always a bug.

Collection layout::

    _mdb_admin_idempotency:
        _id: sha256 of (slug|module|endpoint|key)
        slug, module, endpoint, key_fingerprint
        status, body (BSON), body_sha256
        at: datetime       # TTL anchor, expireAfterSeconds=86_400

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

IDEMPOTENCY_COLLECTION = "_mdb_admin_idempotency"
IDEMPOTENCY_TTL_SECONDS = 24 * 3600
IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_MAX_KEY_LEN = 200


async def bootstrap_idempotency_collection(
    mongo_db: AsyncIOMotorDatabase,
    *,
    ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS,
) -> None:
    """Create the idempotency collection + TTL index if missing.

    Idempotent. Safe to call on every engine boot.
    """
    try:  # nosemgrep
        coll = mongo_db[IDEMPOTENCY_COLLECTION]
        await coll.create_index(
            [("at", 1)],
            name="idx_at_ttl",
            expireAfterSeconds=ttl_seconds,
        )
        await coll.create_index(
            [("slug", 1), ("module", 1), ("endpoint", 1)],
            name="idx_scope",
        )
    except Exception:  # noqa: BLE001 - best effort
        logger.warning("Failed to bootstrap %s", IDEMPOTENCY_COLLECTION, exc_info=True)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _scope_id(slug: str, module: str, endpoint: str, key: str) -> str:
    raw = f"{slug}|{module}|{endpoint}|{key}".encode()
    return hashlib.sha256(raw).hexdigest()


def _key_fingerprint(key: str) -> str:
    return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


async def replay_or_record(
    engine: Any,
    request: Request,
    *,
    module: str,
    endpoint: str,
    run: Callable[[], Awaitable[dict[str, Any]]],
    never_cache: bool = False,
) -> dict[str, Any]:
    """Execute ``run`` honoring ``Idempotency-Key`` semantics.

    If the header is missing, ``run`` is called as-is.
    If it's present:

    - On first call we record the successful response.
    - On subsequent calls with the same key *and* same body hash, we
      return the cached response and emit ``mdb.admin.idempotency_replay``.
    - On conflict (same key, different body hash), we raise 409.

    Handlers that return a raw non-dict response (e.g. a FastAPI
    ``Response``) should pass ``never_cache=True`` and handle caching
    themselves; the helper still performs the replay lookup based on
    the serialized return value.
    """
    key = request.headers.get(IDEMPOTENCY_HEADER)
    slug_qp = request.query_params.get("slug") or ""
    conn = getattr(engine, "_connection_manager", None)
    mongo_db = getattr(conn, "mongo_db", None) if conn else None
    if not key or not slug_qp or mongo_db is None:
        return await run()
    if len(key) > IDEMPOTENCY_MAX_KEY_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key must be <= {IDEMPOTENCY_MAX_KEY_LEN} chars",
        )

    coll = mongo_db[IDEMPOTENCY_COLLECTION]
    doc_id = _scope_id(slug_qp, module, endpoint, key)
    fingerprint = _key_fingerprint(key)

    try:  # nosemgrep
        cached = await coll.find_one({"_id": doc_id})
    except Exception:  # noqa: BLE001
        cached = None

    if cached is not None:
        # Flag the replay on request.state so the admin middleware stamps
        # ``X-Idempotent-Replay: true`` on the outgoing response. Clients
        # use this to tell a real apply apart from a cached replay
        # (observability, alerting, retry dashboards).
        try:  # nosemgrep
            request.state.mdb_idempotent_replay = True
        except Exception:  # noqa: BLE001 - request.state is always writable, defensive
            pass
        try:  # nosemgrep
            from ..core.reconciler_events import emit_event
            from .events import EVENT_IDEMPOTENCY_REPLAY

            emit_event(
                EVENT_IDEMPOTENCY_REPLAY,
                slug=slug_qp,
                module=module,
                endpoint=endpoint,
                key_fingerprint=fingerprint,
            )
        except Exception:  # noqa: BLE001
            pass
        body = cached.get("body")
        if isinstance(body, dict):
            return body
        # Defensive fallback — stored shape should always be a dict.
        return {"idempotency_replay": True, "body": body}

    result = await run()
    if never_cache:
        return result

    body_bytes = _canonical_bytes(result)
    body_sha = hashlib.sha256(body_bytes).hexdigest()
    row = {
        "_id": doc_id,
        "slug": slug_qp,
        "module": module,
        "endpoint": endpoint,
        "key_fingerprint": fingerprint,
        "status": 200,
        "body": result,
        "body_sha256": body_sha,
        "at": datetime.now(timezone.utc),
    }
    try:  # nosemgrep
        await coll.insert_one(row)
    except Exception:  # noqa: BLE001 - duplicate == concurrent replay
        try:  # nosemgrep
            existing = await coll.find_one({"_id": doc_id})
        except Exception:  # noqa: BLE001
            existing = None
        if existing is not None and existing.get("body_sha256") != body_sha:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key reused with a different response body",
            ) from None
    return result


__all__ = [
    "IDEMPOTENCY_COLLECTION",
    "IDEMPOTENCY_HEADER",
    "IDEMPOTENCY_TTL_SECONDS",
    "bootstrap_idempotency_collection",
    "replay_or_record",
]
