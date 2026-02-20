#!/usr/bin/env python3
"""
GDPR Compliance Demo

Demonstrates GDPR compliance features:
- Right to Access: Export all user data
- Right to Erasure: Delete user data (hard delete / soft delete)
- Right to Rectification: Update user data
- Memory Service Integration: Shows how memories are handled in GDPR deletion

This example demonstrates the GDPR deletion strategies:
- Hard Delete (default): Permanently removes all data including cold storage
- Soft Delete: Marks data as deleted for legal retention
- Anonymization: Replaces identifiers with anonymous values
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from bson.objectid import ObjectId
from dotenv import load_dotenv
from fastapi import Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mdb_engine import MongoDBEngine
from mdb_engine.auth.decorators import require_auth
from mdb_engine.auth.users import create_app_session, get_app_user
from mdb_engine.auth.utils import login_user, logout_user, register_user

# Load environment variables
load_dotenv()

logger = logging.getLogger("gdpr_demo")

# App slug constant
APP_SLUG = "gdpr_demo"

# Templates directory
templates_dir = (
    Path("/app/templates")
    if Path("/app/templates").exists()
    else Path(__file__).parent / "templates"
)
templates = Jinja2Templates(directory=str(templates_dir))

# Initialize the MongoDB Engine
from mdb_engine.env import get_mongo_uri, get_db_name

engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(fallback="mongodb://mongodb:27017/"),
    db_name=get_db_name(fallback="gdpr_demo_db"),
)

# -------------------------------------------------------------------------
# STARTUP LIFECYCLE
# -------------------------------------------------------------------------

async def on_startup(app, engine, manifest):
    """Initialize the application."""
    logger.info("GDPR Demo application started")


# -------------------------------------------------------------------------
# CREATE APP
# -------------------------------------------------------------------------

app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    on_startup=on_startup,
)

# -------------------------------------------------------------------------
# AUTHENTICATION ROUTES
# -------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page - redirect to login or dashboard."""
    app_user = await get_app_user(request, APP_SLUG)
    if app_user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request):
    """Handle login."""
    form_data = await request.form()
    email = form_data.get("email", "")
    password = form_data.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    user = await login_user(engine, APP_SLUG, email, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = await create_app_session(engine, APP_SLUG, str(user["_id"]))
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key="gdpr_demo_session",
        value=session_id,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(request: Request):
    """Handle registration."""
    form_data = await request.form()
    email = form_data.get("email", "")
    password = form_data.get("password", "")
    name = form_data.get("name", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    user = await register_user(engine, APP_SLUG, email, password, {"name": name})
    if not user:
        raise HTTPException(status_code=400, detail="Registration failed")

    session_id = await create_app_session(engine, APP_SLUG, str(user["_id"]))
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key="gdpr_demo_session",
        value=session_id,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout(request: Request):
    """Handle logout."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("gdpr_demo_session")
    return response


# -------------------------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
@require_auth()
async def dashboard(request: Request):
    """GDPR dashboard."""
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        return RedirectResponse(url="/login", status_code=302)

    # Get memory statistics
    memory_service = engine.get_memory_service(APP_SLUG)
    memory_count = 0
    if memory_service:
        try:
            user_id = str(app_user["_id"])
            memories = await asyncio.to_thread(
                memory_service.get_all, user_id=user_id, limit=1000
            )
            memory_count = len(memories) if isinstance(memories, list) else 0
        except Exception as e:
            logger.error(f"Error getting memory count: {e}")

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": app_user,
            "memory_count": memory_count,
        },
    )


# -------------------------------------------------------------------------
# GDPR ROUTES
# -------------------------------------------------------------------------

@app.get("/api/gdpr/export")
@require_auth()
async def export_user_data_api(request: Request, format: str = "json"):
    """
    Export all user data (GDPR Right to Access - Article 15).
    
    Returns all user data in JSON format.
    """
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_identifier = app_user.get("email") or str(app_user["_id"])
    identifier_type = "email" if app_user.get("email") else "user_id"

    try:
        export_data = await engine.export_user_data(
            user_identifier=user_identifier,
            identifier_type=identifier_type,
            app_slug=APP_SLUG,
            format=format,
        )
        return JSONResponse(export_data)
    except Exception as e:
        logger.error(f"Error exporting user data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@app.delete("/api/gdpr/delete")
@require_auth()
async def delete_user_data_api(
    request: Request,
    soft_delete: bool = False,
    anonymize: bool = False,
):
    """
    Delete user data (GDPR Right to Erasure - Article 17).
    
    Args:
        soft_delete: If True, mark as deleted (for legal retention).
                     Default is False (hard delete - GDPR compliant).
        anonymize: If True, anonymize instead of delete.
    
    Returns deletion results.
    """
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_identifier = app_user.get("email") or str(app_user["_id"])
    identifier_type = "email" if app_user.get("email") else "user_id"

    try:
        deletion_result = await engine.delete_user_data(
            user_identifier=user_identifier,
            identifier_type=identifier_type,
            app_slug=APP_SLUG,
            soft_delete=soft_delete,
            anonymize=anonymize,
        )

        # Logout user after deletion
        response = JSONResponse(deletion_result)
        response.delete_cookie("gdpr_demo_session")
        return response
    except Exception as e:
        logger.error(f"Error deleting user data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")


@app.put("/api/gdpr/rectify")
@require_auth()
async def update_user_data_api(request: Request):
    """
    Update user data (GDPR Right to Rectification - Article 16).
    
    Accepts JSON body with fields to update.
    """
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        updates = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    user_identifier = app_user.get("email") or str(app_user["_id"])
    identifier_type = "email" if app_user.get("email") else "user_id"

    try:
        result = await engine.update_user_data(
            user_identifier=user_identifier,
            identifier_type=identifier_type,
            app_slug=APP_SLUG,
            updates=updates,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error updating user data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


# -------------------------------------------------------------------------
# MEMORY MANAGEMENT ROUTES (for demo purposes)
# -------------------------------------------------------------------------

@app.post("/api/memories/add")
@require_auth()
async def add_memory(request: Request):
    """Add a memory for demonstration purposes."""
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        data = await request.json()
        memory_text = data.get("memory", "")
        if not memory_text:
            raise HTTPException(status_code=400, detail="Memory text required")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        raise HTTPException(status_code=500, detail="Memory service not available")

    user_id = str(app_user["_id"])
    try:
        result = await asyncio.to_thread(
            memory_service.inject,
            memory=memory_text,
            user_id=user_id,
            metadata={"source": "gdpr_demo", "created_at": datetime.utcnow().isoformat()},
        )
        return JSONResponse({"success": True, "memory": result})
    except Exception as e:
        logger.error(f"Error adding memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to add memory: {str(e)}")


@app.get("/api/memories")
@require_auth()
async def get_memories(request: Request, limit: int = 100):
    """Get all memories for the current user."""
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse({"memories": [], "count": 0})

    user_id = str(app_user["_id"])
    try:
        memories = await asyncio.to_thread(
            memory_service.get_all, user_id=user_id, limit=limit
        )
        return JSONResponse(
            {
                "memories": memories if isinstance(memories, list) else [],
                "count": len(memories) if isinstance(memories, list) else 0,
            }
        )
    except Exception as e:
        logger.error(f"Error getting memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get memories: {str(e)}")


@app.delete("/api/memories")
@require_auth()
async def delete_all_memories(request: Request, hard_delete: bool = True):
    """
    Delete all memories for the current user.
    
    Demonstrates the required hard_delete parameter.
    """
    app_user = await get_app_user(request, APP_SLUG)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse({"success": True, "deleted_count": 0})

    user_id = str(app_user["_id"])
    try:
        # Get count before deletion
        all_memories = await asyncio.to_thread(
            memory_service.get_all, user_id=user_id, limit=1000
        )
        memory_count = len(all_memories) if isinstance(all_memories, list) else 0

        # Delete with explicit hard_delete parameter (required)
        success = await asyncio.to_thread(
            memory_service.delete_all,
            user_id=user_id,
            hard_delete=hard_delete,
        )

        return JSONResponse(
            {
                "success": success,
                "deleted_count": memory_count,
                "hard_delete": hard_delete,
                "message": (
                    f"{'Hard deleted' if hard_delete else 'Soft deleted'} "
                    f"{memory_count} memories for user {user_id}"
                    if success
                    else "Failed to delete memories"
                ),
            }
        )
    except Exception as e:
        logger.error(f"Error deleting memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete memories: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
