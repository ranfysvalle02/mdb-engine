#!/usr/bin/env python3
"""
WebSocket Tickets Multi-App Example
===================================

Demonstrates ticket-based WebSocket authentication in a multi-app setup using create_multi_app().

This example showcases:
- Multi-app architecture with create_multi_app()
- Ticket-based WebSocket authentication (secure, single-use tickets)
- Real-time chat and notifications via WebSocket
- Minimal MDB-Engine usage (just database operations)

Usage:
    uvicorn apps.multi_app_main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
import sys
from pathlib import Path

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

# MongoDB connection
mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
db_name = os.getenv("MONGODB_DB", "websocket_tickets_example")

# Base directory for apps (relative to this file)
APPS_DIR = Path(__file__).parent

# ============================================================================
# CREATE MULTI-APP
# ============================================================================

logger.info("=" * 60)
logger.info("Creating WebSocket Tickets Multi-App")
logger.info("=" * 60)

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=mongo_uri,
    db_name=db_name,
)

# Create multi-app with three apps
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": APPS_DIR / "auth-hub" / "manifest.json",
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "chat-app",
            "manifest": APPS_DIR / "chat-app" / "manifest.json",
            "path_prefix": "/chat-app",
        },
        {
            "slug": "notifications-app",
            "manifest": APPS_DIR / "notifications-app" / "manifest.json",
            "path_prefix": "/notifications-app",
        },
    ],
    title="WebSocket Tickets Multi-App",
    description="Multi-app example demonstrating ticket-based WebSocket authentication",
    version="1.0.0",
)

logger.info("Multi-app created successfully!")
logger.info("Apps will be accessible at:")
logger.info("  - /auth-hub/*           (Auth Hub - Registration/Login)")
logger.info("  - /chat-app/*           (Chat App - WebSocket Chat)")
logger.info("  - /notifications-app/*  (Notifications App - WebSocket Notifications)")
logger.info("  - /auth/ticket          (Ticket endpoint - available on parent app)")
logger.info("  - /health               (Unified health check)")

# ============================================================================
# ADDITIONAL ROUTES
# ============================================================================

# Root redirect to auth hub
@app.get("/")
async def root():
    """Root endpoint - redirects to auth hub."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/auth-hub", status_code=302)


# Info endpoint
@app.get("/info")
async def info():
    """Get information about mounted apps."""
    mounted_apps = getattr(app.state, "mounted_apps", [])
    return {
        "platform": "WebSocket Tickets Multi-App",
        "description": "Demonstrates ticket-based WebSocket authentication",
        "mounted_apps": [
            {
                "slug": app_info["slug"],
                "path_prefix": app_info["path_prefix"],
                "status": app_info.get("status", "active"),
            }
            for app_info in mounted_apps
        ],
        "endpoints": {
            "health": "/health",
            "info": "/info",
            "auth_hub": "/auth-hub",
            "chat_app": "/chat-app",
            "notifications_app": "/notifications-app",
            "ticket_endpoint": "/auth/ticket",
        },
        "websocket_endpoints": {
            "chat": "/chat-app/ws",
            "notifications": "/notifications-app/ws",
        },
    }


# ============================================================================
# RUN SERVER (if executed directly)
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info(f"Starting multi-app server on {host}:{port}")
    logger.info("Access apps at:")
    logger.info(f"  http://{host}:{port}/auth-hub")
    logger.info(f"  http://{host}:{port}/chat-app")
    logger.info(f"  http://{host}:{port}/notifications-app")
    logger.info(f"  http://{host}:{port}/health")

    uvicorn.run(
        "multi_app_main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
