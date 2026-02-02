#!/usr/bin/env python3
"""
Chat App - Real-Time Chat with WebSocket Tickets
=================================================

Demonstrates ticket-based WebSocket authentication for real-time chat.
Messages are stored in MongoDB and broadcast to all connected clients.
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

APP_SLUG = "chat-app"

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
            title="Chat App",
            description="Real-time chat with WebSocket ticket authentication",
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


async def handle_chat_message(websocket, message: dict):
    """
    Handle incoming WebSocket messages from clients.
    
    Message types:
    - {"type": "chat", "text": "message text"} - Chat message
    """
    message_type = message.get("type")
    
    if message_type == "chat":
        text = message.get("text", "")
        if not text:
            return
        
        # Get user info from websocket connection metadata
        # The user_id and user_email are available from the authenticated connection
        user_id = getattr(websocket, "user_id", None)
        user_email = getattr(websocket, "user_email", None)
        
        # Create message document
        message_doc = {
            "text": text,
            "user_id": user_id,
            "user_email": user_email,
            "created_at": datetime.utcnow(),
        }
        
        # Store in database
        db = engine.get_scoped_db(APP_SLUG)
        await db.messages.insert_one(message_doc)
        
        # Broadcast to all connected clients
        await broadcast_to_app(
            APP_SLUG,
            {
                "type": "message",
                "text": text,
                "user_email": user_email,
                "timestamp": message_doc["created_at"].isoformat(),
            },
        )
        
        logger.info(f"Chat message from {user_email}: {text[:50]}...")


# Register WebSocket message handler
register_message_handler(APP_SLUG, "chat", handle_chat_message)
logger.info("WebSocket message handler registered for chat-app")


# ============================================================================
# DATA MODELS
# ============================================================================


class MessageCreate(BaseModel):
    """Request model for creating a chat message."""
    text: str


# ============================================================================
# ROUTES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Chat app home page."""
    # Check if user is authenticated
    user = getattr(request.state, "user", None)
    if not user:
        # Redirect to auth hub for login
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth-hub/login?redirect_to=/chat-app", status_code=302)
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user_email": user.get("email", "Unknown"),
        },
    )


@app.get("/api/messages")
async def get_messages(limit: int = 50, db=Depends(get_scoped_db)):
    """Get recent chat messages."""
    messages = (
        await db.messages.find({})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(length=limit)
    )
    
    # Serialize messages
    for msg in messages:
        if "_id" in msg:
            msg["_id"] = str(msg["_id"])
        if "created_at" in msg and isinstance(msg["created_at"], datetime):
            msg["created_at"] = msg["created_at"].isoformat()
    
    return {"messages": list(reversed(messages))}


@app.post("/api/messages")
async def create_message(message: MessageCreate, request: Request, db=Depends(get_scoped_db)):
    """Create a new chat message and broadcast via WebSocket."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    user_id = user.get("user_id") or user.get("sub") or user.get("_id")
    user_email = user.get("email", "Unknown")
    
    # Create message document
    message_doc = {
        "text": message.text,
        "user_id": str(user_id),
        "user_email": user_email,
        "created_at": datetime.utcnow(),
    }
    
    # Store in database
    result = await db.messages.insert_one(message_doc)
    
    # Broadcast to all connected clients via WebSocket
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "message",
            "text": message.text,
            "user_email": user_email,
            "timestamp": message_doc["created_at"].isoformat(),
        },
    )
    
    logger.info(f"Chat message created and broadcast: {user_email}: {message.text[:50]}...")
    
    return {
        "id": str(result.inserted_id),
        "message": "Message sent successfully",
    }
