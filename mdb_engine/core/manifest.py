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
import json
import logging
from collections import OrderedDict
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

# Re-export canonicalize / hash helpers so callers can do:
#     from mdb_engine.core.manifest import canonicalize_manifest, compute_manifest_hash
from .manifest_hash import (  # noqa: E402,F401
    canonicalize_manifest,
    compute_manifest_hash,
    compute_schema_hash,
)

logger = logging.getLogger(__name__)

# Schema registry: maps version -> schema definition
SCHEMA_REGISTRY: dict[str, dict[str, Any]] = {}

# Validation cache: maps (manifest_hash, version) -> validation_result
# Bounded to prevent memory exhaustion in long-running / multi-tenant processes.
_MAX_VALIDATION_CACHE_SIZE = 1024
_validation_cache: OrderedDict[str, tuple[bool, str | None, list[str] | None]] = OrderedDict()
_cache_lock = asyncio.Lock()


def is_config_enabled(config: Any, *, default: bool = False) -> bool:
    """Check if a service config is enabled, handling shorthand forms.

    Supports: ``True``, ``"preset_name"``, ``{"enabled": True}``,
    ``{"preset": "..."}``, and ``None``/``False``.
    """
    if config is True:
        return True
    if isinstance(config, str):
        return bool(config)
    if isinstance(config, dict):
        return bool(config.get("enabled", default) or "preset" in config)
    return False


