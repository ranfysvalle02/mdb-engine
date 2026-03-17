"""
Template resolver for MQL-as-DSL zero-code collections.

Resolves ``{{user.*}}``, ``{{doc.*}}``, ``{{prev.*}}``, and ``{{env.*}}``
placeholders as well as ``$$NOW`` in MQL filter objects and aggregation
pipelines embedded in the manifest.  Provides a generic filter-merge
utility that generalises the ``$and`` pattern already used by soft-delete
scoping.

Security constraints:
    * ``{{user.<path>}}`` — max depth 3, validated against a strict regex.
    * ``{{doc.<path>}}``  — max depth 3, same traversal rules as user paths.
    * ``{{prev.<path>}}`` — max depth 3, previous document state (update hooks).
    * ``{{env.<KEY>}}``   — restricted to uppercase env-var names
      matching ``^[A-Z_][A-Z0-9_]*$``.
    * ``$$NOW`` — replaced with current UTC datetime.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

_USER_TEMPLATE_RE = re.compile(r"^\{\{user\.([a-zA-Z_][a-zA-Z0-9_.]{0,63})\}\}$")
_DOC_TEMPLATE_RE = re.compile(r"^\{\{doc\.([a-zA-Z_][a-zA-Z0-9_.]{0,63})\}\}$")
_PREV_TEMPLATE_RE = re.compile(r"^\{\{prev\.([a-zA-Z_][a-zA-Z0-9_.]{0,63})\}\}$")
_ENV_TEMPLATE_RE = re.compile(r"^\{\{env\.([A-Z_][A-Z0-9_]{0,63})\}\}$")
_MAX_PATH_DEPTH = 3


# ── Public API ───────────────────────────────────────────────────────────


def resolve_template(
    mql_obj: Any,
    user: dict[str, Any] | None,
    *,
    doc: dict[str, Any] | None = None,
    prev: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Any:
    """Deep-copy *mql_obj* and replace all template placeholders.

    Supported placeholders:

    * ``"{{user.X}}"`` — replaced with the value at key-path *X* in *user*.
      Dot-separated paths (e.g. ``user.profile.team_id``) are traversed up
      to :data:`_MAX_PATH_DEPTH` levels.
    * ``"{{doc.X}}"``  — replaced with the value at key-path *X* in *doc*
      (the document being created/updated).
    * ``"{{prev.X}}"`` — replaced with the value at key-path *X* in *prev*
      (the previous document state, available in update hooks).
    * ``"{{env.KEY}}"`` — replaced with the environment variable *KEY*.
    * ``"$$NOW"`` — replaced with the current UTC datetime (or *now* if
      provided).

    Raises:
        HTTPException(401): if a ``{{user.*}}`` placeholder is found but
            *user* is ``None``.
        HTTPException(400): if a path cannot be resolved.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return _resolve(copy.deepcopy(mql_obj), user, doc, prev, now)


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


def _resolve(
    obj: Any,
    user: dict[str, Any] | None,
    doc: dict[str, Any] | None,
    prev: dict[str, Any] | None,
    now: datetime,
) -> Any:
    if isinstance(obj, str):
        return _resolve_string(obj, user, doc, prev, now)
    if isinstance(obj, dict):
        return {k: _resolve(v, user, doc, prev, now) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(item, user, doc, prev, now) for item in obj]
    return obj


def _resolve_string(
    value: str,
    user: dict[str, Any] | None,
    doc: dict[str, Any] | None,
    prev: dict[str, Any] | None,
    now: datetime,
) -> Any:
    if value == "$$NOW":
        return now

    match = _USER_TEMPLATE_RE.match(value)
    if match:
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required (policy uses user context)",
            )
        return _traverse_dict(user, match.group(1), "user")

    match = _DOC_TEMPLATE_RE.match(value)
    if match:
        if doc is None:
            return value
        return _traverse_dict(doc, match.group(1), "doc")

    match = _PREV_TEMPLATE_RE.match(value)
    if match:
        if prev is None:
            return value
        return _traverse_dict(prev, match.group(1), "prev")

    match = _ENV_TEMPLATE_RE.match(value)
    if match:
        key = match.group(1)
        env_val = os.environ.get(key)
        if env_val is None:
            raise HTTPException(
                status_code=400,
                detail=f"Environment variable not set: {key}",
            )
        return env_val

    return value


def _traverse_dict(data: dict[str, Any], path: str, label: str) -> Any:
    """Walk dot-separated *path* into a dict."""
    parts = path.split(".")
    if len(parts) > _MAX_PATH_DEPTH:
        raise HTTPException(
            status_code=400,
            detail=f"{label} path too deep: {path}",
        )
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resolve {label} path: {path}",
            )
    if current is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} path resolved to None: {path}",
        )
    return current
