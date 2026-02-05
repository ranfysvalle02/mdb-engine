#!/usr/bin/env python3
"""
SSO Auth Hub - SSO Authentication Hub
==========================================

Central authentication hub that handles user authentication and role management
for SSO-enabled apps. Demonstrates Single Sign-On (SSO) architecture.

Key Features:
- User registration and login
- JWT token issuance (shared across all apps)
- Role management for SSO apps
- User dashboard with role assignment
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Import shared security utilities
try:
    from shared_security import get_cookie_settings
except ImportError:
    # Fallback if shared_security not available (shouldn't happen in normal usage)
    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

try:
    from mdb_engine import MongoDBEngine
except ImportError as e:
    logger.error(f"Failed to import mdb_engine: {e}", exc_info=True)
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

APP_SLUG = "auth-hub"
AUTH_COOKIE = "mdb_auth_token"

# Initialize MongoDB Engine
try:
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DB", "oblivio_apps")
    logger.info(f"Initializing MongoDBEngine with URI: {mongo_uri[:50]}... (db: {db_name})")

    engine = MongoDBEngine(
        mongo_uri=mongo_uri,
        db_name=db_name,
    )
    logger.info("MongoDBEngine initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize MongoDBEngine: {e}", exc_info=True)
    sys.exit(1)

# Create FastAPI app
try:
    manifest_path = Path(__file__).parent / "manifest.json"
    logger.info(f"Creating FastAPI app with manifest: {manifest_path}")

    app = engine.create_app(
        slug=APP_SLUG,
        manifest=manifest_path,
        title="SSO Auth Hub",
        description="Central authentication hub for SSO apps",
        version="1.0.0",
    )
    logger.info("FastAPI app created successfully")
except Exception as e:
    logger.error(f"Failed to create FastAPI app: {e}", exc_info=True)
    sys.exit(1)

# Template engine
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def get_user_pool():
    """Get the shared user pool from app state."""
    return getattr(app.state, "user_pool", None)


def get_current_user(request: Request) -> dict | None:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


def get_roles(user: dict | None, app_slug: str = None) -> list:
    """Get user's roles for an app."""
    if not user:
        return []
    app_roles = user.get("app_roles", {})
    if app_slug:
        return app_roles.get(app_slug, [])
    return app_roles


def is_admin(user: dict | None) -> bool:
    """Check if user is admin in auth hub."""
    roles = get_roles(user, APP_SLUG)
    return "admin" in roles


def get_default_app_roles() -> dict[str, list[str]]:
    """
    Get default app roles for new user registration.

    This function returns a dictionary mapping app slugs to their default roles.
    The base role (e.g., "base_user" or "viewer") is assigned to all new users
    for each SSO app they should have access to.

    Configuration options:
    1. Environment variable DEFAULT_BASE_ROLE (default: "base_user")
    2. Environment variable SSO_APPS (comma-separated list of app slugs)
    3. Hardcoded fallback list

    Returns:
        Dictionary of app_slug -> [role_list]
    """
    # Get base role from environment or use default
    base_role = os.getenv("DEFAULT_BASE_ROLE", "base_user")

    # Get list of SSO apps from environment or use defaults
    sso_apps_env = os.getenv("SSO_APPS", "")
    if sso_apps_env:
        sso_apps = [app.strip() for app in sso_apps_env.split(",") if app.strip()]
    else:
        # Default SSO apps for this example
        sso_apps = ["pwd-zero", "flux", "ai-chat"]

    # Build default roles dictionary
    default_roles = {
        APP_SLUG: [base_role],  # Auth hub gets base role
    }

    # Add base role for all SSO apps
    for app_slug in sso_apps:
        default_roles[app_slug] = [base_role]

    return default_roles


