"""
FastAPI Authentication and Authorization Dependencies

Provides FastAPI dependency functions for authentication and authorization.

This module is part of MDB_RUNTIME - MongoDB Multi-Tenant Runtime Engine.
"""

import os
import logging
from typing import Optional, Dict, Any, Mapping, List, Callable

import jwt
from fastapi import Request, Depends, HTTPException, status, Cookie

# Import from local modules
from .provider import AuthorizationProvider
from .jwt import decode_jwt_token

logger = logging.getLogger(__name__)

# Load SECRET_KEY from environment; crucial for JWT security.
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
if not SECRET_KEY:
    logger.critical(
        "❌ SECURITY WARNING: FLASK_SECRET_KEY environment variable not set. Using insecure default. DO NOT USE IN PRODUCTION."
    )
    SECRET_KEY = "a_very_bad_dev_secret_key_12345"  # Insecure default


def _validate_next_url(next_url: Optional[str]) -> str:
    """
    Sanitizes a 'next' URL parameter to prevent Open Redirect vulnerabilities.
    """
    if not next_url:
        return "/"

    if next_url.startswith("/") and "//" not in next_url and ":" not in next_url:
        return next_url

    logger.warning(
        f"Blocked potentially unsafe redirect attempt. Original 'next' URL: '{next_url}'. Sanitized to '/'."
    )
    return "/"


async def get_authz_provider(request: Request) -> AuthorizationProvider:
    """
    FastAPI Dependency: Retrieves the shared, pluggable AuthZ provider
    from app.state.
    """
    # This key 'authz_provider' will be set in main.py's lifespan
    provider = getattr(request.app.state, "authz_provider", None)
    if not provider:
        logger.critical(
            "❌ get_authz_provider: AuthZ provider not found on app.state!"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: Authorization engine not loaded.",
        )
    return provider