def _cache_put(key: str, value: tuple[bool, str | None, list[str] | None]) -> None:
    """Insert into the validation cache, evicting the oldest entry if full."""
    _validation_cache[key] = value
    # Move to end so most-recently-used entries survive eviction
    _validation_cache.move_to_end(key)
    while len(_validation_cache) > _MAX_VALIDATION_CACHE_SIZE:
        _validation_cache.popitem(last=False)


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
    # Normalize manifest by removing metadata fields that don't affect validation
    normalized = {k: v for k, v in manifest_data.items() if k not in ["_id", "_updated", "_created", "url"]}
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
                "Schema version for this manifest (format: 'major.minor'). " "Defaults to 2.0 if not specified."
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
                        "Available roles for this app (shared mode only). " "Example: ['viewer', 'editor', 'admin']"
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
                "default_policy": {
                    "type": "string",
                    "enum": ["protected", "public"],
                    "default": "protected",
                    "description": (
                        "Default auth policy. 'protected' (default) requires auth on all "
                        "routes unless in public_routes. 'public' makes all routes public "
                        "unless in protected_routes."
                    ),
                },
                "protected_routes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Routes that require authentication when default_policy is 'public'. "
                        "Supports glob patterns. Mutually exclusive with public_routes."
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
                                                    "Casbin policy as array: " '["role", "resource", "action"]'
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
                                "If true (default), the app owner " "(developer_id) can always access the app."
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
                                "Session TTL in seconds (default: 86400 = " "24 hours). Used for app-specific sessions."
                            ),
                        },
                        "allow_registration": {
                            "oneOf": [
                                {"type": "boolean"},
                                {"type": "string", "enum": ["invite_only"]},
                            ],
                            "default": False,
                            "description": (
                                "Allow users to self-register. "
                                "true = open, false = disabled, "
                                "'invite_only' = requires invite code."
                            ),
                        },
                        "registration_role": {
                            "type": "string",
                            "default": "guest",
                            "description": ("Role assigned to self-registered users " "(default: 'guest')."),
                        },
                        "roles_field": {
                            "type": "string",
                            "default": "roles",
                            "description": (
                                "Field name for the user's roles array "
                                "(default: 'roles'). Supports multi-role users."
                            ),
                        },
                        "role_hierarchy": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "description": (
                                "Role inheritance map. Each key is a role, "
                                "each value is a list of roles it inherits. "
                                'E.g. {"admin": ["editor", "reader"], '
                                '"editor": ["reader"]}.'
                            ),
                        },
                        "invite_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Valid invite codes for 'invite_only' registration. " "Supports {{env.*}} placeholders."
                            ),
                        },
                        "max_login_attempts": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": ("Max failed login attempts before lockout " "(default: 5). Returns 429."),
                        },
                        "login_lockout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 900,
                            "description": (
                                "Lockout window in seconds after max login " "attempts (default: 900 = 15 minutes)."
                            ),
                        },
                        "max_registration_attempts": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": ("Max registrations per IP within window " "(default: 5). Returns 429."),
                        },
                        "registration_window_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 3600,
                            "description": ("Registration rate limit window in seconds " "(default: 3600 = 1 hour)."),
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
                                        "description": (
                                            "Email address or {{env.*}} placeholder "
                                            "for demo user (defaults to platform "
                                            "demo email if not specified)"
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
                                        "description": ("Role for demo user in app " "(default: 'user')"),
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
                                "description": ("Maximum attempts allowed in the time window " "(default: 5)."),
                            },
                            "window_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 300,
                                "description": (
                                    "Time window in seconds for rate limiting " "(default: 300 = 5 minutes)."
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
                            "description": ("Number of days to retain audit logs " "(default: 90 days)."),
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
                                    "description": ("Enable CSRF protection " "(default: true for shared auth mode)."),
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
                                    "description": ("CSRF token TTL in seconds " "(default: 3600 = 1 hour)."),
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
                                    "description": ("Enable HSTS header in production " "(default: true)."),
                                },
                                "max_age": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "default": 31536000,
                                    "description": ("HSTS max-age in seconds " "(default: 31536000 = 1 year)."),
                                },
                                "include_subdomains": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Include subdomains in HSTS policy " "(default: true)."),
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
                    "description": ("Security settings including HSTS, headers, and other " "security controls."),
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
                        "JWT configuration for shared auth mode. " "Controls algorithm and token lifetime."
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
                            "description": ("Minimum password entropy in bits " "(default: 50). Set to 0 to disable."),
                        },
                        "require_uppercase": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Require at least one uppercase letter " "(default: true)."),
                        },
                        "require_lowercase": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Require at least one lowercase letter " "(default: true)."),
                        },
                        "require_numbers": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Require at least one number " "(default: true)."),
                        },
                        "require_special": {
                            "type": "boolean",
                            "default": False,
                            "description": ("Require at least one special character " "(default: false)."),
                        },
                        "check_common_passwords": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Check against common password list " "(default: true)."),
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
                                "Bind sessions to IP address. Strict mode: " "reject if IP changes (default: false)."
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
                "oauth": {
                    "type": "object",
                    "properties": {
                        "session_secret": {
                            "type": "string",
                            "description": (
                                "Secret key for Starlette SessionMiddleware used during "
                                "OAuth redirect flow (stores OIDC state/nonce). "
                                "Supports ${ENV_VAR} syntax. Required when OAuth is enabled."
                            ),
                        },
                        "providers": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "client_id": {
                                        "type": "string",
                                        "description": ("OAuth client ID. Supports ${ENV_VAR} syntax."),
                                    },
                                    "client_secret": {
                                        "type": "string",
                                        "description": ("OAuth client secret. Supports ${ENV_VAR} syntax."),
                                    },
                                    "server_metadata_url": {
                                        "type": "string",
                                        "format": "uri",
                                        "description": (
                                            "OIDC discovery URL (e.g., "
                                            "'https://accounts.google.com/.well-known/openid-configuration'). "
                                            "If provided, authorize_url, access_token_url, and "
                                            "userinfo_url are auto-discovered."
                                        ),
                                    },
                                    "authorize_url": {
                                        "type": "string",
                                        "format": "uri",
                                        "description": (
                                            "OAuth authorization endpoint URL. Required if "
                                            "server_metadata_url is not provided."
                                        ),
                                    },
                                    "access_token_url": {
                                        "type": "string",
                                        "format": "uri",
                                        "description": (
                                            "OAuth token endpoint URL. Required if "
                                            "server_metadata_url is not provided."
                                        ),
                                    },
                                    "userinfo_url": {
                                        "type": "string",
                                        "format": "uri",
                                        "description": (
                                            "OAuth userinfo endpoint URL. Required for "
                                            "non-OIDC providers (e.g., GitHub)."
                                        ),
                                    },
                                    "scopes": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "OAuth scopes to request. Example: " "['openid', 'email', 'profile']."
                                        ),
                                    },
                                },
                                "required": ["client_id", "client_secret"],
                            },
                            "description": (
                                "Map of provider names to their OAuth configuration. "
                                "Keys are provider names (e.g., 'google', 'github', "
                                "'keycloak'). Each provider must have client_id and "
                                "client_secret. Use server_metadata_url for OIDC "
                                "discovery or provide endpoints manually."
                            ),
                        },
                        "user_strategy": {
                            "type": "string",
                            "enum": ["link_or_create", "create_only"],
                            "default": "link_or_create",
                            "description": (
                                "Strategy for handling OAuth users. 'link_or_create' "
                                "(default) matches by email and links to existing "
                                "users or creates new ones. 'create_only' always "
                                "creates a new user record."
                            ),
                        },
                        "default_role": {
                            "type": "string",
                            "default": "user",
                            "description": ("Default role assigned to new OAuth users " "(default: 'user')."),
                        },
                        "redirect_after_login": {
                            "type": "string",
                            "default": "/",
                            "description": ("URL to redirect to after successful OAuth login " "(default: '/')."),
                        },
                        "redirect_after_logout": {
                            "type": "string",
                            "default": "/",
                            "description": ("URL to redirect to after logout " "(default: '/')."),
                        },
                    },
                    "required": ["session_secret", "providers"],
                    "additionalProperties": False,
                    "description": (
                        "OAuth 2.0 / OpenID Connect configuration. Enables login "
                        "via external identity providers (Google, GitHub, Keycloak, "
                        "Auth0, etc.). After OAuth login, users receive standard "
                        "MDB-Engine JWT tokens so all existing auth middleware, "
                        "decorators, and dependencies continue to work."
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
                    "description": ("Enable refresh token rotation " "(new refresh token on each use). Default: true."),
                },
                "max_sessions_per_user": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 10,
                    "description": ("Maximum number of concurrent sessions per user " "(default: 10)."),
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
                            "description": ("Require HTTPS in production " "(default: false, auto-detected)."),
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
                                    "description": ("Allow plain text passwords " "(NOT recommended, default: false)"),
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
                                    "description": ("Require special characters " "(default: false)"),
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
                                    "description": ("Enable session fingerprinting " "(default: true)"),
                                },
                                "validate_on_login": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Validate fingerprint on login " "(default: true)"),
                                },
                                "validate_on_refresh": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Validate fingerprint on token refresh " "(default: true)"),
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
                                    "description": ("Maximum failed login attempts before " "lockout (default: 5)"),
                                },
                                "lockout_duration_seconds": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 900,
                                    "description": ("Lockout duration in seconds " "(default: 900 = 15 minutes)"),
                                },
                                "reset_on_success": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Reset failed attempts counter on " "successful login (default: true)"
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
                                    "description": ("Enable IP address validation " "(default: false)"),
                                },
                                "strict": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": ("Strict mode: reject requests if IP " "changes (default: false)"),
                                },
                                "allow_ip_change": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": ("Allow IP address changes during session " "(default: true)"),
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
                                    "description": ("Enable token fingerprinting " "(default: true)"),
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
                    "description": ("Automatically set up token management on app startup " "(default: true)."),
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
        "manifest_tracking": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable manifest reconciliation and revision tracking.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["safe", "reconcile", "strict"],
                    "default": "safe",
                    "description": (
                        "Reconciler mode. 'safe' applies adds/updates only and logs "
                        "removals. 'reconcile' quarantines removals into the trash "
                        "namespace. 'strict' is like 'reconcile' but empty collections "
                        "may skip quarantine when allow_immediate_drop is set."
                    ),
                },
                "protect_collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Collection base names that must never be quarantined, " "even when removed from the manifest."
                    ),
                },
                "allow_immediate_drop": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When mode='strict', allow empty collections to be dropped "
                        "immediately instead of quarantined."
                    ),
                },
                "retention": {
                    "type": "object",
                    "properties": {
                        "max_revisions": {"type": "integer", "minimum": 1, "default": 50},
                        "max_age_days": {"type": "integer", "minimum": 1, "default": 90},
                        "trash_ttl_days": {"type": "integer", "minimum": 1, "default": 14},
                    },
                    "additionalProperties": False,
                    "description": ("Retention policy for revision history and quarantined artifacts."),
                },
                "sign_revisions": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, revision documents include an HMAC signature " "derived from the app secret."
                    ),
                },
                "sweeper": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "interval_seconds": {"type": "integer", "minimum": 60, "default": 3600},
                    },
                    "additionalProperties": False,
                    "description": ("Built-in scheduled sweeper that hard-drops expired quarantine entries."),
                },
            },
            "additionalProperties": False,
            "description": (
                "Manifest reconciliation configuration. Controls how startup "
                "cleans up the database when the manifest changes."
            ),
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
        "collections": {
            "type": "object",
            "description": "Collection definitions with auto-CRUD and optional JSON Schema validation",
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {"$ref": "#/definitions/collectionDef"},
            },
            "additionalProperties": False,
        },
        "ssr": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable server-side rendering. Requires a templates/ " "directory next to the manifest."
                    ),
                },
                "site_name": {
                    "type": "string",
                    "description": "Site name for SEO meta tags and JSON-LD.",
                },
                "site_description": {
                    "type": "string",
                    "description": "Default meta description when not overridden per route.",
                },
                "base_url": {
                    "type": "string",
                    "description": (
                        "Base URL for sitemap generation (e.g. 'https://myblog.com'). "
                        "If omitted, uses the request's base URL."
                    ),
                },
                "lang": {
                    "type": "string",
                    "default": "en",
                    "description": (
                        "HTML lang attribute value (e.g. 'en', 'es', 'fr'). "
                        'Sets <html lang="..."> for accessibility and SEO.'
                    ),
                },
                "sitemap": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "routes": {
                                    "type": "object",
                                    "description": (
                                        "Per-route sitemap metadata. Keys are route "
                                        "patterns matching ssr.routes keys."
                                    ),
                                    "additionalProperties": {
                                        "type": "object",
                                        "properties": {
                                            "lastmod": {
                                                "type": "string",
                                                "description": (
                                                    "Last modification date. Supports " "{{doc.field}} placeholders."
                                                ),
                                            },
                                            "changefreq": {
                                                "type": "string",
                                                "enum": [
                                                    "always",
                                                    "hourly",
                                                    "daily",
                                                    "weekly",
                                                    "monthly",
                                                    "yearly",
                                                    "never",
                                                ],
                                            },
                                            "priority": {
                                                "type": "number",
                                                "minimum": 0.0,
                                                "maximum": 1.0,
                                            },
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "max_urls_per_file": {
                                    "type": "integer",
                                    "default": 50000,
                                    "description": (
                                        "Max URLs per sitemap file before splitting " "into a sitemap index."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                        },
                    ],
                    "default": True,
                    "description": (
                        "Sitemap config. Boolean true for auto-generate, or object "
                        "for per-route lastmod/changefreq/priority and index splitting."
                    ),
                },
                "robots": {
                    "type": "object",
                    "properties": {
                        "allow": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "URL paths to allow in robots.txt.",
                        },
                        "disallow": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "URL paths to disallow in robots.txt.",
                        },
                        "sitemap": {
                            "type": "string",
                            "description": (
                                "Sitemap URL for robots.txt. Supports "
                                "{{base_url}} placeholder. Defaults to "
                                "{{base_url}}/sitemap.xml."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": "Generate a robots.txt from allow/disallow rules.",
                },
                "feeds": {
                    "type": "object",
                    "description": (
                        "RSS/Atom feed definitions. Keys are URL paths "
                        "(e.g. '/feed.xml'). Values define format, source "
                        "collection, and item template."
                    ),
                    "patternProperties": {
                        "^/": {
                            "type": "object",
                            "properties": {
                                "format": {
                                    "type": "string",
                                    "enum": ["rss", "atom"],
                                    "default": "rss",
                                    "description": "Feed format: RSS 2.0 or Atom 1.0.",
                                },
                                "collection": {
                                    "type": "string",
                                    "description": "Source collection for feed items.",
                                },
                                "scope": {
                                    "type": "string",
                                    "description": "Named scope to filter items.",
                                },
                                "sort": {
                                    "type": "object",
                                    "description": 'Sort spec (e.g. {"created_at": -1}).',
                                },
                                "limit": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                    "default": 20,
                                    "description": "Max items in the feed.",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "Feed title. Supports {{site_name}} placeholder.",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Feed description. Supports placeholders.",
                                },
                                "item": {
                                    "type": "object",
                                    "description": (
                                        "Item field templates. Keys are RSS/Atom element "
                                        "names (title, link, description, pubDate, author). "
                                        "Values support {{doc.field}} placeholders and "
                                        "pipe transforms (e.g. {{doc.created_at | rfc822}} "
                                        "for RFC 822 dates, {{doc.created_at | rfc3339}} "
                                        "for RFC 3339/ISO 8601 dates)."
                                    ),
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                            "required": ["collection"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "og_image_fallback": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": "Enable auto-generated OG preview images.",
                        },
                        "background": {
                            "type": "string",
                            "default": "#1a1a2e",
                            "description": "Background color (hex).",
                        },
                        "text_color": {
                            "type": "string",
                            "default": "#ffffff",
                            "description": "Text color (hex).",
                        },
                        "font": {
                            "type": "string",
                            "description": "TrueType font name or path.",
                        },
                        "logo": {
                            "type": "string",
                            "description": "Path to logo image to overlay.",
                        },
                        "title_field": {
                            "type": "string",
                            "default": "title",
                            "description": "Document field to use as title text.",
                        },
                        "author_field": {
                            "type": "string",
                            "default": "author",
                            "description": "Document field to use as author text.",
                        },
                        "cover_field": {
                            "type": "string",
                            "default": "cover_image",
                            "description": (
                                "Document field for existing cover image. "
                                "If present, redirects instead of generating."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Auto-generate social preview images for documents " "without a cover image. Requires Pillow."
                    ),
                },
                "routes": {
                    "type": "object",
                    "description": (
                        "SSR route definitions. Keys are URL patterns "
                        "(e.g. '/', '/posts/{id}'). Values define template, "
                        "data sources, SEO, caching, and auth."
                    ),
                    "patternProperties": {
                        "^/": {
                            "type": "object",
                            "properties": {
                                "template": {
                                    "type": "string",
                                    "description": (
                                        "Jinja2 template file name relative to "
                                        "templates/ directory. Can extend mdb_base.html."
                                    ),
                                },
                                "data": {
                                    "type": "object",
                                    "description": (
                                        "Named data sources. Each key becomes a "
                                        "template variable. List results also get "
                                        "a {name}_pagination context object."
                                    ),
                                    "additionalProperties": {
                                        "type": "object",
                                        "properties": {
                                            "collection": {
                                                "type": "string",
                                                "description": "Source collection name.",
                                            },
                                            "id_param": {
                                                "type": "string",
                                                "description": (
                                                    "Path parameter to use as document _id "
                                                    "or slug (for single-document routes)."
                                                ),
                                            },
                                            "scope": {
                                                "type": "string",
                                                "description": (
                                                    "Named scope to apply (must be defined "
                                                    "in the collection's scopes)."
                                                ),
                                            },
                                            "filter": {
                                                "type": "object",
                                                "description": (
                                                    "MQL filter. Supports {{params.*}} "
                                                    "placeholders for path parameters."
                                                ),
                                            },
                                            "sort": {
                                                "type": "object",
                                                "description": 'Sort specification (e.g. {"created_at": -1}).',
                                            },
                                            "limit": {
                                                "type": "integer",
                                                "minimum": 1,
                                                "maximum": 1000,
                                                "default": 20,
                                                "description": "Documents per page (default: 20).",
                                            },
                                            "populate": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": (
                                                    "Relations to populate via $lookup "
                                                    "(must be defined in collection's relations)."
                                                ),
                                            },
                                            "computed": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": (
                                                    "Computed fields to include "
                                                    "(must be defined in collection's computed)."
                                                ),
                                            },
                                            "exclude_fields": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": (
                                                    "Fields to exclude from the data "
                                                    "passed to the template. Merged "
                                                    "with the collection's default_projection."
                                                ),
                                            },
                                        },
                                        "required": ["collection"],
                                    },
                                },
                                "seo": {
                                    "type": "object",
                                    "properties": {
                                        "title": {
                                            "oneOf": [
                                                {
                                                    "type": "string",
                                                    "description": (
                                                        "Page title. Supports {{var.field}} "
                                                        "placeholders and pipe transforms."
                                                    ),
                                                },
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "fallback": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                            "description": (
                                                                "Ordered fallback templates. "
                                                                "First non-empty result wins."
                                                            ),
                                                        },
                                                    },
                                                    "required": ["fallback"],
                                                    "additionalProperties": False,
                                                },
                                            ],
                                        },
                                        "description": {
                                            "oneOf": [
                                                {
                                                    "type": "string",
                                                    "description": (
                                                        "Meta description. Supports placeholders "
                                                        "and pipe transforms "
                                                        "(e.g. {{post.body | plain_text | truncate(160)}})."
                                                    ),
                                                },
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "fallback": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                            "description": (
                                                                "Ordered fallback templates. "
                                                                "First non-empty result wins."
                                                            ),
                                                        },
                                                    },
                                                    "required": ["fallback"],
                                                    "additionalProperties": False,
                                                },
                                            ],
                                        },
                                        "og_image": {
                                            "oneOf": [
                                                {
                                                    "type": "string",
                                                    "description": "Open Graph image URL. Supports placeholders.",
                                                },
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "fallback": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                        },
                                                    },
                                                    "required": ["fallback"],
                                                    "additionalProperties": False,
                                                },
                                            ],
                                        },
                                        "og_type": {
                                            "type": "string",
                                            "default": "website",
                                            "description": "Open Graph type (default: website).",
                                        },
                                        "json_ld": {
                                            "type": "object",
                                            "description": (
                                                "JSON-LD structured data template. "
                                                "All string values support {{var.field}} "
                                                "placeholders. Auto-serialized and injected "
                                                "as seo.json_ld in the template context."
                                            ),
                                        },
                                        "robots": {
                                            "type": "string",
                                            "description": (
                                                "Robots meta tag value "
                                                "(e.g. 'noindex, follow'). "
                                                "Omit to use search-engine defaults."
                                            ),
                                        },
                                        "breadcrumbs": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {
                                                        "type": "string",
                                                        "description": (
                                                            "Breadcrumb label. " "Supports {{var.field}} placeholders."
                                                        ),
                                                    },
                                                    "url": {
                                                        "type": "string",
                                                        "description": (
                                                            "Breadcrumb URL. " "Supports {{var.field}} placeholders."
                                                        ),
                                                    },
                                                },
                                                "required": ["name", "url"],
                                                "additionalProperties": False,
                                            },
                                            "description": (
                                                "Breadcrumb items — auto-generates "
                                                "BreadcrumbList JSON-LD. Merged with "
                                                "explicit json_ld if both are present."
                                            ),
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                                "auth": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Require authentication for this SSR route.",
                                },
                                "cache": {
                                    "type": "object",
                                    "properties": {
                                        "ttl": {
                                            "type": "string",
                                            "pattern": "^[0-9]+(s|m|h|d)$",
                                            "description": "Cache TTL (e.g. '5m', '1h').",
                                        },
                                        "stale_while_revalidate": {
                                            "type": "string",
                                            "pattern": "^[0-9]+(s|m|h|d)$",
                                            "description": "Stale-while-revalidate duration.",
                                        },
                                        "scope": {
                                            "type": "string",
                                            "enum": ["public", "private"],
                                            "default": "public",
                                            "description": (
                                                "Cache scope: 'public' (CDN OK) or " "'private' (browser only)."
                                            ),
                                        },
                                        "vary": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": (
                                                "Header names for the Vary response "
                                                "header (e.g. ['Cookie']). When auth "
                                                "is true and vary is omitted, 'Cookie' "
                                                "is added automatically."
                                            ),
                                        },
                                        "etag": {
                                            "type": "boolean",
                                            "default": False,
                                            "description": (
                                                "Enable ETag / If-None-Match on this "
                                                "route. Opt-in because hashing large "
                                                "pages on every request has CPU cost."
                                            ),
                                        },
                                    },
                                    "additionalProperties": False,
                                    "description": "Cache-Control headers for this SSR route.",
                                },
                                "preload": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "href": {
                                                "type": "string",
                                                "description": "URL of the resource to preload.",
                                            },
                                            "as": {
                                                "type": "string",
                                                "enum": [
                                                    "style",
                                                    "script",
                                                    "font",
                                                    "image",
                                                    "fetch",
                                                    "document",
                                                ],
                                                "description": (
                                                    "Resource type hint for the browser. "
                                                    "Required for rel=preload, ignored "
                                                    "for preconnect/dns-prefetch."
                                                ),
                                            },
                                            "rel": {
                                                "type": "string",
                                                "enum": ["preload", "preconnect", "dns-prefetch"],
                                                "default": "preload",
                                                "description": (
                                                    "Link relation type. Use 'preconnect' "
                                                    "or 'dns-prefetch' for third-party "
                                                    "domain hints."
                                                ),
                                            },
                                            "crossorigin": {
                                                "type": "boolean",
                                                "default": False,
                                                "description": "Add crossorigin attribute (required for fonts).",
                                            },
                                        },
                                        "required": ["href"],
                                        "additionalProperties": False,
                                    },
                                    "description": (
                                        "Resources to preload via Link headers. " "Merged with top-level ssr.preload."
                                    ),
                                },
                            },
                            "required": ["template"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "preload": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "href": {
                                "type": "string",
                                "description": "URL of the resource to preload.",
                            },
                            "as": {
                                "type": "string",
                                "enum": ["style", "script", "font", "image", "fetch", "document"],
                                "description": (
                                    "Resource type hint for the browser. "
                                    "Required for rel=preload, ignored "
                                    "for preconnect/dns-prefetch."
                                ),
                            },
                            "rel": {
                                "type": "string",
                                "enum": ["preload", "preconnect", "dns-prefetch"],
                                "default": "preload",
                                "description": (
                                    "Link relation type. Use 'preconnect' "
                                    "or 'dns-prefetch' for third-party "
                                    "domain hints."
                                ),
                            },
                            "crossorigin": {
                                "type": "boolean",
                                "default": False,
                                "description": "Add crossorigin attribute (required for fonts).",
                            },
                        },
                        "required": ["href"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "Site-wide resources to preload via Link headers on every "
                        "SSR response (CSS, fonts). Per-route preload lists are merged."
                    ),
                },
                "redirects": {
                    "type": "object",
                    "description": (
                        "Manifest-driven redirects. Keys are URL patterns "
                        "(Express-style :param or FastAPI-style {param}). "
                        "Registered before SSR routes so they take precedence."
                    ),
                    "patternProperties": {
                        ".*": {
                            "type": "object",
                            "properties": {
                                "to": {
                                    "type": "string",
                                    "description": (
                                        "Target URL pattern. Path params from " "the source are interpolated."
                                    ),
                                },
                                "status": {
                                    "type": "integer",
                                    "enum": [301, 302],
                                    "default": 301,
                                    "description": "HTTP redirect status code.",
                                },
                            },
                            "required": ["to"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "trailing_slash": {
                    "type": "string",
                    "enum": ["strip", "ensure", "ignore"],
                    "default": "ignore",
                    "description": (
                        "Trailing-slash policy. 'strip' redirects /path/ "
                        "to /path (301). 'ensure' redirects /path to /path/. "
                        "'ignore' does nothing (default)."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Server-side rendering configuration. When enabled with a "
                "templates/ directory, routes are rendered server-side with "
                "data from MongoDB — including relations ($lookup), computed "
                "fields, pagination, policy enforcement, caching, JSON-LD, "
                "auto-sitemap, RSS/Atom feeds, robots.txt, OG image "
                "generation, redirects, and custom error pages."
            ),
        },
        "jobs": {
            "type": "object",
            "description": (
                "Declarative scheduled jobs. Each key is a job name, "
                "each value defines the schedule, action, and target."
            ),
            "patternProperties": {
                "^[a-zA-Z0-9_-]+$": {
                    "type": "object",
                    "properties": {
                        "schedule": {
                            "type": "string",
                            "description": (
                                "Cron expression, shortcut (@hourly, @daily, @weekly), "
                                "or interval string (30m, 6h, 1d)."
                            ),
                        },
                        "action": {
                            "type": "string",
                            "enum": ["update", "delete"],
                            "description": "Job action type (default: update)",
                        },
                        "collection": {
                            "type": "string",
                            "description": "Target collection for the job",
                        },
                        "filter": {
                            "type": "object",
                            "description": (
                                "MongoDB filter. Supports $$NOW and " "$$NOW_MINUS_<N><D|H|M> time variables."
                            ),
                        },
                        "update": {
                            "type": "object",
                            "description": "MongoDB update document (for update action)",
                        },
                    },
                    "required": ["schedule", "collection"],
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
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
                                        "Allow anonymous connections even if " "auth is required (default: false)"
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
                                "Authentication configuration. If not specified, " "uses app's auth.policy settings."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": ("Description of what this WebSocket endpoint " "is used for"),
                        },
                        "ping_interval": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 300,
                            "default": 30,
                            "description": (
                                "Ping interval in seconds to keep connection " "alive (default: 30, min: 5, max: 300)"
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
                "resilience": {
                    "type": "object",
                    "properties": {
                        "max_retries": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3,
                            "description": "Maximum retry attempts for transient failures.",
                        },
                        "backoff_base": {
                            "type": "number",
                            "minimum": 0,
                            "default": 0.5,
                            "description": "Base delay (seconds) for exponential backoff.",
                        },
                        "backoff_max": {
                            "type": "number",
                            "minimum": 0,
                            "default": 15.0,
                            "description": "Maximum backoff delay in seconds.",
                        },
                        "timeout": {
                            "type": ["number", "null"],
                            "minimum": 0,
                            "default": 30,
                            "description": "Per-call timeout in seconds (null = no timeout).",
                        },
                        "circuit_failure_threshold": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": "Consecutive failures before circuit breaker opens.",
                        },
                        "circuit_recovery_window": {
                            "type": "number",
                            "minimum": 0,
                            "default": 30.0,
                            "description": "Seconds before an open circuit transitions to half-open.",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Resilience policy for embedding calls (retry, backoff, circuit breaker).",
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
                        "Supports OpenAI, Azure OpenAI, and Google Gemini via native SDKs. "
                        "Auto-detects provider from environment variables or uses configured model."
                    ),
                },
                "default_model": {
                    "type": "string",
                    "default": "openai/gpt-4o",
                    "description": (
                        "Default model string for chat completions. "
                        "Format: 'provider/model' "
                        "(e.g., 'openai/gpt-4o', 'gemini/gemini-3-flash-preview', "
                        "'anthropic/claude-sonnet-4-20250514', 'azure/gpt-4o'). "
                    ),
                },
                "fallbacks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of fallback models to try if the primary model fails. "
                        "If the primary model returns an error (rate limit, timeout, etc.), "
                        "the engine will automatically try the next model in the list. "
                        "Example: ['gpt-4o-mini', 'claude-3-5-haiku-20241022']. "
                        "Models should be in provider/model format "
                        "(e.g., 'openai/gpt-4o-mini' or just 'gpt-4o-mini'). "
                        "This provides redundancy and automatic failover "
                        "without manual error handling."
                    ),
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
                "providers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Named providers mapping provider names to model strings in provider/model format. "
                        "Each key is a provider name (e.g., 'chat', 'analysis', 'code') "
                        "and each value is a model string in provider/model format (e.g., 'openai/gpt-4o', "
                        "'gemini/gemini-3-flash-preview'). "
                        "Use provider_name parameter in chat_completion() to select a provider. "
                        "The system auto-detects provider from model string and uses "
                        "corresponding environment variables."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "DEPRECATED: Legacy provider format (e.g., 'openai', 'azure', 'gemini'). "
                        "Use 'default_model' with provider/model format instead (e.g., 'openai/gpt-4o'). "
                        "This will be converted to provider/model format automatically."
                    ),
                },
                "resilience": {
                    "type": "object",
                    "properties": {
                        "max_retries": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3,
                            "description": "Maximum retry attempts for transient failures.",
                        },
                        "backoff_base": {
                            "type": "number",
                            "minimum": 0,
                            "default": 1.0,
                            "description": "Base delay (seconds) for exponential backoff.",
                        },
                        "backoff_max": {
                            "type": "number",
                            "minimum": 0,
                            "default": 30.0,
                            "description": "Maximum backoff delay in seconds.",
                        },
                        "timeout": {
                            "type": ["number", "null"],
                            "minimum": 0,
                            "default": 60,
                            "description": "Per-call timeout in seconds (null = no timeout).",
                        },
                        "circuit_failure_threshold": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": "Consecutive failures before circuit breaker opens.",
                        },
                        "circuit_recovery_window": {
                            "type": "number",
                            "minimum": 0,
                            "default": 30.0,
                            "description": "Seconds before an open circuit transitions to half-open.",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Resilience policy for LLM calls (retry, backoff, circuit breaker).",
                },
            },
            "additionalProperties": False,
            "description": (
                "LLM service configuration. Provides chat completions via native SDKs "
                "for OpenAI, Azure OpenAI, and Google Gemini. "
                "Auto-detects provider from environment variables if not specified. "
                "Supports fallback models for automatic failover. "
                "Perfect for chat applications, RAG pipelines, and text generation."
            ),
        },
        "memory_config": {
            "oneOf": [
                {"type": "boolean", "description": 'true as shorthand for {"enabled": true}'},
                {
                    "type": "string",
                    "enum": ["basic", "smart", "full"],
                    "description": "Named preset: 'basic', 'smart', or 'full'.",
                },
                {
                    "type": "object",
                    "properties": {
                        "preset": {
                            "type": "string",
                            "enum": ["basic", "smart", "full"],
                            "description": (
                                "Start from a named preset and override individual "
                                "settings.  'basic' = infer only, no cognitive. "
                                "'smart' = cognitive + categories + salience. "
                                "'full' = everything including reflection and graph."
                            ),
                        },
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
                        "extraction_provider": {
                            "type": "string",
                            "description": (
                                "Named provider from llm_config.providers to use for fast memory extraction. "
                                "If specified, uses synchronous completion with the named provider "
                                "for speed-critical extraction operations. "
                                "Example: Set to 'extraction' and configure "
                                "'extraction': 'gemini/gemini-2.5-flash-lite' in llm_config.providers. "
                                "Falls back to memory_llm_model if not specified."
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
                        "emotion_weight": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 2.0,
                            "default": 0.5,
                            "description": (
                                "Weight of emotional intensity in memory scoring (amygdala effect). "
                                "Higher values make emotionally charged memories surface more strongly. "
                                "0.0 disables emotion weighting. Default: 0.5."
                            ),
                        },
                        "recency_weight": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 2.0,
                            "default": 0.3,
                            "description": (
                                "Strength of temporal recency bias in memory scoring. "
                                "Higher values make recent memories score higher. "
                                "0.0 disables recency bias. Default: 0.3."
                            ),
                        },
                        "recency_half_life_hours": {
                            "type": "number",
                            "minimum": 1.0,
                            "maximum": 87600.0,
                            "default": 168.0,
                            "description": (
                                "Half-life for recency decay in hours. Controls how quickly "
                                "the recency boost fades. 168 = 1 week (default), "
                                "24 = 1 day (aggressive), 8760 = 1 year (gentle)."
                            ),
                        },
                        "spreading_activation": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable spreading activation via knowledge graph. "
                                "After vector search, traverses graph neighbors of seed results "
                                "to find associatively connected memories. Requires graph service."
                            ),
                        },
                        "activation_discount": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.7,
                            "description": (
                                "Score discount for memories discovered via spreading activation. "
                                "Lower values penalize graph-discovered memories more. Default: 0.7."
                            ),
                        },
                        "salience_gate": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable salience-gated encoding. When enabled, low-salience messages "
                                "(chit-chat, greetings) skip expensive LLM fact extraction and are "
                                "stored as episodic memory only. Saves LLM costs."
                            ),
                        },
                        "salience_threshold": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.3,
                            "description": (
                                "Minimum salience score (0.0-1.0) for full fact extraction. "
                                "Messages below this threshold are stored as episodic only. Default: 0.3."
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
                                            "description": ("Enable automatic memory pruning when capacity exceeded."),
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
                                    "enum": ["semantic", "entity", "episodic", "working"],
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
                                "episodic, and working memory types."
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
                        "skills": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Enable procedural memory (skills) layer. "
                                        "When enabled, skills are retrieved during chat() and "
                                        "injected into the system prompt as [AVAILABLE SKILLS]."
                                    ),
                                },
                                "collection_name": {
                                    "type": "string",
                                    "default": "skills",
                                    "description": (
                                        "MongoDB collection name for storing skills. "
                                        "Automatically prefixed with the app slug."
                                    ),
                                },
                                "auto_compile": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": (
                                        "Automatically compile skills from repeated procedural "
                                        "patterns found during memory consolidation. "
                                        "This is the 'myelination' step."
                                    ),
                                },
                                "compile_threshold": {
                                    "type": "integer",
                                    "minimum": 2,
                                    "default": 5,
                                    "description": (
                                        "Number of times a procedure must be extracted before "
                                        "it is compiled into a high-confidence skill."
                                    ),
                                },
                                "min_success_rate": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                    "default": 0.7,
                                    "description": (
                                        "Minimum success rate for a skill to be included "
                                        "in chat context. Skills below this threshold are "
                                        "excluded from search results."
                                    ),
                                },
                                "max_skills_per_query": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                    "default": 3,
                                    "description": (
                                        "Maximum number of skills to retrieve per chat query. "
                                        "Higher values provide more context but consume more tokens."
                                    ),
                                },
                                "prune_below_rate": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                    "default": 0.5,
                                    "description": (
                                        "Skills with success_rate below this value are "
                                        "deactivated during daily hygiene. "
                                        "Only applies after prune_min_uses is reached."
                                    ),
                                },
                                "prune_min_uses": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 5,
                                    "description": (
                                        "Minimum number of uses before a skill can be pruned. "
                                        "Prevents pruning skills that haven't been tried enough."
                                    ),
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Procedural memory (skills) configuration. "
                                "Enables skill retrieval during chat and automatic "
                                "compilation from repeated patterns. Skills are stored "
                                "as vector-searchable procedures with success rate tracking."
                            ),
                        },
                        "max_prompt_tokens": {
                            "type": "integer",
                            "minimum": 100,
                            "description": (
                                "Maximum token budget for context-engineered system prompts. "
                                "When set, the prompt builder truncates lowest-priority sections "
                                "(graph, LTM, STM summary) to stay within this limit. "
                                "Requires the 'tiktoken' package for accurate counting "
                                "(falls back to word-based estimation otherwise). "
                                "Leave unset for unbounded prompts (default behaviour)."
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
                                        "Default persona role (e.g., 'AI Assistant', " "'Senior Python Architect')."
                                    ),
                                },
                                "default_description": {
                                    "type": "string",
                                    "description": (
                                        "Default persona description explaining the AI's " "identity and capabilities."
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
                        "verification": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Enable memory verification.",
                                },
                                "max_file_size_kb": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 100,
                                    "description": "Maximum file size in KB for verification.",
                                },
                            },
                            "additionalProperties": False,
                            "description": "Memory verification configuration.",
                        },
                        "perfect_brain": {
                            "type": "object",
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": (
                                        "Enable Perfect Brain subsystem within memory_config. "
                                        "Initializes selected components during app startup."
                                    ),
                                },
                                "shared_memory": {"type": "boolean", "default": False},
                                "reflective_memory": {"type": "boolean", "default": False},
                                "predictive_memory": {"type": "boolean", "default": False},
                                "memory_veto": {"type": "boolean", "default": False},
                                "prospective_memory": {"type": "boolean", "default": False},
                                "cognitive_memory": {"type": "boolean", "default": False},
                                "timeline_service": {"type": "boolean", "default": False},
                                "memory_versioning": {"type": "boolean", "default": False},
                                "consolidator": {
                                    "type": "object",
                                    "properties": {
                                        "enabled": {"type": "boolean", "default": False},
                                        "interval_hours": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": 168,
                                            "default": 6,
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                                "reflection": {
                                    "type": "object",
                                    "properties": {
                                        "enabled": {"type": "boolean", "default": False},
                                        "interval_hours": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": 168,
                                            "default": 24,
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "additionalProperties": False,
                            "description": (
                                "Perfect Brain configuration (nested inside memory_config). "
                                "Provides SharedMemory, MemoryVeto, Consolidator, etc. "
                                "Access via engine.get_perfect_brain(slug)."
                            ),
                        },
                    },
                },
            ],
            "description": (
                "Memory service configuration. Accepts: true (shorthand for "
                "enabled), a preset name ('basic', 'smart', 'full'), or a full "
                "config object.  Enables intelligent memory management with "
                "MongoDB Atlas Vector Search. Configure LLM/embedding API keys "
                "via environment variables (.env)."
            ),
        },
        "graphrag_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable GraphRAG features (community detection, summaries, query classification)",
                },
                "community_detection": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable hierarchical community detection",
                        },
                        "rebuild_threshold": {
                            "type": "integer",
                            "default": 100,
                            "minimum": 1,
                            "description": "Number of graph changes before triggering community rebuild",
                        },
                        "rebuild_interval_hours": {
                            "type": "integer",
                            "default": 24,
                            "minimum": 1,
                            "description": "Time-based rebuild interval in hours",
                        },
                        "min_community_size": {
                            "type": "integer",
                            "default": 2,
                            "minimum": 1,
                            "description": "Minimum nodes required for a community",
                        },
                        "max_community_size": {
                            "type": "integer",
                            "default": 1000,
                            "minimum": 1,
                            "description": "Maximum nodes allowed in a community",
                        },
                    },
                    "additionalProperties": False,
                },
                "community_summaries": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable LLM-powered community summary generation",
                        },
                        "generate_on_detection": {
                            "type": "boolean",
                            "default": True,
                            "description": "Generate summaries immediately after community detection",
                        },
                        "regenerate_on_rebuild": {
                            "type": "boolean",
                            "default": True,
                            "description": "Regenerate summaries when communities are rebuilt",
                        },
                    },
                    "additionalProperties": False,
                },
                "query_classification": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable automatic query classification",
                        },
                        "use_llm": {
                            "type": "boolean",
                            "default": True,
                            "description": "Use LLM for ambiguous query classification",
                        },
                        "cache_results": {
                            "type": "boolean",
                            "default": True,
                            "description": "Cache classification results for performance",
                        },
                    },
                    "additionalProperties": False,
                },
                "search_methods": {
                    "type": "object",
                    "properties": {
                        "local_enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable Local Search (entity-focused queries)",
                        },
                        "global_enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable Global Search (thematic queries with map-reduce)",
                        },
                        "drift_enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable DRIFT Search (entity + community context)",
                        },
                        "basic_fallback": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable Basic Search fallback (vector similarity)",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
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
                    "default": "kg",
                    "description": (
                        "Collection name for knowledge graph nodes "
                        "(defaults to 'kg'). Will be prefixed with app slug."
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
                        "Allowed node types for graph extraction. " "LLM will classify nodes into these types."
                    ),
                },
                "osi_models_path": {
                    "type": "string",
                    "description": (
                        "Path to directory containing OSI semantic model YAML files. "
                        "Models are auto-loaded and used to enrich extraction prompts "
                        "with domain-specific entity types, synonyms, and relationships. "
                        "Requires PyYAML (pip install pyyaml)."
                    ),
                },
                "resilience": {
                    "type": "object",
                    "properties": {
                        "max_retries": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 2,
                            "description": "Maximum retry attempts for transient failures.",
                        },
                        "backoff_base": {
                            "type": "number",
                            "minimum": 0,
                            "default": 1.0,
                            "description": "Base delay (seconds) for exponential backoff.",
                        },
                        "backoff_max": {
                            "type": "number",
                            "minimum": 0,
                            "default": 15.0,
                            "description": "Maximum backoff delay in seconds.",
                        },
                        "timeout": {
                            "type": ["number", "null"],
                            "minimum": 0,
                            "default": 45,
                            "description": "Per-call timeout in seconds (null = no timeout).",
                        },
                        "circuit_failure_threshold": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": "Consecutive failures before circuit breaker opens.",
                        },
                        "circuit_recovery_window": {
                            "type": "number",
                            "minimum": 0,
                            "default": 30.0,
                            "description": "Seconds before an open circuit transitions to half-open.",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Resilience policy for graph calls (retry, backoff, circuit breaker).",
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
        "osi_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable Open Semantic Interchange (OSI) integration. "
                        "When enabled, loads semantic models and provides entity resolution, "
                        "metric routing, and graph-to-OSI export capabilities."
                    ),
                },
                "models_path": {
                    "type": "string",
                    "description": (
                        "Path to directory containing OSI semantic model YAML files. "
                        "All .yaml/.yml files in this directory will be loaded."
                    ),
                },
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific OSI model YAML file paths to load.",
                },
                "entity_resolution": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable post-extraction entity resolution against OSI definitions. "
                        "Re-maps extracted node types to match OSI dataset names using synonyms."
                    ),
                },
                "metric_routing": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable query routing for OSI-defined governed metrics. "
                        "Detects when user queries match metric definitions and surfaces them."
                    ),
                },
                "export_enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable graph-to-OSI export API endpoint (/api/osi/export).",
                },
                "sync_interval_minutes": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 60,
                    "description": (
                        "Polling interval (minutes) for OSI model file changes. "
                        "Set to 0 to disable automatic reloading."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Open Semantic Interchange (OSI) integration configuration. "
                "Enables MDB-Engine to consume, produce, and bridge OSI semantic models "
                "with conversational knowledge graphs. YAML files in semantic_models/ "
                "are the single source of truth. Use models_path to point to the directory."
            ),
        },
        "profile_config": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Enable the profile system. Materializes user profiles "
                        "and optional community profiles from memory + graph data "
                        "for instant AI context injection."
                    ),
                },
                "user_profiles": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": True,
                            "description": "Enable per-user materialized profiles.",
                        },
                        "rebuild_interval_hours": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 24,
                            "description": (
                                "Hours between automatic full profile rebuilds. "
                                "A full rebuild re-synthesizes from all memories + graph."
                            ),
                        },
                        "incremental_on_memory_add": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Incrementally update profile when new memories are stored. "
                                "Cheaper than full rebuild — merges new facts only."
                            ),
                        },
                        "collection_name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_]+$",
                            "default": "user_profiles",
                            "description": "MongoDB collection name for user profiles.",
                        },
                    },
                    "additionalProperties": False,
                },
                "community_profile": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable app-wide community profile. Aggregates anonymous "
                                "statistics across all users (no individual data exposed)."
                            ),
                        },
                        "rebuild_interval_hours": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 168,
                            "description": "Hours between community profile rebuilds (default: weekly).",
                        },
                        "min_users_for_aggregation": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 3,
                            "description": (
                                "Minimum number of users required before community "
                                "aggregation runs (privacy threshold)."
                            ),
                        },
                        "collection_name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_]+$",
                            "default": "community_profile",
                            "description": "MongoDB collection name for community profile.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
            "description": (
                "Profile system configuration. Materializes user profiles (prefrontal "
                "self-model) and community profiles from memory + graph data for "
                "instant AI context injection. User profiles are updated incrementally "
                "on memory.add() and fully rebuilt periodically."
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
                        "List of allowed origins (use ['*'] for all origins, " "not recommended for production)"
                    ),
                },
                "allow_credentials": {
                    "type": "boolean",
                    "default": False,
                    "description": ("Allow credentials (cookies, authorization headers) " "in CORS requests"),
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
                        "List of app slugs this app can read from. " "Defaults to [app_slug] if not specified."
                    ),
                },
                "write_scope": {
                    "type": "string",
                    "description": ("App slug this app writes to. " "Defaults to app_slug if not specified."),
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
                            "description": ("Collect operation-level metrics " "(duration, errors, etc.)"),
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
                "tracing": {
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Enable OpenTelemetry distributed tracing. "
                                "Requires `pip install mdb-engine[otel]` (default: false)"
                            ),
                        },
                        "exporter": {
                            "type": "string",
                            "enum": ["otlp", "console", "none"],
                            "default": "otlp",
                            "description": (
                                "Span exporter type: 'otlp' sends to an OTLP collector, "
                                "'console' prints to stdout, 'none' disables export (default: 'otlp')"
                            ),
                        },
                        "endpoint": {
                            "type": "string",
                            "default": "http://localhost:4317",
                            "description": "OTLP collector gRPC endpoint (default: 'http://localhost:4317')",
                        },
                        "service_name": {
                            "type": "string",
                            "description": (
                                "Logical service name attached to every span. " "Defaults to the app slug when omitted."
                            ),
                        },
                        "sample_rate": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 1.0,
                            "description": (
                                "Fraction of traces to sample (0.0–1.0). " "1.0 means sample everything (default: 1.0)"
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": "OpenTelemetry distributed tracing configuration",
                },
            },
            "additionalProperties": False,
            "description": "Observability configuration (health checks, metrics, logging, tracing)",
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
                                "description": ("App slug (lowercase alphanumeric, underscores, hyphens)"),
                            },
                            "manifest": {
                                "type": "string",
                                "description": (
                                    "Path to manifest.json file " "(relative to multi_app manifest or absolute)"
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
                            "description": ("Enable rate limiting middleware at parent level (default: true)"),
                        },
                        "health_checks": {
                            "type": "boolean",
                            "default": True,
                            "description": ("Enable unified health check endpoint at /health (default: true)"),
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
        "prompt_safety": {
            "type": "object",
            "properties": {
                "injection_mode": {
                    "type": "string",
                    "enum": ["log", "block", "block_high_confidence"],
                    "default": "log",
                    "description": (
                        "How to handle detected prompt-injection patterns. "
                        "'log' (default) logs a warning but never blocks. "
                        "'block' raises PromptInjectionError on any match. "
                        "'block_high_confidence' blocks only high-confidence "
                        "patterns (ChatML tokens, Llama markers) while logging the rest."
                    ),
                },
                "max_input_length": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": (
                        "Maximum character length for user input text. "
                        "0 means no limit (default). When exceeded, "
                        "sanitize_for_prompt raises ValueError."
                    ),
                },
                "custom_blocked_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional regex patterns to treat as injection attempts. "
                        "Patterns are compiled with IGNORECASE flag. Invalid regexes "
                        "are logged and skipped."
                    ),
                },
                "allow_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Regex patterns to whitelist. If any allow_pattern matches "
                        "the input text, injection detection is skipped entirely. "
                        "Useful for known-safe content patterns."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Prompt safety configuration. Controls how prompt injection "
                "detection behaves, including block vs log-only mode, custom "
                "patterns, and input length limits."
            ),
        },
        "actions": {
            "type": "object",
            "description": (
                "Manifest-driven actions. Each key is an action name matching "
                "a file in the actions/ directory. Actions support three trigger "
                "types: 'http' (default), 'schedule', and 'event'."
            ),
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "trigger": {
                        "type": "string",
                        "enum": ["http", "schedule", "event"],
                        "default": "http",
                        "description": "How the action is invoked.",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "default": "POST",
                        "description": "HTTP method (trigger=http only).",
                    },
                    "auth": {
                        "type": "object",
                        "properties": {
                            "required": {
                                "type": "boolean",
                                "description": "Require authentication.",
                            },
                            "roles": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Require one of these roles.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "timeout": {
                        "type": "number",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 300,
                        "description": "Max execution time in seconds.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "Cron expression for trigger=schedule " "(requires croniter). Example: '0 */6 * * *'."
                        ),
                    },
                    "interval_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "description": ("Simple interval for trigger=schedule " "(alternative to cron expression)."),
                    },
                    "event": {
                        "type": "string",
                        "enum": [
                            "before_create",
                            "before_update",
                            "after_create",
                            "after_update",
                            "after_delete",
                        ],
                        "description": "Hook event name (trigger=event only).",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Target collection (trigger=event only).",
                    },
                },
                "additionalProperties": False,
            },
        },
        "compression": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable response compression (GZip or Brotli). " "Enabled by default; set to false to disable."
                    ),
                },
                "minimum_size": {
                    "type": "integer",
                    "default": 500,
                    "minimum": 0,
                    "description": "Minimum response size in bytes before compression is applied.",
                },
            },
            "additionalProperties": False,
            "description": (
                "Response compression settings. Prefers Brotli when " "brotli-asgi is installed, otherwise uses GZip."
            ),
        },
        "static_cache": {
            "type": "object",
            "properties": {
                "fonts": {
                    "type": "string",
                    "default": "max-age=31536000, immutable",
                    "description": "Cache-Control header for font files (.woff2, .woff, .ttf, .otf).",
                },
                "styles": {
                    "type": "string",
                    "default": "max-age=86400, stale-while-revalidate=3600",
                    "description": "Cache-Control header for CSS files.",
                },
                "scripts": {
                    "type": "string",
                    "default": "max-age=86400, stale-while-revalidate=3600",
                    "description": "Cache-Control header for JS files.",
                },
                "images": {
                    "type": "string",
                    "default": "max-age=604800",
                    "description": "Cache-Control header for image files (.png, .jpg, .svg, etc.).",
                },
                "default": {
                    "type": "string",
                    "default": "max-age=3600",
                    "description": "Cache-Control header for all other static files.",
                },
                "minify": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Minify CSS and JS files at startup. Requires "
                        "rjsmin and csscompressor (pip install mdb-engine[perf])."
                    ),
                },
            },
            "additionalProperties": False,
            "description": (
                "Cache-Control headers for static assets served from public/. "
                "Each key maps to a category of files. Default values provide "
                "sensible caching for production deployments."
            ),
        },
        "uploads": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable GridFS-backed file upload service.",
                },
                "max_size": {
                    "type": "string",
                    "default": "5MB",
                    "pattern": "^\\d+[KMG]B$",
                    "description": (
                        "Maximum file size per upload (e.g. '5MB', '10MB'). " "Files exceeding this limit are rejected."
                    ),
                },
                "allowed_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "image/jpeg",
                        "image/png",
                        "image/gif",
                        "image/webp",
                    ],
                    "description": "Allowed MIME types for uploads.",
                },
                "path_prefix": {
                    "type": "string",
                    "default": "/uploads",
                    "description": (
                        "URL path prefix for serving uploaded files. " "Files are served at {path_prefix}/{hash}.{ext}."
                    ),
                },
                "auth": {
                    "type": "object",
                    "properties": {
                        "required": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Require authentication for the upload endpoint. " "Serving files is always public."
                            ),
                        },
                        "roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Roles allowed to upload files. " "If omitted, any authenticated user can upload."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
            "description": (
                "Image/file upload service backed by GridFS. "
                "Provides content-addressed storage with deduplication. "
                "Upload via POST /api/_uploads, serve at {path_prefix}/{hash}.{ext}."
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
                "rename_from": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Previous index base names that should be treated as the same "
                        "index during reconciliation. Prevents drop+add when an index "
                        "is renamed."
                    ),
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
                    "description": ("Index keys (required for regular, text, geospatial, " "ttl, partial indexes)"),
                },
                "definition": {
                    "type": "object",
                    "description": ("Index definition (required for vectorSearch and " "search indexes)"),
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
                                    "description": ("Name for the vector index " "(defaults to '{name}_vector')"),
                                },
                                "definition": {
                                    "type": "object",
                                    "description": (
                                        "Vector index definition with " "mappings.fields containing knnVector " "fields"
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
                                    "description": ("Name for the text index " "(defaults to '{name}_text')"),
                                },
                                "definition": {
                                    "type": "object",
                                    "description": ("Text index definition with mappings " "for full-text search"),
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
                            "patternProperties": {"^[a-zA-Z0-9_.]+$": {"type": "integer", "minimum": 1}},
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
                    "else": {"properties": {"options": {"not": {"required": ["partialFilterExpression"]}}}},
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
        "collectionDef": {
            "type": "object",
            "properties": {
                "auto_crud": {
                    "type": "boolean",
                    "default": True,
                    "description": "Generate REST endpoints for this collection",
                },
                "rename_from": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Previous collection base names that should be treated as the "
                        "same collection during reconciliation. When the reconciler "
                        "finds a matching trashed/orphan collection it renames it back "
                        "instead of creating a new empty collection."
                    ),
                },
                "schema": {
                    "type": "object",
                    "description": "JSON Schema for document validation on insert/update",
                },
                "read_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only generate GET endpoints (no POST/PUT/PATCH/DELETE)",
                },
                "timestamps": {
                    "type": "boolean",
                    "default": True,
                    "description": "Auto-inject created_at/updated_at on writes",
                },
                "soft_delete": {
                    "type": "boolean",
                    "default": False,
                    "description": "DELETE sets deleted_at instead of removing; reads exclude deleted docs",
                },
                "bulk_insert": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable POST /_bulk endpoint for batch inserts",
                },
                "auth": {
                    "type": "object",
                    "properties": {
                        "public_read": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Allow anonymous read access to this collection "
                                "even when app-level auth is enabled. "
                                "Writes still require authentication."
                            ),
                        },
                        "required": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Require authentication for this collection's "
                                "auto-CRUD endpoints. Additive to app-level auth."
                            ),
                        },
                        "roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Roles required to access this collection's "
                                "auto-CRUD endpoints. Implies auth is required."
                            ),
                        },
                        "create_roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Roles required for the POST (create) endpoint. "
                                "Overrides write_roles for creation only."
                            ),
                        },
                        "create_required": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Require authentication for the POST (create) "
                                "endpoint. Overrides write_required for creation."
                            ),
                        },
                        "write_roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Roles required for mutation endpoints "
                                "(PUT/PATCH/DELETE). Also applies to POST "
                                "unless create_roles is set. Read endpoints "
                                "remain public."
                            ),
                        },
                        "write_required": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Require authentication for mutation endpoints " "only. Read endpoints remain public."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Per-collection auth requirements. Additive to app-level "
                        "auth middleware — never weakens app-level security."
                    ),
                },
                "realtime": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Push insert/update/delete events to WebSocket " "subscribers via MongoDB Change Streams"
                    ),
                },
                "policy": {
                    "type": "object",
                    "properties": {
                        "read": {
                            "type": "object",
                            "description": (
                                "MQL filter merged into every read query. " "Supports {{user.*}} template placeholders."
                            ),
                        },
                        "write": {
                            "type": "object",
                            "description": (
                                "MQL filter merged into update/replace lookups. "
                                "Supports {{user.*}} template placeholders."
                            ),
                        },
                        "delete": {
                            "type": "object",
                            "description": (
                                "MQL filter merged into delete lookups. "
                                "Supports {{user.*}} template placeholders. "
                                "Falls back to write policy if omitted."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Document-level access policies expressed as MQL filters. "
                        "Resolved at runtime with {{user.*}} placeholders from "
                        "the authenticated user. Merged into every query via $and."
                    ),
                },
                "scopes": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z_][a-zA-Z0-9_]*$": {
                            "type": "object",
                            "description": (
                                'Plain MQL filter: {"status": "published"}  '
                                "or extended format with auth: "
                                '{"filter": {...}, "auth": {"roles": ["admin"]}}'
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Named scopes activated via ?scope=name. Can be a plain "
                        "MQL filter or extended format with auth requirements: "
                        '{"filter": {...}, "auth": {"roles": ["admin"]}}. '
                        "Supports {{user.*}} template placeholders."
                    ),
                },
                "pipelines": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z_][a-zA-Z0-9_]*$": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (
                                "MongoDB aggregation pipeline stages. " "Exposed as GET /api/{collection}/_agg/{name}."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Named aggregation pipelines exposed as read-only "
                        "endpoints. app_id scoping is applied automatically "
                        "by ScopedCollectionWrapper. Supports {{user.*}} "
                        "template placeholders in pipeline stages."
                    ),
                },
                "defaults": {
                    "type": "object",
                    "description": (
                        "Default field values applied to new documents via "
                        "setdefault (caller-provided values take precedence). "
                        "Supports {{user.*}} template placeholders."
                    ),
                },
                "default_projection": {
                    "type": "object",
                    "description": (
                        "MongoDB projection applied to list/get queries when "
                        "the client does not specify ?fields=. Useful for "
                        'hiding internal fields (e.g. {"internal_notes": 0}).'
                    ),
                },
                "owner_field": {
                    "type": "string",
                    "description": (
                        "Field name that stores the document owner's user ID. "
                        "Auto-generates defaults ({{user._id}}) and write/delete "
                        "policies so users can only modify their own documents. "
                        "Admin role bypasses ownership checks."
                    ),
                },
                "immutable_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fields that cannot be changed after document creation. "
                        "Silently stripped from PATCH/PUT request bodies."
                    ),
                },
                "writable_fields": {
                    "oneOf": [
                        {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Flat allowlist applied to all authenticated users.",
                        },
                        {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "description": (
                                "Per-role allowlist. Keys are role names, values are lists of "
                                "writable fields for that role. Admin bypasses the allowlist."
                            ),
                        },
                    ],
                    "description": (
                        "Allowlist of fields that clients can write. "
                        "Accepts a flat list (all users) or a role map "
                        '(e.g. {"editor": ["title", "body"], "reader": ["body"]}). '
                        "Admin users bypass role-map restrictions."
                    ),
                },
                "max_body_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Maximum request body size in bytes for write endpoints. "
                        "Defaults to 1048576 (1 MB). Returns 413 if exceeded."
                    ),
                },
                "rate_limits": {
                    "type": "object",
                    "properties": {
                        "reads": {
                            "type": "object",
                            "properties": {
                                "max_attempts": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Maximum requests allowed in the window.",
                                },
                                "window_seconds": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Sliding window duration in seconds.",
                                },
                            },
                            "required": ["max_attempts", "window_seconds"],
                            "additionalProperties": False,
                        },
                        "writes": {
                            "type": "object",
                            "properties": {
                                "max_attempts": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Maximum requests allowed in the window.",
                                },
                                "window_seconds": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": "Sliding window duration in seconds.",
                                },
                            },
                            "required": ["max_attempts", "window_seconds"],
                            "additionalProperties": False,
                        },
                        "per": {
                            "type": "string",
                            "enum": ["user", "ip"],
                            "default": "ip",
                            "description": (
                                "Rate-limit key strategy. 'user' tracks by "
                                "authenticated user ID; 'ip' tracks by client IP."
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Per-collection rate limits for auto-CRUD endpoints. "
                        "Separate limits for reads (GET) and writes "
                        "(POST/PUT/PATCH/DELETE)."
                    ),
                },
                "hooks": {
                    "type": "object",
                    "properties": {
                        "transactional": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Wrap the primary write and all hook actions in a "
                                "MongoDB multi-document transaction. Requires a "
                                "replica set. If any hook fails, the entire "
                                "operation rolls back."
                            ),
                        },
                        "before_create": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/hookAction"},
                            "description": (
                                "Actions to run before a document is created. "
                                "Errors propagate and abort the write. "
                                "Hooks may mutate the document body in-place for enrichment."
                            ),
                        },
                        "before_update": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/hookAction"},
                            "description": (
                                "Actions to run before a document is updated. "
                                "Errors propagate and abort the write. "
                                "Receives {{prev.*}} for the current document state."
                            ),
                        },
                        "after_create": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/hookAction"},
                            "description": "Actions to run after a document is created",
                        },
                        "after_update": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/hookAction"},
                            "description": "Actions to run after a document is updated",
                        },
                        "after_delete": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/hookAction"},
                            "description": "Actions to run after a document is deleted",
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Declarative lifecycle hooks. before_* hooks run before "
                        "the write and abort on error; after_* hooks are "
                        "fire-and-forget. Supports {{doc.*}}, {{user.*}}, "
                        "{{prev.*}}, and $$NOW template placeholders. "
                        "Set transactional: true to wrap writes + hooks in a "
                        "MongoDB multi-document transaction (requires replica set)."
                    ),
                },
                "relations": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z_][a-zA-Z0-9_]*$": {
                            "type": "object",
                            "properties": {
                                "from": {
                                    "type": "string",
                                    "description": "Target collection name (logical, without app prefix)",
                                },
                                "local_field": {
                                    "type": "string",
                                    "description": "Field on this collection that references the target",
                                },
                                "foreign_field": {
                                    "type": "string",
                                    "default": "_id",
                                    "description": "Field on the target collection to match against",
                                },
                                "as": {
                                    "type": "string",
                                    "description": "Output field name (defaults to relation name)",
                                },
                                "single": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Unwind to single object instead of array",
                                },
                            },
                            "required": ["from", "local_field"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Declarative cross-collection joins activated via "
                        "?populate=name query parameter. Injects $lookup "
                        "stages into read queries."
                    ),
                },
                "computed": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z_][a-zA-Z0-9_]*$": {
                            "type": "object",
                            "description": (
                                "Computed field definition. Either a MongoDB "
                                "expression (injected as $addFields) or an object "
                                'with "pipeline" key containing aggregation stages.'
                            ),
                        },
                    },
                    "additionalProperties": False,
                    "description": (
                        "Virtual fields computed at read time via aggregation. "
                        "Activated via ?computed=name query parameter."
                    ),
                },
                "cache": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "ttl": {
                                "type": "string",
                                "pattern": "^[0-9]+(s|m|h|d)$",
                                "description": "Cache TTL (e.g. '5m', '30s', '1h')",
                            },
                            "stale_while_revalidate": {
                                "type": "string",
                                "pattern": "^[0-9]+(s|m|h|d)$",
                                "description": "Stale-while-revalidate duration",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "description": (
                        "Cache directives per scope or 'default'. "
                        "Keys: 'default', 'scope:<name>'. "
                        "Values: {ttl, stale_while_revalidate}."
                    ),
                },
                "cascade": {
                    "type": "object",
                    "properties": {
                        "on_delete": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "collection": {"type": "string"},
                                    "match_field": {"type": "string"},
                                    "action": {
                                        "type": "string",
                                        "enum": ["delete", "soft_delete"],
                                        "default": "delete",
                                    },
                                },
                                "required": ["collection", "match_field"],
                                "additionalProperties": False,
                            },
                            "description": "Cascade rules to execute on hard delete.",
                        },
                        "on_soft_delete": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "collection": {"type": "string"},
                                    "match_field": {"type": "string"},
                                    "action": {
                                        "type": "string",
                                        "enum": ["delete", "soft_delete"],
                                        "default": "soft_delete",
                                    },
                                },
                                "required": ["collection", "match_field"],
                                "additionalProperties": False,
                            },
                            "description": "Cascade rules to execute on soft delete.",
                        },
                    },
                    "additionalProperties": False,
                    "description": "Declarative cascade behavior on delete.",
                },
                "ttl": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "description": "Date field to use for TTL expiration",
                        },
                        "expire_after": {
                            "type": "string",
                            "pattern": "^[0-9]+(s|m|h|d)$",
                            "description": (
                                "Duration after which documents expire. "
                                "Format: number + unit (s/m/h/d). Example: 24h, 90d"
                            ),
                        },
                    },
                    "required": ["field", "expire_after"],
                    "additionalProperties": False,
                    "description": (
                        "Auto-expiring documents via MongoDB TTL index. "
                        "Creates an index on the specified field that removes "
                        "documents after the given duration."
                    ),
                },
            },
            "additionalProperties": False,
        },
        "hookAction": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["insert", "update", "delete", "http"],
                    "description": "Hook action type",
                },
                "collection": {
                    "type": "string",
                    "description": "Target collection for the side effect (not required for http)",
                },
                "document": {
                    "type": "object",
                    "description": (
                        "Document to insert (for insert action). " "Supports {{doc.*}}, {{user.*}}, $$NOW placeholders."
                    ),
                },
                "filter": {
                    "type": "object",
                    "description": "Filter for update/delete actions",
                },
                "update": {
                    "type": "object",
                    "description": (
                        "Update specification. Supports MongoDB update operators "
                        "($set, $inc, $push, $pull, $addToSet, $unset, $min, $max, etc.). "
                        "If no operator keys are present, the value is wrapped in $set."
                    ),
                },
                "if": {
                    "type": "object",
                    "description": (
                        "Conditional expression (MQL match). Hook fires only when "
                        "the condition matches. Supports doc.* and prev.* field paths "
                        "and operators: $ne, $in, $nin, $exists, $gt, $lt, $gte, $lte."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "URL for http action. Supports {{doc.*}}, {{user.*}} placeholders.",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method for http action (default: POST)",
                },
                "body": {
                    "type": "object",
                    "description": "JSON body for http action. Supports template placeholders.",
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers for http action. Supports template placeholders.",
                },
                "timeout": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 60,
                    "description": "Timeout in seconds for http action (default: 10)",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run this hook in the background with retry (default: false)",
                },
                "retry": {
                    "type": "object",
                    "properties": {
                        "attempts": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "description": "Number of retry attempts (default: 3)",
                        },
                        "backoff": {
                            "type": "string",
                            "enum": ["exponential", "linear", "fixed"],
                            "description": "Backoff strategy (default: exponential)",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["action"],
            "additionalProperties": False,
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
            raise ValueError(f"Invalid schema_version format: {version}. Expected format: 'major.minor'")
        return str(version)

    # Heuristic: If manifest has new fields, assume 2.0, otherwise 1.0
    v2_fields = ["auth", "collection_settings", "collections", "auth_policy", "prompt_safety"]
    if any(field in manifest_data for field in v2_fields):
        return "2.0"

    return DEFAULT_SCHEMA_VERSION


def migrate_manifest(manifest_data: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION) -> dict[str, Any]:
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
            logger.warning(f"Schema version {version} not found, using compatible version {reg_version}")
            return SCHEMA_REGISTRY[reg_version]

    # Fallback to current
    logger.warning(f"Schema version {version} not found, using current version " f"{CURRENT_SCHEMA_VERSION}")
    return SCHEMA_REGISTRY[CURRENT_SCHEMA_VERSION]


async def validate_manifest(
    manifest_data: dict[str, Any], use_cache: bool = True
) -> tuple[bool, str | None, list[str] | None]:
    """
    Validate a manifest against the JSON Schema with versioning and caching support.

    This function:
    1. Detects schema version from manifest (defaults to 1.0 if not specified)
    2. Uses appropriate schema for validation
    3. Caches validation results for performance
    4. Validates SSO WebSocket auth requirements (security check)
    5. Supports parallel validation for scale

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
            _validation_cache.move_to_end(cache_key)
            return _validation_cache[cache_key]

    try:
        # Get schema version
        version = get_schema_version(manifest_data)
        schema = get_schema_for_version(version)

        # Note: Tuple-to-list conversion should happen at the API boundary (register_app),
        # not here. This keeps validation logic clean and schema-agnostic.
        # Validate against appropriate schema
        validate(instance=manifest_data, schema=schema)

        # Validate SSO WebSocket auth requirements (security check)
        sso_result = _validate_sso_websocket_auth(manifest_data)
        if not sso_result[0]:
            if use_cache:
                cache_key = _get_manifest_hash(manifest_data) + "_" + version
                _cache_put(cache_key, sso_result)
            return sso_result

        # Cache success result
        result = (True, None, None)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + version
            _cache_put(cache_key, result)

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
            _cache_put(cache_key, result)

        return result

    except SchemaError as e:
        error_message = f"Invalid schema definition: {e.message}"
        result = (False, error_message, ["schema"])
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
            _cache_put(cache_key, result)

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
            _cache_put(cache_key, result)

        return result
    except (TypeError, ValueError, KeyError) as e:
        # Programming errors - these should not happen in normal operation
        error_message = f"Manifest structure error: {str(e)}"
        logger.exception("Unexpected error during manifest validation")
        result = (False, error_message, None)
        if use_cache:
            cache_key = _get_manifest_hash(manifest_data) + "_" + get_schema_version(manifest_data)
            _cache_put(cache_key, result)

        return result


def clear_validation_cache() -> None:
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
        is_valid, error, paths = await validate_manifest(manifest, use_cache=use_cache)
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
    # First validate schema and SSO WebSocket auth (included in validate_manifest)
    is_valid, error_message, error_paths = await validate_manifest(manifest_data, use_cache=use_cache)
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
                f"SSO app '{manifest_data.get('slug', 'unknown')}' allows anonymous "
                f"WebSocket connections on endpoint '{endpoint_name}' despite requiring "
                f"role '{require_role}' for HTTP routes. This may be intentional, but "
                f"ensure this is the desired security model."
            )

    return True, None, None


def _validate_regular_index(
    index_def: dict[str, Any], collection_name: str, index_name: str
) -> tuple[bool, str | None]:
    """Validate a regular index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Regular index '{index_name}' in collection " f"'{collection_name}' requires 'keys' field",
        )
    keys = index_def.get("keys")
    if not keys or (isinstance(keys, dict) and len(keys) == 0) or (isinstance(keys, list) and len(keys) == 0):
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


def _validate_ttl_index(index_def: dict[str, Any], collection_name: str, index_name: str) -> tuple[bool, str | None]:
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
            f"TTL index '{index_name}' in collection '{collection_name}' " f"requires 'expireAfterSeconds' in options",
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
            f"Partial index '{index_name}' in collection " f"'{collection_name}' requires 'keys' field",
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


def _validate_text_index(index_def: dict[str, Any], collection_name: str, index_name: str) -> tuple[bool, str | None]:
    """Validate a text index definition."""
    if "keys" not in index_def:
        return (
            False,
            f"Text index '{index_name}' in collection '{collection_name}' " f"requires 'keys' field",
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
            f"Geospatial index '{index_name}' in collection " f"'{collection_name}' requires 'keys' field",
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
            f"{index_type} index '{index_name}' in collection " f"'{collection_name}' requires 'definition' field",
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


def _validate_hybrid_index(index_def: dict[str, Any], collection_name: str, index_name: str) -> tuple[bool, str | None]:
    """Validate a hybrid index definition."""
    if "hybrid" not in index_def:
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' " f"requires 'hybrid' field",
        )
    hybrid = index_def.get("hybrid")
    if not isinstance(hybrid, dict):
        return (
            False,
            f"Hybrid index '{index_name}' in collection '{collection_name}' " f"requires 'hybrid' to be an object",
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
            f"Unknown index type '{index_type}' for index '{index_name}' " f"in collection '{collection_name}'",
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
    async def validate(manifest: dict[str, Any], use_cache: bool = True) -> tuple[bool, str | None, list[str] | None]:
        """
        Validate manifest against schema.

        Args:
            manifest: Manifest dictionary to validate
            use_cache: Whether to use validation cache

        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        return await validate_manifest(manifest, use_cache=use_cache)

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
        return await validate_manifest(manifest, use_cache=use_cache)

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
    def migrate(manifest: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION) -> dict[str, Any]:
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
        from pathlib import Path

        path_obj = Path(path) if not isinstance(path, Path) else path

        def _load_manifest_sync() -> dict[str, Any]:
            if not path_obj.exists():
                raise FileNotFoundError(f"Manifest file not found: {path_obj}")
            content = path_obj.read_text(encoding="utf-8")
            return json.loads(content)

        # Read file without blocking the event loop
        manifest_data = await asyncio.to_thread(_load_manifest_sync)

        # Validate if requested
        if validate:
            is_valid, error, paths = await ManifestValidator.validate(manifest_data)
            if not is_valid:
                error_path_str = f" (errors in: {', '.join(paths[:3])})" if paths else ""
                raise ValueError(f"Manifest validation failed: {error}{error_path_str}")

        # Inject source path AFTER validation so downstream consumers
        # (e.g. OSI loader) can resolve relative paths like models_path
        # against the manifest file's directory.
        manifest_data["_source_path"] = str(path_obj.resolve())

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
            is_valid, error, paths = await ManifestValidator.validate(data)
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
