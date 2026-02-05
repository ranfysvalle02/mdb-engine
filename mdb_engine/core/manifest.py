"""
Manifest validation and parsing system.

This module provides:
- Multi-version schema support
- Schema migration functions for upgrading manifests
- Optimized validation with caching for scale
- Parallel manifest processing capabilities

This module is part of MDB_ENGINE - MongoDB Engine.

SCHEMA VERSIONING STRATEGY
==========================

Versions:
- 1.0: Initial schema (default for manifests without version field)
- 2.0: Current schema with all features (auth.policy, auth.users, managed_indexes, etc.)

Migration Strategy:
- Automatically detects schema version from manifest
- Migrates older versions to current schema if needed
- Allows apps to specify target schema version

For Scale:
- Schema validation results are cached
- Supports parallel manifest processing
- Lazy schema loading for multiple apps
- Optimized validation paths for common cases
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jsonschema import SchemaError, ValidationError, validate

from ..constants import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_SCHEMA_VERSION,
    MAX_TTL_SECONDS,
    MAX_VECTOR_DIMENSIONS,
    MIN_TTL_SECONDS,
    MIN_VECTOR_DIMENSIONS,
)

logger = logging.getLogger(__name__)

# Schema registry: maps version -> schema definition
SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {}

# Validation cache: maps (manifest_hash, version) -> validation_result
_validation_cache: dict[str, tuple[bool, str | None, list[str] | None]] = {}
_cache_lock = asyncio.Lock()


def _convert_tuples_to_lists(obj: Any) -> Any:
    """
    Recursively convert tuples to lists for JSON schema compatibility.

    This function normalizes Python data structures to be JSON schema compliant.
    JSON schema expects lists (arrays), but Python code often uses tuples for
    immutable sequences (e.g., index keys: [("field1", 1), ("field2", -1)]).

    This normalization should happen at the API boundary (e.g., in register_app)
    before validation, keeping the validation layer schema-agnostic.

    Args:
        obj: Object to convert (dict, list, tuple, or primitive)

    Returns:
        Object with all tuples converted to lists (preserves structure)

    Example:
        >>> _convert_tuples_to_lists({"keys": [("field1", 1)]})
        {"keys": [["field1", 1]]}
    """
    if isinstance(obj, tuple):
        return list(obj)
    elif isinstance(obj, dict):
        return {key: _convert_tuples_to_lists(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_convert_tuples_to_lists(item) for item in obj]
    else:
        return obj


def _get_manifest_hash(manifest_data: dict[str, Any]) -> str:
    """Generate a hash for manifest caching."""
    import json

    # Normalize manifest by removing metadata fields that don't affect validation
    normalized = {
        k: v for k, v in manifest_data.items() if k not in ["_id", "_updated", "_created", "url"]
    }
    normalized_str = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(normalized_str.encode()).hexdigest()[:16]


# JSON Schema definition for manifest.json (Version 2.0 - Current)
MANIFEST_SCHEMA_V2 = {
    "type": "object",
    "properties": {
        "schema_version": {
            "type": "string",
            "pattern": "^\\d+\\.\\d+$",
            "default": "2.0",
            "description": (
                "Schema version for this manifest (format: 'major.minor'). "
                "Defaults to 2.0 if not specified."
            ),
        },
        "slug": {
            "type": "string",
            "pattern": "^[a-z0-9_-]+$",
            "description": "App slug (lowercase alphanumeric, underscores, hyphens)",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Human-readable app name",
        },
        "description": {"type": "string", "description": "App description"},
        "status": {
            "type": "string",
            "enum": ["active", "draft", "archived", "inactive"],
            "default": "draft",
            "description": "App status",
        },
        "auth": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["app", "shared"],
                    "default": "app",
                    "description": (
                        "Authentication mode: 'app' for per-app tokens "
                        "(isolated auth per app, default), 'shared' for "
                        "shared user pool with SSO across all apps. "
                        "Modes are mutually exclusive."
                    ),
                },
                "roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Available roles for this app (shared mode only). "
                        "Example: ['viewer', 'editor', 'admin']"
                    ),
                },
                "default_role": {
                    "type": "string",
                    "description": (
                        "Default role assigned to new users for this app "
                        "(shared mode only). Must be one of the defined roles."
                    ),
                },
                "require_role": {
                    "type": "string",
                    "description": (
                        "Minimum role required to access this app "
                        "(shared mode only). Users without this role "
                        "will be denied access."
                    ),
                },
                "public_routes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Routes that don't require authentication. "
                        "Supports wildcards, e.g., ['/health', '/api/public/*']. "
                        "Works in both auth modes."
                    ),
                },
                "auth_hub_url": {
                    "type": "string",
                    "format": "uri",
                    "description": (
                        "URL of the authentication hub for SSO apps (shared mode only). "
                        "Used for redirecting unauthenticated users to login. "
                        "Example: 'http://localhost:8000' or 'https://auth.example.com'. "
                        "Can be overridden via AUTH_HUB_URL environment variable."
                    ),
                },
                "related_apps": {
                    "type": "object",
                    "additionalProperties": {"type": "string", "format": "uri"},
                    "description": (
                        "Map of related app slugs to their URLs for cross-app navigation "
                        "(useful in shared auth mode). Keys are app slugs, values are URLs. "
                        "Example: {'dashboard': 'http://localhost:8001', "
                        "'click_tracker': 'http://localhost:8000'}. "
                        "Can be overridden via {APP_SLUG_UPPER}_URL environment variables."
                    ),
                },
                "policy": {
                    "type": "object",
                    "properties": {
                        "required": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Whether authentication is required "
                                "(default: true). If false, allows anonymous "
                                "access but still checks other policies."
                            ),
                        },
                        "provider": {
                            "type": "string",
                            "enum": ["casbin", "oso", "custom"],
                            "default": "casbin",
                            "description": (
                                "Authorization provider to use. 'casbin' "
                                "(default) auto-creates Casbin with MongoDB "
                                "adapter, 'oso' uses OSO/Polar, 'custom' "
                                "expects manual provider setup."
                            ),
                        },
                        "authorization": {
                            "type": "object",
                            "properties": {
                                # Casbin-specific properties
                                "model": {
                                    "type": "string",
                                    "default": "rbac",
                                    "description": (
                                        "Casbin model type or path. Use 'rbac' "
                                        "for default RBAC model, or provide "
                                        "path to custom model file. Only used "
                                        "when provider is 'casbin'."
                                    ),
                                },
                                "policies_collection": {
                                    "type": "string",
                                    "pattern": "^[a-zA-Z0-9_]+$",
                                    "default": "casbin_policies",
                                    "description": (
                                        "MongoDB collection name for storing "
                                        "Casbin policies (default: "
                                        "'casbin_policies'). Only used when "
                                        "provider is 'casbin'."
                                    ),
                                },
                                "link_users_roles": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "If true, automatically assign Casbin "
                                        "roles to app-level users when they are "
                                        "created or updated. Only used when "
                                        "provider is 'casbin'."
                                    ),
                                },
                                "default_roles": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "List of default roles to create in "
                                        "Casbin (e.g., ['user', 'admin']). "
                                        "These roles are created automatically "
                                        "when the provider is initialized. "
                                        "Only used when provider is 'casbin'."
                                    ),
                                },
                                # OSO Cloud-specific properties
                                "api_key": {
                                    "type": ["string", "null"],
                                    "description": (
                                        "OSO Cloud API key. If not provided, "
                                        "reads from OSO_AUTH environment "
                                        "variable. Only used when provider is "
                                        "'oso'."
                                    ),
                                },
                                "url": {
                                    "type": ["string", "null"],
                                    "description": (
                                        "OSO Cloud URL. If not provided, "
                                        "reads from OSO_URL environment "
                                        "variable. Only used when provider is "
                                        "'oso'."
                                    ),
                                },
                                "initial_roles": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "user": {
                                                "type": "string",
                                                "format": "email",
                                            },
                                            "role": {"type": "string"},
                                            "resource": {
                                                "type": "string",
                                                "default": "app",
                                            },
                                        },
                                        "required": ["user", "role"],
                                        "additionalProperties": False,
                                    },
                                    "description": (
                                        "Initial role assignments to set up in "
                                        "OSO Cloud on startup. Only used when "
                                        "provider is 'oso'. Example: "
                                        '[{"user": "admin@example.com", '
                                        '"role": "admin"}]'
                                    ),
                                },
                                "initial_policies": {
                                    "type": "array",
                                    "items": {
                                        "oneOf": [
                                            {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "minItems": 3,
                                                "maxItems": 3,
                                                "description": (
                                                    "Casbin policy as array: "
                                                    '["role", "resource", "action"]'
                                                ),
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "role": {"type": "string"},
                                                    "resource": {
                                                        "type": "string",
                                                        "default": "documents",
                                                    },
                                                    "action": {"type": "string"},
                                                },
                                                "required": ["role", "action"],
                                                "additionalProperties": False,
                                                "description": (
                                                    "OSO policy as object: "
                                                    '{"role": "admin", "resource": "documents", '
                                                    '"action": "read"}'
                                                ),
                                            },
                                        ],
                                    },
                                    "description": (
                                        "Initial permission policies to set up on startup. "
                                        "For Casbin provider: use arrays like "
                                        '["admin", "clicks", "read"]. '
                                        "For OSO Cloud provider: use objects like "
                                        '{"role": "admin", "resource": "documents", '
                                        '"action": "read"}.'
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Authorization configuration. For Casbin "
                                "provider: use 'model', 'policies_collection', "
                                "'default_roles'. For OSO Cloud provider: use "
                                "'api_key' (or env var), 'url' (or env var), "
                                "'initial_roles', 'initial_policies'."
                            ),
                        },
                        "allowed_roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of roles that can access this app "
                                "(e.g., ['admin', 'developer']). Users must "
                                "have at least one of these roles."
                            ),
                        },
                        "allowed_users": {
                            "type": "array",
                            "items": {"type": "string", "format": "email"},
                            "description": (
                                "List of specific user emails that can access "
                                "this app (whitelist). If provided, only "
                                "these users can access regardless of roles."
                            ),
                        },
                        "denied_users": {
                            "type": "array",
                            "items": {"type": "string", "format": "email"},
                            "description": (
                                "List of user emails that are explicitly "
                                "denied access (blacklist). Takes precedence "
                                "over allowed_users and allowed_roles."
                            ),
                        },
                        "required_permissions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of required permissions (format: "
                                "'resource:action', e.g., ['apps:view', "
                                "'apps:manage_own']). User must have all "
                                "listed permissions."
                            ),
                        },
                        "custom_resource": {
                            "type": "string",
                            "pattern": "^[a-z0-9_:]+$",
                            "description": (
                                "Custom Casbin resource name (e.g., "
                                "'app:storyweaver'). If not provided, "
                                "defaults to 'app:{slug}'."
                            ),
                        },
                        "custom_actions": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["access", "read", "write", "admin"],
                            },
                            "description": (
                                "Custom actions to check (defaults to "
                                "['access']). Used with custom_resource for "
                                "fine-grained permission checks."
                            ),
                        },
                        "allow_anonymous": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "If true, allows anonymous (unauthenticated) "
                                "access. Only applies if required is false or "
                                "not specified."
                            ),
                        },
                        "owner_can_access": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "If true (default), the app owner "
                                "(developer_id) can always access the app."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Intelligent authorization policy for app-level access "
                        "control. Supports role-based, user-based, and "
                        "permission-based access. Takes precedence over "
                        "auth.policy.required."
                    ),
                },
                "users": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable app-level user management. When "
                                "enabled, app manages its own users separate "
                                "from platform users."
                            ),
                        },
                        "strategy": {
                            "type": "string",
                            "enum": [
                                "app_users",
                                "anonymous_session",
                            ],
                            "default": "app_users",
                            "description": (
                                "User management strategy. 'app_users' = "
                                "app-specific user accounts, "
                                "'anonymous_session' = session-based anonymous "
                                "users."
                            ),
                        },
                        "collection_name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_]+$",
                            "default": "users",
                            "description": (
                                "Collection name for app-specific users "
                                "(default: 'users'). Will be prefixed with "
                                "app slug."
                            ),
                        },
                        "session_cookie_name": {
                            "type": "string",
                            "pattern": "^[a-z0-9_-]+$",
                            "default": "app_session",
                            "description": (
                                "Cookie name for app-specific session "
                                "(default: 'app_session'). Will be suffixed "
                                "with app slug."
                            ),
                        },
                        "session_ttl_seconds": {
                            "type": "integer",
                            "minimum": 60,
                            "default": 86400,
                            "description": (
                                "Session TTL in seconds (default: 86400 = "
                                "24 hours). Used for app-specific sessions."
                            ),
                        },
                        "allow_registration": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Allow users to self-register in the app "
                                "(when strategy is 'app_users')."
                            ),
                        },
                        "link_platform_users": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Link app users to platform users. Allows platform users "
                                "to have app-specific profiles."
                            ),
                        },
                        "anonymous_user_prefix": {
                            "type": "string",
                            "default": "guest",
                            "description": (
                                "Prefix for anonymous user IDs (default: "
                                "'guest'). Used for anonymous_session strategy."
                            ),
                        },
                        "user_id_field": {
                            "type": "string",
                            "default": "app_user_id",
                            "description": (
                                "Field name in platform user JWT "
                                "for storing app user ID "
                                "(default: 'app_user_id'). "
                                "Used for linking."
                            ),
                        },
                        "demo_users": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "email": {
                                        "type": "string",
                                        "format": "email",
                                        "description": (
                                            "Email address for demo user "
                                            "(defaults to platform demo email "
                                            "if not specified)"
                                        ),
                                    },
                                    "password": {
                                        "type": "string",
                                        "description": (
                                            "Password for demo user (defaults "
                                            "to platform demo password if not "
                                            "specified, or plain text for demo "
                                            "purposes)"
                                        ),
                                    },
                                    "role": {
                                        "type": "string",
                                        "default": "user",
                                        "description": (
                                            "Role for demo user in app " "(default: 'user')"
                                        ),
                                    },
                                    "auto_create": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": (
                                            "Automatically create this demo "
                                            "user if it doesn't exist "
                                            "(default: true)"
                                        ),
                                    },
                                    "link_to_platform": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": (
                                            "Link this demo user to platform "
                                            "demo user (if platform demo "
                                            "exists, default: false)"
                                        ),
                                    },
                                    "extra_data": {
                                        "type": "object",
                                        "description": (
                                            "Additional data to store with "
                                            "demo user (e.g., store_id, "
                                            "preferences, etc.)"
                                        ),
                                    },
                                },
                                "required": [],
                            },
                            "description": (
                                "Array of demo users to automatically "
                                "create/link for this app. If empty, "
                                "automatically uses platform demo user if "
                                "available."
                            ),
                        },
                        "auto_link_platform_demo": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Automatically link platform demo user to "
                                "experiment demo user if platform demo exists "
                                "(default: true). Works in combination with "
                                "link_platform_users and demo_users."
                            ),
                        },
                        "demo_user_seed_strategy": {
                            "type": "string",
                            "enum": ["auto", "manual", "disabled"],
                            "default": "auto",
                            "description": (
                                "Strategy for demo user seeding: 'auto' = "
                                "automatically create/link on first access or "
                                "actor init, 'manual' = require explicit "
                                "creation via API, 'disabled' = no automatic "
                                "demo user handling (default: 'auto')"
                            ),
                        },
                        "allow_demo_access": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable automatic demo user access. When "
                                "enabled, unauthenticated users are "
                                "automatically logged in as demo user, "
                                "providing seamless demo experience. "
                                "Requires demo users to be configured via "
                                "demo_users or auto_link_platform_demo. "
                                "(default: false)"
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "App-level user management configuration. Enables apps "
                        "to have their own user accounts and sessions "
                        "independent of platform authentication."
                    ),
                },
                "rate_limits": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "max_attempts": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 5,
                                "description": (
                                    "Maximum attempts allowed in the time window " "(default: 5)."
                                ),
                            },
                            "window_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 300,
                                "description": (
                                    "Time window in seconds for rate limiting "
                                    "(default: 300 = 5 minutes)."
                                ),
                            },
                        },
                        "additionalProperties": False,
                    },
                    "description": (
                        "Rate limiting configuration for auth endpoints. "
                        "Keys are endpoint paths (e.g., '/login', '/register'). "
                        "Example: {'/login': {'max_attempts': 5, 'window_seconds': 300}}"
                    ),
                },
                "audit": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable audit logging for authentication events "
                                "(default: true for shared auth mode)."
                            ),
                        },
                        "retention_days": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 90,
                            "description": (
                                "Number of days to retain audit logs " "(default: 90 days)."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Audit logging configuration for authentication events. "
                        "Logs are stored in MongoDB with automatic TTL cleanup."
                    ),
                },
                "csrf_protection": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable CSRF protection "
                                        "(default: true for shared auth mode)."
                                    ),
                                },
                                "exempt_routes": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Routes exempt from CSRF validation. "
                                        "Supports wildcards (e.g., '/api/*'). "
                                        "Defaults to public_routes if not specified."
                                    ),
                                },
                                "rotate_tokens": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Rotate CSRF token on each request "
                                        "(more secure, less convenient). Default: false."
                                    ),
                                },
                                "token_ttl": {
                                    "type": "integer",
                                    "minimum": 60,
                                    "default": 3600,
                                    "description": (
                                        "CSRF token TTL in seconds " "(default: 3600 = 1 hour)."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                        },
                    ],
                    "default": True,
                    "description": (
                        "CSRF protection configuration. Auto-enabled for shared "
                        "auth mode. Set to false to disable, or provide object "
                        "for detailed configuration. Uses double-submit cookie "
                        "pattern with SameSite=Lax cookies."
                    ),
                },
                "security": {
                    "type": "object",
                    "properties": {
                        "hsts": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable HSTS header in production " "(default: true)."
                                    ),
                                },
                                "max_age": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "default": 31536000,
                                    "description": (
                                        "HSTS max-age in seconds " "(default: 31536000 = 1 year)."
                                    ),
                                },
                                "include_subdomains": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Include subdomains in HSTS policy " "(default: true)."
                                    ),
                                },
                                "preload": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Add preload directive for HSTS preload "
                                        "list submission (default: false). Only "
                                        "enable if you're ready for permanent HTTPS."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "HTTP Strict Transport Security configuration. "
                                "Forces HTTPS connections in production."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Security settings including HSTS, headers, and other " "security controls."
                    ),
                },
                "jwt": {
                    "type": "object",
                    "properties": {
                        "algorithm": {
                            "type": "string",
                            "enum": ["HS256", "RS256", "ES256"],
                            "default": "HS256",
                            "description": (
                                "JWT signing algorithm. HS256 (HMAC, default) "
                                "uses symmetric secret. RS256 (RSA) and ES256 "
                                "(ECDSA) use asymmetric key pairs for better "
                                "security in distributed systems."
                            ),
                        },
                        "token_expiry_hours": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 24,
                            "description": ("JWT token expiry in hours (default: 24)."),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "JWT configuration for shared auth mode. "
                        "Controls algorithm and token lifetime."
                    ),
                },
                "password_policy": {
                    "type": "object",
                    "properties": {
                        "min_length": {
                            "type": "integer",
                            "minimum": 6,
                            "default": 12,
                            "description": ("Minimum password length (default: 12)."),
                        },
                        "min_entropy_bits": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 50,
                            "description": (
                                "Minimum password entropy in bits "
                                "(default: 50). Set to 0 to disable."
                            ),
                        },
                        "require_uppercase": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Require at least one uppercase letter " "(default: true)."
                            ),
                        },
                        "require_lowercase": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Require at least one lowercase letter " "(default: true)."
                            ),
                        },
                        "require_numbers": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Require at least one number " "(default: true)."),
                        },
                        "require_special": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Require at least one special character " "(default: false)."
                            ),
                        },
                        "check_common_passwords": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Check against common password list " "(default: true)."
                            ),
                        },
                        "check_breaches": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Check against HaveIBeenPwned breach database "
                                "(default: false). Requires network access."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Password policy configuration for shared auth mode. "
                        "Enforces password strength requirements."
                    ),
                },
                "session_binding": {
                    "type": "object",
                    "properties": {
                        "bind_ip": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Bind sessions to IP address. Strict mode: "
                                "reject if IP changes (default: false)."
                            ),
                        },
                        "bind_fingerprint": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Bind sessions to device fingerprint. Soft mode: "
                                "log warning if fingerprint changes (default: true)."
                            ),
                        },
                        "allow_ip_change_with_reauth": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Allow IP change if user re-authenticates "
                                "(default: true). Only applies when bind_ip=true."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Session binding configuration. Ties sessions to client "
                        "characteristics for additional security against session "
                        "hijacking."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Authentication and authorization configuration. Combines "
                "authorization policy (who can access) and user management "
                "(user accounts and sessions)."
            ),
        },
        "token_management": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable enhanced token management features "
                        "(refresh tokens, blacklist, sessions). Default: true."
                    ),
                },
                "access_token_ttl": {
                    "type": "integer",
                    "minimum": 60,
                    "default": 900,
                    "description": ("Access token TTL in seconds " "(default: 900 = 15 minutes)."),
                },
                "refresh_token_ttl": {
                    "type": "integer",
                    "minimum": 3600,
                    "default": 604800,
                    "description": ("Refresh token TTL in seconds " "(default: 604800 = 7 days)."),
                },
                "token_rotation": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable refresh token rotation "
                        "(new refresh token on each use). Default: true."
                    ),
                },
                "max_sessions_per_user": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 10,
                    "description": (
                        "Maximum number of concurrent sessions per user " "(default: 10)."
                    ),
                },
                "session_inactivity_timeout": {
                    "type": "integer",
                    "minimum": 60,
                    "default": 1800,
                    "description": (
                        "Session inactivity timeout in seconds before "
                        "automatic cleanup (default: 1800 = 30 minutes)."
                    ),
                },
                "security": {
                    "type": "object",
                    "properties": {
                        "require_https": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Require HTTPS in production " "(default: false, auto-detected)."
                            ),
                        },
                        "cookie_secure": {
                            "type": "string",
                            "enum": ["auto", "true", "false"],
                            "default": "auto",
                            "description": (
                                "Secure cookie flag: 'auto' = detect from "
                                "request, 'true' = always secure, "
                                "'false' = never secure (default: 'auto')."
                            ),
                        },
                        "cookie_samesite": {
                            "type": "string",
                            "enum": ["strict", "lax", "none"],
                            "default": "lax",
                            "description": "SameSite cookie attribute (default: 'lax').",
                        },
                        "cookie_httponly": {
                            "type": "boolean",
                            "default": True,
                            "description": ("HttpOnly cookie flag (default: true)."),
                        },
                        "csrf_protection": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Enable CSRF protection (default: true)."),
                        },
                        "rate_limiting": {
                            "type": "object",
                            "properties": {
                                "login": {
                                    "type": "object",
                                    "properties": {
                                        "max_attempts": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 5,
                                        },
                                        "window_seconds": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 300,
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                                "register": {
                                    "type": "object",
                                    "properties": {
                                        "max_attempts": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 3,
                                        },
                                        "window_seconds": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 600,
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                                "refresh": {
                                    "type": "object",
                                    "properties": {
                                        "max_attempts": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 10,
                                        },
                                        "window_seconds": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "default": 60,
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "additionalProperties": False,
                            "description": ("Rate limiting configuration per endpoint type."),
                        },
                        "password_policy": {
                            "type": "object",
                            "properties": {
                                "allow_plain_text": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Allow plain text passwords "
                                        "(NOT recommended, default: false)"
                                    ),
                                },
                                "min_length": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 8,
                                    "description": "Minimum password length (default: 8)",
                                },
                                "require_uppercase": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Require uppercase letters (default: true)",
                                },
                                "require_lowercase": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Require lowercase letters " "(default: true)"),
                                },
                                "require_numbers": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Require numbers (default: true)"),
                                },
                                "require_special": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Require special characters " "(default: false)"
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": ("Password policy configuration"),
                        },
                        "session_fingerprinting": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable session fingerprinting " "(default: true)"
                                    ),
                                },
                                "validate_on_login": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Validate fingerprint on login " "(default: true)"
                                    ),
                                },
                                "validate_on_refresh": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Validate fingerprint on token refresh " "(default: true)"
                                    ),
                                },
                                "validate_on_request": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Validate fingerprint on every request "
                                        "(default: false, may impact performance)"
                                    ),
                                },
                                "strict_mode": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Strict mode: reject requests if "
                                        "fingerprint doesn't match "
                                        "(default: false)"
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": "Session fingerprinting configuration for security",
                        },
                        "account_lockout": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Enable account lockout (default: true)",
                                },
                                "max_failed_attempts": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 5,
                                    "description": (
                                        "Maximum failed login attempts before "
                                        "lockout (default: 5)"
                                    ),
                                },
                                "lockout_duration_seconds": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 900,
                                    "description": (
                                        "Lockout duration in seconds " "(default: 900 = 15 minutes)"
                                    ),
                                },
                                "reset_on_success": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Reset failed attempts counter on "
                                        "successful login (default: true)"
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": "Account lockout configuration",
                        },
                        "ip_validation": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Enable IP address validation " "(default: false)"
                                    ),
                                },
                                "strict": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Strict mode: reject requests if IP "
                                        "changes (default: false)"
                                    ),
                                },
                                "allow_ip_change": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Allow IP address changes during session " "(default: true)"
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": "IP address validation configuration",
                        },
                        "token_fingerprinting": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable token fingerprinting " "(default: true)"
                                    ),
                                },
                                "bind_to_device": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Bind tokens to device ID " "(default: true)"),
                                },
                            },
                            "additionalProperties": False,
                            "description": ("Token fingerprinting configuration"),
                        },
                    },
                    "additionalProperties": False,
                    "description": ("Security settings for token management."),
                },
                "auto_setup": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Automatically set up token management on app startup " "(default: true)."
                    ),
                },
            },
            "additionalProperties": False,
            "description": "Token management configuration for enhanced authentication features.",
        },
        "data_scope": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "default": ["self"],
            "description": "List of app slugs whose data this app can access",
        },
        "pip_deps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of pip dependencies for isolated environment",
        },
        "managed_indexes": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/indexDefinition"},
                    "minItems": 1,
                }
            },
            "description": "Collection name -> list of index definitions",
        },
        "encrypted_fields": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "List of field names to encrypt in this collection",
                }
            },
            "additionalProperties": False,
            "description": (
                "Collection name -> list of fields to encrypt using MongoDB CSFLE. "
                "Similar to managed_indexes, enables encrypting fields in ANY collection. "
                'Example: {"payments": ["card_number", "cvv"], "users": ["ssn"]}. '
                "Collection names are automatically prefixed with app slug. "
                "Requires CRYPT_SHARED_LIB_PATH environment variable."
            ),
        },
        "encryption_config": {
            "type": "object",
            "properties": {
                "kms_provider": {
                    "type": "string",
                    "enum": ["local", "aws", "azure", "gcp"],
                    "default": "local",
                    "description": (
                        "KMS provider for encryption keys. Default: 'local'. "
                        "For production, use 'aws', 'azure', or 'gcp' with "
                        "appropriate credentials in environment variables."
                    ),
                },
                "key_vault_namespace": {
                    "type": "string",
                    "default": "encryption.__keyVault",
                    "description": "Namespace for CSFLE key vault (database.collection)",
                },
                "crypt_shared_lib_path": {
                    "type": "string",
                    "description": (
                        "Path to crypt_shared library. Auto-detected from "
                        "CRYPT_SHARED_LIB_PATH environment variable if not set."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Global encryption settings for encrypted_fields. "
                "Configures KMS provider and key vault for all encrypted collections."
            ),
        },
        "collection_settings": {
            "type": "object",
            "patternProperties": {"^[a-zA-Z0-9_]+$": {"$ref": "#/definitions/collectionSettings"}},
            "description": "Collection name -> collection settings",
        },
        "websockets": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z0-9_-]+$": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "pattern": "^/[a-zA-Z0-9_/-]+$",
                            "description": (
                                "WebSocket path (e.g., '/ws', '/events', "
                                "'/realtime'). Must start with '/'. "
                                "Routes are automatically registered."
                            ),
                        },
                        "auth": {
                            "type": "object",
                            "properties": {
                                "required": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Whether authentication is required "
                                        "(default: true). Uses app's auth.policy "
                                        "if not specified."
                                    ),
                                },
                                "allow_anonymous": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Allow anonymous connections even if "
                                        "auth is required (default: false)"
                                    ),
                                },
                                "csrf_required": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Require CSRF validation for WebSocket connections "
                                        "(default: true - security by default). "
                                        "When true, uses encrypted session keys stored in "
                                        "private collection for CSRF protection. "
                                        "Set to false to use Origin validation + "
                                        "SameSite cookies only."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Authentication configuration. If not specified, "
                                "uses app's auth.policy settings."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Description of what this WebSocket endpoint " "is used for"
                            ),
                        },
                        "ping_interval": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 300,
                            "default": 30,
                            "description": (
                                "Ping interval in seconds to keep connection "
                                "alive (default: 30, min: 5, max: 300)"
                            ),
                        },
                        "ticket_ttl_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 60,
                            "default": 10,
                            "description": (
                                "WebSocket ticket time-to-live in seconds (default: 10). "
                                "Tickets are single-use authentication tokens for "
                                "WebSocket connections. "
                                "Shorter TTL (3-5s) provides better security but requires "
                                "faster connection. "
                                "Longer TTL (10-30s) is more forgiving for slow networks."
                            ),
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                    "description": (
                        "WebSocket endpoint configuration. Each endpoint is "
                        "automatically isolated to this app. Only 'path' is "
                        "required - all other settings have sensible defaults."
                    ),
                }
            },
            "description": (
                "WebSocket endpoints configuration. Super simple setup - "
                "just specify the path! Each endpoint is automatically "
                "scoped and isolated to this app. Key is the endpoint name "
                "(e.g., 'realtime', 'events'), value contains path and "
                "optional settings. Routes are automatically registered with "
                "FastAPI during app registration."
            ),
        },
        "embedding_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable semantic text splitting and embedding service. "
                        "When enabled, EmbeddingService will be available for "
                        "chunking text and generating embeddings."
                    ),
                },
                "max_tokens_per_chunk": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 10000,
                    "default": 1000,
                    "description": (
                        "Maximum tokens per chunk when splitting text. The "
                        "semantic-text-splitter ensures chunks never exceed "
                        "this limit while preserving semantic boundaries."
                    ),
                },
                "tokenizer_model": {
                    "type": "string",
                    "default": "gpt-3.5-turbo",
                    "description": (
                        "Optional: Tokenizer model name for counting tokens "
                        "during chunking (e.g., 'gpt-3.5-turbo', 'gpt-4', "
                        "'gpt-4o'). Must be a valid OpenAI model name. "
                        "Defaults to 'gpt-3.5-turbo' (uses cl100k_base "
                        "encoding internally, which works for GPT-3.5, GPT-4, "
                        "and most models). This is ONLY for token counting, "
                        "NOT for embeddings. You typically don't need to set "
                        "this - the platform default works for most cases."
                    ),
                },
                "default_embedding_model": {
                    "type": "string",
                    "default": "text-embedding-3-small",
                    "description": (
                        "Default embedding model for chunk embeddings "
                        "(e.g., 'text-embedding-3-small', "
                        "'text-embedding-ada-002'). Examples should implement "
                        "their own embedding clients."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Semantic text splitting and embedding configuration. "
                "Enables intelligent chunking with Rust-based "
                "semantic-text-splitter. Examples should implement their own "
                "embedding clients. Perfect for RAG (Retrieval Augmented "
                "Generation) applications."
            ),
        },
        "llm_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable LLM service for this app. When enabled, "
                        "LLMService will be available for chat completions and text generation. "
                        "Powered by LiteLLM - supports 100+ LLM providers. "
                        "Auto-detects provider from environment variables or uses configured model."
                    ),
                },
                "default_model": {
                    "type": "string",
                    "default": "openai/gpt-4o",
                    "description": (
                        "Default LiteLLM model string for chat completions. "
                        "Format: 'provider/model' "
                        "(e.g., 'openai/gpt-4o', 'gemini/gemini-3-flash-preview', "
                        "'anthropic/claude-sonnet-4-20250514', 'azure/gpt-4o'). "
                        "Supports 100+ providers via LiteLLM. "
                        "See: https://docs.litellm.ai/docs/providers"
                    ),
                },
                "fallbacks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of fallback models to try if the primary model fails. "
                        "If the primary model returns an error (rate limit, timeout, etc.), "
                        "LiteLLM will automatically try the next model in the list. "
                        "Example: ['gpt-4o-mini', 'claude-3-5-haiku-20241022']. "
                        "Models should be in LiteLLM format "
                        "(e.g., 'openai/gpt-4o-mini' or just 'gpt-4o-mini'). "
                        "This provides redundancy and automatic failover "
                        "without manual error handling."
                    ),
                },
                "litellm_config": {
                    "type": "object",
                    "description": (
                        "LiteLLM configuration options passed directly to LiteLLM. "
                        "Common options include: "
                        "- 'num_retries': Number of retries per request (default: 0) "
                        "- 'request_timeout': Request timeout in seconds (default: 600) "
                        "- 'max_budget': Maximum budget per request "
                        "- 'drop_params': Drop unsupported parameters "
                        "See: https://docs.litellm.ai/docs/completion/configuration for full list."
                    ),
                    "additionalProperties": True,
                },
                "temperature": {
                    "type": "number",
                    "default": 0.7,
                    "description": (
                        "Default temperature for LLM chat completions. "
                        "Provider-specific rules apply: "
                        "- Gemini models: Always enforced to 1.0 "
                        "- Azure OpenAI and OpenAI: Always enforced to 0.3 "
                        "Can be overridden per-request via chat_completion() temperature parameter."
                    ),
                },
                "persona": {
                    "type": "string",
                    "default": "helpful assistant",
                    "description": (
                        "Default persona/system prompt for LLM chat completions. "
                        "This will be automatically prepended as a system message "
                        "if no system message is present in the messages array. "
                        "Default: 'helpful assistant'."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "DEPRECATED: Legacy provider format (e.g., 'openai', 'azure', 'gemini'). "
                        "Use 'default_model' with LiteLLM format instead (e.g., 'openai/gpt-4o'). "
                        "This will be converted to LiteLLM format automatically."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "LLM service configuration. Powered by LiteLLM - "
                "provides unified interface for chat completions "
                "across 100+ providers "
                "(OpenAI, Azure OpenAI, Anthropic, Google Gemini, Cohere, "
                "HuggingFace, Bedrock, Vertex AI, Ollama, Groq, Together AI, "
                "and 90+ more). "
                "Auto-detects provider from environment variables if not specified. "
                "Supports fallback models for automatic failover "
                "and LiteLLM configuration options. "
                "Perfect for chat applications, RAG pipelines, and text generation. "
                "See: https://docs.litellm.ai/docs/providers for complete provider list."
            ),
        },
        "memory_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable memory service for this app. When "
                        "enabled, CustomMemoryService will be initialized and "
                        "available for intelligent memory management using "
                        "MongoDB Atlas Vector Search. Uses mdb_engine.embeddings and "
                        "OpenAI SDK directly. Configure embeddings and LLM via "
                        "environment variables (.env)."
                    ),
                },
                "encrypted": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable field-level encryption for memory content. "
                        "When true, sensitive memory fields (content, text) are "
                        "automatically encrypted at rest using MongoDB CSFLE. "
                        "Query fields (user_id, timestamps, importance) remain "
                        "searchable. Uses local KMS by default - auto-generates "
                        "ephemeral key if MDB_CSFLE_LOCAL_KEY not set. For "
                        "production, configure 'encryption' object with "
                        "AWS/Azure/GCP KMS. Requires CRYPT_SHARED_LIB_PATH "
                        "environment variable pointing to crypt_shared library."
                    ),
                },
                "encryption": {
                    "type": "object",
                    "properties": {
                        "kms_provider": {
                            "type": "string",
                            "enum": ["local", "aws", "azure", "gcp"],
                            "default": "local",
                            "description": (
                                "KMS provider for encryption keys. 'local' uses "
                                "MDB_CSFLE_LOCAL_KEY env var (auto-generates if "
                                "not set). 'aws', 'azure', 'gcp' use respective "
                                "cloud KMS services with credentials from env vars."
                            ),
                        },
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": ["content", "text"],
                            "description": (
                                "Fields to encrypt (default: content, text). "
                                "Supports nested fields like 'metadata.pii'. "
                                "Query fields (user_id, embedding, etc.) should "
                                "NOT be encrypted as they need to remain searchable."
                            ),
                        },
                        "key_vault_namespace": {
                            "type": "string",
                            "default": "encryption.__keyVault",
                            "description": (
                                "Namespace for CSFLE key vault (database.collection). "
                                "Default: encryption.__keyVault"
                            ),
                        },
                        "crypt_shared_lib_path": {
                            "type": "string",
                            "description": (
                                "Path to crypt_shared library. Auto-detected from "
                                "CRYPT_SHARED_LIB_PATH environment variable if not set."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Advanced encryption settings (only used when encrypted=true). "
                        "Allows customizing KMS provider, encrypted fields, and key vault."
                    ),
                },
                "collection_name": {
                    "type": "string",
                    "pattern": "^[a-zA-Z0-9_]+$",
                    "description": (
                        "MongoDB collection name for storing memories "
                        "(defaults to '{app_slug}_memories'). Will be prefixed "
                        "with app slug if not already prefixed."
                    ),
                },
                "index_name": {
                    "type": "string",
                    "pattern": "^[a-zA-Z0-9_]+$",
                    "description": (
                        "MongoDB Atlas Vector Search index name "
                        "(defaults to '{collection_name}_vector_index'). "
                        "This is the name of the vector search index in MongoDB Atlas."
                    ),
                },
                "embedding_model_dims": {
                    "type": "integer",
                    "minimum": 128,
                    "maximum": 4096,
                    "default": 1536,
                    "description": (
                        "Dimensions of the embedding vectors (OPTIONAL - "
                        "auto-detected by embedding a test string). Only "
                        "specify if you need to override auto-detection. "
                        "Default: 1536. The system will automatically detect "
                        "the correct dimensions from your embedding model."
                    ),
                },
                "infer": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Whether to infer memories from conversations "
                        "(default: true). If false, stores messages as-is "
                        "without inference. Requires LLM configured via "
                        "environment variables if true."
                    ),
                },
                "embedding_model": {
                    "type": "string",
                    "description": (
                        "Embedding model name (e.g., 'text-embedding-3-small'). "
                        "If not provided, the memory service will use environment variables "
                        "or defaults."
                    ),
                },
                "chat_model": {
                    "type": "string",
                    "description": (
                        "Chat model name for inference (e.g., 'gpt-4o', 'gemini-3-flash-preview'). "
                        "Used as fallback for memory_llm_model if not specified. "
                        "If not provided, the memory service will use environment variables "
                        "or defaults."
                    ),
                },
                "memory_llm_model": {
                    "type": "string",
                    "description": (
                        "LLM model name for memory operations "
                        "(fact extraction, importance assessment). "
                        "Defaults to chat_model if not specified. "
                        "This allows using different models for chat "
                        "(via CognitiveEngine) vs memory operations. "
                        "Example: Use 'gemini-3-flash-preview' for chat "
                        "but 'gpt-4o' for memory operations. "
                        "Note: Memory operations currently require "
                        "OpenAI or Azure OpenAI compatible models."
                    ),
                },
                "temperature": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "default": 0.0,
                    "description": (
                        "Temperature for LLM inference in memory operations "
                        "(fact extraction, importance assessment, memory merging). "
                        "Can also be set via MEMORY_LLM_TEMPERATURE environment variable. "
                        "Default: 0.0 (deterministic output). "
                        "Higher values make output more random. "
                        "Only used if infer=true."
                    ),
                },
                "async_mode": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Whether to process memories asynchronously "
                        "(default: true). Enables better performance for "
                        "memory ingestion."
                    ),
                },
                "provider": {
                    "type": "string",
                    "enum": ["custom", "cognitive"],
                    "default": "cognitive",
                    "description": (
                        "Memory service provider to use (both use CognitiveMemoryService). "
                        "'cognitive' (default): Customizable memory service with optional "
                        "cognitive features. 'custom': Alias for cognitive (backwards "
                        "compatibility). Cognitive features can be disabled via "
                        "enable_cognitive=false or max_depth=None."
                    ),
                },
                "enable_cognitive": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable cognitive features (importance scoring, reinforcement, "
                        "decay, merging, pruning). Set to false for basic memory service "
                        "behavior."
                    ),
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 10000,
                    "default": 100,
                    "description": (
                        "Maximum number of memories to store per user (cognitive provider only). "
                        "When exceeded, least important memories are pruned."
                    ),
                },
                "similarity_threshold": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.7,
                    "description": (
                        "Similarity threshold for memory reinforcement (cognitive provider "
                        "only). Memories above this threshold are reinforced instead of "
                        "creating duplicates."
                    ),
                },
                "reinforcement_factor": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 2.0,
                    "default": 1.1,
                    "description": (
                        "Factor by which to increase importance when reinforcing memories "
                        "(cognitive provider only)."
                    ),
                },
                "decay_factor": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.99,
                    "description": (
                        "Factor by which to decrease importance for unused memories "
                        "(cognitive provider only)."
                    ),
                },
                "merge_threshold_low": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.7,
                    "description": (
                        "Lower similarity threshold for memory merging (cognitive provider only). "
                        "Memories between merge_threshold_low and merge_threshold_high are merged."
                    ),
                },
                "merge_threshold_high": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.85,
                    "description": (
                        "Upper similarity threshold for memory merging (cognitive provider only). "
                        "Memories between merge_threshold_low and merge_threshold_high are merged."
                    ),
                },
                "fusion": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable LLM-powered memory fusion for intelligent deduplication. "
                                "When enabled, semantically similar facts extracted from the same "
                                "message are fused into a single, high-quality memory using LLM."
                            ),
                        },
                        "use_llm": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Use LLM for fusion (default: true). If false, uses simple "
                                "heuristic merge (longest text, highest emotion, best category)."
                            ),
                        },
                        "similarity_threshold": {
                            "type": "number",
                            "minimum": 0.5,
                            "maximum": 1.0,
                            "default": 0.8,
                            "description": (
                                "Cosine similarity threshold for clustering facts. "
                                "Facts above this threshold are grouped and fused together."
                            ),
                        },
                        "fallback_to_simple": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Fall back to simple heuristic merge if LLM fusion fails. "
                                "Provides graceful degradation on LLM errors or timeouts."
                            ),
                        },
                        "parallel_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                            "description": (
                                "Maximum concurrent LLM fusion calls. "
                                "Controls parallelization for multiple clusters."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 60,
                            "default": 10,
                            "description": "Timeout for each LLM fusion call in seconds.",
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Memory fusion configuration. Enables intelligent deduplication "
                        "of facts extracted from the same message using LLM-powered semantic "
                        "synthesis. Prevents storing duplicates like 'User loves chocolate' "
                        "and 'Chocolate is user's favorite candy' by fusing them into a "
                        "single, comprehensive memory."
                    ),
                },
                "redaction": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable redaction layer to protect sensitive data "
                                "from being stored in memory or sent to LLMs. "
                                "Disabled by default - opt-in for privacy protection."
                            ),
                        },
                        "provider": {
                            "type": "string",
                            "enum": ["regexp", "presidio"],
                            "default": "regexp",
                            "description": (
                                "Redaction provider: 'regexp' (default, pattern-based) "
                                "or 'presidio' (ML-based, requires presidio-analyzer)."
                            ),
                        },
                        "replacement": {
                            "type": "string",
                            "default": "[REDACTED]",
                            "description": "Text to replace sensitive data with.",
                        },
                        "patterns": {
                            "type": "object",
                            "properties": {
                                "ssn": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Redact Social Security Numbers (XXX-XX-XXXX).",
                                },
                                "credit_card": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Redact credit card numbers (13-16 digits).",
                                },
                                "phone": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Redact phone numbers.",
                                },
                                "email": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Redact email addresses.",
                                },
                                "ip_address": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Redact IP addresses.",
                                },
                                "api_key": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Redact API keys and secrets.",
                                },
                                "password": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Redact password assignments.",
                                },
                                "custom": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Custom regex patterns to redact. "
                                        "Example: ['\\\\bsecret\\\\s*[:=]\\\\s*\\\\S+']"
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": "Configure which patterns to redact.",
                        },
                        "allow_list": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Values that should never be redacted. "
                                "Example: ['support@company.com']"
                            ),
                        },
                        "log_redactions": {
                            "type": "boolean",
                            "default": True,
                            "description": "Log redaction counts (without exposing data).",
                        },
                        "entities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Entity types to detect (Presidio provider only). "
                                "Example: ['NAME', 'PHONE_NUMBER', 'EMAIL_ADDRESS']"
                            ),
                        },
                        "language": {
                            "type": "string",
                            "default": "en",
                            "description": "Language code for Presidio provider (default: 'en').",
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Redaction layer configuration. Protects sensitive data "
                        "(SSN, credit cards, passwords, API keys) from being stored "
                        "in memory or sent to LLMs for fact extraction."
                    ),
                },
                "reflection": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable periodic memory consolidation. "
                                "Summarizes atomic memories into narrative reflections."
                            ),
                        },
                        "interval_hours": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 168,
                            "default": 24,
                            "description": "Hours between reflection cycles (default: 24).",
                        },
                        "message_threshold": {
                            "type": "integer",
                            "minimum": 10,
                            "maximum": 500,
                            "default": 50,
                            "description": (
                                "Number of memories to trigger reflection "
                                "(alternative to time-based trigger)."
                            ),
                        },
                        "min_salience_to_keep": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.4,
                            "description": (
                                "Minimum importance score to keep after reflection. "
                                "Memories below this are pruned after consolidation."
                            ),
                        },
                        "store_reflections": {
                            "type": "boolean",
                            "default": True,
                            "description": "Store reflection summaries in separate collection.",
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Memory reflection and consolidation configuration. "
                        "Periodically consolidates atomic memories into narrative summaries "
                        "to prevent memory bloat and improve retrieval quality."
                    ),
                },
                "categories": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable memory categorization during fact extraction. "
                                "Categories: biographical, preferences, temporal, relational."
                            ),
                        },
                        "custom_categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Additional custom categories beyond the defaults. "
                                "Example: ['work', 'health', 'hobbies']"
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Memory categorization configuration. "
                        "Enhances fact extraction to categorize memories for "
                        "better organization and retrieval."
                    ),
                },
                "cognitive": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable advanced cognitive memory features. "
                                "Includes decay-aware retrieval, emotion tagging, "
                                "conflict resolution, and soft-delete pruning."
                            ),
                        },
                        "decay": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable Ebbinghaus Forgetting Curve decay. "
                                        "Memories weaken over time based on stability."
                                    ),
                                },
                                "default_stability_hours": {
                                    "type": "number",
                                    "minimum": 0.1,
                                    "maximum": 10000,
                                    "default": 24,
                                    "description": (
                                        "Default stability (half-life) in hours for new memories. "
                                        "Higher values = slower decay. Default: 24 hours."
                                    ),
                                },
                                "min_stability": {
                                    "type": "number",
                                    "minimum": 0.01,
                                    "maximum": 1,
                                    "default": 0.1,
                                    "description": (
                                        "Minimum stability value to prevent division by zero."
                                    ),
                                },
                                "use_server_side_pipeline": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Use MongoDB aggregation pipeline for decay calculations. "
                                        "Improves performance for large memory stores."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Decay configuration based on Ebbinghaus Forgetting Curve. "
                                "Retrieval strength = importance * exp(-time / stability)."
                            ),
                        },
                        "emotion": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable emotional intensity extraction (Flashbulb Memory). "
                                        "High-emotion facts get higher initial stability."
                                    ),
                                },
                                "flashbulb_threshold": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                    "default": 0.7,
                                    "description": (
                                        "Emotion threshold for flashbulb memory effect. "
                                        "Facts above this get significantly higher stability."
                                    ),
                                },
                                "max_stability_multiplier": {
                                    "type": "number",
                                    "minimum": 1,
                                    "maximum": 1000,
                                    "default": 100,
                                    "description": (
                                        "Maximum stability bonus for high-emotion memories. "
                                        "stability = default + (emotion * multiplier)."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Emotion (Flashbulb Memory) configuration. "
                                "High-emotion events are remembered with exceptional clarity."
                            ),
                        },
                        "conflict_resolution": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable LLM-based conflict detection. "
                                        "Prevents storing contradictory facts."
                                    ),
                                },
                                "similarity_threshold": {
                                    "type": "number",
                                    "minimum": 0.5,
                                    "maximum": 1,
                                    "default": 0.85,
                                    "description": (
                                        "Similarity threshold to consider facts related. "
                                        "Only check conflicts for highly similar memories."
                                    ),
                                },
                                "llm_model": {
                                    "type": "string",
                                    "description": (
                                        "Override LLM model for conflict detection. "
                                        "Defaults to memory_llm_model."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Conflict resolution (integrity check) configuration. "
                                "Prevents digital dementia by detecting contradictions."
                            ),
                        },
                        "pruning": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable automatic memory pruning when capacity exceeded."
                                    ),
                                },
                                "max_capacity": {
                                    "type": "integer",
                                    "minimum": 10,
                                    "maximum": 100000,
                                    "default": 1000,
                                    "description": (
                                        "Maximum active memories per user. "
                                        "Weakest memories are pruned when exceeded."
                                    ),
                                },
                                "prune_percentage": {
                                    "type": "number",
                                    "minimum": 0.01,
                                    "maximum": 0.5,
                                    "default": 0.1,
                                    "description": (
                                        "Extra percentage to prune to avoid constant triggers. "
                                        "Default: 10% buffer."
                                    ),
                                },
                                "strategy": {
                                    "type": "string",
                                    "enum": ["soft_delete", "hard_delete"],
                                    "default": "soft_delete",
                                    "description": (
                                        "Pruning strategy. 'soft_delete' moves to cold storage; "
                                        "'hard_delete' permanently removes memories."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Memory pruning configuration. "
                                "Removes weakest memories when capacity is exceeded."
                            ),
                        },
                        "cold_storage": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Enable cold storage for pruned memories. "
                                        "Provides paper trail for analytics and recovery."
                                    ),
                                },
                                "retention_days": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 3650,
                                    "default": 365,
                                    "description": (
                                        "Days to retain memories in cold storage. "
                                        "Default: 365 days (1 year)."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Cold storage (paper trail) configuration. "
                                "Preserves pruned memories for analytics and recovery."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Advanced cognitive memory configuration. "
                        "Enables biological-style memory features including "
                        "Ebbinghaus Forgetting Curve decay, Flashbulb Memory emotion tagging, "
                        "conflict resolution, and soft-delete pruning with cold storage."
                    ),
                },
                "graph": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable graph-powered memory layer. When enabled, memories "
                                "automatically extract nodes and relationships to the "
                                "knowledge graph. Requires graph_config to be enabled."
                            ),
                        },
                        "auto_extract": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Automatically extract graph nodes when memories are added. "
                                "If false, graph extraction must be triggered manually."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Graph-powered memory configuration. Enables automatic node "
                        "and relationship extraction from memories to the knowledge "
                        "graph. Works with graph_config to provide GraphRAG capabilities "
                        "for memory retrieval."
                    ),
                },
                "memory_types": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable memory type classification "
                                "(Cognitive Blueprint v2.0). Classifies memories as "
                                "semantic, entity, procedural, episodic, or working."
                            ),
                        },
                        "auto_detect": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Automatically detect memory type using LLM classification. "
                                "If false, uses default_type for all memories."
                            ),
                        },
                        "default_type": {
                            "type": "string",
                            "enum": ["semantic", "entity", "procedural", "episodic", "working"],
                            "default": "semantic",
                            "description": "Default memory type when auto_detect is disabled.",
                        },
                        "episodic_retention_days": {
                            "type": "integer",
                            "minimum": 30,
                            "maximum": 3650,
                            "default": 730,
                            "description": (
                                "Retention period for episodic memories in days "
                                "(default: 730 = 2 years). Episodic memories are "
                                "automatically deleted after this period."
                            ),
                        },
                        "working_ttl_hours": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 168,
                            "default": 24,
                            "description": (
                                "Time-to-live for working memory in hours (default: 24). "
                                "Working memory is automatically deleted after this period."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Memory types configuration (Cognitive Blueprint v2.0). "
                        "Enables directory strategy with semantic, entity, "
                        "procedural, episodic, and working memory types."
                    ),
                },
                "procedural": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable procedural memory support. Stores how-to "
                                "workflows, code snippets, and step-by-step procedures."
                            ),
                        },
                        "auto_extract": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Automatically extract procedural content from memories. "
                                "Detects code, workflows, and procedures."
                            ),
                        },
                        "detect_code": {
                            "type": "boolean",
                            "default": True,
                            "description": "Detect code snippets and programming instructions.",
                        },
                        "detect_workflows": {
                            "type": "boolean",
                            "default": True,
                            "description": "Detect step-by-step workflows and procedures.",
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Procedural memory configuration. Enables storage of how-to workflows, "
                        "code snippets, and procedures with success tracking."
                    ),
                },
                "consolidation": {
                    "type": "object",
                    "properties": {
                        "extract_entities": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Extract entities from episodic memories during consolidation. "
                                "Creates entity memories for people, projects, objects."
                            ),
                        },
                        "route_by_type": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Route consolidated memories to appropriate memory types. "
                                "Ensures memories are stored in the correct category."
                            ),
                        },
                        "link_to_graph": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Link extracted entities to GraphService during consolidation. "
                                "Creates graph nodes for entity memories."
                            ),
                        },
                        "extract_procedural": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Extract procedural knowledge during consolidation. "
                                "Identifies workflows and code patterns."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Consolidation configuration for reflection service. "
                        "Enhances memory consolidation with entity extraction, "
                        "routing, and procedural knowledge extraction."
                    ),
                },
                "persona": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable persona engine for dynamic persona management.",
                        },
                        "default_role": {
                            "type": "string",
                            "description": (
                                "Default persona role (e.g., 'AI Assistant', "
                                "'Senior Python Architect')."
                            ),
                        },
                        "default_description": {
                            "type": "string",
                            "description": (
                                "Default persona description explaining the AI's "
                                "identity and capabilities."
                            ),
                        },
                        "default_traits": {
                            "type": "object",
                            "description": (
                                "Default persona traits as key-value pairs. "
                                "Common traits: technical_focus, humor, formality, "
                                "empathy, creativity. Values typically range from "
                                "0.0 to 1.0."
                            ),
                        },
                    },
                    "additionalProperties": True,
                    "description": (
                        "Persona configuration for Context Engineering. "
                        "Defines the AI's identity, role, description, and behavioral traits. "
                        "Used by PersonaEngine to maintain consistent persona across conversations."
                    ),
                },
                "perceptions": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable perception analysis for memory context.",
                        },
                        "auto_analyze": {
                            "type": "boolean",
                            "default": True,
                            "description": "Automatically analyze perceptions from memories.",
                        },
                        "update_frequency": {
                            "type": "string",
                            "enum": ["per_interaction", "daily", "weekly"],
                            "default": "per_interaction",
                            "description": "Frequency of perception updates.",
                        },
                    },
                    "additionalProperties": True,
                    "description": (
                        "Perceptions configuration for cognitive memory features. "
                        "Enables analysis of user perceptions and context from memories."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Memory service configuration. Enables intelligent memory "
                "management that automatically extracts, stores, and retrieves "
                "user memories. Uses MongoDB Atlas Vector Search (native "
                "integration with mdb-engine). Uses mdb_engine.embeddings and OpenAI SDK directly. "
                "Configure AZURE_OPENAI_API_KEY/AZURE_OPENAI_ENDPOINT or OPENAI_API_KEY "
                "in your .env file."
            ),
        },
        "graph_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable graph service for this app (enabled by default). "
                        "Set to false to disable. Creates a standalone knowledge graph "
                        "with MongoDB $graphLookup traversal for multi-hop reasoning "
                        "queries. Can be used independently or with memory service."
                    ),
                },
                "collection_name": {
                    "type": "string",
                    "pattern": "^[a-zA-Z0-9_]+$",
                    "default": "__kg",
                    "description": (
                        "Collection name for knowledge graph nodes "
                        "(defaults to '__kg'). Will be prefixed with app slug."
                    ),
                },
                "auto_extract": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable LLM-powered automatic entity/relationship extraction. "
                        "Extracts nodes (people, interests, events, locations) and "
                        "edges (relationships) from text."
                    ),
                },
                "llm_model": {
                    "type": "string",
                    "description": (
                        "LLM model for graph extraction (e.g., 'openai/gpt-4o'). "
                        "Uses llm_config.default_model if not specified."
                    ),
                },
                "temperature": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "default": 0.0,
                    "description": "Temperature for LLM extraction (default: 0.0).",
                },
                "default_max_depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 2,
                    "description": (
                        "Default traversal depth for $graphLookup queries. "
                        "Depth 2 covers most use cases (e.g., 'brother's interests')."
                    ),
                },
                "vector_index_name": {
                    "type": "string",
                    "pattern": "^[a-zA-Z0-9_]+$",
                    "default": "graph_vector_index",
                    "description": "Name of the vector search index for hybrid search.",
                },
                "embedding_dims": {
                    "type": "integer",
                    "minimum": 128,
                    "maximum": 4096,
                    "default": 1536,
                    "description": "Embedding dimensions for vector index (default: 1536).",
                },
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "person",
                        "interest",
                        "event",
                        "location",
                        "organization",
                        "product",
                        "concept",
                    ],
                    "description": (
                        "Allowed node types for graph extraction. "
                        "LLM will classify nodes into these types."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Graph service configuration. Enables standalone knowledge graph "
                "with MongoDB $graphLookup for multi-hop traversal. Combines vector "
                "search (semantic entry points) with graph traversal (structural "
                "relationships) for GraphRAG. Can be used independently of memory service."
            ),
        },
        "cors": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable CORS for this app (default: false)",
                },
                "allow_origins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["*"],
                    "description": (
                        "List of allowed origins (use ['*'] for all origins, "
                        "not recommended for production)"
                    ),
                },
                "allow_credentials": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Allow credentials (cookies, authorization headers) " "in CORS requests"
                    ),
                },
                "allow_methods": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "GET",
                            "POST",
                            "PUT",
                            "DELETE",
                            "PATCH",
                            "OPTIONS",
                            "HEAD",
                            "*",
                        ],
                    },
                    "default": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "description": (
                        "List of allowed HTTP methods. Use ['*'] to allow all "
                        "methods (not recommended for production)"
                    ),
                },
                "allow_headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["*"],
                    "description": "List of allowed headers (use ['*'] for all headers)",
                },
                "expose_headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of headers to expose to the client",
                },
                "max_age": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 3600,
                    "description": "Max age for preflight requests in seconds (default: 3600)",
                },
            },
            "additionalProperties": False,
            "description": "CORS (Cross-Origin Resource Sharing) configuration for web apps",
        },
        "data_access": {
            "type": "object",
            "properties": {
                "read_scopes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of app slugs this app can read from. "
                        "Defaults to [app_slug] if not specified."
                    ),
                },
                "write_scope": {
                    "type": "string",
                    "description": (
                        "App slug this app writes to. " "Defaults to app_slug if not specified."
                    ),
                },
                "cross_app_policy": {
                    "type": "string",
                    "enum": ["explicit", "deny_all"],
                    "default": "explicit",
                    "description": (
                        "Policy for cross-app access. 'explicit' allows access "
                        "to apps listed in read_scopes. 'deny_all' blocks all "
                        "cross-app access regardless of read_scopes."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Data access configuration defining which apps this app can "
                "read from and write to. Used for cross-app data access control."
            ),
        },
        "observability": {
            "type": "object",
            "properties": {
                "health_checks": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable health check endpoint (default: true)",
                        },
                        "endpoint": {
                            "type": "string",
                            "pattern": "^/[a-zA-Z0-9_/-]*$",
                            "default": "/health",
                            "description": "Health check endpoint path (default: '/health')",
                        },
                        "interval_seconds": {
                            "type": "integer",
                            "minimum": 5,
                            "default": 30,
                            "description": "Health check interval in seconds (default: 30)",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Health check configuration",
                },
                "metrics": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable metrics collection (default: true)",
                        },
                        "collect_operation_metrics": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Collect operation-level metrics " "(duration, errors, etc.)"
                            ),
                        },
                        "collect_performance_metrics": {
                            "type": "boolean",
                            "default": True,
                            "description": "Collect performance metrics (memory, CPU, etc.)",
                        },
                        "custom_metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of custom metric names to track",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Metrics collection configuration",
                },
                "logging": {
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "string",
                            "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                            "default": "INFO",
                            "description": "Logging level (default: 'INFO')",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["json", "text"],
                            "default": "json",
                            "description": "Log format (default: 'json')",
                        },
                        "include_request_id": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include request ID in logs (default: true)",
                        },
                        "log_sensitive_data": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Log sensitive data (passwords, tokens, etc.) - "
                                "NOT recommended for production (default: false)"
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": "Logging configuration",
                },
            },
            "additionalProperties": False,
            "description": "Observability configuration (health checks, metrics, logging)",
        },
        "multi_app": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable multi-app mounting mode",
                },
                "apps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug": {
                                "type": "string",
                                "pattern": "^[a-z0-9_-]+$",
                                "description": (
                                    "App slug (lowercase alphanumeric, underscores, hyphens)"
                                ),
                            },
                            "manifest": {
                                "type": "string",
                                "description": (
                                    "Path to manifest.json file "
                                    "(relative to multi_app manifest or absolute)"
                                ),
                            },
                            "path_prefix": {
                                "type": "string",
                                "pattern": "^/.*",
                                "default": "/{slug}",
                                "description": (
                                    "Path prefix for mounting (defaults to /{slug}). "
                                    "Must start with '/' and be unique across all apps."
                                ),
                            },
                            "on_startup": {
                                "type": "string",
                                "description": (
                                    "Optional: Python function path for startup callback "
                                    "(e.g., 'module.function_name'). "
                                    "Not yet supported in manifest-based config."
                                ),
                            },
                            "on_shutdown": {
                                "type": "string",
                                "description": (
                                    "Optional: Python function path for shutdown callback "
                                    "(e.g., 'module.function_name'). "
                                    "Not yet supported in manifest-based config."
                                ),
                            },
                        },
                        "required": ["slug", "manifest"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "description": "List of apps to mount in multi-app mode",
                },
                "shared_middleware": {
                    "type": "object",
                    "properties": {
                        "cors": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable CORS middleware at parent level (default: true)",
                        },
                        "rate_limiting": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable rate limiting middleware at parent level (default: true)"
                            ),
                        },
                        "health_checks": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Enable unified health check endpoint at /health (default: true)"
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": "Shared middleware configuration for parent app",
                },
            },
            "additionalProperties": False,
            "description": (
                "Multi-app mounting configuration. When enabled, allows mounting "
                "multiple FastAPI apps under a single parent app with path prefixes. "
                "Useful for deploying multiple apps (e.g., SSO apps) on a single service."
            ),
        },
        "initial_data": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Collection name -> array of documents to seed",
                }
            },
            "description": (
                "Initial data to seed into collections. Only seeds if "
                "collection is empty (idempotent). Each key is a collection "
                "name, value is an array of documents to insert."
            ),
        },
        "developer_id": {
            "type": "string",
            "format": "email",
            "description": "Email of the developer who owns this app",
        },
    },
    "required": ["slug", "name"],
    "definitions": {
        "indexDefinition": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "pattern": "^[a-zA-Z0-9_]+$",
                    "minLength": 1,
                    "description": "Base index name (will be prefixed with slug)",
                },
                "type": {
                    "type": "string",
                    "enum": [
                        "regular",
                        "vectorSearch",
                        "search",
                        "text",
                        "geospatial",
                        "ttl",
                        "partial",
                        "hybrid",
                    ],
                    "description": (
                        "Index type. 'hybrid' creates both vector and text "
                        "indexes for hybrid search with $rankFusion."
                    ),
                },
                "keys": {
                    "oneOf": [
                        {
                            "type": "object",
                            "patternProperties": {
                                "^[a-zA-Z0-9_.]+$": {
                                    "oneOf": [
                                        {"type": "integer", "enum": [1, -1]},
                                        {
                                            "type": "string",
                                            "enum": [
                                                "text",
                                                "2dsphere",
                                                "2d",
                                                "geoHaystack",
                                                "hashed",
                                            ],
                                        },
                                    ]
                                }
                            },
                        },
                        {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 2,
                                "prefixItems": [
                                    {"type": "string"},
                                    {
                                        "oneOf": [
                                            {"type": "integer", "enum": [1, -1]},
                                            {
                                                "type": "string",
                                                "enum": [
                                                    "text",
                                                    "2dsphere",
                                                    "2d",
                                                    "geoHaystack",
                                                    "hashed",
                                                ],
                                            },
                                        ]
                                    },
                                ],
                                "items": False,
                            },
                        },
                    ],
                    "description": (
                        "Index keys (required for regular, text, geospatial, "
                        "ttl, partial indexes)"
                    ),
                },
                "definition": {
                    "type": "object",
                    "description": (
                        "Index definition (required for vectorSearch and " "search indexes)"
                    ),
                },
                "hybrid": {
                    "type": "object",
                    "properties": {
                        "vector_index": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "pattern": "^[a-zA-Z0-9_]+$",
                                    "description": (
                                        "Name for the vector index " "(defaults to '{name}_vector')"
                                    ),
                                },
                                "definition": {
                                    "type": "object",
                                    "description": (
                                        "Vector index definition with "
                                        "mappings.fields containing knnVector "
                                        "fields"
                                    ),
                                },
                            },
                            "required": ["definition"],
                            "additionalProperties": False,
                        },
                        "text_index": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "pattern": "^[a-zA-Z0-9_]+$",
                                    "description": (
                                        "Name for the text index " "(defaults to '{name}_text')"
                                    ),
                                },
                                "definition": {
                                    "type": "object",
                                    "description": (
                                        "Text index definition with mappings "
                                        "for full-text search"
                                    ),
                                },
                            },
                            "required": ["definition"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["vector_index", "text_index"],
                    "additionalProperties": False,
                    "description": (
                        "Hybrid search configuration (required when type is "
                        "'hybrid'). Defines both vector and text indexes for "
                        "$rankFusion."
                    ),
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "unique": {"type": "boolean"},
                        "sparse": {"type": "boolean"},
                        "background": {"type": "boolean"},
                        "name": {"type": "string"},
                        "partialFilterExpression": {
                            "type": "object",
                            "description": "Filter expression for partial indexes",
                        },
                        "expireAfterSeconds": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "TTL in seconds (required for TTL indexes)",
                        },
                        "weights": {
                            "type": "object",
                            "patternProperties": {
                                "^[a-zA-Z0-9_.]+$": {"type": "integer", "minimum": 1}
                            },
                            "description": "Field weights for text indexes",
                        },
                        "default_language": {
                            "type": "string",
                            "description": "Default language for text indexes",
                        },
                        "language_override": {
                            "type": "string",
                            "description": "Language override field for text indexes",
                        },
                    },
                    "description": "Index options (varies by index type)",
                },
            },
            "required": ["name", "type"],
            "allOf": [
                {
                    "if": {"properties": {"type": {"const": "regular"}}},
                    "then": {"required": ["keys"]},
                },
                {
                    "if": {"properties": {"type": {"const": "text"}}},
                    "then": {"required": ["keys"]},
                },
                {
                    "if": {"properties": {"type": {"const": "geospatial"}}},
                    "then": {"required": ["keys"]},
                },
                {
                    "if": {"properties": {"type": {"const": "ttl"}}},
                    "then": {"required": ["keys"]},
                },
                {
                    "if": {"properties": {"type": {"const": "partial"}}},
                    "then": {"required": ["keys", "options"]},
                    "else": {
                        "properties": {
                            "options": {"not": {"required": ["partialFilterExpression"]}}
                        }
                    },
                },
                {
                    "if": {"properties": {"type": {"const": "vectorSearch"}}},
                    "then": {"required": ["definition"]},
                },
                {
                    "if": {"properties": {"type": {"const": "search"}}},
                    "then": {"required": ["definition"]},
                },
                {
                    "if": {"properties": {"type": {"const": "hybrid"}}},
                    "then": {"required": ["hybrid"]},
                },
            ],
        },
        "collectionSettings": {
            "type": "object",
            "properties": {
                "validation": {
                    "type": "object",
                    "properties": {
                        "validator": {"type": "object"},
                        "validationLevel": {
                            "type": "string",
                            "enum": ["off", "strict", "moderate"],
                        },
                        "validationAction": {
                            "type": "string",
                            "enum": ["error", "warn"],
                        },
                    },
                },
                "collation": {
                    "type": "object",
                    "properties": {
                        "locale": {"type": "string"},
                        "caseLevel": {"type": "boolean"},
                        "caseFirst": {"type": "string"},
                        "strength": {"type": "integer"},
                        "numericOrdering": {"type": "boolean"},
                        "alternate": {"type": "string"},
                        "maxVariable": {"type": "string"},
                        "normalization": {"type": "boolean"},
                        "backwards": {"type": "boolean"},
                    },
                },
                "capped": {"type": "boolean"},
                "size": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum size in bytes for capped collection",
                },
                "max": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of documents for capped collection",
                },
                "timeseries": {
                    "type": "object",
                    "properties": {
                        "timeField": {"type": "string"},
                        "metaField": {"type": "string"},
                        "granularity": {
                            "type": "string",
                            "enum": ["seconds", "minutes", "hours"],
                        },
                    },
                    "required": ["timeField"],
                },
            },
        },
    },
}

# Schema for Version 1.0 (backward compatibility - simplified)
# Version 1.0 had: slug, name, description, status, auth_required,
# data_scope, pip_deps, managed_indexes
MANIFEST_SCHEMA_V1 = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "pattern": "^1\\.0$", "const": "1.0"},
        "slug": {
            "type": "string",
            "pattern": "^[a-z0-9_-]+$",
            "description": "App slug (lowercase alphanumeric, underscores, hyphens)",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Human-readable app name",
        },
        "description": {"type": "string", "description": "App description"},
        "status": {
            "type": "string",
            "enum": ["active", "draft", "archived", "inactive"],
            "default": "draft",
            "description": "App status",
        },
        "data_scope": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "default": ["self"],
            "description": "List of app slugs whose data this app can access",
        },
        "pip_deps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of pip dependencies for isolated environment",
        },
        "managed_indexes": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/indexDefinition"},
                    "minItems": 1,
                }
            },
            "description": "Collection name -> list of index definitions",
        },
        "developer_id": {
            "type": "string",
            "format": "email",
            "description": "Email of the developer who owns this app",
        },
    },
    "required": ["slug", "name"],
    "definitions": {
        # Reuse same indexDefinition from V2
        "indexDefinition": MANIFEST_SCHEMA_V2["definitions"]["indexDefinition"]
    },
}

# Register schemas (use constants for version strings)
SCHEMA_REGISTRY[DEFAULT_SCHEMA_VERSION] = MANIFEST_SCHEMA_V1
SCHEMA_REGISTRY[CURRENT_SCHEMA_VERSION] = MANIFEST_SCHEMA_V2
# Also register as default/legacy
SCHEMA_REGISTRY["default"] = MANIFEST_SCHEMA_V2
MANIFEST_SCHEMA = MANIFEST_SCHEMA_V2  # Backward compatibility


def get_schema_version(manifest_data: dict[str, Any]) -> str:
    """
    Detect schema version from manifest.

    Args:
    manifest_data: Manifest dictionary

    Returns:
        Schema version string (e.g., "1.0", "2.0")

    Raises:
        ValueError: If schema version format is invalid
    """
    version: str | None = manifest_data.get("schema_version")
    if version:
        # Validate version format
        if not isinstance(version, str) or not version.replace(".", "").isdigit():
            raise ValueError(
                f"Invalid schema_version format: {version}. Expected format: 'major.minor'"
            )
        return str(version)

    # Heuristic: If manifest has new fields, assume 2.0, otherwise 1.0
    v2_fields = ["auth", "collection_settings", "auth_policy"]
    if any(field in manifest_data for field in v2_fields):
        return "2.0"

    return DEFAULT_SCHEMA_VERSION


def migrate_manifest(
    manifest_data: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION
) -> dict[str, Any]:
    """
    Migrate manifest from one schema version to another.

    Args:
    manifest_data: Manifest dictionary to migrate
    target_version: Target schema version (default: current)

    Returns:
        Migrated manifest dictionary
    """
    current_version = get_schema_version(manifest_data)

    if current_version == target_version:
        return manifest_data.copy()

    migrated = manifest_data.copy()

    # Migration path: 1.0 -> 2.0
    if current_version == "1.0" and target_version == "2.0":
        # V1.0 to V2.0: Add schema_version, new fields already present are kept
        if "schema_version" not in migrated:
            migrated["schema_version"] = "2.0"

        # Old auth_policy/sub_auth format is no longer supported
        # Manifests must use auth.policy/auth.users format
        if "auth_policy" in migrated or "sub_auth" in migrated:
            raise ValueError(
                f"Manifest {migrated.get('slug', 'unknown')} uses deprecated "
                f"'auth_policy'/'sub_auth' format. "
                f"Please migrate to 'auth.policy'/'auth.users' format."
            )

        # No data transformation needed - V2.0 is backward compatible
        # New fields (auth, etc.) are optional
        logger.debug(f"Migrated manifest from 1.0 to 2.0: {migrated.get('slug', 'unknown')}")

    # Future: Add more migration paths as needed
    # Example: 2.0 -> 3.0, etc.

    migrated["schema_version"] = target_version
    return migrated


def get_schema_for_version(version: str) -> dict[str, Any]:
    """
    Get schema definition for a specific version.

    Args:
    version: Schema version string

    Returns:
        Schema definition dictionary

    Raises:
        ValueError: If version not found in registry
    """
    if version in SCHEMA_REGISTRY:
        return SCHEMA_REGISTRY[version]

    # Try to find compatible version
    major = version.split(".")[0]
    for reg_version in sorted(SCHEMA_REGISTRY.keys(), reverse=True):
        if reg_version.startswith(major + "."):
            logger.warning(
                f"Schema version {version} not found, using compatible version {reg_version}"
            )
            return SCHEMA_REGISTRY[reg_version]

    # Fallback to current
    logger.warning(
        f"Schema version {version} not found, using current version " f"{CURRENT_SCHEMA_VERSION}"
    )
    return SCHEMA_REGISTRY[CURRENT_SCHEMA_VERSION]


async def _validate_manifest_async(
    manifest_data: dict[str, Any], use_cache: bool = True
) -> tuple[bool, str | None, list[str] | None]:
    """
    Validate a manifest against the JSON Schema with versioning and caching support.

    This function:
    1. Detects schema version from manifest (defaults to 1.0 if not specified)
    2. Uses appropriate schema for validation
    3. Caches validation results for performance
    4. Supports parallel validation for scale

    Args:
    manifest_data: The manifest data to validate
    use_cache: Whether to use validation cache (default: True, set False to force re-validation)

    Returns:
        Tuple of (is_valid, error_message, error_paths)
        - is_valid: True if valid, False otherwise
        - error_message: Human-readable error message (None if valid)
        - error_paths: List of JSON paths with errors (None if valid)

    Note: This function does NOT validate developer_id against the database.
          Use validate_manifest_with_db() for database validation.
    """
    # Check cache first
    cache_key = None
    if use_cache:
        cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
        if cache_key in _validation_cache:
            return _validation_cache[cache_key]

    try:
        # Get schema version
        version = get_schema_version(manifest_data)
        schema = get_schema_for_version(version)

        # Note: Tuple-to-list conversion should happen at the API boundary (register_app),
        # not here. This keeps validation logic clean and schema-agnostic.
        # Validate against appropriate schema
        validate(instance=manifest_data, schema=schema)

        # Cache success result
        result = (True, None, None)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + version
            _validation_cache[cache_key] = result

        return result

    except ValidationError as e:
        error_paths = []
        error_messages = []

        # Extract error paths and messages
        path_parts = list(e.absolute_path)
        if path_parts:
            error_paths.append(".".join(str(p) for p in path_parts))
        else:
            error_paths.append("root")

        error_messages.append(e.message)

        # Follow the error chain for nested errors
        error = e
        while hasattr(error, "context") and error.context:
            for suberror in error.context:
                subpath_parts = list(suberror.absolute_path)
                if subpath_parts:
                    error_paths.append(".".join(str(p) for p in subpath_parts))
                error_messages.append(suberror.message)
            break  # Only process first level of context

        error_message = "; ".join(set(error_messages))  # Deduplicate messages

        # Cache error result
        result = (False, error_message, error_paths)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + version
            _validation_cache[cache_key] = result

        return result

    except SchemaError as e:
        error_message = f"Invalid schema definition: {e.message}"
        result = (False, error_message, ["schema"])
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
            _validation_cache[cache_key] = result

        return result

    except (ValidationError, SchemaError) as e:
        # Expected validation errors - extract details
        error_paths = []
        error_messages = []
        if isinstance(e, ValidationError):
            error_paths = [f".{'.'.join(str(p) for p in error.path)}" for error in e.context or [e]]
            error_messages = [error.message for error in e.context or [e]]
        else:
            error_messages = [str(e)]

        error_message = "; ".join(error_messages) if error_messages else str(e)
        result = (False, error_message, error_paths if error_paths else None)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
            _validation_cache[cache_key] = result

        return result
    except (TypeError, ValueError, KeyError) as e:
        # Programming errors - these should not happen in normal operation
        error_message = f"Manifest structure error: {str(e)}"
        logger.exception("Unexpected error during manifest validation")
        result = (False, error_message, None)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
            _validation_cache[cache_key] = result

        return result


def clear_validation_cache():
    """Clear the validation cache. Useful for testing or when schemas change."""
    global _validation_cache
    _validation_cache.clear()
    logger.debug("Validation cache cleared")


async def validate_manifests_parallel(
    manifests: list[dict[str, Any]], use_cache: bool = True
) -> list[tuple[bool, str | None, list[str] | None, str | None]]:
    """
    Validate multiple manifests in parallel for scale.

    Args:
    manifests: List of manifest dictionaries to validate
    use_cache: Whether to use validation cache

    Returns:
        List of tuples: (is_valid, error_message, error_paths, slug)
        Each tuple corresponds to the manifest at the same index
    """

    async def validate_one(
        manifest: dict[str, Any],
    ) -> tuple[bool, str | None, list[str] | None, str | None]:
        slug = manifest.get("slug", "unknown")
        is_valid, error, paths = await _validate_manifest_async(manifest, use_cache=use_cache)
        return (is_valid, error, paths, slug)

    # Run validations in parallel
    results = await asyncio.gather(*[validate_one(m) for m in manifests], return_exceptions=True)

    # Handle exceptions
    validated_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            slug = manifests[i].get("slug", "unknown")
            validated_results.append((False, f"Validation error: {str(result)}", None, slug))
        else:
            validated_results.append(result)

    return validated_results


async def validate_developer_id(
    developer_id: str, db_validator: Callable[[str], Awaitable[bool]] | None = None
) -> tuple[bool, str | None]:
    """
    Validate that a developer_id exists in the system and has developer role.

    Args:
    developer_id: The developer email to validate
    db_validator: Optional async function that checks if user exists and has developer role
                    Should return True if valid, False otherwise

    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if valid, False otherwise
        - error_message: Human-readable error message (None if valid)
    """
    if not developer_id:
        return False, "developer_id cannot be empty"

    if not isinstance(developer_id, str):
        return False, "developer_id must be a string (email)"

    # Basic email format check (JSON schema will also validate format)
    if "@" not in developer_id or "." not in developer_id:
        return (
            False,
            f"developer_id '{developer_id}' does not appear to be a valid email",
        )

    # If db_validator is provided, check database
    if db_validator:
        try:
            is_valid = await db_validator(developer_id)
            if not is_valid:
                return (
                    False,
                    f"developer_id '{developer_id}' does not exist or does not have developer role",
                )
        except (ValueError, TypeError, AttributeError) as e:
            logger.exception(f"Validation error validating developer_id '{developer_id}'")
            return False, f"Error validating developer_id: {e}"

    return True, None


async def validate_manifest_with_db(
    manifest_data: dict[str, Any],
    db_validator: Callable[[str], Awaitable[bool]],
    use_cache: bool = True,
) -> tuple[bool, str | None, list[str] | None]:
    """
    Validate a manifest against the JSON Schema (with versioning) and check
    developer_id exists in system.

    Args:
    manifest_data: The manifest data to validate
    db_validator: Async function that checks if developer_id exists and has developer role
                    Should accept developer_id (str) and return bool
    use_cache: Whether to use validation cache (default: True)

    Returns:
        Tuple of (is_valid, error_message, error_paths)
        - is_valid: True if valid, False otherwise
        - error_message: Human-readable error message (None if valid)
        - error_paths: List of JSON paths with errors (None if valid)
    """
    # First validate schema (with versioning support) - use async version directly
    is_valid, error_message, error_paths = await _validate_manifest_async(
        manifest_data, use_cache=use_cache
    )
    if not is_valid:
        return False, error_message, error_paths

    # Then validate developer_id if present
    if "developer_id" in manifest_data:
        dev_id = manifest_data.get("developer_id")
        is_valid, error_msg = await validate_developer_id(dev_id, db_validator)
        if not is_valid:
            return (
                False,
                f"developer_id validation failed: {error_msg}",
                ["developer_id"],
            )

    # Validate SSO apps require WebSocket auth (security requirement)
    validation_result = _validate_sso_websocket_auth(manifest_data)
    if not validation_result[0]:
        return validation_result

    return True, None, None


def _validate_sso_websocket_auth(
    manifest_data: dict[str, Any],
) -> tuple[bool, str | None, list[str] | None]:
    """
    Validate that SSO apps require WebSocket authentication for security.

    Security Rule: Apps using SSO (auth.mode="shared") should require WebSocket
    authentication unless explicitly allowing anonymous access. This prevents
    anonymous connections to authenticated apps, which could lead to:
    - User-scoped broadcast failures
    - Security vulnerabilities
    - Inconsistent authentication state

    Args:
        manifest_data: Manifest dictionary to validate

    Returns:
        Tuple of (is_valid, error_message, error_paths)
    """
    auth_config = manifest_data.get("auth", {})
    auth_mode = auth_config.get("mode", "app")

    # Only validate SSO apps (shared mode)
    if auth_mode != "shared":
        return True, None, None

    # Check if app requires role (indicates authenticated app)
    require_role = auth_config.get("require_role")

    # Check WebSocket configuration
    websockets_config = manifest_data.get("websockets", {})

    for endpoint_name, endpoint_config in websockets_config.items():
        if not isinstance(endpoint_config, dict):
            continue

        ws_auth = endpoint_config.get("auth", {})
        require_auth = ws_auth.get("required", True)  # Default is True
        allow_anonymous = ws_auth.get("allow_anonymous", False)  # Default is False

        # Security check: If SSO app requires role, WebSocket should require auth
        # unless explicitly allowing anonymous
        if require_role and not require_auth and not allow_anonymous:
            error_msg = (
                f"Security violation: SSO app with 'require_role' must require "
                f"WebSocket authentication. Endpoint '{endpoint_name}' has "
                f"'auth.required=false' without 'allow_anonymous=true'. "
                f"This creates a security vulnerability where anonymous connections "
                f"can access authenticated app resources."
            )
            error_paths = [f"websockets.{endpoint_name}.auth.required"]
            return False, error_msg, error_paths

        # Security check: If SSO app requires role and allows anonymous, warn but allow
        # (some apps may legitimately need public WebSocket endpoints)
        if require_role and not require_auth and allow_anonymous:
            logger.warning(
                f"⚠️ SSO app '{manifest_data.get('slug', 'unknown')}' allows anonymous "
                f"WebSocket connections on endpoint '{endpoint_name}' despite requiring "
                f"role '{require_role}' for HTTP routes. This may be intentional, but "
                f"ensure this is the desired security model."
            )

    return True, None, None


# Public API: Synchronous wrapper for backward compatibility
# Most callers use this synchronously, so we provide a sync wrapper
def validate_manifest(
    manifest_data: dict[str, Any], use_cache: bool = True
) -> tuple[bool, str | None, list[str] | None]:
    """
    Validate a manifest against the JSON Schema with versioning and caching
    support (synchronous wrapper).

    This function wraps the async validation for backward compatibility.
    In async contexts, use _validate_manifest_async() directly for better performance.

    Args:
    manifest_data: The manifest data to validate
    use_cache: Whether to use validation cache (default: True)

    Returns:
        Tuple of (is_valid, error_message, error_paths)
        - is_valid: True if valid, False otherwise
        - error_message: Human-readable error message (None if valid)
        - error_paths: List of JSON paths with errors (None if valid)
    """
    import asyncio

    # First validate schema
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, use a thread pool to run sync
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    lambda: asyncio.run(_validate_manifest_async(manifest_data, use_cache))
                )
                schema_result = future.result()
        else:
            schema_result = loop.run_until_complete(
                _validate_manifest_async(manifest_data, use_cache)
            )
    except RuntimeError:
        # No event loop, create one
        schema_result = asyncio.run(_validate_manifest_async(manifest_data, use_cache))

    # If schema validation failed, return early
    if not schema_result[0]:
        return schema_result

    # Then validate SSO WebSocket auth requirements (security check)
    sso_result = _validate_sso_websocket_auth(manifest_data)
    if not sso_result[0]:
        return sso_result

    return True, None, None


def _validate_regular_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a regular index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Regular index '{index_name}' in collection "
            f"'{collection_name}' requires 'keys' field",
        )
    keys = index_def.get("keys")
    if (
        not keys
        or (isinstance(keys, dict) and len(keys) == 0)
        or (isinstance(keys, list) and len(keys) == 0)
    ):
        return (
            False,
            f"Regular index '{index_name}' in collection " f"'{collection_name}' has empty 'keys'",
        )

    # Check for _id index
    is_id_index = False
    if isinstance(keys, dict):
        is_id_index = len(keys) == 1 and "_id" in keys
    elif isinstance(keys, list):
        is_id_index = len(keys) == 1 and len(keys[0]) >= 1 and keys[0][0] == "_id"

    if is_id_index:
        return (
            False,
            f"Index '{index_name}' in collection '{collection_name}' "
            f"cannot target '_id' field (MongoDB creates _id indexes "
            f"automatically)",
        )
    return True, None


def _validate_ttl_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a TTL index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"TTL index '{index_name}' in collection '{collection_name}' " f"requires 'keys' field",
        )
    options = index_def.get("options", {})
    if "expireAfterSeconds" not in options:
        return (
            False,
            f"TTL index '{index_name}' in collection '{collection_name}' "
            f"requires 'expireAfterSeconds' in options",
        )
    expire_after = options.get("expireAfterSeconds")
    if not isinstance(expire_after, int) or expire_after < MIN_TTL_SECONDS:
        return (
            False,
            f"TTL index '{index_name}' in collection '{collection_name}' "
            f"requires 'expireAfterSeconds' to be >= {MIN_TTL_SECONDS}",
        )
    if expire_after > MAX_TTL_SECONDS:
        return (
            False,
            f"TTL index '{index_name}' in collection '{collection_name}' "
            f"has 'expireAfterSeconds' too large ({expire_after}). "
            f"Maximum recommended is {MAX_TTL_SECONDS} (1 year). "
            f"Consider if this is intentional.",
        )
    return True, None


def _validate_partial_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a partial index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Partial index '{index_name}' in collection "
            f"'{collection_name}' requires 'keys' field",
        )
    options = index_def.get("options", {})
    if "partialFilterExpression" not in options:
        return (
            False,
            f"Partial index '{index_name}' in collection "
            f"'{collection_name}' requires 'partialFilterExpression' in "
            f"options",
        )
    return True, None


def _validate_text_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a text index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Text index '{index_name}' in collection '{collection_name}' "
            f"requires 'keys' field",
        )
    keys = index_def.get("keys")
    # Text indexes should have text type in keys
    has_text = False
    if isinstance(keys, dict):
        has_text = any(v == "text" or v == "TEXT" for v in keys.values())
    elif isinstance(keys, list):
        has_text = any(len(k) >= 2 and (k[1] == "text" or k[1] == "TEXT") for k in keys)
    if not has_text:
        return (
            False,
            f"Text index '{index_name}' in collection '{collection_name}' "
            f"must have at least one field with 'text' type in keys",
        )
    return True, None


def _validate_geospatial_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a geospatial index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Geospatial index '{index_name}' in collection "
            f"'{collection_name}' requires 'keys' field",
        )
    keys = index_def.get("keys")
    # Geospatial indexes should have geospatial type in keys
    has_geo = False
    if isinstance(keys, dict):
        has_geo = any(v in ["2dsphere", "2d", "geoHaystack"] for v in keys.values())
    elif isinstance(keys, list):
        has_geo = any(len(k) >= 2 and k[1] in ["2dsphere", "2d", "geoHaystack"] for k in keys)
    if not has_geo:
        return (
            False,
            f"Geospatial index '{index_name}' in collection "
            f"'{collection_name}' must have at least one field with "
            f"geospatial type ('2dsphere', '2d', or 'geoHaystack') in keys",
        )
    return True, None


def _validate_vector_search_index(
    index_def: dict[str, Any], collection_name: str, index_name: str, index_type: str
) -> tuple[bool, str | None]:
    """Validate a vectorSearch or search index definition."""
    if "definition" not in index_def:
        return (
            False,
            f"{index_type} index '{index_name}' in collection "
            f"'{collection_name}' requires 'definition' field",
        )
    definition = index_def.get("definition")
    if not isinstance(definition, dict):
        return (
            False,
            f"{index_type} index '{index_name}' in collection "
            f"'{collection_name}' requires 'definition' to be an object",
        )

    # Additional validation for vectorSearch indexes
    if index_type == "vectorSearch":
        fields = definition.get("fields", [])
        if not isinstance(fields, list) or len(fields) == 0:
            return (
                False,
                f"VectorSearch index '{index_name}' in collection "
                f"'{collection_name}' requires 'definition.fields' to be "
                f"a non-empty array",
            )

        # Validate vector field dimensions
        for field in fields:
            if isinstance(field, dict) and field.get("type") == "vector":
                num_dims = field.get("numDimensions")
                if (
                    not isinstance(num_dims, int)
                    or num_dims < MIN_VECTOR_DIMENSIONS
                    or num_dims > MAX_VECTOR_DIMENSIONS
                ):
                    return (
                        False,
                        f"VectorSearch index '{index_name}' in collection "
                        f"'{collection_name}' requires 'numDimensions' "
                        f"to be between {MIN_VECTOR_DIMENSIONS} and "
                        f"{MAX_VECTOR_DIMENSIONS}, got: {num_dims}",
                    )
    return True, None


def _validate_hybrid_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a hybrid index definition."""
    if "hybrid" not in index_def:
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid' field",
        )
    hybrid = index_def.get("hybrid")
    if not isinstance(hybrid, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid' to be an object",
        )

    # Validate vector_index
    vector_index = hybrid.get("vector_index")
    if not vector_index or not isinstance(vector_index, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.vector_index' to be an object",
        )
    if "definition" not in vector_index:
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.vector_index.definition' field",
        )
    vector_def = vector_index.get("definition")
    if not isinstance(vector_def, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.vector_index.definition' to be an object",
        )

    # Validate text_index
    text_index = hybrid.get("text_index")
    if not text_index or not isinstance(text_index, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.text_index' to be an object",
        )
    if "definition" not in text_index:
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.text_index.definition' field",
        )
    text_def = text_index.get("definition")
    if not isinstance(text_def, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' "
            f"requires 'hybrid.text_index.definition' to be an object",
        )
    return True, None


