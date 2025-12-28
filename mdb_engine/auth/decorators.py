"""
Authentication Decorators

Decorators for simplifying authentication and security enforcement.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import time
from collections import defaultdict
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from .dependencies import get_current_user_from_request

logger = logging.getLogger(__name__)

# Rate limiting storage (in-memory, can be replaced with Redis for distributed systems)
_rate_limit_storage: Dict[str, Dict[str, Any]] = defaultdict(dict)


def require_auth(redirect_to: str = "/login"):
    """
    Decorator for routes requiring authentication.

    Automatically injects user into request.state.user and redirects to login if not authenticated.

    Usage:
        @app.get("/dashboard")
        @require_auth()
        async def dashboard(request: Request):
            user = request.state.user  # Automatically available
            ...

    Args:
        redirect_to: URL to redirect to if not authenticated (default: "/login")
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            user = await get_current_user_from_request(request)
            if not user:
                # Check if it's an API request (JSON) or web request
                accept = request.headers.get("accept", "")
                if "application/json" in accept:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authentication required",
                    )
                else:
                    # Web request - redirect
                    return RedirectResponse(url=redirect_to, status_code=302)

            # Inject user into request state
            request.state.user = user

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def _is_production_environment() -> bool:
    """Check if running in production environment."""
    import os

    return (
        os.getenv("G_NOME_ENV") == "production"
        or os.getenv("ENVIRONMENT") == "production"
    )


def _validate_https(request: Request) -> None:
    """Validate HTTPS requirement in production."""
    if _is_production_environment() and request.url.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HTTPS required in production",
        )


async def _get_csrf_token(request: Request) -> Optional[str]:
    """Extract CSRF token from request headers or form data."""
    csrf_token = request.headers.get("X-CSRF-Token")
    if csrf_token:
        return csrf_token

    # Try to get from form data if not in headers
    try:
        form_data = await request.form()
        return form_data.get("csrf_token")
    except (RuntimeError, ValueError):
        # Type 2: Recoverable - form parsing failed, return None
        return None


def _is_state_changing_method(method: str) -> bool:
    """Check if HTTP method is state-changing."""
    return method in ["POST", "PUT", "DELETE", "PATCH"]


async def _validate_csrf_token(request: Request) -> None:
    """Validate CSRF token for state-changing requests."""
    csrf_token = await _get_csrf_token(request)
    session_csrf = request.cookies.get("csrf_token")

    # Only validate CSRF if a session token exists
    # If no session token exists yet (e.g., first registration), allow the request
    if session_csrf and (not csrf_token or csrf_token != session_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token",
        )


