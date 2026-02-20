"""
MongoDB utility functions for MDB Engine.

This module provides utility functions for working with MongoDB documents,
including JSON serialization helpers.
"""

from typing import Any


def clean_mongo_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Convert MongoDB document to JSON-serializable format.

    Recursively converts MongoDB-specific types to JSON-compatible types:
    - ObjectId -> str
    - datetime -> ISO format string
    - Nested dictionaries and lists are processed recursively

    Args:
        doc: MongoDB document (dict) or None

    Returns:
        Cleaned document with all MongoDB types converted, or None if input was None

    Example:
        ```python
        from mdb_engine.utils import clean_mongo_doc

        # MongoDB document with ObjectId and datetime
        doc = {
            "_id": ObjectId("507f1f77bcf86cd799439011"),
            "name": "John",
            "created_at": datetime(2024, 1, 1, 12, 0, 0),
            "nested": {
                "id": ObjectId("507f1f77bcf86cd799439012")
            }
        }

        # Convert to JSON-serializable format
        cleaned = clean_mongo_doc(doc)
        # {
        #     "_id": "507f1f77bcf86cd799439011",
        #     "name": "John",
        #     "created_at": "2024-01-01T12:00:00",
        #     "nested": {
        #         "id": "507f1f77bcf86cd799439012"
        #     }
        # }
        ```
    """
    from datetime import datetime

    from bson import ObjectId

    if doc is None:
        return None

    if not isinstance(doc, dict):
        # If it's not a dict, try to convert it
        if isinstance(doc, ObjectId):
            return str(doc)
        elif isinstance(doc, datetime):
            return doc.isoformat() if hasattr(doc, "isoformat") else str(doc)
        else:
            return doc

    cleaned: dict[str, Any] = {}

    for key, value in doc.items():
        if isinstance(value, ObjectId):
            cleaned[key] = str(value)
        elif isinstance(value, datetime):
            cleaned[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)
        elif isinstance(value, dict):
            cleaned[key] = clean_mongo_doc(value)
        elif isinstance(value, list):
            cleaned[key] = [
                clean_mongo_doc(item)
                if isinstance(item, dict)
                else (
                    str(item)
                    if isinstance(item, ObjectId)
                    else (item.isoformat() if isinstance(item, datetime) else item)
                )
                for item in value
            ]
        else:
            cleaned[key] = value

    return cleaned


def clean_mongo_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert a list of MongoDB documents to JSON-serializable format.

    Convenience function that applies clean_mongo_doc to each document in a list.

    Args:
        docs: List of MongoDB documents

    Returns:
        List of cleaned documents

    Example:
        ```python
        from mdb_engine.utils import clean_mongo_docs

        # List of MongoDB documents
        docs = await db.collection.find({}).to_list(length=10)

        # Convert all to JSON-serializable format
        cleaned = clean_mongo_docs(docs)
        ```
    """
    return [clean_mongo_doc(doc) for doc in docs]
