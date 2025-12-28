"""
Authentication and Authorization Module

Provides authentication, authorization, and access control for the MongoDB Engine.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

# Casbin Factory
from .casbin_factory import (create_casbin_enforcer, get_casbin_model,
                             initialize_casbin_from_manifest)
# Cookie utilities
from .cookie_utils import (clear_auth_cookies, get_secure_cookie_settings,
                           set_auth_cookies)
# Decorators
from .decorators import (auto_token_setup, rate_limit_auth, require_auth,
                         token_security)
from .dependencies import (SECRET_KEY, _validate_next_url, get_authz_provider,
                           get_current_user, get_current_user_from_request,
                           get_current_user_or_redirect, get_refresh_token,
                           get_session_manager, get_token_blacklist,
                           refresh_access_token, require_admin,
                           require_admin_or_developer, require_permission)
from .helpers import initialize_token_management
# Integration
from .integration import get_auth_config, setup_auth_from_manifest
from .jwt import (decode_jwt_token, encode_jwt_token, extract_token_metadata,
                  generate_token_pair)
# Middleware
from .middleware import SecurityMiddleware, create_security_middleware
from .provider import (AUTHZ_CACHE_TTL, AuthorizationProvider, CasbinAdapter,
                       OsoAdapter)
from .restrictions import block_demo_users, is_demo_user, require_non_demo_user
from .session_manager import SessionManager
from .token_lifecycle import (get_time_until_expiry, get_token_age,
                              get_token_expiry_time, get_token_info,
                              is_token_expiring_soon, should_refresh_token,
                              validate_token_version)
# Token management
from .token_store import TokenBlacklist
from .users import (authenticate_app_user, create_app_session, create_app_user,
                    ensure_demo_users_exist, ensure_demo_users_for_actor,
                    get_app_user, get_app_user_role,
                    get_or_create_anonymous_user, get_or_create_demo_user,
                    get_or_create_demo_user_for_request,
                    sync_app_user_to_casbin)
# Utilities
from .utils import (get_device_info, login_user, logout_user, register_user,
                    validate_password_strength)

__all__ = [
    # Provider
    "AuthorizationProvider",
    "CasbinAdapter",
    "OsoAdapter",
    "AUTHZ_CACHE_TTL",
    # JWT
    "decode_jwt_token",
    "encode_jwt_token",
    "generate_token_pair",
    "extract_token_metadata",
    # Dependencies
    "SECRET_KEY",
    "get_authz_provider",
    "get_current_user",
    "get_current_user_from_request",
    "require_admin",
    "require_admin_or_developer",
    "get_current_user_or_redirect",
    "require_permission",
    "_validate_next_url",
    "get_token_blacklist",
    "get_session_manager",
    "get_refresh_token",
    "refresh_access_token",
    # App-level user management
    "get_app_user",
    "create_app_session",
    "authenticate_app_user",
    "create_app_user",
    "get_or_create_anonymous_user",
    "ensure_demo_users_exist",
    "get_or_create_demo_user_for_request",
    "get_or_create_demo_user",
    "ensure_demo_users_for_actor",
    "sync_app_user_to_casbin",
    "get_app_user_role",
    # Restrictions
    "is_demo_user",
    "require_non_demo_user",
    "block_demo_users",
    # Token Management
    "TokenBlacklist",
    "SessionManager",
    "get_token_expiry_time",
    "is_token_expiring_soon",
    "should_refresh_token",
    "get_token_age",
    "get_time_until_expiry",
    "validate_token_version",
    "get_token_info",
    "initialize_token_management",
    # Utilities
    "login_user",
    "register_user",
    "logout_user",
    "validate_password_strength",
    "get_device_info",
    # Decorators
    "require_auth",
    "token_security",
    "rate_limit_auth",
    "auto_token_setup",
    # Cookie utilities
    "get_secure_cookie_settings",
    "set_auth_cookies",
    "clear_auth_cookies",
    # Middleware
    "SecurityMiddleware",
    "create_security_middleware",
    # Integration
    "get_auth_config",
    "setup_auth_from_manifest",
    # Casbin Factory
    "get_casbin_model",
    "create_casbin_enforcer",
    "initialize_casbin_from_manifest",
]
