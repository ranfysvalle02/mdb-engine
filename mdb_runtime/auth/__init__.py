"""
Authentication and Authorization Module

Provides authentication, authorization, and access control for the runtime engine.

This module is part of MDB_RUNTIME - MongoDB Runtime Engine.
"""

from .provider import (
    AuthorizationProvider,
    CasbinAdapter,
    OsoAdapter,
    AUTHZ_CACHE_TTL,
)
from .jwt import (
    decode_jwt_token,
    encode_jwt_token,
    generate_token_pair,
    extract_token_metadata,
)
from .dependencies import (
    SECRET_KEY,
    get_authz_provider,
    get_current_user,
    get_current_user_from_request,
    require_admin,
    require_admin_or_developer,
    get_current_user_or_redirect,
    require_permission,
    _validate_next_url,
    get_token_blacklist,
    get_session_manager,
    get_refresh_token,
    refresh_access_token,
)
from .sub_auth import (
    get_app_sub_user,
    create_app_session,
    authenticate_app_user,
    create_app_user,
    get_or_create_anonymous_user,
    ensure_demo_users_exist,
    get_or_create_demo_user_for_request,
    get_or_create_demo_user,
    ensure_demo_users_for_actor,
)
from .restrictions import (
    is_demo_user,
    require_non_demo_user,
    block_demo_users,
)

# Token management
from .token_store import TokenBlacklist
from .session_manager import SessionManager
from .token_lifecycle import (
    get_token_expiry_time,
    is_token_expiring_soon,
    should_refresh_token,
    get_token_age,
    get_time_until_expiry,
    validate_token_version,
    get_token_info,
)
from .helpers import initialize_token_management

# Utilities
from .utils import (
    login_user,
    register_user,
    logout_user,
    validate_password_strength,
    get_device_info,
)

# Decorators
from .decorators import (
    require_auth,
    token_security,
    rate_limit_auth,
    auto_token_setup,
)

# Cookie utilities
from .cookie_utils import (
    get_secure_cookie_settings,
    set_auth_cookies,
    clear_auth_cookies,
)

# Middleware
from .middleware import (
    SecurityMiddleware,
    create_security_middleware,
)

# Integration
from .integration import (
    get_auth_config,
    setup_auth_from_manifest,
    add_security_middleware,
)

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
    
    # Sub-authentication
    "get_app_sub_user",
    "create_app_session",
    "authenticate_app_user",
    "create_app_user",
    "get_or_create_anonymous_user",
    "ensure_demo_users_exist",
    "get_or_create_demo_user_for_request",
    "get_or_create_demo_user",
    "ensure_demo_users_for_actor",
    
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
    "add_security_middleware",
]
