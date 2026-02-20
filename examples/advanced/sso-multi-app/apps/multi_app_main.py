#!/usr/bin/env python3
"""
Multi-App Main - Single Deployment Example
==========================================

Demonstrates how to deploy multiple SSO apps under a single FastAPI instance
using create_multi_app(). Perfect for platforms like Render.com where you want
a single service deployment.

This example shows two approaches:
1. Programmatic configuration (this file)
2. Manifest-based configuration (see multi_app_manifest.json)

Key Features:
- Single FastAPI app mounts multiple child apps
- Shared authentication (SSO) works seamlessly
- All apps accessible under one domain with path prefixes
- Unified health check endpoint
- Perfect for Render.com single-service deployment

Usage:
    # Run with uvicorn
    uvicorn multi_app_main:app --host 0.0.0.0 --port 8000 --reload

    # Or use the manifest-based approach
    uvicorn multi_app_main:app_manifest --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from mdb_engine import MongoDBEngine
except ImportError as e:
    logger.error(f"Failed to import mdb_engine: {e}", exc_info=True)
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

# MongoDB connection — use env.py helpers so both old (MONGODB_URI)
# and new (MDB_MONGO_URI) env var names work.
from mdb_engine.env import get_db_name, get_mongo_uri

mongo_uri = get_mongo_uri()
db_name = get_db_name(fallback="oblivio_apps")

# Base directory for apps (relative to this file)
APPS_DIR = Path(__file__).parent

# ============================================================================
# APPROACH 1: PROGRAMMATIC CONFIGURATION
# ============================================================================

logger.info("=" * 60)
logger.info("Creating Multi-App (Programmatic Configuration)")
logger.info("=" * 60)

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=mongo_uri,
    db_name=db_name,
)

# Create multi-app with programmatic configuration
# Note: create_multi_app is async -- use asyncio.run() at module level.
# uvicorn creates its own event loop at runtime, so this is safe.
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": APPS_DIR / "auth-hub" / "manifest.json",
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "pwd-zero",
            "manifest": APPS_DIR / "sso-app-1" / "manifest.json",
            "path_prefix": "/pwd-zero",
        },
        {
            "slug": "flux",
            "manifest": APPS_DIR / "sso-app-2" / "manifest.json",
            "path_prefix": "/flux",
        },
        {
            "slug": "member",
            "manifest": APPS_DIR / "member" / "manifest.json",
            "path_prefix": "/member",
        },
        {
            "slug": "ai-chat",
            "manifest": APPS_DIR / "sso-app-3" / "manifest.json",
            "path_prefix": "/ai-chat",
        },
    ],
    title="SSO Multi-App Platform",
    description="Multi-app deployment with SSO support",
    version="1.0.0",
)

logger.info("Multi-app created successfully!")
logger.info("Apps will be accessible at:")
logger.info("  - /auth-hub/*  (Auth Hub)")
logger.info("  - /pwd-zero/*  (pwd-zero)")
logger.info("  - /flux/*      (FLUX)")
logger.info("  - /member/*    (Member - Cognitive Memory Showcase)")
logger.info("  - /ai-chat/*   (AI Chat - True Perfect Recall)")
logger.info("  - /health      (Unified health check)")

# ============================================================================
# APPROACH 2: MANIFEST-BASED CONFIGURATION (Alternative)
# ============================================================================

# Uncomment to use manifest-based configuration instead:
#
# app_manifest = engine.create_multi_app(
#     multi_app_manifest=APPS_DIR.parent / "multi_app_manifest.json",
#     title="SSO Multi-App Platform",
# )
#
# logger.info("Multi-app created from manifest!")

# ============================================================================
# ADDITIONAL ROUTES (Optional)
# ============================================================================

# Add a root route that redirects to auth hub
@app.get("/")
async def root():
    """Root endpoint - redirects to auth hub."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/auth-hub", status_code=302)


# Add info endpoint
@app.get("/info")
async def info():
    """Get information about mounted apps."""
    mounted_apps = getattr(app.state, "mounted_apps", [])
    return {
        "platform": "SSO Multi-App Platform",
        "mounted_apps": [
            {
                "slug": app_info["slug"],
                "path_prefix": app_info["path_prefix"],
                "status": app_info["status"],
            }
            for app_info in mounted_apps
        ],
        "endpoints": {
            "health": "/health",
            "info": "/info",
            "auth_hub": "/auth-hub",
            "pwd_zero": "/pwd-zero",
            "flux": "/flux",
            "member": "/member",
            "ai_chat": "/ai-chat",
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
    logger.info(f"  http://{host}:{port}/pwd-zero")
    logger.info(f"  http://{host}:{port}/flux")
    logger.info(f"  http://{host}:{port}/member")
    logger.info(f"  http://{host}:{port}/ai-chat")
    logger.info(f"  http://{host}:{port}/health")

    uvicorn.run(
        "multi_app_main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
