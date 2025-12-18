"""
Authentication and Authorization Module

Provides authentication, authorization, and access control for the runtime engine.

This module is part of MDB_RUNTIME - MongoDB Multi-Tenant Runtime Engine.
"""

from .provider import (
    AuthorizationProvider,
    CasbinAdapter,
    OsoAdapter,
    AUTHZ_CACHE_TTL,
)
from .jwt import decode_jwt_token
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

__all__ = [
    # Provider
    "AuthorizationProvider",
    "CasbinAdapter",
    "OsoAdapter",
    "AUTHZ_CACHE_TTL",
    
    # JWT
    "decode_jwt_token",
    
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
]
