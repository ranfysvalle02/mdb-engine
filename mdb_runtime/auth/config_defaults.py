"""
Authentication Configuration Defaults

Centralized default values for all authentication and security configurations.
This module provides a single source of truth for all config defaults.

This module is part of MDB_RUNTIME - MongoDB Runtime Engine.
"""

from typing import Dict, Any

SECURITY_CONFIG_DEFAULTS: Dict[str, Any] = {
    "password_policy": {
        "allow_plain_text": False,
        "min_length": 8,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_numbers": True,
        "require_special": False
    },
    "session_fingerprinting": {
        "enabled": True,
        "validate_on_login": True,
        "validate_on_refresh": True,
        "validate_on_request": False,
        "strict_mode": False
    },
    "account_lockout": {
        "enabled": True,
        "max_failed_attempts": 5,
        "lockout_duration_seconds": 900,
        "reset_on_success": True
    },
    "ip_validation": {
        "enabled": False,
        "strict": False,
        "allow_ip_change": True
    },
    "token_fingerprinting": {
        "enabled": True,
        "bind_to_device": True
    }
}

TOKEN_MANAGEMENT_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "access_token_ttl": 900,
    "refresh_token_ttl": 604800,
    "token_rotation": True,
    "max_sessions_per_user": 10,
    "session_inactivity_timeout": 1800,
    "auto_setup": True
}

CORS_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "allow_origins": ["*"],
    "allow_credentials": False,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "allow_headers": ["*"],
    "max_age": 3600
}

OBSERVABILITY_DEFAULTS: Dict[str, Any] = {
    "health_checks": {
        "enabled": True,
        "endpoint": "/health",
        "interval_seconds": 30
    },
    "metrics": {
        "enabled": True,
        "collect_operation_metrics": True,
        "collect_performance_metrics": True,
        "custom_metrics": []
    },
    "logging": {
        "level": "INFO",
        "format": "json",
        "include_request_id": True,
        "log_sensitive_data": False
    }
}

