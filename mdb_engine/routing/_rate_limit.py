"""
Per-collection rate limiting for auto-CRUD endpoints.

Provides a FastAPI dependency factory that enforces manifest-driven rate
limits on individual collections.  Reuses the sliding-window stores from
``mdb_engine.auth.rate_limiter``.

Manifest config example::

    {
      "collections": {
        "orders": {
          "rate_limits": {
            "reads":  { "max_attempts": 100, "window_seconds": 60 },
            "writes": { "max_attempts": 20,  "window_seconds": 60 },
            "per": "user"
          }
        }
      }
    }
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from ..auth.rate_limiter import InMemoryRateLimitStore, RateLimit

logger = logging.getLogger(__name__)

_store = InMemoryRateLimitStore()


def _get_identifier(request: Request, collection: str, operation: str, per: str) -> str:
    """Build a rate-limit key from the request context."""
    user = getattr(request.state, "user", None)
    if per == "user" and user:
        actor = str(user.get("_id", "")) or user.get("email", "")
    else:
        actor = request.client.host if request.client else "unknown"
    return f"col:{collection}:{operation}:{actor}"


def create_collection_rate_limit_dependency(
    collection_name: str,
    operation: str,
    config: dict[str, Any],
) -> Callable:
    """Return a FastAPI ``Depends()``-compatible callable.

    Args:
        collection_name: Logical collection name.
        operation: ``"reads"`` or ``"writes"``.
        config: The ``rate_limits`` dict from the collection manifest.
    """
    op_config = config.get(operation)
    if not op_config:
        return _noop_dependency

    limit = RateLimit(
        max_attempts=op_config.get("max_attempts", 100),
        window_seconds=op_config.get("window_seconds", 60),
    )
    per = config.get("per", "ip")

    async def _check_rate_limit(request: Request) -> None:
        identifier = _get_identifier(request, collection_name, operation, per)
        count = await _store.record_attempt(identifier, limit.window_seconds)
        if count > limit.max_attempts:
            retry_after = limit.window_seconds
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {collection_name} {operation}",
                headers={"Retry-After": str(retry_after)},
            )

    return _check_rate_limit


async def _noop_dependency(request: Request) -> None:
    """Placeholder dependency when no rate limit is configured for an operation."""
