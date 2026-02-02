#!/usr/bin/env python3
"""
Auth Hub - Authentication Hub for WebSocket Tickets Example
============================================================

Handles user registration and login for the multi-app WebSocket tickets example.
Sets JWT cookies that can be exchanged for WebSocket tickets.
"""

import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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

# Note: This app can run standalone OR be mounted in a multi-app setup.
# When mounted, routes defined here will be available on the mounted app.

# Initialize MongoDB Engine (or use injected engine from multi-app setup)
try:
    # Check if engine is already injected (multi-app mode)
    if "engine" in globals() and engine is not None:
        logger.info(f"Using injected MongoDBEngine for '{APP_SLUG}' (multi-app mode)")
    else:
        # Standalone mode - create new engine
        mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.getenv("MONGODB_DB", "websocket_tickets_example")
        logger.info(f"Initializing MongoDBEngine with URI: {mongo_uri[:50]}... (db: {db_name})")

        engine = MongoDBEngine(
            mongo_uri=mongo_uri,
            db_name=db_name,
        )
        logger.info("MongoDBEngine initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize MongoDBEngine: {e}", exc_info=True)
    sys.exit(1)

# Create FastAPI app (or use injected app from multi-app setup)
try:
    manifest_path = Path(__file__).parent / "manifest.json"
    
    # Check if app is already injected (multi-app mode)
    if "app" in globals() and app is not None:
        logger.info(f"Using injected FastAPI app for '{APP_SLUG}' (multi-app mode)")
        # Ensure engine is available in app state
        if not hasattr(app.state, "engine"):
            app.state.engine = engine
    else:
        # Standalone mode - create new app
        logger.info(f"Creating FastAPI app with manifest: {manifest_path}")
        app = engine.create_app(
            slug=APP_SLUG,
            manifest=manifest_path,
            title="Auth Hub",
            description="Authentication hub for WebSocket tickets example",
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


# ============================================================================
# ROUTES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root page - redirect to login if not authenticated, else redirect to chat-app."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/chat-app", status_code=302)
    # Use the app's path prefix for the redirect
    return RedirectResponse(url="/auth-hub/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    from mdb_engine.auth.csrf import get_csrf_token
    
    user = get_current_user(request)
    if user:
        # Already logged in, redirect to chat-app
        return RedirectResponse(url="/chat-app", status_code=302)
    csrf_token = get_csrf_token(request)
    return templates.TemplateResponse("login.html", {"request": request, "csrf_token": csrf_token})


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Login endpoint - authenticates user and sets JWT cookie."""
    user_pool = get_user_pool()
    if not user_pool:
        raise HTTPException(status_code=500, detail="User pool not available")

    try:
        from mdb_engine.auth.csrf import get_csrf_token
        
        # Authenticate user and get JWT token (authenticate() returns the token directly)
        token = await user_pool.authenticate(email, password)
        if not token:
            csrf_token = get_csrf_token(request)
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid email or password", "csrf_token": csrf_token},
                status_code=401,
            )

        # Create response with redirect
        response = RedirectResponse(url="/chat-app", status_code=302)

        # Set JWT cookie
        from mdb_engine.auth.cookie_utils import get_secure_cookie_settings

        cookie_settings = get_secure_cookie_settings(request)
        response.set_cookie(
            key="mdb_auth_token",
            value=token,
            **cookie_settings,
        )

        logger.info(f"User '{email}' logged in successfully")
        return response

    except Exception as e:
        from mdb_engine.auth.csrf import get_csrf_token
        
        logger.exception(f"Login error: {e}")
        csrf_token = get_csrf_token(request)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Login failed: {str(e)}", "csrf_token": csrf_token},
            status_code=500,
        )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    from mdb_engine.auth.csrf import get_csrf_token
    
    user = get_current_user(request)
    if user:
        # Already logged in, redirect to chat-app
        return RedirectResponse(url="/chat-app", status_code=302)
    csrf_token = get_csrf_token(request)
    return templates.TemplateResponse("register.html", {"request": request, "csrf_token": csrf_token})


@app.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),  # Name field kept for form, but not stored in user doc
):
    """Registration endpoint - creates new user."""
    user_pool = get_user_pool()
    if not user_pool:
        raise HTTPException(status_code=500, detail="User pool not available")

    try:
        from mdb_engine.auth.csrf import get_csrf_token
        
        # Check if user already exists
        existing_user = await user_pool.get_user_by_email(email)
        if existing_user:
            csrf_token = get_csrf_token(request)
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "User with this email already exists", "csrf_token": csrf_token},
                status_code=400,
            )

        # Create new user
        user = await user_pool.create_user(
            email=email,
            password=password,
            app_roles={"auth-hub": ["user"], "chat-app": ["user"], "notifications-app": ["user"]},
        )

        # Add name/metadata using the public API method
        if name:
            updated_user = await user_pool.update_user_metadata(email, {"name": name})
            if updated_user:
                user = updated_user

        # Authenticate to get JWT token (authenticate() returns the token)
        token = await user_pool.authenticate(email, password)
        if not token:
            raise HTTPException(status_code=500, detail="Failed to generate authentication token")

        # Create response with redirect
        response = RedirectResponse(url="/chat-app", status_code=302)

        # Set JWT cookie
        from mdb_engine.auth.cookie_utils import get_secure_cookie_settings

        cookie_settings = get_secure_cookie_settings(request)
        response.set_cookie(
            key="mdb_auth_token",
            value=token,
            **cookie_settings,
        )

        logger.info(f"User '{email}' registered successfully")
        return response

    except Exception as e:
        from mdb_engine.auth.csrf import get_csrf_token
        
        logger.exception(f"Registration error: {e}")
        csrf_token = get_csrf_token(request)
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": f"Registration failed: {str(e)}", "csrf_token": csrf_token},
            status_code=500,
        )


@app.post("/logout")
async def logout(request: Request):
    """Logout endpoint - clears JWT cookie."""
    from mdb_engine.auth.cookie_utils import clear_auth_cookies

    response = RedirectResponse(url="/auth-hub/login", status_code=302)
    clear_auth_cookies(response)
    return response
