#!/usr/bin/env python3
"""
FastAPI Web Application for Vector Hacking Example

This demonstrates MDB_ENGINE with a vector hacking demo including:
- Vector inversion attack visualization
- Real-time status updates
- Modern, responsive UI
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

# Setup logger
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from openai import AzureOpenAI

from mdb_engine import MongoDBEngine
from mdb_engine.embeddings import EmbeddingService

# Load environment variables
load_dotenv()


class StartAttackRequest(BaseModel):
    target: Optional[str] = None
    generate_random: bool = False  # If True, generate random target using LLM


# Initialize FastAPI app
app = FastAPI(
    title="Vector Hacking - MDB_ENGINE Demo",
    description="A demonstration of vector inversion/hacking using LLMs",
    version="1.0.0",
)

# CORS is now handled automatically by setup_auth_from_manifest() based on manifest.json

# Templates directory - works in both Docker (/app) and local development
templates_dir = Path("/app/templates")
if not templates_dir.exists():
    templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Global engine instance (will be initialized in startup)
engine: Optional[MongoDBEngine] = None
db = None
vector_hacking_service = None

import importlib.util

# Import vector hacking service
import sys
from pathlib import Path

# Add current directory to path for imports
current_dir = Path(__file__).parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Import using importlib to handle both local and Docker paths
vector_hacking_file = current_dir / "vector_hacking.py"
if vector_hacking_file.exists():
    spec = importlib.util.spec_from_file_location("vector_hacking_module", vector_hacking_file)
    vector_hacking_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vector_hacking_module)
    VectorHackingService = vector_hacking_module.VectorHackingService
else:
    # Fallback: try regular import
    try:
        from vector_hacking import VectorHackingService
    except ImportError:
        raise ImportError(f"Could not find vector_hacking.py in {current_dir}")


@app.on_event("startup")
async def startup_event():
    """Initialize the MongoDB Engine on startup"""
    global engine, db, vector_hacking_service

    logger.info("Starting Vector Hacking Web Application...")

    # Get MongoDB connection from environment
    mongo_uri = os.getenv("MONGO_URI", "mongodb://admin:password@mongodb:27017/?authSource=admin")
    db_name = os.getenv("MONGO_DB_NAME", "vector_hacking_db")

    # Initialize the MongoDB Engine
    engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

    # Connect to MongoDB
    await engine.initialize()
    logger.info("Engine initialized successfully")

    # Load and register the app manifest
    manifest_path = Path("/app/manifest.json")
    if not manifest_path.exists():
        manifest_path = Path(__file__).parent / "manifest.json"

    if manifest_path.exists():
        manifest = await engine.load_manifest(manifest_path)
        success = await engine.register_app(manifest, create_indexes=True)
        if success:
            logger.info(f"App '{manifest['slug']}' registered successfully")
        else:
            logger.warning("Failed to register app")

    # Get scoped database and store globally
    db = engine.get_scoped_db("vector_hacking")

    # Initialize vector hacking service with Azure OpenAI client and EmbeddingService
    # Type 4: Let service initialization errors bubble up to framework handler
    # Create Azure OpenAI client
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    if not endpoint or not key:
        logger.error(
            "Azure OpenAI not configured! Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables."
        )
        raise RuntimeError("Azure OpenAI not configured - required for vector hacking")

    openai_client = AzureOpenAI(api_version=api_version, azure_endpoint=endpoint, api_key=key)

    # Get EmbeddingService - try memory service first, fallback to standalone
    app_config = engine.get_app("vector_hacking")
    embedding_model = "text-embedding-3-small"
    temperature = 0.8

    # Detect if using Azure OpenAI
    is_azure = bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"))

    # Get embedding model and temperature from manifest.json config
    if app_config:
        if "embedding_config" in app_config:
            config_embedding_model = app_config["embedding_config"].get("default_embedding_model")
            # If using Azure OpenAI, prefer Azure-compatible models
            if config_embedding_model:
                if is_azure and not config_embedding_model.startswith(("text-embedding", "ada")):
                    logger.warning(
                        f"Embedding model '{config_embedding_model}' may not be compatible with Azure OpenAI. Using '{embedding_model}' instead."
                    )
                else:
                    embedding_model = config_embedding_model

    # Try memory service first (if memory_config is enabled)
    memory_service = engine.get_memory_service("vector_hacking")
    if memory_service:
        # Type 4: Let EmbeddingService creation errors bubble up
        # Note: EmbeddingService doesn't use memory_service - it's standalone
        # Use get_embedding_service helper instead
        from mdb_engine.embeddings import get_embedding_service

        embedding_service = get_embedding_service(config={})
        logger.info("EmbeddingService initialized with mem0 (via memory service)")

    # Fallback: initialize standalone using manifest.json config
    if not memory_service:
        embedding_config = app_config.get("embedding_config", {}) if app_config else {}

        config = {}
        # Use the embedding_model we determined above (which already handles Azure compatibility)
        config["default_embedding_model"] = embedding_model

        from mdb_engine.embeddings import get_embedding_service

        embedding_service = get_embedding_service(config=config)
        logger.info(
            f"EmbeddingService initialized standalone (from manifest.json, model: {embedding_model})"
        )

    logger.info(
        f"Vector hacking config: chat={deployment_name}, embedding={embedding_model}, temp={temperature}"
    )

    # Initialize vector hacking service
    vector_hacking_service = VectorHackingService(
        mongo_uri=mongo_uri,
        db_name=db_name,
        write_scope="vector_hacking",
        read_scopes=["vector_hacking"],
        openai_client=openai_client,
        embedding_service=embedding_service,
        deployment_name=deployment_name,
        embedding_model=embedding_model,
        temperature=temperature,
    )
    logger.info("Vector hacking service initialized with Azure OpenAI and EmbeddingService")

    logger.info("Web application ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global engine, vector_hacking_service

    if vector_hacking_service:
        try:
            await vector_hacking_service.stop_attack()
        except (RuntimeError, AttributeError):
            # Type 2: Recoverable - service may not be initialized, continue cleanup
            pass

    if engine:
        await engine.shutdown()
        logger.info("Cleaned up and shut down")


def get_db():
    """Get the scoped database"""
    global engine, db
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    if db is None:
        return engine.get_scoped_db("vector_hacking")
    return db


# ============================================================================
# Routes
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Home page - shows the vector hacking interface"""
    if vector_hacking_service:
        try:
            # Render the index page using the service's template rendering
            html_content = await vector_hacking_service.render_index()
            return HTMLResponse(content=html_content)
        except (AttributeError, RuntimeError):
            # Type 2: Recoverable - service not available, try fallback template
            try:
                return templates.TemplateResponse(request, "index.html")
            except (RuntimeError, FileNotFoundError):
                # Type 2: Recoverable - template not found, return basic HTML
                return HTMLResponse(
                    content="<h1>Vector Hacking Demo</h1><p>Template rendering failed. Check logs.</p>"
                )
    else:
        # Fallback if service not available
        try:
            return templates.TemplateResponse(request, "index.html")
        except (RuntimeError, FileNotFoundError):
            # Type 2: Recoverable - template not found, return basic HTML
            return HTMLResponse(
                content="<h1>Vector Hacking Demo</h1><p>Vector hacking service not initialized. Please check configuration.</p>"
            )


