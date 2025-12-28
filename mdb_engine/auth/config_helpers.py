"""
Authentication Config Helper Utilities

Type-safe utility functions for accessing and merging authentication configurations
from manifest.json and app.state.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from typing import Any, Dict

from fastapi import Request

from .config_defaults import (CORS_DEFAULTS, OBSERVABILITY_DEFAULTS,
                              SECURITY_CONFIG_DEFAULTS,
                              TOKEN_MANAGEMENT_DEFAULTS)

logger = logging.getLogger(__name__)


def merge_config_with_defaults(
    user_config: Dict[str, Any], defaults: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Deep merge user config with defaults.

    User config values take precedence over defaults. Nested dictionaries
    are merged recursively.

    Args:
        user_config: User-provided configuration (from manifest)
        defaults: Default configuration values

    Returns:
        Merged configuration dictionary
    """
    if not user_config:
        return defaults.copy()

    if not defaults:
        return user_config.copy()

    merged = defaults.copy()

    for key, value in user_config.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_config_with_defaults(value, merged[key])
        else:
            merged[key] = value

    return merged


def get_security_config(request: Request) -> Dict[str, Any]:
    """
    Get security configuration from app.state with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Security configuration dictionary with defaults applied
    """
    try:
        security_config = getattr(request.app.state, "security_config", None)
        if security_config:
            return merge_config_with_defaults(security_config, SECURITY_CONFIG_DEFAULTS)
        return SECURITY_CONFIG_DEFAULTS.copy()
    except (AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Error getting security config: {e}, using defaults")
        return SECURITY_CONFIG_DEFAULTS.copy()


def get_password_policy(request: Request) -> Dict[str, Any]:
    """
    Get password policy configuration with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Password policy configuration dictionary
    """
    security_config = get_security_config(request)
    return security_config.get(
        "password_policy", SECURITY_CONFIG_DEFAULTS["password_policy"].copy()
    )


def get_session_fingerprinting_config(request: Request) -> Dict[str, Any]:
    """
    Get session fingerprinting configuration with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Session fingerprinting configuration dictionary
    """
    security_config = get_security_config(request)
    return security_config.get(
        "session_fingerprinting",
        SECURITY_CONFIG_DEFAULTS["session_fingerprinting"].copy(),
    )


def get_account_lockout_config(request: Request) -> Dict[str, Any]:
    """
    Get account lockout configuration with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Account lockout configuration dictionary
    """
    security_config = get_security_config(request)
    return security_config.get(
        "account_lockout", SECURITY_CONFIG_DEFAULTS["account_lockout"].copy()
    )


def get_ip_validation_config(request: Request) -> Dict[str, Any]:
    """
    Get IP validation configuration with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        IP validation configuration dictionary
    """
    security_config = get_security_config(request)
    return security_config.get(
        "ip_validation", SECURITY_CONFIG_DEFAULTS["ip_validation"].copy()
    )


def get_token_fingerprinting_config(request: Request) -> Dict[str, Any]:
    """
    Get token fingerprinting configuration with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Token fingerprinting configuration dictionary
    """
    security_config = get_security_config(request)
    return security_config.get(
        "token_fingerprinting", SECURITY_CONFIG_DEFAULTS["token_fingerprinting"].copy()
    )


def get_token_management_config(request: Request) -> Dict[str, Any]:
    """
    Get token management configuration from app.state with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Token management configuration dictionary with defaults applied
    """
    try:
        token_config = getattr(request.app.state, "token_management_config", None)
        if token_config:
            return merge_config_with_defaults(token_config, TOKEN_MANAGEMENT_DEFAULTS)
        return TOKEN_MANAGEMENT_DEFAULTS.copy()
    except (AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Error getting token management config: {e}, using defaults")
        return TOKEN_MANAGEMENT_DEFAULTS.copy()


def get_cors_config(request: Request) -> Dict[str, Any]:
    """
    Get CORS configuration from app.state with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        CORS configuration dictionary with defaults applied
    """
    try:
        cors_config = getattr(request.app.state, "cors_config", None)
        if cors_config:
            return merge_config_with_defaults(cors_config, CORS_DEFAULTS)
        return CORS_DEFAULTS.copy()
    except (AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Error getting CORS config: {e}, using defaults")
        return CORS_DEFAULTS.copy()


def get_observability_config(request: Request) -> Dict[str, Any]:
    """
    Get observability configuration from app.state with defaults merged.

    Args:
        request: FastAPI Request object

    Returns:
        Observability configuration dictionary with defaults applied
    """
    try:
        obs_config = getattr(request.app.state, "observability_config", None)
        if obs_config:
            return merge_config_with_defaults(obs_config, OBSERVABILITY_DEFAULTS)
        return OBSERVABILITY_DEFAULTS.copy()
    except (AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Error getting observability config: {e}, using defaults")
        return OBSERVABILITY_DEFAULTS.copy()
