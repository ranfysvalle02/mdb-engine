"""
GDPR Helper Functions

Utility functions for GDPR operations including query building and anonymization.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Known user collections that should always be checked
KNOWN_USER_COLLECTIONS = {
    "users",
    "shared_users",
    "user_sessions",
    "token_blacklist",
    "chat_history",
    "_mdb_engine_websocket_sessions",
}

# System collections to skip
SYSTEM_COLLECTION_PREFIXES = ("system.", "_")


def build_user_query(
    user_identifier: str,
    identifier_type: str = "email",
) -> dict[str, Any]:
    """
    Build MongoDB query to find user data.

    Args:
        user_identifier: User email or user_id
        identifier_type: Type of identifier ("email" or "user_id")

    Returns:
        MongoDB query dictionary
    """
    if identifier_type == "email":
        return {
            "$or": [
                {"email": user_identifier},
                {"user_email": user_identifier},
            ]
        }
    else:  # user_id
        return {
            "$or": [
                {"user_id": user_identifier},
                {"_id": user_identifier},
            ]
        }


def generate_anonymous_id(user_identifier: str) -> str:
    """
    Generate anonymous identifier from user identifier.

    Args:
        user_identifier: Original user identifier (email or user_id)

    Returns:
        Anonymous identifier (16 character hex string)
    """
    hash_obj = hashlib.sha256(user_identifier.encode())
    return hash_obj.hexdigest()[:16]


def generate_anonymous_email(user_identifier: str) -> str:
    """
    Generate anonymous email address.

    Args:
        user_identifier: Original user identifier

    Returns:
        Anonymous email address
    """
    anonymous_id = generate_anonymous_id(user_identifier)
    return f"deleted_{anonymous_id}@deleted.local"


def should_skip_collection(collection_name: str) -> bool:
    """
    Check if collection should be skipped during GDPR operations.

    Args:
        collection_name: Collection name to check

    Returns:
        True if collection should be skipped, False otherwise
    """
    # Skip system collections
    if collection_name.startswith(SYSTEM_COLLECTION_PREFIXES):
        return True

    # Skip internal MongoDB collections
    if collection_name.startswith("system."):
        return True

    return False


def serialize_document(doc: dict[str, Any]) -> dict[str, Any]:
    """
    Serialize MongoDB document for JSON export.

    Converts ObjectId to string and handles other non-serializable types.

    Args:
        doc: MongoDB document

    Returns:
        Serialized document
    """
    from bson import ObjectId

    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = serialize_document(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_document(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            serialized[key] = value

    return serialized