@app.post("/start", response_class=JSONResponse)
async def start_attack(request: Optional[StartAttackRequest] = None):
    """
    Start the vector hacking attack.

    Can use:
    - Custom target (if request.target is provided)
    - AI-generated random target (if request.generate_random is True)
    - Default target (if neither is provided)
    """
    if not vector_hacking_service:
        raise HTTPException(
            status_code=503,
            detail="Vector hacking service not initialized. Check LLM service configuration.",
        )

    # Type 4: Let errors bubble up to framework handler
    # Get parameters from request
    target = request.target if request and request.target else None
    generate_random = request.generate_random if request else False

    result = await vector_hacking_service.start_attack(
        custom_target=target, generate_random=generate_random
    )
    return result


@app.post("/stop", response_class=JSONResponse)
async def stop_attack():
    """Stop the vector hacking attack"""
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    # Type 4: Let errors bubble up to framework handler
    result = await vector_hacking_service.stop_attack()
    return result


@app.get("/api/status", response_class=JSONResponse)
async def get_attack_status():
    """Get the current status of the vector hacking attack"""
    if not vector_hacking_service:
        return {
            "status": "not_available",
            "running": False,
            "error": "Vector hacking service not initialized. Check LLM service configuration.",
        }

    # Type 4: Let errors bubble up to framework handler
    status_data = await vector_hacking_service.get_status()
    return status_data


@app.post("/api/generate-target", response_class=JSONResponse)
async def generate_random_target():
    """Generate a random target phrase using LLM"""
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    # Type 4: Let errors bubble up to framework handler
    target = await vector_hacking_service.generate_random_target()
    return {"target": target, "status": "generated"}


@app.post("/api/reset", response_class=JSONResponse)
async def reset_attack(request: Optional[StartAttackRequest] = None):
    """
    Reset attack state for a new attack - game-like experience.

    Can optionally set a new target or generate a random one.
    """
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    # Type 4: Let errors bubble up to framework handler
    # Get parameters from request
    new_target = request.target if request and request.target else None
    generate_random = request.generate_random if request else False

    result = await vector_hacking_service.reset_attack(
        new_target=new_target, generate_random=generate_random
    )
    return result


@app.post("/api/restart", response_class=JSONResponse)
async def restart_attack():
    """
    Restart attack - resets state and signals frontend to reload page.

    This endpoint resets the state. The frontend will reload the page
    for a fresh game-like experience.
    """
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    # Type 4: Let errors bubble up to framework handler
    # Simple reset - stop current attack and clear all state
    await vector_hacking_service.stop_attack()
    await vector_hacking_service.reset_attack()

    return {"status": "ready", "reload": True, "message": "State reset. Page will reload."}


@app.get("/api/health", response_class=JSONResponse)
async def health_api():
    """Health status API endpoint"""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    health = await engine.get_health_status()
    return health


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, ws="auto")