def token_security(enforce_https: bool = True, check_csrf: bool = True):
    """
    Decorator to enforce security settings from manifest.

    Validates HTTPS in production, CSRF tokens, and secure cookie enforcement.

    Usage:
        @app.post("/api/data")
        @token_security()
        async def update_data(request: Request):
            ...

    Args:
        enforce_https: Enforce HTTPS in production (default: True)
        check_csrf: Check CSRF tokens (default: True)
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            if enforce_https:
                _validate_https(request)

            if check_csrf and _is_state_changing_method(request.method):
                await _validate_csrf_token(request)

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def rate_limit_auth(
    endpoint: str = "login",
    max_attempts: Optional[int] = None,
    window_seconds: Optional[int] = None,
):
    """
    Rate limiting decorator for auth endpoints.

    Tracks attempts by IP + email and returns 429 when exceeded.
    If max_attempts/window_seconds not provided, reads from manifest config.

    Usage:
        @app.post("/login")
        @rate_limit_auth(endpoint="login")
        async def login(request: Request, email: str, password: str):
            ...

    Args:
        endpoint: Endpoint identifier for rate limiting (default: "login")
        max_attempts: Maximum attempts allowed (default: from manifest config or 5)
        window_seconds: Time window in seconds (default: from manifest config or 300)
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            # Get rate limit config from manifest if available
            config = getattr(request.state, "token_management_config", None)
            rate_limit_config = None

            if config:
                security = config.get("security", {})
                rate_limiting = security.get("rate_limiting", {})
                rate_limit_config = rate_limiting.get(endpoint)

            # Use provided values or config values or defaults
            if max_attempts is None:
                max_attempts_val = (
                    rate_limit_config.get("max_attempts") if rate_limit_config else 5
                )
            else:
                max_attempts_val = max_attempts

            if window_seconds is None:
                window_seconds_val = (
                    rate_limit_config.get("window_seconds")
                    if rate_limit_config
                    else 300
                )
            else:
                window_seconds_val = window_seconds

            # Get identifier (IP + email if available)
            ip_address = request.client.host if request.client else "unknown"
            email = kwargs.get("email") or (
                await request.form() if request.method == "POST" else {}
            ).get("email", "")

            identifier = f"{endpoint}:{ip_address}:{email}"
            current_time = time.time()

            # Clean old entries
            if identifier in _rate_limit_storage:
                attempts = _rate_limit_storage[identifier]
                # Remove old attempts outside window
                _rate_limit_storage[identifier] = {
                    ts: count
                    for ts, count in attempts.items()
                    if current_time - ts < window_seconds_val
                }

            # Count attempts in window
            attempts_in_window = sum(_rate_limit_storage[identifier].values())

            if attempts_in_window >= max_attempts_val:
                logger.warning(
                    f"Rate limit exceeded for {identifier}: "
                    f"{attempts_in_window} attempts in {window_seconds_val}s"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many attempts. Please try again in {window_seconds_val} seconds.",
                    headers={"Retry-After": str(window_seconds_val)},
                )

            # Record this attempt
            if identifier not in _rate_limit_storage:
                _rate_limit_storage[identifier] = {}
            _rate_limit_storage[identifier][current_time] = 1

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def auto_token_setup(func: Optional[Callable[..., Awaitable[Any]]] = None):
    """
    Decorator to automatically set up tokens on successful login/register.

    This decorator wraps login/register functions and automatically:
    - Generates token pair
    - Sets cookies with correct security settings
    - Creates session if enabled
    - Reads config from manifest

    Usage:
        @app.post("/login")
        @auto_token_setup
        async def login(request: Request, email: str, password: str):
            # Your login logic that returns user dict
            user = await authenticate_user(email, password)
            return {"user": user}  # Decorator handles token setup
    """

    def decorator(f: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(f)
        async def wrapper(request: Request, *args, **kwargs):
            # Call original function
            result = await f(request, *args, **kwargs)

            # If result contains user, set up tokens
            if isinstance(result, dict) and "user" in result:
                try:
                    from .cookie_utils import set_auth_cookies
                    from .dependencies import SECRET_KEY, get_session_manager
                    from .jwt import generate_token_pair
                    from .utils import get_device_info

                    user = result["user"]
                    user_data = {
                        "user_id": str(user.get("_id") or user.get("user_id")),
                        "email": user.get("email"),
                    }

                    # Get device info
                    device_info = get_device_info(request)

                    # Generate token pair
                    access_token, refresh_token, token_metadata = generate_token_pair(
                        user_data, str(SECRET_KEY), device_info=device_info
                    )

                    # Create session if available
                    session_mgr = await get_session_manager(request)
                    if session_mgr:
                        await session_mgr.create_session(
                            user_id=user_data["email"],
                            device_id=device_info["device_id"],
                            refresh_jti=token_metadata.get("refresh_jti"),
                            device_info=device_info,
                            ip_address=device_info.get("ip_address"),
                        )

                    # Get config from request state or manifest
                    config = getattr(request.state, "token_management_config", None)

                    # Create response if not already a response
                    if not hasattr(result, "set_cookie"):
                        from fastapi.responses import JSONResponse

                        response = JSONResponse(result)
                    else:
                        response = result

                    # Set cookies
                    set_auth_cookies(
                        response,
                        access_token,
                        refresh_token,
                        request=request,
                        config=config,
                    )

                    return response
                except (
                    ValueError,
                    TypeError,
                    AttributeError,
                    KeyError,
                    RuntimeError,
                ) as e:
                    logger.error(f"Error in auto_token_setup: {e}", exc_info=True)
                    # Return original result if token setup fails
                    return result

            return result

        return wrapper

    # Support both @auto_token_setup and @auto_token_setup()
    if func is None:
        return decorator
    else:
        return decorator(func)
