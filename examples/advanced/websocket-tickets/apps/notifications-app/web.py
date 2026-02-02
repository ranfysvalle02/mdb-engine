#!/usr/bin/env python3
"""
Notifications App - Real-Time Notifications with WebSocket Tickets
===================================================================

Demonstrates ticket-based WebSocket authentication for real-time notifications.
Notifications are stored in MongoDB and broadcast to all connected clients.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

try:
    from mdb_engine import MongoDBEngine
    from mdb_engine.dependencies import get_scoped_db
    from mdb_engine.routing.websockets import broadcast_to_app, register_message_handler
except ImportError as e:
    logger.error(f"Failed to import mdb_engine: {e}", exc_info=True)
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

APP_SLUG = "notifications-app"

# Note: This app can run standalone OR be mounted in a multi-app setup.
# When mounted, routes defined here will be available on the mounted app.
# WebSocket message handlers registered here will work in both modes.

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
            title="Notifications App",
            description="Real-time notifications with WebSocket ticket authentication",
            version="1.0.0",
        )
        logger.info("FastAPI app created successfully")
except Exception as e:
    logger.error(f"Failed to create FastAPI app: {e}", exc_info=True)
    sys.exit(1)

# Template engine
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# WEB SOCKET MESSAGE HANDLER
# ============================================================================


async def handle_notification_message(websocket, message: dict):
    """
    Handle incoming WebSocket messages from clients.
    
    Message types:
    - {"type": "subscribe", "user_id": "..."} - Subscribe to user-specific notifications
    """
    message_type = message.get("type")
    
    if message_type == "subscribe":
        user_id = message.get("user_id")
        logger.info(f"Client subscribed to notifications for user: {user_id}")
    # Other message types can be handled here


# Register WebSocket message handler
register_message_handler(APP_SLUG, "notifications", handle_notification_message)
logger.info("WebSocket message handler registered for notifications-app")


# ============================================================================
# DATA MODELS
# ============================================================================


class NotificationCreate(BaseModel):
    """Request model for creating a notification."""
    title: str
    message: str
    user_id: str | None = None  # If None, broadcasts to all users


# ============================================================================
# ROUTES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Notifications app home page."""
    # Check if user is authenticated
    user = getattr(request.state, "user", None)
    if not user:
        # Redirect to auth hub for login
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth-hub/login?redirect_to=/notifications-app", status_code=302)
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user_email": user.get("email", "Unknown"),
            "user_id": str(user.get("user_id") or user.get("sub") or user.get("_id", "")),
        },
    )


@app.get("/api/notifications")
async def get_notifications(user_id: str | None = None, limit: int = 50, db=Depends(get_scoped_db)):
    """Get recent notifications, optionally filtered by user_id."""
    query = {}
    if user_id:
        query["user_id"] = user_id
    
    notifications = (
        await db.notifications.find(query)
        .sort("created_at", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    
    # Serialize notifications
    for notif in notifications:
        if "_id" in notif:
            notif["_id"] = str(notif["_id"])
        if "created_at" in notif and isinstance(notif["created_at"], datetime):
            notif["created_at"] = notif["created_at"].isoformat()
    
    return {"notifications": list(reversed(notifications))}


@app.post("/api/notifications")
async def create_notification(
    notification: NotificationCreate, request: Request, db=Depends(get_scoped_db)
):
    """Create a new notification and broadcast via WebSocket."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    user_id = user.get("user_id") or user.get("sub") or user.get("_id")
    user_email = user.get("email", "Unknown")
    
    # Use provided user_id or default to current user
    target_user_id = notification.user_id or str(user_id)
    
    # Create notification document
    notification_doc = {
        "title": notification.title,
        "message": notification.message,
        "user_id": target_user_id,
        "created_by": user_email,
        "created_at": datetime.utcnow(),
    }
    
    # Store in database
    result = await db.notifications.insert_one(notification_doc)
    
    # Broadcast to all connected clients via WebSocket
    # If user_id is specified, filter by user_id in the broadcast
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "notification",
            "title": notification.title,
            "message": notification.message,
            "user_id": target_user_id,
            "created_by": user_email,
            "timestamp": notification_doc["created_at"].isoformat(),
        },
        user_id=target_user_id if notification.user_id else None,  # Filter if specific user
    )
    
    logger.info(
        f"Notification created and broadcast: {notification.title} "
        f"(target_user: {target_user_id})"
    )
    
    return {
        "id": str(result.inserted_id),
        "message": "Notification created successfully",
    }
