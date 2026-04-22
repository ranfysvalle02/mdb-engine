"""
Admin plane audit middleware.

Every authenticated admin call writes one row to ``_mdb_admin_audit``::

    {
      slug, module, endpoint, method, status, duration_ms,
      principal_token_id,       # HMAC fingerprint (non-reversible)
      principal_label,          # human-facing identifier (e.g. "ci-gha")
      request_summary,
      response_summary,
      extra,                    # module-provided context
      at                        # TTL anchor
    }

The collection is intentionally schemaless beyond the indexed fields
(``slug``, ``at``, ``module``) so modules can attach their own
arbitrary context without a migration. We never store the raw token
or the full request body — only the HMAC fingerprint (for "which key
called this?" debugging) and a short summary string.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import hashlib
import logging
import time
import warnings
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

ADMIN_AUDIT_COLLECTION = "_mdb_admin_audit"
"""Collection storing one row per authenticated admin call."""

DEFAULT_AUDIT_RETENTION_DAYS = 365


def fingerprint_token(token: str | None) -> str | None:
    """Deprecated: bare SHA-256 prefix for tokens.

    Use :meth:`AppSecretsManager.fingerprint` instead, which HMACs
    under a secret derived from the engine's master key and returns
    ``hmac:<hex16>``. This function is kept only for call sites
    unable to reach the secrets manager; it logs a deprecation
    warning the first time it's hit.
    """
    if not token:
        return None
    warnings.warn(
        "mdb_engine.admin.audit.fingerprint_token is deprecated; " "use AppSecretsManager.fingerprint() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


async def bootstrap_admin_collections(
    mongo_db: AsyncIOMotorDatabase,
    *,
    retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
) -> None:
    """Ensure admin plane collections exist with the right indexes.

    Idempotent: safe to call on every engine boot. Creates:

    - ``_mdb_admin_audit`` with TTL keyed to ``retention_days``.
    - ``_mdb_admin_idempotency`` with 24h TTL (see :mod:`idempotency`).
    """
    try:  # nosemgrep
        coll = mongo_db[ADMIN_AUDIT_COLLECTION]
        await coll.create_index([("slug", 1), ("at", -1)], name="idx_slug_at")
        await coll.create_index([("module", 1), ("at", -1)], name="idx_module_at")
        await coll.create_index(
            [("at", 1)],
            name="idx_at_ttl",
            expireAfterSeconds=retention_days * 86400,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to bootstrap %s indexes", ADMIN_AUDIT_COLLECTION, exc_info=True)

    try:  # nosemgrep
        from .idempotency import bootstrap_idempotency_collection

        await bootstrap_idempotency_collection(mongo_db)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to bootstrap idempotency collection", exc_info=True)


class AuditContext:
    """Per-request accumulator passed to module handlers.

    Modules can attach extra structured context via
    :meth:`record` without knowing how the row is ultimately persisted.
    This keeps module code agnostic of the audit backend.
    """

    def __init__(self, slug: str, module: str, endpoint: str, method: str):
        self.slug = slug
        self.module = module
        self.endpoint = endpoint
        self.method = method
        self.extra: dict[str, Any] = {}
        self._started = time.perf_counter()

    def record(self, **kv: Any) -> None:
        """Merge ``kv`` into the audit row's ``extra`` field."""
        self.extra.update(kv)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0


async def write_audit_row(
    mongo_db: AsyncIOMotorDatabase,
    *,
    ctx: AuditContext,
    status_code: int,
    token_id: str | None = None,
    label: str | None = None,
    request_summary: str = "",
    response_summary: str = "",
    # Back-compat kwarg (deprecated, use token_id)
    token_fingerprint: str | None = None,
) -> None:
    """Persist a single audit row.

    Failures are logged but never raised — an audit outage must not
    break the API surface. Operators can detect gaps via gauges.
    """
    resolved_token_id = token_id or token_fingerprint
    row = {
        "slug": ctx.slug,
        "module": ctx.module,
        "endpoint": ctx.endpoint,
        "method": ctx.method.upper(),
        "status": int(status_code),
        "duration_ms": round(ctx.elapsed_ms(), 2),
        "principal_token_id": resolved_token_id,
        "principal_label": label,
        "request_summary": request_summary[:500],
        "response_summary": response_summary[:500],
        "extra": ctx.extra or None,
        "at": datetime.now(timezone.utc),
    }
    try:  # nosemgrep
        await mongo_db[ADMIN_AUDIT_COLLECTION].insert_one(row)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to write admin audit row", exc_info=True)


__all__ = [
    "ADMIN_AUDIT_COLLECTION",
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "AuditContext",
    "bootstrap_admin_collections",
    "fingerprint_token",
    "write_audit_row",
]
