"""
CORS Utility Functions

Centralized CORS validation, origin normalization, and origin checking logic.
Used by both HTTP middleware and WebSocket handlers for consistent behavior.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def _is_development_environment() -> bool:
    """Check if running in development/Docker environment."""
    return (
        os.getenv("ENVIRONMENT", "").lower() in ["development", "dev"]
        or os.getenv("G_NOME_ENV", "").lower() in ["development", "dev"]
        or os.path.exists("/.dockerenv")
    )


def validate_cors_config(config: dict[str, Any], app_slug: str | None = None) -> None:
    """
    Validate CORS configuration for common errors.

    Args:
        config: CORS configuration dictionary
        app_slug: Optional app slug for error messages

    Raises:
        ValueError: If configuration is invalid

    Examples:
        >>> validate_cors_config({"allow_origins": ["*"], "allow_credentials": True})
        Traceback (most recent call last):
        ValueError: Cannot use wildcard origins (*) with allow_credentials=true...
    """
    if not isinstance(config, dict):
        raise ValueError("CORS config must be a dictionary")

    allow_origins = config.get("allow_origins", [])
    allow_credentials = config.get("allow_credentials", False)

    if not isinstance(allow_origins, list):
        raise ValueError("allow_origins must be a list")

    if not isinstance(allow_credentials, bool):
        raise ValueError("allow_credentials must be a boolean")

    if "*" in allow_origins and allow_credentials:
        app_context = f" for '{app_slug}'" if app_slug else ""
        raise ValueError(
            f"CORS config error{app_context}: "
            "Cannot use wildcard origins (*) with allow_credentials=true. "
            "Browsers reject this combination for security reasons. "
            "Use specific origins instead, e.g., ['http://localhost:3000', 'https://example.com']"
        )


def normalize_origin(origin: str, is_dev: bool | None = None) -> str:
    """
    Normalize origin for comparison.

    Handles:
    - Protocol conversion: ws/wss -> http/https (browsers send http/https)
    - Localhost normalization: 127.0.0.1, 0.0.0.0, ::1 -> localhost
    - Docker IP normalization: container IPs -> localhost (in development)
    - Port normalization: removes :80 and :443

    Args:
        origin: Origin string to normalize
        is_dev: Whether in development mode (auto-detected if None)

    Returns:
        Normalized origin string

    Examples:
        >>> normalize_origin("ws://localhost:8000")
        'http://localhost:8000'
        >>> normalize_origin("http://127.0.0.1:3000")
        'http://localhost:3000'
        >>> normalize_origin("http://172.20.0.6:8000", is_dev=True)
        'http://localhost:8000'
    """
    if not origin:
        return origin

    if is_dev is None:
        is_dev = _is_development_environment()

    normalized = origin.lower()

    normalized = re.sub(r"^ws://", "http://", normalized)
    normalized = re.sub(r"^wss://", "https://", normalized)

    if is_dev:
        normalized = re.sub(
            r"://(0\.0\.0\.0|127\.0\.0\.1|localhost|::1|172\.(17|20)\.\d+\.\d+)",
            "://localhost",
            normalized,
            flags=re.IGNORECASE,
        )
    else:
        normalized = re.sub(
            r"://(0\.0\.0\.0|127\.0\.0\.1|localhost|::1)",
            "://localhost",
            normalized,
            flags=re.IGNORECASE,
        )

    normalized = re.sub(r":80$", "", normalized)
    normalized = re.sub(r":443$", "", normalized)

    return normalized.rstrip("/")


def is_origin_allowed(
    origin: str | None,
    allowed_origins: list[str],
    is_dev: bool | None = None,
) -> bool:
    """
    Check if origin is allowed by CORS configuration.

    Args:
        origin: Origin to check (None for missing origin)
        allowed_origins: List of allowed origins (may include "*")
        is_dev: Whether in development mode (auto-detected if None)

    Returns:
        True if origin is allowed, False otherwise

    Examples:
        >>> is_origin_allowed("http://localhost:3000", ["*"])
        True
        >>> is_origin_allowed("http://localhost:3000", ["http://localhost:8000"])
        False
        >>> is_origin_allowed("http://localhost:3000", ["http://localhost:3000"])
        True
        >>> is_origin_allowed(None, ["*"])
        True
    """
    if not allowed_origins:
        return False

    if "*" in allowed_origins:
        return True

    if not origin:
        return False

    if is_dev is None:
        is_dev = _is_development_environment()

    normalized_origin = normalize_origin(origin, is_dev)

    for allowed in allowed_origins:
        normalized_allowed = normalize_origin(allowed, is_dev)
        if normalized_origin == normalized_allowed:
            return True

    if is_dev and normalized_origin.startswith(("http://localhost:", "https://localhost:")):
        port_match = re.search(r":(\d+)$", normalized_origin)
        if port_match:
            try:
                port_num = int(port_match.group(1))
                if 1 <= port_num <= 65535:
                    for allowed in allowed_origins:
                        normalized_allowed = normalize_origin(allowed, is_dev)
                        if normalized_allowed.startswith(("http://localhost:", "https://localhost:")):
                            return True
            except ValueError:
                pass

    return False


def get_cors_allowed_origins(cors_config: dict[str, Any]) -> list[str]:
    """
    Extract and validate allowed origins from CORS config.

    Args:
        cors_config: CORS configuration dictionary

    Returns:
        List of allowed origins

    Raises:
        ValueError: If config is invalid
    """
    if not isinstance(cors_config, dict):
        raise ValueError("CORS config must be a dictionary")

    origins = cors_config.get("allow_origins", [])
    if not isinstance(origins, list):
        raise ValueError("allow_origins must be a list")

    return origins if isinstance(origins, list) else [origins]
