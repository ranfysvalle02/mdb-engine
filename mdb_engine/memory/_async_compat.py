"""
Shared async compatibility helpers for the memory subsystem.

These small utilities smooth over the Motor async / PyMongo sync boundary
so callers don't need to care which driver returned a value or cursor.
"""

from __future__ import annotations

import inspect
from typing import Any


async def maybe_await(value: Any) -> Any:
    """Await *value* if it is awaitable, otherwise return it directly."""
    if inspect.isawaitable(value):
        return await value
    return value


async def cursor_to_list(cursor: Any, limit: int | None = None) -> list[Any]:
    """Convert a Motor async cursor **or** sync PyMongo cursor to a list.

    Handles both async Motor cursors (where ``to_list`` returns a coroutine)
    and sync PyMongo cursors (where ``to_list`` returns a plain ``list``).

    Args:
        cursor: Motor ``AsyncIOMotorCursor``, PyMongo cursor, or any iterable.
        limit: Maximum number of documents to materialise (``None`` = all).
    """
    if hasattr(cursor, "to_list"):
        result = cursor.to_list(length=limit)
        return await maybe_await(result)
    return list(cursor)
