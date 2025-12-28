"""
JWT Token Utilities

Provides JWT encoding and decoding utilities with automatic format handling.
Supports access tokens, refresh tokens, and token pairs.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import jwt

from ..config import ACCESS_TOKEN_TTL as CONFIG_ACCESS_TTL
from ..config import REFRESH_TOKEN_TTL as CONFIG_REFRESH_TTL
from ..constants import CURRENT_TOKEN_VERSION

logger = logging.getLogger(__name__)


def decode_jwt_token(token: Any, secret_key: str) -> Dict[str, Any]:
    """
    Helper function to decode JWT tokens with automatic fallback to bytes format.

    Handles cases where PyJWT might expect bytes instead of strings (version-specific behavior).

    Args:
        token: JWT token (can be str, bytes, or other)
        secret_key: Secret key for decoding (str)

    Returns:
        Decoded JWT payload as dict

    Raises:
        jwt.ExpiredSignatureError: If token has expired
        jwt.InvalidTokenError: If token is invalid
    """
    # Normalize token to string first
    if isinstance(token, bytes):
        token_str = token.decode("utf-8")
    elif isinstance(token, str):
        token_str = token
    else:
        token_str = str(token)

    # Normalize secret_key to string
    if isinstance(secret_key, bytes):
        secret_key_str = secret_key.decode("utf-8")
    elif isinstance(secret_key, str):
        secret_key_str = secret_key
    else:
        secret_key_str = str(secret_key)

    # Try decoding with string format first (standard PyJWT behavior)
    try:
        return jwt.decode(token_str, secret_key_str, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        # If string format fails with "must be bytes" error, try bytes format
        error_msg = str(e)
        if "must be a <class 'bytes'>" in error_msg or (
            "bytes" in error_msg.lower() and "token" in error_msg.lower()
        ):
            logger.debug(f"JWT decode: Retrying with bytes format (error: {e})")
            # Convert to bytes and try again
            token_bytes = token_str.encode("utf-8")
            secret_key_bytes = secret_key_str.encode("utf-8")
            return jwt.decode(token_bytes, secret_key_bytes, algorithms=["HS256"])
        else:
            # Re-raise if it's a different error
            raise


def encode_jwt_token(
    payload: Dict[str, Any], secret_key: str, expires_in: Optional[int] = None
) -> str:
    """
    Encode a JWT token with enhanced claims.

    Args:
        payload: Token payload (will be enhanced with standard claims)
        secret_key: Secret key for signing
        expires_in: Optional expiration time in seconds (defaults to ACCESS_TOKEN_TTL)

    Returns:
        Encoded JWT token string
    """
    # Normalize secret_key
    if isinstance(secret_key, bytes):
        secret_key_str = secret_key.decode("utf-8")
    elif isinstance(secret_key, str):
        secret_key_str = secret_key
    else:
        secret_key_str = str(secret_key)

    # Create enhanced payload with standard claims
    now = datetime.utcnow()
    enhanced_payload = {
        **payload,
        "iat": now,  # Issued at
        "nbf": now,  # Not before
        "jti": payload.get("jti") or str(uuid.uuid4()),  # JWT ID
        "version": payload.get("version", CURRENT_TOKEN_VERSION),  # Token version
    }

    # Add expiration
    if expires_in is None:
        expires_in = CONFIG_ACCESS_TTL
    enhanced_payload["exp"] = now + timedelta(seconds=expires_in)

    # Encode token
    token = jwt.encode(enhanced_payload, secret_key_str, algorithm="HS256")

    # Ensure token is a string (some PyJWT versions return bytes)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    elif not isinstance(token, str):
        token = str(token)

    return token


def generate_token_pair(
    user_data: Dict[str, Any],
    secret_key: str,
    device_info: Optional[Dict[str, Any]] = None,
    access_token_ttl: Optional[int] = None,
    refresh_token_ttl: Optional[int] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Generate a pair of access and refresh tokens.

    Args:
        user_data: User information to include in tokens (email, user_id, etc.)
        secret_key: Secret key for signing tokens
        device_info: Optional device information (device_id, user_agent, ip_address)
        access_token_ttl: Optional access token TTL in seconds (defaults to config)
        refresh_token_ttl: Optional refresh token TTL in seconds (defaults to config)

    Returns:
        Tuple of (access_token, refresh_token, token_metadata)
        token_metadata contains: jti (access), refresh_jti, expires_at, device_id
    """
    if access_token_ttl is None:
        access_token_ttl = CONFIG_ACCESS_TTL
    if refresh_token_ttl is None:
        refresh_token_ttl = CONFIG_REFRESH_TTL

    device_id = None
    if device_info:
        device_id = device_info.get("device_id") or str(uuid.uuid4())
    else:
        device_id = str(uuid.uuid4())

    now = datetime.utcnow()

    # Generate access token
    access_jti = str(uuid.uuid4())
    access_payload = {
        **user_data,
        "type": "access",
        "jti": access_jti,
        "device_id": device_id,
    }
    access_token = encode_jwt_token(
        access_payload, secret_key, expires_in=access_token_ttl
    )

    # Generate refresh token
    refresh_jti = str(uuid.uuid4())
    refresh_payload = {
        "type": "refresh",
        "jti": refresh_jti,
        "user_id": user_data.get("user_id") or user_data.get("email"),
        "email": user_data.get("email"),
        "device_id": device_id,
    }
    refresh_token = encode_jwt_token(
        refresh_payload, secret_key, expires_in=refresh_token_ttl
    )

    # Token metadata
    token_metadata = {
        "access_jti": access_jti,
        "refresh_jti": refresh_jti,
        "device_id": device_id,
        "access_expires_at": now + timedelta(seconds=access_token_ttl),
        "refresh_expires_at": now + timedelta(seconds=refresh_token_ttl),
        "issued_at": now,
    }

    return access_token, refresh_token, token_metadata


def extract_token_metadata(token: str, secret_key: str) -> Optional[Dict[str, Any]]:
    """
    Extract metadata from a token without full validation.

    Useful for getting token info before checking blacklist.

    Args:
        token: JWT token string
        secret_key: Secret key for decoding

    Returns:
        Token metadata dict or None if invalid
    """
    try:
        # Decode without verification to get claims
        payload = jwt.decode(token, options={"verify_signature": False})
        return {
            "jti": payload.get("jti"),
            "type": payload.get("type"),
            "user_id": payload.get("user_id") or payload.get("email"),
            "email": payload.get("email"),
            "device_id": payload.get("device_id"),
            "exp": payload.get("exp"),
            "iat": payload.get("iat"),
            "version": payload.get("version"),
        }
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        logger.debug(f"Error extracting token metadata: {e}")
        return None