def serialize_user_for_json(user: dict[str, Any]) -> dict[str, Any]:
    """
    Serialize user data for JSON responses by converting datetime objects to ISO strings.

    Handles:
    - datetime.datetime objects -> ISO format strings
    - ObjectId objects -> strings
    - None values -> None (preserved)
    - Other types -> preserved as-is

    Args:
        user: User dictionary that may contain datetime objects

    Returns:
        Serialized user dictionary safe for JSON encoding
    """
    if not user:
        return user

    # Try to import ObjectId for proper type checking
    try:
        from bson import ObjectId
    except ImportError:
        ObjectId = None

    serialized = {}
    for key, value in user.items():
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif ObjectId is not None and isinstance(value, ObjectId):
            # Handle MongoDB ObjectId
            serialized[key] = str(value)
        elif isinstance(value, dict):
            # Recursively handle nested dictionaries
            serialized[key] = serialize_user_for_json(value)
        elif isinstance(value, list):
            # Handle lists (e.g., roles list)
            serialized[key] = [
                serialize_user_for_json(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            serialized[key] = value

    return serialized


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root page - redirect to dashboard if logged in, else login."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    user = get_current_user(request)

    # Check if user is already logged in
    if user:
        # If redirect_to parameter exists, exchange token with SSO app
        redirect_to = request.query_params.get("redirect_to")
        if redirect_to:
            # URL-decode redirect_to (FastAPI auto-decodes query params, but be safe)
            from urllib.parse import unquote_plus

            redirect_to = unquote_plus(redirect_to)

            # Get token from cookie
            token = request.cookies.get(AUTH_COOKIE)
            if token:
                # Properly construct redirect URL with token
                # Check if redirect_to already has query parameters
                separator = "&" if "?" in redirect_to else "?"
                redirect_url = f"{redirect_to}{separator}token={quote_plus(token)}"
                return RedirectResponse(url=redirect_url, status_code=302)
            # If no token in cookie but user is logged in, something is wrong
            # Fall through to dashboard

        # No redirect_to or no token - go to dashboard
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Authenticate user and issue JWT token."""
    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Authenticate and get JWT token
    token = await pool.authenticate(email, password)

    if not token:
        return JSONResponse(
            status_code=401, content={"success": False, "detail": "Invalid credentials"}
        )

    # Get user for response
    user = await pool.validate_token(token)

    # Check if there's a redirect_to parameter (from SSO app)
    redirect_to = request.query_params.get("redirect_to")

    # Always return JSON - frontend will handle redirect
    response = JSONResponse(
        content={
            "success": True,
            "user": {"email": email, "roles": get_roles(user)},
            "redirect": redirect_to if redirect_to else "/dashboard",
            "token": token,  # Include token for frontend to exchange with SSO apps
            "redirect_to": redirect_to,  # Include for frontend
        }
    )

    # Set cookie for auth hub (no domain - works on this port only)
    cookie_settings = get_cookie_settings()
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=86400,  # 24 hours
        path="/",
        # No domain - cookie works on this port only
    )

    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    # CSRF token is automatically set by middleware, but we ensure it's available
    # The route is exempt from CSRF validation but token is still generated for frontend
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(None),
):
    """Register a new user in the shared pool."""
    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    try:
        # Get default app roles for new user (uses base_user or configured role)
        default_roles = get_default_app_roles()

        # Create user with default roles for all apps
        user = await pool.create_user(
            email=email,
            password=password,
            app_roles=default_roles,
        )

        # Add name/metadata if provided using the public API method
        if full_name:
            updated_user = await pool.update_user_metadata(email, {"name": full_name})
            if updated_user:
                user = updated_user

        # Authenticate and get token
        token = await pool.authenticate(email, password)

        # Check if there's a redirect_to parameter (from SSO app)
        redirect_to = request.query_params.get("redirect_to")

        # Always return JSON - frontend will handle redirect
        response = JSONResponse(
            content={
                "success": True,
                "user": serialize_user_for_json(user),
                "redirect": redirect_to if redirect_to else "/dashboard",
                "token": token,  # Include token for frontend to exchange with SSO apps
                "redirect_to": redirect_to,  # Include for frontend
            }
        )

        # Set cookie for auth hub (no domain - works on this port only)
        cookie_settings = get_cookie_settings()
        response.set_cookie(
            key=AUTH_COOKIE,
            value=token,
            httponly=cookie_settings["httponly"],
            samesite=cookie_settings["samesite"],
            secure=cookie_settings["secure"],
            max_age=86400,
            path="/",
            # No domain - cookie works on this port only
        )

        return response
    except ValueError as e:
        # Log detailed error server-side, return generic message to client
        logger.error(f"Registration error: {e}", exc_info=True)
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Registration failed. Please check your input and try again."},
        )


@app.post("/logout")
async def logout(request: Request):
    """Logout and revoke token."""
    pool = get_user_pool()

    # Get token from cookie
    token = request.cookies.get(AUTH_COOKIE)

    # Revoke token if we have pool and token
    if pool and token:
        try:
            await pool.revoke_token(token)
        except (ValueError, KeyError):
            pass  # Token may already be invalid

    # Redirect to login page after logout
    response = RedirectResponse(url="/login", status_code=302)
    # Delete cookie
    host = request.url.hostname
    cookie_domain = None
    if host == "localhost" or host == "127.0.0.1":
        cookie_domain = "localhost"
    cookie_settings = get_cookie_settings()
    response.delete_cookie(
        AUTH_COOKIE,
        path="/",
        domain=cookie_domain,
        secure=cookie_settings["secure"],
        samesite=cookie_settings["samesite"],
    )
    return response


# ============================================================================
# DASHBOARD ROUTES
# ============================================================================


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Auth hub dashboard with user and role management."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Get all users (admin only)
    all_users = []
    if is_admin(user):
        try:
            # Access shared users collection via connection manager
            raw_db = engine.connection_manager.mongo_db
            users_collection = raw_db["_mdb_engine_shared_users"]
            users_cursor = users_collection.find({})
            all_users = await users_cursor.to_list(length=100)
            # Sanitize users (remove password_hash)
            for u in all_users:
                u.pop("password_hash", None)
                if "_id" in u:
                    u["_id"] = str(u["_id"])
        except Exception as e:
            logger.error(f"Failed to fetch users: {e}", exc_info=True)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin(user),
            "all_users": all_users,
            "sso_apps": ["pwd-zero", "flux", "ai-chat"],
        },
    )


# ============================================================================
# API ROUTES - ROLE MANAGEMENT
# ============================================================================


@app.get("/api/users/{email}/roles")
async def get_user_roles(email: str, request: Request):
    """Get user roles across all apps (admin only)."""
    user = get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(403, "Admin access required")

    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    user_data = await pool.get_user_by_email(email)
    if not user_data:
        raise HTTPException(404, "User not found")

    return {"email": email, "app_roles": user_data.get("app_roles", {})}


@app.put("/api/users/{email}/roles/{app_slug}")
async def update_user_roles(
    email: str,
    app_slug: str,
    request: Request,
    roles: list[str] = None,
):
    """Update user roles for a specific app (admin only)."""
    user = get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(403, "Admin access required")

    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Get roles from request body
    body = await request.json()
    roles = body.get("roles", [])

    success = await pool.update_user_roles(email, app_slug, roles)

    if not success:
        raise HTTPException(404, "User not found")

    return {"success": True, "email": email, "app_slug": app_slug, "roles": roles}


@app.post("/api/users/{email}/grant-access/{app_slug}")
async def grant_access(
    email: str,
    app_slug: str,
    request: Request,
):
    """Grant base_user access to an SSO app (admin only)."""
    user = get_current_user(request)
    if not user or not is_admin(user):
        raise HTTPException(403, "Admin access required")

    pool = get_user_pool()
    if not pool:
        raise HTTPException(500, "User pool not initialized")

    # Get current roles
    user_data = await pool.get_user_by_email(email)
    if not user_data:
        raise HTTPException(404, "User not found")

    current_roles = user_data.get("app_roles", {}).get(app_slug, [])

    # Get base role from environment or use default
    base_role = os.getenv("DEFAULT_BASE_ROLE", "base_user")

    # Add base role if not present
    if base_role not in current_roles:
        current_roles.append(base_role)

    success = await pool.update_user_roles(email, app_slug, current_roles)

    if not success:
        raise HTTPException(404, "User not found")

    return {
        "success": True,
        "email": email,
        "app_slug": app_slug,
        "message": f"Granted {base_role} access to {app_slug}",
    }


@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    return {
        "email": user["email"],
        "roles": get_roles(user),
        "is_admin": is_admin(user),
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "app": APP_SLUG, "auth": "shared"}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting SSO Auth Hub on 0.0.0.0:8000")
    try:
        uvicorn.run(
            app, host="0.0.0.0", port=8000, log_level=os.getenv("LOG_LEVEL", "info").lower()
        )
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        sys.exit(1)
