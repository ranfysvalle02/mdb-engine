"""
Helper functions for index management.

This module contains shared utility functions to reduce code duplication
in index creation and management.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_keys(
    keys: dict[str, Any] | list[tuple[str, Any]],
) -> list[tuple[str, Any]]:
    """
    Normalize index keys to a consistent format.

    Args:
        keys: Index keys as dict or list of tuples

    Returns:
        List of (field_name, direction) tuples
    """
    if isinstance(keys, dict):
        return [(k, v) for k, v in keys.items()]
    return keys


def keys_to_dict(keys: dict[str, Any] | list[tuple[str, Any]]) -> dict[str, Any]:
    """
    Convert index keys to dictionary format for comparison.

    Args:
        keys: Index keys as dict or list of tuples

    Returns:
        Dictionary representation of keys
    """
    if isinstance(keys, dict):
        return keys
    return {k: v for k, v in keys}


def is_id_index(keys: dict[str, Any] | list[tuple[str, Any]]) -> bool:
    """
    Check if index keys target the _id field (which MongoDB creates automatically).

    Args:
        keys: Index keys to check

    Returns:
        True if this is an _id index
    """
    if isinstance(keys, dict):
        return len(keys) == 1 and "_id" in keys
    elif isinstance(keys, list):
        return len(keys) == 1 and len(keys[0]) >= 1 and keys[0][0] == "_id"
    return False


async def check_and_update_index(
    index_manager: Any,
    index_name: str,
    expected_keys: dict[str, Any] | list[tuple[str, Any]],
    expected_options: dict[str, Any] | None = None,
    log_prefix: str = "",
) -> tuple[bool, dict[str, Any] | None]:
    """
    Check if an index exists and matches the expected definition.

    Args:
        index_manager: AsyncAtlasIndexManager instance
        index_name: Name of the index
        expected_keys: Expected index keys
        expected_options: Expected index options (for comparison)
        log_prefix: Logging prefix for messages

    Returns:
        Tuple of (index_exists, existing_index_dict or None)
        If index exists and matches, returns (True, existing_index)
        If index exists but doesn't match, returns (True, None) and drops it
        If index doesn't exist, returns (False, None)
    """
    existing_index = await index_manager.get_index(index_name)

    if not existing_index:
        return (False, None)

    # Compare keys
    existing_key = existing_index.get("key", {})
    expected_key_dict = keys_to_dict(expected_keys)

    keys_match = existing_key == expected_key_dict

    # Compare options if provided
    options_match = True
    if expected_options:
        for key, value in expected_options.items():
            if key == "name":
                continue  # Skip name comparison
            if existing_index.get(key) != value:
                options_match = False
                break

    if keys_match and options_match:
        return (True, existing_index)

    # Index exists but doesn't match - drop it
    logger.warning(
        f"{log_prefix} Index '{index_name}' definition mismatch. "
        f"Existing: keys={existing_key}, Expected: keys={expected_key_dict}. "
        f"Dropping existing index and recreating."
    )
    await index_manager.drop_index(index_name)
    return (True, None)


def validate_index_definition_basic(
    index_def: dict[str, Any],
    index_name: str,
    required_fields: list[str],
    log_prefix: str = "",
) -> tuple[bool, str | None]:
    """
    Basic validation for index definitions.

    Args:
        index_def: Index definition dictionary
        index_name: Name of the index
        required_fields: List of required field names
        log_prefix: Logging prefix

    Returns:
        Tuple of (is_valid, error_message)
    """
    for field in required_fields:
        if field not in index_def or not index_def[field]:
            return (
                False,
                f"{log_prefix} Missing '{field}' field on index '{index_name}'. "
                f"Index requires a '{field}' field. Skipping this index definition.",
            )
    return (True, None)
