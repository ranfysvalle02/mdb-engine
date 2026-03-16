"""
Shared document serialization for MongoDB → JSON.

Recursively converts ``ObjectId`` and ``datetime`` instances so that
documents can be returned directly from FastAPI endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId


def _serialize_value(value: Any) -> Any:
    """Recursively convert a single value for JSON serialization."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a MongoDB document for JSON serialization.

    Handles ``ObjectId``, ``datetime``, nested sub-documents, and arrays
    at any depth.
    """
    if doc is None:
        return None
    return {k: _serialize_value(v) for k, v in doc.items()}
