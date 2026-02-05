#!/usr/bin/env python3
"""
Vector Hacking API
==================

Demonstrates how an LLM can reverse-engineer hidden text using vector distances.

This example showcases mdb_engine best practices:
1. `engine.create_app()` for automatic FastAPI lifecycle management
2. Service initialization via `on_startup` callback
3. Clean separation of concerns (config, service, schemas)

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import sys
from pathlib import Path

# Ensure local imports work in Docker and local dev
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request

# ============================================================================
# MDB_ENGINE IMPORTS
# ============================================================================

from mdb_engine import MongoDBEngine

# ============================================================================
# LOCAL IMPORTS
# ============================================================================

from config import config, get_llm_client
from schemas import (
    AttackHistoryResponse,
    AttackStartResponse,
    AttackStatusResponse,
    AttackStopResponse,
    ErrorResponse,
    HealthResponse,
    StartAttackRequest,
)
from service import VectorHackingService

# ============================================================================
# SETUP
# ============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ============================================================================
# STEP 1: CREATE THE ENGINE
# ============================================================================
# MongoDBEngine manages database connections and provides automatic
# data isolation per app via the `app_slug`.

engine = MongoDBEngine(
    mongo_uri=config.mongo_uri,
    db_name=config.db_name,
)

# ============================================================================
# STEP 2: DEFINE STARTUP CALLBACK (OPTIONAL)
# ============================================================================
# Use this to initialize services after the engine is ready.


async def on_startup(app, engine, manifest):
    """Create and store the service instance on app.state."""
    from mdb_engine.embeddings.service import get_embedding_service
    
    app.state.vector_service = VectorHackingService(
        embedding_service=get_embedding_service(config={"enabled": True}),
        llm_client=get_llm_client(),
        config=config,
    )
    logger.info("VectorHackingService initialized")


# ============================================================================
# STEP 3: CREATE THE FASTAPI APP
# ============================================================================
# `engine.create_app()` handles:
# - Automatic engine initialization on startup
# - Manifest loading and validation
# - App registration for data isolation
# - Graceful shutdown

app = engine.create_app(
    slug=config.app_slug,
    manifest=Path(__file__).parent / "manifest.json",
    title="Vector Hacking API",
    description="Demonstrates vector inversion attacks using mdb_engine",
    version="1.0.0",
    on_startup=on_startup,
    openapi_tags=[
        {"name": "health", "description": "Health check endpoints"},
        {"name": "attack", "description": "Vector attack operations"},
    ],
)


# ============================================================================
# STEP 4: DEFINE DEPENDENCIES
# ============================================================================


def get_service(request: Request) -> VectorHackingService:
    """Get the service from app state."""
    return request.app.state.vector_service


# ============================================================================
# STEP 5: DEFINE ROUTES
# ============================================================================
# Routes use FastAPI's dependency injection to get the service.


@app.get("/", tags=["health"])
async def root():
    """API info."""
    return {
        "name": "Vector Hacking API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health():
    """Health check endpoint."""
    return HealthResponse(status="healthy", service_initialized=True)


@app.post(
    "/api/attack/start",
    response_model=AttackStartResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["attack"],
)
async def start_attack(
    request: StartAttackRequest,
    svc: VectorHackingService = Depends(get_service),
):
    """
    Start a vector hacking attack.
    
    Optionally provide a target phrase or let the LLM generate one randomly.
    """
    try:
        target = request.target
        if request.generate_random or not target:
            target = await svc.generate_random_target()

        result = await svc.start_attack(target)

        if result.get("status") == "error":
            raise HTTPException(status_code=503, detail=result.get("error"))

        return AttackStartResponse(
            status="started",
            target=target,
            message=f"Attack started against: {target}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
    "/api/attack/stop",
    response_model=AttackStopResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["attack"],
)
async def stop_attack(svc: VectorHackingService = Depends(get_service)):
    """Stop the current attack."""
    await svc.stop_attack()
    return AttackStopResponse(status="stopped", message="Attack stopped")


@app.get("/api/attack/status", response_model=AttackStatusResponse, tags=["attack"])
async def get_status(svc: VectorHackingService = Depends(get_service)):
    """Get current attack status and progress."""
    return AttackStatusResponse(**await svc.get_status())


@app.get("/api/attack/history", response_model=AttackHistoryResponse, tags=["attack"])
async def get_history():
    """Get attack history."""
    return AttackHistoryResponse(history=[], total=0)