def validate_index_definition(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """
    Validate a single index definition with context-specific checks.

    Args:
    index_def: The index definition to validate
    collection_name: Name of the collection (for error context)
    index_name: Name of the index (for error context)

    Returns:
        Tuple of (is_valid, error_message)
    """
    index_type = index_def.get("type")
    if not index_type:
        return (
            False,
            f"Index '{index_name}' in collection '{collection_name}' " f"is missing 'type' field",
        )

    # Type-specific validation
    if index_type == "regular":
        return _validate_regular_index(index_def, collection_name, index_name)
    elif index_type == "ttl":
        return _validate_ttl_index(index_def, collection_name, index_name)
    elif index_type == "partial":
        return _validate_partial_index(index_def, collection_name, index_name)
    elif index_type == "text":
        return _validate_text_index(index_def, collection_name, index_name)
    elif index_type == "geospatial":
        return _validate_geospatial_index(index_def, collection_name, index_name)
    elif index_type in ("vectorSearch", "search"):
        return _validate_vector_search_index(index_def, collection_name, index_name, index_type)
    elif index_type == "hybrid":
        return _validate_hybrid_index(index_def, collection_name, index_name)
    else:
        return (
            False,
            f"Unknown index type '{index_type}' for index '{index_name}' "
            f"in collection '{collection_name}'",
        )


def validate_managed_indexes(
    managed_indexes: dict[str, list[dict[str, Any]]],
) -> tuple[bool, str | None]:
    """
    Validate all managed indexes with collection and index context.

    Args:
    managed_indexes: The managed_indexes object from manifest

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(managed_indexes, dict):
        return (
            False,
            "'managed_indexes' must be an object mapping collection names to index arrays",
        )

    for collection_name, indexes in managed_indexes.items():
        if not isinstance(collection_name, str) or not collection_name:
            return (
                False,
                f"Collection name must be a non-empty string, got: {collection_name}",
            )

        if not isinstance(indexes, list):
            return False, f"Indexes for collection '{collection_name}' must be an array"

        if len(indexes) == 0:
            return False, f"Collection '{collection_name}' has an empty indexes array"

        for idx, index_def in enumerate(indexes):
            if not isinstance(index_def, dict):
                return (
                    False,
                    f"Index #{idx} in collection '{collection_name}' must be an object",
                )

            index_name = index_def.get("name", f"index_{idx}")
            is_valid, error_msg = validate_index_definition(index_def, collection_name, index_name)
            if not is_valid:
                return False, error_msg

    return True, None


# ============================================================================
# CLASS-BASED API (Enterprise-ready)
# ============================================================================


class ManifestValidator:
    """
    Enterprise-grade manifest validator with versioning and caching.

    Provides a clean class-based API for manifest validation while
    maintaining backward compatibility with functional API.
    """

    def __init__(self, use_cache: bool = True):
        """
        Initialize validator.

        Args:
            use_cache: Whether to use validation cache (default: True)
        """
        self.use_cache = use_cache

    @staticmethod
    def validate(
        manifest: dict[str, Any], use_cache: bool = True
    ) -> tuple[bool, str | None, list[str] | None]:
        """
        Validate manifest against schema.

        Args:
            manifest: Manifest dictionary to validate
            use_cache: Whether to use validation cache

        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        return validate_manifest(manifest, use_cache=use_cache)

    @staticmethod
    async def validate_async(
        manifest: dict[str, Any], use_cache: bool = True
    ) -> tuple[bool, str | None, list[str] | None]:
        """
        Validate manifest asynchronously.

        This includes:
        - JSON Schema validation
        - Cross-field dependency validation

        Args:
            manifest: Manifest dictionary to validate
            use_cache: Whether to use validation cache

        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        return await _validate_manifest_async(manifest, use_cache=use_cache)

    @staticmethod
    async def validate_with_db(
        manifest: dict[str, Any],
        db_validator: Callable[[str], Awaitable[bool]],
        use_cache: bool = True,
    ) -> tuple[bool, str | None, list[str] | None]:
        """
        Validate manifest and check developer_id exists in database.

        Args:
            manifest: Manifest dictionary to validate
            db_validator: Async function that checks if developer_id exists
            use_cache: Whether to use validation cache

        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        return await validate_manifest_with_db(manifest, db_validator, use_cache=use_cache)

    @staticmethod
    def validate_managed_indexes(
        managed_indexes: dict[str, list[dict[str, Any]]],
    ) -> tuple[bool, str | None]:
        """
        Validate managed indexes configuration.

        Args:
            managed_indexes: Managed indexes dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        return validate_managed_indexes(managed_indexes)

    @staticmethod
    def validate_index_definition(
        index_def: dict[str, Any], collection_name: str, index_name: str
    ) -> tuple[bool, str | None]:
        """
        Validate a single index definition.

        Args:
            index_def: Index definition dictionary
            collection_name: Collection name for context
            index_name: Index name for context

        Returns:
            Tuple of (is_valid, error_message)
        """
        return validate_index_definition(index_def, collection_name, index_name)

    @staticmethod
    def get_schema_version(manifest: dict[str, Any]) -> str:
        """
            Get schema version from manifest.

            Args:
        manifest: Manifest dictionary

            Returns:
                Schema version string (e.g., "1.0", "2.0")
        """
        return get_schema_version(manifest)

    @staticmethod
    def migrate(
        manifest: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION
    ) -> dict[str, Any]:
        """
        Migrate manifest to target schema version.

        Args:
            manifest: Manifest dictionary to migrate
            target_version: Target schema version

        Returns:
            Migrated manifest dictionary
        """
        return migrate_manifest(manifest, target_version)

    @staticmethod
    def clear_cache():
        """Clear validation cache."""
        clear_validation_cache()


class ManifestParser:
    """
    Manifest parser for loading and processing manifest files.

    Provides utilities for loading manifests from files or dictionaries
    with automatic validation and migration.
    """

    def __init__(self, validator: ManifestValidator | None = None):
        """
        Initialize parser.

        Args:
            validator: Optional ManifestValidator instance (creates default if None)
        """
        self.validator = validator or ManifestValidator()

    @staticmethod
    async def load_from_file(path: Any, validate: bool = True) -> dict[str, Any]:
        """
        Load and validate manifest from file.

        Args:
            path: Path to manifest.json file (Path object or string)
            validate: Whether to validate after loading (default: True)

        Returns:
            Manifest dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If validation fails
        """
        import json
        from pathlib import Path

        path_obj = Path(path) if not isinstance(path, Path) else path

        if not path_obj.exists():
            raise FileNotFoundError(f"Manifest file not found: {path_obj}")

        # Read file
        content = path_obj.read_text(encoding="utf-8")
        manifest_data = json.loads(content)

        # Validate if requested
        if validate:
            is_valid, error, paths = ManifestValidator.validate(manifest_data)
            if not is_valid:
                error_path_str = f" (errors in: {', '.join(paths[:3])})" if paths else ""
                raise ValueError(f"Manifest validation failed: {error}{error_path_str}")

        return manifest_data

    @staticmethod
    async def load_from_dict(data: dict[str, Any], validate: bool = True) -> dict[str, Any]:
        """
        Load and validate manifest from dictionary.

        Args:
            data: Manifest dictionary
            validate: Whether to validate (default: True)

        Returns:
            Validated manifest dictionary

        Raises:
            ValueError: If validation fails
        """
        # Validate if requested
        if validate:
            is_valid, error, paths = ManifestValidator.validate(data)
            if not is_valid:
                error_path_str = f" (errors in: {', '.join(paths[:3])})" if paths else ""
                raise ValueError(f"Manifest validation failed: {error}{error_path_str}")

        return data.copy()

    @staticmethod
    async def load_from_string(content: str, validate: bool = True) -> dict[str, Any]:
        """
        Load and validate manifest from JSON string.

        Args:
            content: JSON string content
            validate: Whether to validate (default: True)

        Returns:
            Manifest dictionary

        Raises:
            json.JSONDecodeError: If JSON is invalid
            ValueError: If validation fails
        """
        import json

        manifest_data = json.loads(content)
        return await ManifestParser.load_from_dict(manifest_data, validate=validate)

    @staticmethod
    async def load_and_migrate(
        manifest: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION
    ) -> dict[str, Any]:
        """
        Load manifest and migrate to target version.

        Args:
            manifest: Manifest dictionary
            target_version: Target schema version

        Returns:
            Migrated manifest dictionary
        """
        return ManifestValidator.migrate(manifest, target_version)