async def get_current_user(
    token: Optional[str] = Cookie(default=None),
) -> Optional[Dict[str, Any]]:
    """
    FastAPI Dependency: Decodes and validates the JWT stored in the 'token' cookie.
    """
    if not token:
        logger.debug("get_current_user: No 'token' cookie found.")
        return None

    try:
        payload = decode_jwt_token(token, SECRET_KEY)
        logger.debug(
            f"get_current_user: Token successfully decoded for user '{payload.get('email', 'N/A')}'."
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("get_current_user: Authentication token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"get_current_user: Invalid JWT token presented: {e}")
        return None
    except Exception as e:
        logger.error(
            f"get_current_user: Unexpected error decoding JWT: {e}", exc_info=True
        )
        return None


async def get_current_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """
    Helper function to get current user from a Request object.
    This is useful when you need to call get_current_user outside of FastAPI dependency injection.
    
    Args:
        request: FastAPI Request object
    
    Returns:
        Optional[Dict[str, Any]]: User dict if authenticated, None otherwise
    """
    token = request.cookies.get("token")
    if not token:
        logger.debug("get_current_user_from_request: No 'token' cookie found.")
        return None
    
    try:
        payload = decode_jwt_token(token, SECRET_KEY)
        logger.debug(
            f"get_current_user_from_request: Token successfully decoded for user '{payload.get('email', 'N/A')}'."
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("get_current_user_from_request: Authentication token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"get_current_user_from_request: Invalid JWT token presented: {e}")
        return None
    except Exception as e:
        logger.error(
            f"get_current_user_from_request: Unexpected error decoding JWT: {e}", exc_info=True
        )
        return None


async def require_admin(
    user: Optional[Mapping[str, Any]] = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider),
) -> Dict[str, Any]:
    """
    FastAPI Dependency: Enforces admin privileges via the pluggable AuthZ provider.
    """
    user_identifier = "anonymous"
    has_perm = False

    if user and user.get("email"):
        user_identifier = user.get("email")
        # Use the generic, async interface method
        has_perm = await authz.check(
            subject=user_identifier,
            resource="admin_panel",
            action="access",
            user_object=dict(user),  # Pass full context
        )

    if not has_perm:
        logger.warning(
            f"require_admin: Admin access DENIED for {user_identifier}. Failed provider check."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges are required to access this resource.",
        )

    logger.debug(
        f"require_admin: Admin access GRANTED for user '{user.get('email')}' via {authz.__class__.__name__}."
    )
    return dict(user)


async def require_admin_or_developer(
    user: Optional[Mapping[str, Any]] = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider),
) -> Dict[str, Any]:
    """
    FastAPI Dependency: Enforces admin OR developer privileges.
    Developers can upload apps, admins can upload any app.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to upload apps.",
        )
    
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
    
    # Check if user is an admin
    is_admin = await authz.check(
        subject=user_email,
        resource="admin_panel",
        action="access",
        user_object=dict(user)
    )
    
    if is_admin:
        logger.debug(
            f"require_admin_or_developer: Admin '{user_email}' granted access to upload apps"
        )
        return dict(user)
    
    # Check if user is a developer (has apps:manage_own permission)
    is_developer = await authz.check(
        subject=user_email,
        resource="experiments",
        action="manage_own",
        user_object=dict(user)
    )
    
    if is_developer:
        logger.debug(
            f"require_admin_or_developer: Developer '{user_email}' granted access to upload experiments"
        )
        return dict(user)
    
    # Neither admin nor developer
    logger.warning(
        f"require_admin_or_developer: Access DENIED for '{user_email}'. User is not an admin or developer."
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator or developer privileges are required to upload experiments.",
    )


async def get_current_user_or_redirect(
    request: Request, user: Optional[Mapping[str, Any]] = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    FastAPI Dependency: Enforces user authentication. Redirects to login if not authenticated.
    """
    if not user:
        try:
            login_route_name = "login_get"
            login_url = request.url_for(login_route_name)
            original_path = request.url.path
            safe_next_path = _validate_next_url(original_path)
            redirect_url = f"{login_url}?next={safe_next_path}"

            logger.info(
                f"get_current_user_or_redirect: User not authenticated. Redirecting to login. Original path: '{original_path}', Redirect URL: '{redirect_url}'"
            )
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": redirect_url},
                detail="Not authenticated. Redirecting to login.",
            )
        except Exception as e:
            logger.error(
                f"get_current_user_or_redirect: Failed to generate login redirect URL for route '{login_route_name}': {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required, but redirect failed.",
            )
    return dict(user)


def require_permission(obj: str, act: str, force_login: bool = True):
    """
    Dependency Factory: Creates a dependency checking for a specific permission
    using the pluggable AuthZ provider.

    Args:
        obj: The resource (object) to check.
        act: The action (permission) to check.
        force_login: If True (default), uses `get_current_user_or_redirect`.
                     If False, uses `get_current_user` and checks permissions
                     for 'anonymous' if no user is found.
    """

    # 1. Choose the correct user dependency based on the flag
    user_dependency = get_current_user_or_redirect if force_login else get_current_user

    async def _check_permission(
        # 2. The type hint MUST be Optional now
        user: Optional[Dict[str, Any]] = Depends(user_dependency),
        # 3. Ask for the generic INTERFACE
        authz: AuthorizationProvider = Depends(get_authz_provider),
    ) -> Optional[Dict[str, Any]]:  # 4. Return type is also Optional
        """Internal dependency function performing the AuthZ check."""

        # 5. Check for 'anonymous' if user is None
        user_email = user.get("email") if user else "anonymous"

        if not user_email:
            # This should be unreachable if 'anonymous' is the fallback
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
            )

        # 6. Use the generic, async interface method
        has_perm = await authz.check(
            subject=user_email,
            resource=obj,
            action=act,
            user_object=user,  # Pass full context (or None)
        )

        if not has_perm:
            logger.warning(
                f"require_permission: Access DENIED for user '{user_email}' to ('{obj}', '{act}')."
            )

            # 7. Handle the failure
            if not user:
                # User is anonymous and lacks permission.
                # 401 suggests logging in might help.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"You must be logged in with permission to '{act}' on '{obj}'.",
                )
            else:
                # User is logged in but lacks permission
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"You do not have permission to perform '{act}' on the resource '{obj}'.",
                )

        logger.debug(
            f"require_permission: Access GRANTED for user '{user_email}' to ('{obj}', '{act}')."
        )
        return user  # Returns the user dict or None

    return _check_permission


# Note: require_experiment_access and require_experiment_ownership_or_admin
# are large functions that depend on get_experiment_config from the application layer.
# They will be extracted in a follow-up step or can be imported from the application layer.

