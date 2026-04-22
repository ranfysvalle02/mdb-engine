"""
Internal storage bootstrap for the manifest reconciler.

This module owns the engine-internal collections that back the reconciler:

- ``_mdb_manifest_revisions``: append-only revision history per app slug.
- ``_mdb_owned_artifacts``: ledger of engine-owned artifacts (collections,
  indexes, service artifacts) per app slug — the "desired state cache".
- ``_mdb_trash``: tombstones for quarantined artifacts. Deletion is owned
  solely by the trash sweeper; there is no TTL index on the tombstone.
- ``_mdb_manifest_locks``: per-slug advisory lock docs with TTL on the
  **collection** index (bounded hold time) plus explicit fencing tokens
  so lock holders are unambiguous even across pid reuse / containers.
- ``_mdb_meta``: tiny marker collection used to short-circuit bootstrap
  on warm boots.

The functions in this module are idempotent. Failures here are logged
but not fatal — the engine still boots; the reconciler simply degrades
to best-effort.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import (
    CollectionInvalid,
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from ..constants import (
    DEFAULT_MANIFEST_LOCK_TTL_SECONDS,
    MANIFEST_LOCKS_COLLECTION,
    MANIFEST_REVISIONS_COLLECTION,
    OWNED_ARTIFACTS_COLLECTION,
    TRASH_COLLECTION,
)

logger = logging.getLogger(__name__)

# Name of the engine-internal marker collection. Not listed in
# RECONCILER_INTERNAL_COLLECTIONS on purpose — it has no per-slug
# artifacts and is never diffed by the reconciler.
META_COLLECTION: str = "_mdb_meta"

# Monotonic version of the bootstrap layout. Bump when the index shape
# on any internal collection changes so existing deployments rebuild.
BOOTSTRAP_VERSION: int = 2

# Stable per-process nonce used as part of every fencing token so two
# workers that happen to share a pid (containers, PID namespaces) never
# collide. Generated once per process import.
_BOOT_NONCE: str = secrets.token_hex(8)


async def bootstrap_reconciler_collections(
    db: AsyncIOMotorDatabase,
    *,
    force: bool = False,
) -> bool:
    """Create required indexes on the reconciler's internal collections.

    Safe to call multiple times. On warm boots this reads a marker doc in
    ``_mdb_meta`` and short-circuits when the stored ``BOOTSTRAP_VERSION``
    matches the module-level constant, avoiding repeated
    ``createIndexes`` round-trips on every startup (which add noticeable
    latency on Atlas cold clients).

    Args:
        db: Motor database handle (the engine's primary database).
        force: When True, run the full bootstrap regardless of the marker
            doc. Used by migrations and ``--force`` CLI flows.

    Returns:
        True if a full bootstrap ran, False when short-circuited via the
        marker doc. Both outcomes are considered success.
    """
    if not force:
        try:
            marker = await db[META_COLLECTION].find_one({"_id": "__bootstrap__"})
            if marker and int(marker.get("version", 0)) >= BOOTSTRAP_VERSION:
                logger.debug(
                    "Reconciler bootstrap skipped (marker version=%s)",
                    marker.get("version"),
                )
                return False
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.debug("Bootstrap marker probe failed; running full bootstrap: %s", e)

    try:
        # Revisions: one doc per (slug, revision), also index by slug for history scans.
        revisions = db[MANIFEST_REVISIONS_COLLECTION]
        await revisions.create_index(
            [("slug", ASCENDING), ("revision", DESCENDING)],
            name="slug_revision_idx",
            unique=True,
        )
        await revisions.create_index(
            [("slug", ASCENDING), ("applied_at", DESCENDING)],
            name="slug_applied_at_idx",
        )
        await revisions.create_index(
            [("hash", ASCENDING)],
            name="hash_idx",
        )

        # Ledger: one doc per (slug, artifact_type, collection, name).
        ledger = db[OWNED_ARTIFACTS_COLLECTION]
        await ledger.create_index(
            [
                ("slug", ASCENDING),
                ("artifact_type", ASCENDING),
                ("collection", ASCENDING),
                ("name", ASCENDING),
            ],
            name="slug_artifact_idx",
            unique=True,
        )

        # Trash: the sweeper is sole deletion authority. We intentionally
        # do NOT put a TTL on expires_at — if the sweeper is late, we want
        # the tombstone to survive so `trash ls` still shows it.
        trash = db[TRASH_COLLECTION]
        await trash.create_index(
            [("slug", ASCENDING), ("expires_at", ASCENDING)],
            name="slug_expires_at_idx",
        )
        await trash.create_index(
            [("slug", ASCENDING), ("quarantined_at", DESCENDING)],
            name="slug_quarantined_at_idx",
        )
        await trash.create_index(
            [("trash_name", ASCENDING)],
            name="trash_name_idx",
            unique=True,
            sparse=True,
        )
        # One-shot migration: earlier versions put a TTL on expires_at
        # which could delete tombstones before the sweeper ran. Drop it
        # if present; swallow IndexNotFound (code 27).
        try:
            await trash.drop_index("expires_at_ttl")
            logger.info("Dropped legacy TTL index on %s.expires_at", TRASH_COLLECTION)
        except OperationFailure as e:
            if getattr(e, "code", None) not in (27, 26) and "index not found" not in str(e).lower():
                logger.debug("Legacy TTL index drop skipped: %s", e)

        # Locks: TTL on acquired_at so a crashed worker eventually releases.
        # The fencing token is validated on release/takeover; the TTL is
        # a safety net, not the primary correctness mechanism.
        locks = db[MANIFEST_LOCKS_COLLECTION]
        await locks.create_index(
            [("acquired_at", ASCENDING)],
            name="acquired_at_ttl",
            expireAfterSeconds=DEFAULT_MANIFEST_LOCK_TTL_SECONDS,
        )

        # Persist the bootstrap marker. Failures here just mean the next
        # boot will redo the index creates — safe.
        try:
            await db[META_COLLECTION].update_one(
                {"_id": "__bootstrap__"},
                {
                    "$set": {
                        "version": BOOTSTRAP_VERSION,
                        "done_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.debug("Bootstrap marker write failed (non-fatal): %s", e)

        logger.debug("Reconciler internal collections bootstrapped (version=%s)", BOOTSTRAP_VERSION)
        return True
    except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.warning(f"Failed to bootstrap reconciler internal collections: {e}")
        return False


async def acquire_lock(
    db: AsyncIOMotorDatabase,
    slug: str,
    *,
    holder: str,
    ttl_seconds: int = DEFAULT_MANIFEST_LOCK_TTL_SECONDS,
) -> bool:
    """Try to acquire the per-slug reconciler advisory lock.

    Returns True if the lock was acquired, False if another holder owns
    it. The lock is automatically released after ``ttl_seconds`` via a
    TTL index, so a crashed worker won't block the next startup forever.
    The ``holder`` value must be a fencing token produced by
    :func:`make_holder_id`; ``release_lock`` will only release a lock
    whose stored holder matches exactly.
    """
    now = datetime.now(timezone.utc)
    try:
        await db[MANIFEST_LOCKS_COLLECTION].insert_one(
            {
                "_id": f"reconcile::{slug}",
                "slug": slug,
                "holder": holder,
                "acquired_at": now,
                "ttl_seconds": ttl_seconds,
            }
        )
        return True
    except OperationFailure as e:
        # DuplicateKey means another holder; try stale takeover below.
        if getattr(e, "code", None) != 11000 and "duplicate key" not in str(e).lower():
            logger.warning(f"[{slug}] Lock acquire failed: {e}")
            return False

    # Attempt stale takeover — find the existing lock; if it's older than TTL, replace it.
    try:
        stale_before = now - timedelta(seconds=ttl_seconds)
        result = await db[MANIFEST_LOCKS_COLLECTION].find_one_and_update(
            {
                "_id": f"reconcile::{slug}",
                "acquired_at": {"$lt": stale_before},
            },
            {
                "$set": {
                    "holder": holder,
                    "acquired_at": now,
                    "ttl_seconds": ttl_seconds,
                }
            },
            return_document=True,
        )
        return result is not None
    except (OperationFailure, ConnectionFailure) as e:
        logger.debug(f"[{slug}] Stale lock takeover failed: {e}")
        return False


async def release_lock(db: AsyncIOMotorDatabase, slug: str, *, holder: str) -> None:
    """Release the per-slug reconciler lock if (and only if) held by ``holder``.

    The fencing-token match is critical: if the original holder's TTL
    already expired and another worker took over, the original worker
    must *not* release the new holder's lock.
    """
    try:
        await db[MANIFEST_LOCKS_COLLECTION].delete_one({"_id": f"reconcile::{slug}", "holder": holder})
    except (OperationFailure, ConnectionFailure) as e:
        logger.debug(f"[{slug}] Lock release best-effort failed: {e}")


async def next_revision_number(db: AsyncIOMotorDatabase, slug: str) -> int:
    """Return the next revision number for an app slug (starts at 1)."""
    cursor = (
        db[MANIFEST_REVISIONS_COLLECTION].find({"slug": slug}, {"revision": 1}).sort("revision", DESCENDING).limit(1)
    )
    docs = await cursor.to_list(length=1)
    if not docs:
        return 1
    return int(docs[0].get("revision", 0)) + 1


async def ensure_collection_exists(db: AsyncIOMotorDatabase, name: str) -> None:
    """Create a collection if it doesn't already exist (idempotent)."""
    try:
        await db.create_collection(name)
    except CollectionInvalid:
        pass
    except OperationFailure as e:
        if getattr(e, "code", None) == 48 or "already exists" in str(e).lower():
            return
        raise


def make_holder_id(process_tag: str = "reconciler") -> str:
    """Return a cryptographically-unique fencing token for the advisory lock.

    The token encodes hostname, pid, a per-process boot nonce, and a
    random ``uuid4``. Two workers that happen to share a pid (containers,
    pid-namespaces, process reuse across test runs) will still produce
    distinct tokens, so ``release_lock`` can never free another worker's
    lock by accident.
    """
    try:  # nosemgrep
        hostname = socket.gethostname() or "unknown"
    except Exception:  # noqa: BLE001 - hostname lookup best-effort
        hostname = "unknown"
    pid = os.getpid()
    token = uuid.uuid4().hex
    return f"{process_tag}:{token}@{hostname}:{pid}:{_BOOT_NONCE}"


__all__ = [
    "bootstrap_reconciler_collections",
    "acquire_lock",
    "release_lock",
    "next_revision_number",
    "ensure_collection_exists",
    "make_holder_id",
    "BOOTSTRAP_VERSION",
    "META_COLLECTION",
]


def _reconciler_internal_names() -> list[str]:
    """Names of all reconciler-owned internal collections.

    Exposed so the reconciler can skip them when listing artifacts.
    """
    return [
        MANIFEST_REVISIONS_COLLECTION,
        OWNED_ARTIFACTS_COLLECTION,
        TRASH_COLLECTION,
        MANIFEST_LOCKS_COLLECTION,
        META_COLLECTION,
    ]


# Expose as a constant the core module can import.
RECONCILER_INTERNAL_COLLECTIONS: tuple[str, ...] = tuple(_reconciler_internal_names())


def _ignored(_obj: Any) -> None:  # pragma: no cover
    """No-op used to silence unused imports when debugging."""
    return None
