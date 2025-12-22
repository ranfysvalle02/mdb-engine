"""
App Authentication Restrictions

This module provides reusable decorators and dependencies for restricting
demo users from certain endpoints across apps.

Pattern:
- Demo users are "trapped" in their demo role for security
- They cannot access authentication routes (login/register/logout)
- They cannot create new content (projects, etc.)
- They can only view and interact with pre-seeded demo content

This ensures demo users remain in a safe, read-only demo environment.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from typing import Dict, Any, Optional, Callable
from fastapi import Request, HTTPException, status, Depends
from functools import wraps

logger = logging.getLogger(__name__)

# Import demo configuration
from ..config import DEMO_EMAIL_DEFAULT

# Import user detection utility
from .users import get_app_user
from .dependencies import get_current_user_from_request
from typing import Optional, Callable, Awaitable, Dict, Any


def is_demo_user(user: Optional[Dict[str, Any]] = None, email: Optional[str] = None) -> bool:
    """
    Check if a user is a demo user.
    
    Args:
        user: User dict (from authentication)
        email: Email address (optional, for fallback check)
    
    Returns:
        bool: True if user is a demo user, False otherwise
    """
    if user:
        # Check user flags
        if user.get('is_demo') or user.get('demo_mode'):
            return True
        
        # Check email
        user_email = user.get('email', '')
        if user_email == DEMO_EMAIL_DEFAULT or user_email.startswith('demo@'):
            return True
    
    if email:
        if email == DEMO_EMAIL_DEFAULT or email.startswith('demo@'):
            return True
    
    return False


async def require_non_demo_user(
    request: Request,
    get_app_config_func: Optional[Callable[[Request, str, Dict], Awaitable[Dict]]] = None,
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]] = None
) -> Dict[str, Any]:
    """
    FastAPI dependency that blocks demo users from accessing an endpoint.
    
    This is a reusable dependency that apps can use to restrict
    certain endpoints from demo users (e.g., login, register, logout, create).
    
    Usage:
        @bp.post("/api/projects")
        async def create_project(
            request: Request,
            user: Dict[str, Any] = Depends(require_non_demo_user)
        ):
            # user is guaranteed to NOT be a demo user
            ...
    
    Raises:
        HTTPException: 403 Forbidden if user is a demo user
    
    Returns:
        Dict[str, Any]: User dict (guaranteed to be non-demo)
    """
    slug_id = getattr(request.state, "slug_id", None)
    
    if not slug_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="App slug_id not found in request state"
        )
    
    # Try to get user from request
    user = None
    
    # First, try platform auth
    try:
        platform_user = await get_current_user_from_request(request)
        if platform_user:
            user = platform_user
    except HTTPException:
        pass  # Not authenticated via platform, try sub-auth
    except Exception as e:
        logger.debug(f"Error checking platform auth: {e}")
    
    # Try sub-auth if platform auth didn't work
    if not user:
        try:
            # Get database wrapper
            if not get_experiment_db_func:
                raise ValueError(
                    "get_app_db_func must be provided. "
                    "Provide a callable that takes a Request and returns AppDB or ScopedMongoWrapper."
                )
            db = await get_app_db_func(request)
            
            # Get config
            if not get_experiment_config_func:
                raise ValueError(
                    "get_app_config_func must be provided. "
                    "Provide a callable that takes (Request, slug_id, options) and returns config dict."
                )
            config = await get_app_config_func(request, slug_id, {"auth": 1})
            
            auth = config.get("auth", {}) if config else {}
            users_config = auth.get("users", {})
            if config and users_config.get("enabled", False):
                app_user = await get_app_user(request, slug_id, db, config, allow_demo_fallback=False)
                if sub_auth_user:
                    user = {
                        "user_id": str(sub_auth_user.get("_id")),
                        "email": sub_auth_user.get("email"),
                        "app_user_id": str(sub_auth_user.get("_id")),
                        "is_demo": sub_auth_user.get("is_demo", False),
                        "demo_mode": sub_auth_user.get("demo_mode", False)
                    }
        except HTTPException:
            pass  # Not authenticated
        except Exception as e:
            logger.debug(f"Error checking sub-auth: {e}")
    
    # Check if user is demo
    if user and is_demo_user(user):
        logger.info(f"Demo user '{user.get('email')}' blocked from accessing restricted endpoint: {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo users cannot access this endpoint. Demo mode is read-only."
        )
    
    # If no user found, raise unauthorized
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    return user


async def block_demo_users(
    request: Request,
    get_app_config_func: Optional[Callable[[Request, str, Dict], Awaitable[Dict]]] = None,
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]] = None
):
    """
    FastAPI dependency that blocks demo users and returns an error response.
    
    This dependency can be used in routes that should reject demo users
    with a clear error message. If no user is authenticated, it allows access
    (useful for login/register pages where unauthenticated users should be allowed).
    
    Usage:
        @bp.post("/logout")
        async def logout(
            request: Request,
            _ = Depends(block_demo_users)
        ):
            # This route is blocked for demo users, but allows unauthenticated users
            ...
    
    Raises:
        HTTPException: 403 Forbidden if user is a demo user
    
    Note: This dependency doesn't return a user object, it just blocks demo users.
    For routes that need the user object, use `require_non_demo_user` instead.
    
    Important: This allows unauthenticated users to pass through (useful for login/register pages).
    Only authenticated demo users are blocked.
    """
    slug_id = getattr(request.state, "slug_id", None)
    
    # Try to get user to check if they're demo
    # If no user is found, allow access (for login/register pages)
    user = None
    
    # Check platform auth
    try:
        platform_user = await get_current_user_from_request(request)
        if platform_user:
            user = platform_user
    except HTTPException:
        # Not authenticated - that's okay, allow access
        return None
    except Exception as e:
        logger.debug(f"Error checking platform auth: {e}")
    
    # Check sub-auth
    if not user:
        try:
            # Get database wrapper and config - if not provided, skip sub-auth check
            if not get_experiment_db_func or not get_experiment_config_func:
                return None
            
            db = await get_app_db_func(request)
            config = await get_app_config_func(request, slug_id, {"auth": 1})
            
            auth = config.get("auth", {}) if config else {}
            users_config = auth.get("users", {})
            if config and users_config.get("enabled", False):
                app_user = await get_app_user(request, slug_id, db, config, allow_demo_fallback=False)
                if sub_auth_user:
                    user = {
                        "user_id": str(sub_auth_user.get("_id")),
                        "email": sub_auth_user.get("email"),
                        "app_user_id": str(sub_auth_user.get("_id")),
                        "is_demo": sub_auth_user.get("is_demo", False),
                        "demo_mode": sub_auth_user.get("demo_mode", False)
                    }
        except HTTPException:
            # Not authenticated - that's okay, allow access
            return None
        except Exception as e:
            logger.debug(f"Error checking sub-auth: {e}")
    
    # Block if demo user (only if user exists)
    if user and is_demo_user(user):
        logger.info(f"Demo user '{user.get('email')}' blocked from accessing: {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo users cannot access this endpoint. Demo mode is read-only."
        )
    
    # Allow access (either no user or non-demo user)
    return None

