"""
FastAPI Authentication and Authorization Dependencies

Provides FastAPI dependency functions for authentication and authorization.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import os
import uuid
import logging
from typing import Optional, Dict, Any, Mapping, List, Callable, Tuple
from datetime import datetime, timedelta

import jwt
from fastapi import Request, Depends, HTTPException, status, Cookie

# Import from local modules
from .provider import AuthorizationProvider
from .jwt import decode_jwt_token, extract_token_metadata
from .token_store import TokenBlacklist
from .session_manager import SessionManager

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


async def get_token_blacklist(request: Request) -> Optional[TokenBlacklist]:
    """
    FastAPI Dependency: Retrieves token blacklist from app.state.
    
    Returns None if blacklist is not configured (backward compatibility).
    """
    blacklist = getattr(request.app.state, "token_blacklist", None)
    return blacklist


async def get_session_manager(request: Request) -> Optional[SessionManager]:
    """
    FastAPI Dependency: Retrieves session manager from app.state.
    
    Returns None if session manager is not configured (backward compatibility).
    """
    session_mgr = getattr(request.app.state, "session_manager", None)
    return session_mgr


async def get_current_user(
    request: Request,
    token: Optional[str] = Cookie(default=None),
) -> Optional[Dict[str, Any]]:
    """
    FastAPI Dependency: Decodes and validates the JWT stored in the 'token' cookie.
    
    Enhanced with token blacklist checking if blacklist is available.
    """
    if not token:
        logger.debug("get_current_user: No 'token' cookie found.")
        return None

    try:
        # Extract token metadata first to get jti
        metadata = extract_token_metadata(token, SECRET_KEY)
        jti = metadata.get("jti") if metadata else None
        
        # Check blacklist if available
        if jti:
            blacklist = await get_token_blacklist(request)
            if blacklist:
                is_revoked = await blacklist.is_revoked(jti)
                if is_revoked:
                    logger.info(f"get_current_user: Token {jti} is blacklisted (revoked)")
                    return None
                
                # Also check user-level revocation
                user_id = metadata.get("user_id") or metadata.get("email")
                if user_id:
                    user_revoked = await blacklist.is_user_revoked(user_id)
                    if user_revoked:
                        logger.info(f"get_current_user: All tokens for user {user_id} are revoked")
                        return None
        
        payload = decode_jwt_token(token, SECRET_KEY)
        
        # Verify token type (should be access token for backward compatibility, or no type)
        token_type = payload.get("type")
        if token_type and token_type not in ("access", None):
            logger.warning(f"get_current_user: Invalid token type '{token_type}' for access token")
            return None
        
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
        # Extract token metadata first to get jti
        metadata = extract_token_metadata(token, SECRET_KEY)
        jti = metadata.get("jti") if metadata else None
        
        # Check blacklist if available
        if jti:
            blacklist = await get_token_blacklist(request)
            if blacklist:
                is_revoked = await blacklist.is_revoked(jti)
                if is_revoked:
                    logger.info(f"get_current_user_from_request: Token {jti} is blacklisted (revoked)")
                    return None
                
                # Also check user-level revocation
                user_id = metadata.get("user_id") or metadata.get("email")
                if user_id:
                    user_revoked = await blacklist.is_user_revoked(user_id)
                    if user_revoked:
                        logger.info(f"get_current_user_from_request: All tokens for user {user_id} are revoked")
                        return None
        
        payload = decode_jwt_token(token, SECRET_KEY)
        
        # Verify token type (should be access token for backward compatibility, or no type)
        token_type = payload.get("type")
        if token_type and token_type not in ("access", None):
            logger.warning(f"get_current_user_from_request: Invalid token type '{token_type}' for access token")
            return None
        
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


async def get_refresh_token(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None),
) -> Optional[Dict[str, Any]]:
    """
    FastAPI Dependency: Validates refresh token from cookie.
    
    Args:
        request: FastAPI Request object
        refresh_token: Refresh token from cookie (default cookie name: 'refresh_token')
    
    Returns:
        Decoded refresh token payload or None if invalid
    """
    if not refresh_token:
        # Try alternative cookie name
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            logger.debug("get_refresh_token: No refresh token cookie found.")
            return None
    
    try:
        # Extract token metadata first
        metadata = extract_token_metadata(refresh_token, SECRET_KEY)
        jti = metadata.get("jti") if metadata else None
        
        # Check blacklist if available
        if jti:
            blacklist = await get_token_blacklist(request)
            if blacklist:
                is_revoked = await blacklist.is_revoked(jti)
                if is_revoked:
                    logger.info(f"get_refresh_token: Refresh token {jti} is blacklisted")
                    return None
        
        payload = decode_jwt_token(refresh_token, SECRET_KEY)
        
        # Verify token type
        token_type = payload.get("type")
        if token_type != "refresh":
            logger.warning(f"get_refresh_token: Invalid token type '{token_type}' for refresh token")
            return None
        
        # Check session if available
        session_mgr = await get_session_manager(request)
        if session_mgr and jti:
            session = await session_mgr.get_session_by_refresh_token(jti)
            if not session or not session.get("active"):
                logger.info(f"get_refresh_token: Session not found or inactive for refresh token {jti}")
                return None
            
            # Validate session fingerprint if enabled
            from .config_helpers import get_session_fingerprinting_config
            fingerprinting_config = get_session_fingerprinting_config(request)
            if fingerprinting_config.get("enabled", True) and fingerprinting_config.get("validate_on_refresh", True):
                stored_fingerprint = session.get("session_fingerprint")
                if stored_fingerprint:
                    from .utils import generate_session_fingerprint
                    device_id = request.cookies.get("device_id") or payload.get("device_id")
                    if device_id:
                        current_fingerprint = generate_session_fingerprint(request, device_id)
                        if current_fingerprint != stored_fingerprint:
                            logger.warning(f"get_refresh_token: Session fingerprint mismatch for refresh token {jti}")
                            return None
        
        logger.debug(f"get_refresh_token: Refresh token validated for user '{payload.get('email', 'N/A')}'")
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("get_refresh_token: Refresh token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"get_refresh_token: Invalid refresh token: {e}")
        return None
    except Exception as e:
        logger.error(
            f"get_refresh_token: Unexpected error decoding refresh token: {e}", exc_info=True
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


async def refresh_access_token(
    request: Request,
    refresh_token_payload: Dict[str, Any],
    device_info: Optional[Dict[str, Any]] = None
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """
    Refresh an access token using a valid refresh token.
    
    This function:
    1. Validates the refresh token
    2. Checks session status
    3. Generates new token pair (with rotation if enabled)
    4. Updates session activity
    5. Revokes old refresh token if rotation is enabled
    
    Args:
        request: FastAPI Request object
        refresh_token_payload: Decoded refresh token payload
        device_info: Optional device information for new tokens
    
    Returns:
        Tuple of (access_token, refresh_token, metadata) or None if refresh failed
    """
    try:
        from .jwt import generate_token_pair
        from ..config import TOKEN_ROTATION_ENABLED
        
        user_id = refresh_token_payload.get("user_id") or refresh_token_payload.get("email")
        old_refresh_jti = refresh_token_payload.get("jti")
        device_id = refresh_token_payload.get("device_id")
        
        if not user_id:
            logger.warning("refresh_access_token: No user_id in refresh token")
            return None
        
        # Check session if available
        session_mgr = await get_session_manager(request)
        session = None
        if session_mgr:
            session = await session_mgr.get_session_by_refresh_token(old_refresh_jti)
            if not session or not session.get("active"):
                logger.warning(f"refresh_access_token: Session not found or inactive for {old_refresh_jti}")
                return None
            
            # Validate session fingerprint if enabled
            from .config_helpers import get_session_fingerprinting_config
            fingerprinting_config = get_session_fingerprinting_config(request)
            if fingerprinting_config.get("enabled", True) and fingerprinting_config.get("validate_on_refresh", True):
                stored_fingerprint = session.get("session_fingerprint")
                if stored_fingerprint:
                    from .utils import generate_session_fingerprint
                    device_id = device_id or request.cookies.get("device_id")
                    if device_id:
                        current_fingerprint = generate_session_fingerprint(request, device_id)
                        if current_fingerprint != stored_fingerprint:
                            logger.warning(f"refresh_access_token: Session fingerprint mismatch for user {user_id}")
                            return None
        
        # Prepare user data for new tokens
        user_data = {
            "user_id": user_id,
            "email": refresh_token_payload.get("email"),
        }
        
        # Use existing device_id or generate new one
        if not device_id:
            device_id = str(uuid.uuid4()) if not device_info else device_info.get("device_id")
        
        if device_info:
            device_info["device_id"] = device_id
        else:
            device_info = {"device_id": device_id}
        
        # Generate new token pair
        access_token, new_refresh_token, token_metadata = generate_token_pair(
            user_data,
            SECRET_KEY,
            device_info=device_info
        )
        
        # If rotation enabled, revoke old refresh token
        if TOKEN_ROTATION_ENABLED and old_refresh_jti:
            blacklist = await get_token_blacklist(request)
            if blacklist:
                # Get expiry from old token
                from ..config import REFRESH_TOKEN_TTL
                expires_at = datetime.utcnow() + timedelta(seconds=REFRESH_TOKEN_TTL)
                await blacklist.revoke_token(
                    old_refresh_jti,
                    user_id=user_id,
                    expires_at=expires_at,
                    reason="token_rotation"
                )
            
            # Revoke old session if rotation enabled
            if session_mgr:
                await session_mgr.revoke_session_by_refresh_token(old_refresh_jti)
        
        # Create or update session with new refresh token
        if session_mgr:
            new_refresh_jti = token_metadata.get("refresh_jti")
            ip_address = request.client.host if request.client else None
            
            from .utils import generate_session_fingerprint
            new_fingerprint = generate_session_fingerprint(request, device_id) if device_id else None
            
            if old_refresh_jti and TOKEN_ROTATION_ENABLED:
                update_data = {
                    "refresh_jti": new_refresh_jti,
                    "last_seen": datetime.utcnow(),
                    "ip_address": ip_address
                }
                if new_fingerprint:
                    update_data["session_fingerprint"] = new_fingerprint
                await session_mgr.collection.update_one(
                    {"refresh_jti": old_refresh_jti},
                    {"$set": update_data}
                )
            else:
                await session_mgr.create_session(
                    user_id=user_id,
                    device_id=device_id,
                    refresh_jti=new_refresh_jti,
                    device_info=device_info,
                    ip_address=ip_address,
                    session_fingerprint=new_fingerprint
                )
        
        logger.debug(f"refresh_access_token: New tokens generated for user {user_id}")
        return access_token, new_refresh_token, token_metadata
    except Exception as e:
        logger.error(f"refresh_access_token: Error refreshing token: {e}", exc_info=True)
        return None

