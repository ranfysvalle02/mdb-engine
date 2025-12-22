"""
Token Lifecycle Management

Provides utilities for managing token lifecycle, rotation, and expiration handling.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from .jwt import decode_jwt_token, extract_token_metadata
from ..constants import ACCESS_TOKEN_TTL
from ..config import ACCESS_TOKEN_TTL as CONFIG_ACCESS_TTL, TOKEN_ROTATION_ENABLED

logger = logging.getLogger(__name__)


def get_token_expiry_time(token: str, secret_key: str) -> Optional[datetime]:
    """
    Get the expiration time of a token.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
    
    Returns:
        Expiration datetime or None if token is invalid
    """
    try:
        metadata = extract_token_metadata(token, secret_key)
        if metadata and metadata.get("exp"):
            exp_timestamp = metadata["exp"]
            if isinstance(exp_timestamp, (int, float)):
                return datetime.utcfromtimestamp(exp_timestamp)
        return None
    except Exception as e:
        logger.debug(f"Error getting token expiry time: {e}")
        return None


def is_token_expiring_soon(
    token: str,
    secret_key: str,
    threshold_seconds: Optional[int] = None
) -> bool:
    """
    Check if a token is expiring soon.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
        threshold_seconds: Seconds before expiry to consider "soon" (default: 10% of TTL)
    
    Returns:
        True if token is expiring soon, False otherwise
    """
    try:
        if threshold_seconds is None:
            threshold_seconds = int(CONFIG_ACCESS_TTL * 0.1)  # 10% of TTL
        
        expiry_time = get_token_expiry_time(token, secret_key)
        if expiry_time is None:
            return False
        
        time_until_expiry = (expiry_time - datetime.utcnow()).total_seconds()
        return time_until_expiry <= threshold_seconds
    except Exception as e:
        logger.debug(f"Error checking if token expiring soon: {e}")
        return False


def should_refresh_token(
    token: str,
    secret_key: str,
    refresh_threshold: Optional[int] = None
) -> bool:
    """
    Determine if a token should be refreshed.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
        refresh_threshold: Seconds before expiry to trigger refresh (default: 20% of TTL)
    
    Returns:
        True if token should be refreshed, False otherwise
    """
    try:
        if refresh_threshold is None:
            refresh_threshold = int(CONFIG_ACCESS_TTL * 0.2)  # 20% of TTL
        
        expiry_time = get_token_expiry_time(token, secret_key)
        if expiry_time is None:
            return False
        
        time_until_expiry = (expiry_time - datetime.utcnow()).total_seconds()
        return time_until_expiry <= refresh_threshold
    except Exception as e:
        logger.debug(f"Error determining if token should refresh: {e}")
        return False


def get_token_age(token: str, secret_key: str) -> Optional[float]:
    """
    Get the age of a token in seconds.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
    
    Returns:
        Token age in seconds or None if invalid
    """
    try:
        metadata = extract_token_metadata(token, secret_key)
        if metadata and metadata.get("iat"):
            iat_timestamp = metadata["iat"]
            if isinstance(iat_timestamp, (int, float)):
                issued_at = datetime.utcfromtimestamp(iat_timestamp)
                age = (datetime.utcnow() - issued_at).total_seconds()
                return age
        return None
    except Exception as e:
        logger.debug(f"Error getting token age: {e}")
        return None


def get_time_until_expiry(token: str, secret_key: str) -> Optional[float]:
    """
    Get time until token expiry in seconds.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
    
    Returns:
        Seconds until expiry (negative if expired) or None if invalid
    """
    try:
        expiry_time = get_token_expiry_time(token, secret_key)
        if expiry_time is None:
            return None
        
        time_until = (expiry_time - datetime.utcnow()).total_seconds()
        return time_until
    except Exception as e:
        logger.debug(f"Error getting time until expiry: {e}")
        return None


def validate_token_version(token: str, secret_key: str, required_version: Optional[str] = None) -> bool:
    """
    Validate token version compatibility.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
        required_version: Optional required version (defaults to current version)
    
    Returns:
        True if token version is valid, False otherwise
    """
    try:
        from ..constants import CURRENT_TOKEN_VERSION
        
        metadata = extract_token_metadata(token, secret_key)
        if not metadata:
            return False
        
        token_version = metadata.get("version")
        if required_version is None:
            required_version = CURRENT_TOKEN_VERSION
        
        # For now, exact match required (can be extended for version ranges)
        return token_version == required_version
    except Exception as e:
        logger.debug(f"Error validating token version: {e}")
        return False


def get_token_info(token: str, secret_key: str) -> Optional[Dict[str, Any]]:
    """
    Get comprehensive token information.
    
    Args:
        token: JWT token string
        secret_key: Secret key for decoding
    
    Returns:
        Dictionary with token information or None if invalid
    """
    try:
        metadata = extract_token_metadata(token, secret_key)
        if not metadata:
            return None
        
        expiry_time = get_token_expiry_time(token, secret_key)
        age = get_token_age(token, secret_key)
        time_until_expiry = get_time_until_expiry(token, secret_key)
        
        info = {
            **metadata,
            "expiry_time": expiry_time.isoformat() if expiry_time else None,
            "age_seconds": age,
            "time_until_expiry_seconds": time_until_expiry,
            "is_expired": time_until_expiry is not None and time_until_expiry < 0,
            "is_expiring_soon": is_token_expiring_soon(token, secret_key),
            "should_refresh": should_refresh_token(token, secret_key),
        }
        
        return info
    except Exception as e:
        logger.debug(f"Error getting token info: {e}")
        return None

