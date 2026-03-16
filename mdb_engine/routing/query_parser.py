"""
Query parameter parser for auto-CRUD endpoints.

Translates URL query parameters into MongoDB query components
(filter, sort, pagination, projection).

Filtering syntax:
    ?status=pending          -> {"status": "pending"}
    ?age=gt:18               -> {"age": {"$gt": 18}}
    ?tags=in:a,b,c           -> {"tags": {"$in": ["a", "b", "c"]}}

Sorting:
    ?sort=-created_at,name   -> [("created_at", -1), ("name", 1)]

Pagination:
    ?limit=10&skip=20        -> skip=20, limit=10

Field selection:
    ?fields=title,status     -> {"title": 1, "status": 1}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_RESERVED_PARAMS = frozenset({"sort", "limit", "skip", "fields", "scope"})

_OPERATORS = {
    "gt": "$gt",
    "gte": "$gte",
    "lt": "$lt",
    "lte": "$lte",
    "ne": "$ne",
    "in": "$in",
}

_FIELD_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


@dataclass
class ParsedQuery:
    """Result of parsing URL query parameters into MongoDB components."""

    filter: dict[str, Any] = field(default_factory=dict)
    sort: list[tuple[str, int]] | None = None
    skip: int = 0
    limit: int = DEFAULT_LIMIT
    projection: dict[str, int] | None = None
    scope: list[str] | None = None


def _coerce_value(raw: str) -> str | int | float | bool:
    """Attempt to coerce a string value to a native Python type."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _is_safe_field(name: str) -> bool:
    """Reject field names that could be used for query injection."""
    if name.startswith("$") or name.startswith("_"):
        return False
    return bool(_FIELD_NAME_RE.match(name))


def _parse_filter_value(raw: str) -> Any:
    """Parse a single filter value, handling operator prefixes."""
    for prefix, mongo_op in _OPERATORS.items():
        tag = f"{prefix}:"
        if raw.startswith(tag):
            remainder = raw[len(tag) :]
            if mongo_op == "$in":
                return {mongo_op: [_coerce_value(v) for v in remainder.split(",") if v]}
            return {mongo_op: _coerce_value(remainder)}
    return _coerce_value(raw)


def _parse_sort(raw: str) -> list[tuple[str, int]]:
    """Parse sort parameter: ``-created_at,name`` -> ``[("created_at", -1), ("name", 1)]``."""
    result: list[tuple[str, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            field_name = part[1:]
            direction = -1
        else:
            field_name = part.lstrip("+")
            direction = 1
        if _is_safe_field(field_name):
            result.append((field_name, direction))
    return result or None  # type: ignore[return-value]


def _parse_projection(raw: str) -> dict[str, int] | None:
    """Parse fields parameter: ``title,status`` -> ``{"title": 1, "status": 1}``."""
    proj: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if part and _is_safe_field(part):
            proj[part] = 1
    return proj or None


def parse_query_params(params: dict[str, str]) -> ParsedQuery:
    """Parse URL query parameters into MongoDB query components.

    Args:
        params: Dictionary of query parameter name -> value.

    Returns:
        ParsedQuery with filter, sort, skip, limit, and projection.
    """
    result = ParsedQuery()

    for key, value in params.items():
        if key == "sort":
            result.sort = _parse_sort(value)
        elif key == "limit":
            try:
                result.limit = min(max(int(value), 1), MAX_LIMIT)
            except (ValueError, TypeError):
                pass
        elif key == "skip":
            try:
                result.skip = max(int(value), 0)
            except (ValueError, TypeError):
                pass
        elif key == "fields":
            result.projection = _parse_projection(value)
        elif key == "scope":
            names = [s.strip() for s in value.split(",") if s.strip()]
            result.scope = names or None
        elif _is_safe_field(key):
            result.filter[key] = _parse_filter_value(value)

    return result
