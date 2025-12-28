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
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException, Request, status

from ..config import DEMO_EMAIL_DEFAULT
from .dependencies import get_current_user_from_request
from .users import get_app_user

logger = logging.getLogger(__name__)


def is_demo_user(
    user: Optional[Dict[str, Any]] = None, email: Optional[str] = None
) -> bool:
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
        if user.get("is_demo") or user.get("demo_mode"):
            return True

        # Check email
        user_email = user.get("email", "")
        if user_email == DEMO_EMAIL_DEFAULT or user_email.startswith("demo@"):
            return True

    if email:
        if email == DEMO_EMAIL_DEFAULT or email.startswith("demo@"):
            return True

    return False


async def _get_platform_user(request: Request) -> Optional[Dict[str, Any]]:
    """Try to get user from platform authentication."""
    try:
        platform_user = await get_current_user_from_request(request)
        return platform_user if platform_user else None
    except HTTPException:
        return None  # Not authenticated via platform
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
        logger.debug(f"Error checking platform auth: {e}")
        return None


async def _get_sub_auth_user(
    request: Request,
    slug_id: str,
    get_app_config_func: Callable[[Request, str, Dict], Awaitable[Dict]],
    get_app_db_func: Callable[[Request], Awaitable[Any]],
) -> Optional[Dict[str, Any]]:
    """Try to get user from sub-authentication."""
    try:
        db = await get_app_db_func(request)
        config = await get_app_config_func(request, slug_id, {"auth": 1})

        auth = config.get("auth", {}) if config else {}
        users_config = auth.get("users", {})
        if not (config and users_config.get("enabled", False)):
            return None

        app_user = await get_app_user(
            request, slug_id, db, config, allow_demo_fallback=False
        )
        if not app_user:
            return None

        return {
            "user_id": str(app_user.get("_id")),
            "email": app_user.get("email"),
            "app_user_id": str(app_user.get("_id")),
            "is_demo": app_user.get("is_demo", False),
            "demo_mode": app_user.get("demo_mode", False),
        }
    except HTTPException:
        return None  # Not authenticated
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
        logger.debug(f"Error checking sub-auth: {e}")
        return None


async def _get_authenticated_user(
    request: Request,
    slug_id: str,
    get_app_config_func: Optional[Callable[[Request, str, Dict], Awaitable[Dict]]],
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]],
) -> Optional[Dict[str, Any]]:
    """Get authenticated user from platform or sub-auth."""
    # Try platform auth first
    user = await _get_platform_user(request)
    if user:
        return user

    # Try sub-auth if platform auth didn't work
    if get_app_db_func and get_app_config_func:
        return await _get_sub_auth_user(
            request, slug_id, get_app_config_func, get_app_db_func
        )

    return None


def _validate_slug_id(request: Request) -> str:
    """Validate and return slug_id from request state."""
    slug_id = getattr(request.state, "slug_id", None)
    if not slug_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="App slug_id not found in request state",
        )
    return slug_id


def _validate_dependencies(
    get_app_config_func: Optional[Callable[[Request, str, Dict], Awaitable[Dict]]],
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]],
) -> None:
    """Validate that required dependencies are provided."""
    if not get_app_db_func:
        raise ValueError(
            "get_app_db_func must be provided. "
            "Provide a callable that takes a Request and returns "
            "AppDB or ScopedMongoWrapper."
        )
    if not get_app_config_func:
        raise ValueError(
            "get_app_config_func must be provided. "
            "Provide a callable that takes (Request, slug_id, options) "
            "and returns config dict."
        )


async def require_non_demo_user(
    request: Request,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]] = None,
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
    slug_id = _validate_slug_id(request)

    if get_app_db_func and get_app_config_func:
        _validate_dependencies(get_app_config_func, get_app_db_func)

    user = await _get_authenticated_user(
        request, slug_id, get_app_config_func, get_app_db_func
    )

    # Check if user is demo
    if user and is_demo_user(user):
        logger.info(
            f"Demo user '{user.get('email')}' blocked from accessing "
            f"restricted endpoint: {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo users cannot access this endpoint. Demo mode is read-only.",
        )

    # If no user found, raise unauthorized
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    return user


async def block_demo_users(
    request: Request,
    get_app_config_func: Optional[
        Callable[[Request, str, Dict], Awaitable[Dict]]
    ] = None,
    get_app_db_func: Optional[Callable[[Request], Awaitable[Any]]] = None,
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
    user = await _get_platform_user(request)

    # Check sub-auth if platform auth didn't work
    if not user and get_app_db_func and get_app_config_func and slug_id:
        user = await _get_sub_auth_user(
            request, slug_id, get_app_config_func, get_app_db_func
        )

    # Block if demo user (only if user exists)
    if user and is_demo_user(user):
        logger.info(
            f"Demo user '{user.get('email')}' blocked from accessing: {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo users cannot access this endpoint. Demo mode is read-only.",
        )

    # Allow access (either no user or non-demo user)
    return None
