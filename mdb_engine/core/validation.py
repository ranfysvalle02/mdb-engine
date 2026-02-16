"""
Input validation utilities for user-supplied identifiers.

These helpers provide defence-in-depth against MongoDB query injection
by restricting identifier values to a safe character set.  The existing
``QueryValidator`` (in ``mdb_engine.database.query_validator``) already
blocks dangerous operators in query *filters*, but it does not validate
individual *field values* like ``user_id`` or ``bucket_id``.  The
functions here fill that gap.
"""

import re

# Allow alphanumeric, underscores, hyphens, dots, colons, and @.
# This is broad enough for UUIDs, email-style IDs, and bucket IDs
# (e.g. "category:CODE:team-001") while rejecting injection-prone
# characters like $, {, }, and quotes.
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.@:]+$")

# Reasonable upper bound for identifier length.
_MAX_ID_LENGTH = 512


class InputValidationError(ValueError):
    """Raised when a user-supplied identifier fails validation."""


def validate_identifier(
    value: str,
    field_name: str = "identifier",
    *,
    max_length: int = _MAX_ID_LENGTH,
    allow_none: bool = False,
) -> str | None:
    """
    Validate a user-supplied identifier string.

    Args:
        value: The value to validate.
        field_name: Human-readable field name for error messages.
        max_length: Maximum allowed length (default 512).
        allow_none: If ``True``, ``None`` values pass through unchanged.

    Returns:
        The validated (stripped) string, or ``None`` if *allow_none* is set
        and the input was ``None``.

    Raises:
        InputValidationError: If the value is invalid.
    """
    if value is None:
        if allow_none:
            return None
        raise InputValidationError(f"{field_name} must not be None")

    if not isinstance(value, str):
        raise InputValidationError(f"{field_name} must be a string, got {type(value).__name__}")

    value = value.strip()

    if not value:
        raise InputValidationError(f"{field_name} must not be empty")

    if len(value) > max_length:
        raise InputValidationError(f"{field_name} exceeds maximum length ({len(value)} > {max_length})")

    if not _SAFE_ID_PATTERN.match(value):
        raise InputValidationError(
            f"{field_name} contains invalid characters. "
            f"Only alphanumeric, underscore, hyphen, dot, colon, and @ are allowed."
        )

    return value


def validate_user_id(user_id: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a ``user_id`` value."""
    return validate_identifier(user_id, "user_id", allow_none=allow_none)


def validate_bucket_id(bucket_id: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a ``bucket_id`` value."""
    return validate_identifier(bucket_id, "bucket_id", allow_none=allow_none)


def validate_session_id(session_id: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a ``session_id`` value."""
    return validate_identifier(session_id, "session_id", allow_none=allow_none)


def validate_node_id(node_id: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a graph ``node_id`` value."""
    return validate_identifier(node_id, "node_id", allow_none=allow_none)


def validate_edge_id(edge_id: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a graph ``edge_id`` value."""
    return validate_identifier(edge_id, "edge_id", allow_none=allow_none)


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------

_VALID_SCOPES = frozenset({"user", "shared", "app", "family"})


def validate_scope(scope: str | None, *, allow_none: bool = False) -> str | None:
    """Validate a memory ``scope`` value.

    Scope must be one of: ``user``, ``shared``, ``app``, ``family``.

    Raises:
        InputValidationError: If *scope* is not a recognised value.
    """
    if scope is None:
        if allow_none:
            return None
        raise InputValidationError("scope must not be None")
    if not isinstance(scope, str):
        raise InputValidationError(f"scope must be a string, got {type(scope).__name__}")
    scope = scope.strip().lower()
    if scope not in _VALID_SCOPES:
        raise InputValidationError(f"scope must be one of {sorted(_VALID_SCOPES)}, got '{scope}'")
    return scope


# ---------------------------------------------------------------------------
# Text content validation (for data going into prompts or DB)
# ---------------------------------------------------------------------------

_MAX_TEXT_LENGTH = 100_000  # 100 KB is generous for any single text field


def validate_text_content(
    text: str | None,
    field_name: str = "text",
    *,
    max_length: int = _MAX_TEXT_LENGTH,
    allow_none: bool = False,
) -> str | None:
    """Validate free-form text content.

    Checks for:
    - ``None`` / empty (unless *allow_none*)
    - Excessive length
    - Embedded null bytes (common injection vector)

    Returns:
        The validated text, or ``None`` if *allow_none* is set.

    Raises:
        InputValidationError: If the text fails validation.
    """
    if text is None:
        if allow_none:
            return None
        raise InputValidationError(f"{field_name} must not be None")
    if not isinstance(text, str):
        raise InputValidationError(f"{field_name} must be a string, got {type(text).__name__}")
    if "\x00" in text:
        raise InputValidationError(f"{field_name} contains null bytes")
    if len(text) > max_length:
        raise InputValidationError(f"{field_name} exceeds maximum length ({len(text)} > {max_length})")
    return text
