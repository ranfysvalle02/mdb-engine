#!/usr/bin/env python3
"""
OSO Cloud Hello World Example
=============================

A clean, minimal example demonstrating OSO Cloud authorization with mdb-engine.

This example shows:
- How to set up OSO Cloud authorization with mdb-engine
- How to use FastAPI dependencies for authentication and authorization
- How to enforce permissions on API endpoints
- How mdb-engine automatically handles app lifecycle and configuration

Key Concepts:
- engine.create_app() - Creates FastAPI app with automatic lifecycle management
- get_scoped_db() - Provides database access scoped to your app
- get_authz_provider() - Provides OSO Cloud authorization provider
- authz.check() - Checks permissions using OSO Cloud policy evaluation
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_authz_provider
from mdb_engine.auth import (
    authenticate_app_user,
    create_app_session,
    get_app_user,
    logout_user,
)

# ============================================================================
# CONFIGURATION
# ============================================================================

load_dotenv()

logger = logging.getLogger(__name__)

# App identifier - must match the slug in manifest.json
APP_SLUG = "oso_hello_world"

# ============================================================================
# STEP 1: INITIALIZE MONGODB ENGINE
# ============================================================================
# The MongoDBEngine is the core of mdb-engine. It manages:
# - Database connections
# - App registration and configuration
# - Authorization providers (OSO Cloud, Casbin, etc.)
# - Lifecycle management

from mdb_engine.env import get_mongo_uri, get_db_name

engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(fallback="mongodb://mongodb:27017/"),
    db_name=get_db_name(fallback="oso_hello_world_db"),
)

# ============================================================================
# STEP 2: CREATE FASTAPI APP WITH MDB-ENGINE
# ============================================================================
# engine.create_app() does the heavy lifting:
# - Loads manifest.json configuration
# - Sets up OSO Cloud provider from manifest
# - Seeds demo users (if configured)
# - Configures CORS, middleware, etc.
# - Returns a fully configured FastAPI app

app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="OSO Cloud Hello World",
    description="Interactive demo showing OSO Cloud authorization with mdb-engine",
    version="1.0.0",
)

# Template engine for rendering HTML
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# STEP 3: DEFINE DEPENDENCIES
# ============================================================================
# Dependencies are reusable functions that FastAPI injects into route handlers.
# mdb-engine provides several built-in dependencies:
# - get_scoped_db: Database access scoped to your app
# - get_authz_provider: Authorization provider (OSO Cloud, Casbin, etc.)
# - get_current_user: Current authenticated user (from mdb_engine.dependencies)


async def get_current_user(request: Request):
    """
    Get the currently authenticated user from session cookie.
    
    This dependency:
    1. Reads the session cookie set during login
    2. Validates the session is still valid
    3. Returns the user document from MongoDB
    4. Raises 401 if not authenticated
    
    Note: Uses get_scoped_db(request) because we're in a request context.
    For non-request contexts, use engine.get_scoped_db(APP_SLUG) directly.
    """
    db = await get_scoped_db(request)  # Auto-scoped to this app
    app_config = engine.get_app(APP_SLUG)
    user = await get_app_user(
        request=request,
        slug_id=APP_SLUG,
        db=db,
        config=app_config,
        allow_demo_fallback=False,
    )
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ============================================================================
# STEP 4: DEFINE ROUTES
# ============================================================================
# Routes demonstrate the authorization flow:
# 1. User authentication (login)
# 2. Permission checking (authorization)
# 3. Protected resource access


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the main demo page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health", response_class=JSONResponse)
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": APP_SLUG,
        "engine": "initialized" if engine.initialized else "starting",
    }


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db=Depends(get_scoped_db),
):
    """
    Authenticate user and create session.
    
    Flow:
    1. Authenticate user credentials against MongoDB
    2. Create session cookie using mdb-engine's session management
    3. Return success response
    
    Demo users are auto-seeded from manifest.json on first run.
    """
    user = await authenticate_app_user(
        db=db,
        email=email,
        password=password,
        collection_name="users",
    )

    if not user:
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": "Invalid credentials"},
        )

    # Create session cookie - mdb-engine handles secure session management
    response = JSONResponse(content={"success": True, "user_id": str(user["_id"])})
    app_config = engine.get_app(APP_SLUG)

    await create_app_session(
        request=request,
        slug_id=APP_SLUG,
        user_id=str(user["_id"]),
        config=app_config,
        response=response,
    )

    logger.info(f"User logged in: {email}")
    return response


@app.post("/logout")
async def logout(request: Request):
    """
    Clear session and logout user.
    
    Clears the session cookie and invalidates the user session.
    """
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)

    # Also clear app-specific cookie
    app_config = engine.get_app(APP_SLUG)
    if app_config:
        auth = app_config.get("auth", {})
        users_config = auth.get("users", {})
        cookie_name = f"{users_config.get('session_cookie_name', 'app_session')}_{APP_SLUG}"
        response.delete_cookie(key=cookie_name, httponly=True, samesite="lax")

    return response


@app.get("/api/me")
async def get_me(
    request: Request,
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider),
):
    """
    Get current user info and their permissions.
    
    This endpoint demonstrates permission checking:
    - authz.check(subject, resource, action) queries OSO Cloud
    - OSO evaluates the policy (main.polar) against stored role facts
    - Returns True/False based on role assignments in manifest.json
    
    Example: authz.check("alice@example.com", "documents", "read")
    - Checks if alice@example.com has "read" permission on "documents"
    - OSO Cloud evaluates: Does user have "editor" or "viewer" role?
    - Policy says: "editor" → read + write, "viewer" → read only
    """
    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    email = user.get("email", "unknown")

    # Check permissions by querying OSO Cloud
    # OSO evaluates main.polar policy against role facts from manifest.json
    permissions = []
    for action in ["read", "write"]:
        try:
            if await authz.check(email, "documents", action):
                permissions.append(action)
        except Exception as e:
            logger.warning(f"Permission check failed for {email}/{action}: {e}")

    # Determine role for UI display
    role = "editor" if "write" in permissions else "viewer" if "read" in permissions else "none"

    return {
        "user_id": user.get("user_id"),
        "email": email,
        "permissions": permissions,
        "role": role,
    }


@app.get("/api/documents")
async def list_documents(
    request: Request,
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """
    List all documents. Requires 'read' permission.
    
    Authorization flow:
    1. User must be authenticated (get_current_user dependency)
    2. OSO Cloud checks if user has 'read' permission for 'documents'
    3. If denied → 403 Forbidden
    4. If allowed → Query MongoDB (automatically scoped to this app)
    
    The database query is automatically scoped to this app via get_scoped_db.
    This ensures data isolation between different apps.
    """
    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    # Authorization check: OSO Cloud evaluates policy
    if not await authz.check(user.get("email"), "documents", "read"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot read documents")

    # Database query - automatically scoped to this app
    docs = await db.documents.find({}).sort("created_at", -1).to_list(100)

    return {
        "documents": [
            {
                "id": str(d["_id"]),
                "title": d.get("title"),
                "content": d.get("content"),
                "created_by": d.get("created_by"),
                "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
            }
            for d in docs
        ]
    }


@app.post("/api/documents")
async def create_document(
    request: Request,
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    """
    Create a new document. Requires 'write' permission.
    
    Same authorization pattern as list_documents, but checks 'write' permission.
    Only users with 'editor' role (configured in manifest.json) can create documents.
    
    Authorization check happens BEFORE database write, ensuring security.
    """
    if not authz:
        raise HTTPException(status_code=503, detail="Authorization service unavailable")

    # Authorization check: Only editors have 'write' permission
    if not await authz.check(user.get("email"), "documents", "write"):
        raise HTTPException(status_code=403, detail="Permission denied: cannot write documents")

    body = await request.json()

    # Create document with user metadata
    doc = {
        "title": body.get("title", "Untitled"),
        "content": body.get("content", ""),
        "created_by": user.get("email"),
        "created_at": datetime.utcnow(),
    }

    # Insert into MongoDB (automatically scoped to this app)
    result = await db.documents.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    doc["created_at"] = doc["created_at"].isoformat()

    logger.info(f"Document created by {user.get('email')}: {doc['title']}")
    return {"success": True, "document": doc}


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
