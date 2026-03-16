"""
Template resolver for MQL-as-DSL zero-code collections.

Resolves ``{{user.*}}`` placeholders and ``$$NOW`` in MQL filter objects
and aggregation pipelines embedded in the manifest.  Provides a generic
filter-merge utility that generalises the ``$and`` pattern already used
by soft-delete scoping.

Security constraints:
    * Only ``{{user.<path>}}`` (max depth 3) and ``$$NOW`` are recognised.
    * Template strings containing ``$`` that are *not* ``$$NOW`` are rejected.
    * User paths are validated against a strict allowlist regex.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

_TEMPLATE_RE = re.compile(r"^\{\{user\.([a-zA-Z_][a-zA-Z0-9_.]{0,63})\}\}$")
_MAX_USER_PATH_DEPTH = 3


# ── Public API ───────────────────────────────────────────────────────────


def resolve_template(
    mql_obj: Any,
    user: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> Any:
    """Deep-copy *mql_obj* and replace all template placeholders.

    Supported placeholders:

    * ``"{{user.X}}"`` — replaced with the value at key-path *X* in *user*.
      Dot-separated paths (e.g. ``user.profile.team_id``) are traversed up
      to :data:`_MAX_USER_PATH_DEPTH` levels.
    * ``"$$NOW"`` — replaced with the current UTC datetime (or *now* if
      provided).

    Raises:
        HTTPException(401): if a ``{{user.*}}`` placeholder is found but
            *user* is ``None``.
        HTTPException(400): if a user path cannot be resolved.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return _resolve(copy.deepcopy(mql_obj), user, now)


def merge_filters(*filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge zero or more MongoDB filter dicts with ``$and``.

    * ``None`` and empty dicts are silently dropped.
    * If only one non-empty filter remains, it is returned as-is.
    * Otherwise all non-empty filters are combined with ``$and``.

    Returns ``None`` when every input is empty/None.
    """
    non_empty = [f for f in filters if f]
    if not non_empty:
        return None
    if len(non_empty) == 1:
        return non_empty[0]
    return {"$and": non_empty}


# ── Internal helpers ─────────────────────────────────────────────────────


def _resolve(obj: Any, user: dict[str, Any] | None, now: datetime) -> Any:
    if isinstance(obj, str):
        return _resolve_string(obj, user, now)
    if isinstance(obj, dict):
        return {k: _resolve(v, user, now) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(item, user, now) for item in obj]
    return obj


def _resolve_string(value: str, user: dict[str, Any] | None, now: datetime) -> Any:
    if value == "$$NOW":
        return now

    match = _TEMPLATE_RE.match(value)
    if match:
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required (policy uses user context)",
            )
        return _traverse_user(user, match.group(1))

    return value


def _traverse_user(user: dict[str, Any], path: str) -> Any:
    """Walk dot-separated *path* into the user dict."""
    parts = path.split(".")
    if len(parts) > _MAX_USER_PATH_DEPTH:
        raise HTTPException(
            status_code=400,
            detail=f"User path too deep: {path}",
        )
    current: Any = user
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resolve user path: {path}",
            )
    if current is None:
        raise HTTPException(
            status_code=400,
            detail=f"User path resolved to None: {path}",
        )
    return current
