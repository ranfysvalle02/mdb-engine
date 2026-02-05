#!/usr/bin/env python3
"""
Shared Security Configuration Helper
====================================

Centralized security configuration utilities for SSO multi-app example.
Provides secure defaults and environment-aware settings.
"""

import os
from typing import Dict


def get_cookie_settings() -> Dict[str, any]:
    """
    Get secure cookie settings based on environment.
    
    Returns:
        Dictionary with cookie settings:
        - httponly: Always True
        - samesite: "strict" in production, "lax" in development
        - secure: True in production or if SECURE_COOKIES env var is set
    """
    is_production = os.getenv("ENVIRONMENT", "development").lower() == "production"
    secure_cookies_env = os.getenv("SECURE_COOKIES", "false").lower() == "true"
    
    return {
        "httponly": True,
        "samesite": "strict" if is_production else "lax",
        "secure": is_production or secure_cookies_env,
    }


def is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def validate_jwt_token_format(token: str) -> bool:
    """
    Validate JWT token format before processing.
    
    JWT tokens have the format: header.payload.signature (3 parts separated by dots)
    
    Args:
        token: Token string to validate
        
    Returns:
        True if token format is valid, False otherwise
    """
    if not token or not isinstance(token, str):
        return False
    
    # Check length (JWT tokens are typically 100-2000 characters)
    if len(token) < 10 or len(token) > 2000:
        return False
    
    # Check JWT format: should have exactly 2 dots separating 3 parts
    parts = token.split(".")
    if len(parts) != 3:
        return False
    
    # Each part should be non-empty
    if not all(part for part in parts):
        return False
    
    return True
