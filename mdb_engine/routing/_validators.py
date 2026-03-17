"""
Extended schema validators beyond JSON Schema.

Supports engine-specific schema extensions:
- ``x-values-from``: validates field values against a lookup collection.
- ``x-references``: validates foreign key references exist (A.7).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def validate_schema_extensions(
    body: dict[str, Any],
    schema: dict[str, Any] | None,
    db: Any,
    *,
    partial: bool = False,
) -> None:
    """Run engine-specific validators that require DB access.

    Called after standard jsonschema validation. Checks ``x-values-from``
    and ``x-references`` in schema property definitions.
    """
    if schema is None:
        return
    properties = schema.get("properties", {})
    if not properties:
        return

    for field_name, prop_def in properties.items():
        if not isinstance(prop_def, dict):
            continue
        if field_name not in body:
            continue

        values_from = prop_def.get("x-values-from")
        if values_from:
            await _validate_values_from(field_name, body[field_name], values_from, db)

        references = prop_def.get("x-references")
        if references:
            await _validate_reference(field_name, body[field_name], references, db)


async def _validate_values_from(
    field_name: str,
    value: Any,
    config: dict[str, str],
    db: Any,
) -> None:
    """Validate that *value* (or each element if a list) exists in a
    lookup collection's field."""
    collection_name = config.get("collection", "")
    lookup_field = config.get("field", "")
    if not collection_name or not lookup_field:
        return

    values_to_check = value if isinstance(value, list) else [value]
    collection = db[collection_name]
    cursor = collection.find({}, {lookup_field: 1})
    if hasattr(cursor, "__await__"):
        cursor = await cursor
    docs = await cursor.to_list(length=10000)
    allowed_values = {doc.get(lookup_field) for doc in docs}

    invalid = [v for v in values_to_check if v not in allowed_values]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid value(s) for '{field_name}': {invalid}. " f"Must be from {collection_name}.{lookup_field}"
            ),
        )


async def _validate_reference(
    field_name: str,
    value: Any,
    config: dict[str, str],
    db: Any,
) -> None:
    """Validate that a foreign key reference points to an existing document."""
    collection_name = config.get("collection", "")
    ref_field = config.get("field", "_id")
    if not collection_name:
        return

    collection = db[collection_name]

    if ref_field == "_id":
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            lookup_value = ObjectId(value)
        except (InvalidId, TypeError):
            lookup_value = value
        doc = await collection.find_one({"_id": lookup_value})
    else:
        doc = await collection.find_one({ref_field: value})

    if doc is None:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid reference for '{field_name}': '{value}' not found " f"in {collection_name}.{ref_field}"),
        )
