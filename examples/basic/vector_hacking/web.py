#!/usr/bin/env python3
"""
FastAPI Web Application for Vector Hacking Example

This demonstrates MDB_ENGINE with a vector hacking demo including:
- Vector inversion attack visualization
- Real-time status updates
- Modern, responsive UI

Uses engine.create_app() for automatic lifecycle management with on_startup/on_shutdown callbacks.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from openai import AzureOpenAI
from pydantic import BaseModel
from vector_hacking import VectorHackingService

from mdb_engine import MongoDBEngine

# Load environment variables
load_dotenv()

# Configure logging to show INFO level (and respect LOG_LEVEL env var)
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

# Setup logger
logger = logging.getLogger(__name__)

# App configuration
APP_SLUG = os.getenv("APP_SLUG", "vector_hacking")

# Get MongoDB connection from environment
mongo_uri = os.getenv(
    "MONGO_URI",
    "mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true",
)
db_name = os.getenv("MONGO_DB_NAME", "vector_hacking_db")

# Initialize the MongoDB Engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)


class StartAttackRequest(BaseModel):
    target: Optional[str] = None
    generate_random: bool = False  # If True, generate random target using LLM


# Templates directory - works in both Docker (/app) and local development
templates_dir = Path("/app/templates")
if not templates_dir.exists():
    templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Vector hacking service instance (initialized via on_startup callback)
vector_hacking_service: Optional[VectorHackingService] = None


async def on_startup(app, eng, manifest):
    """Initialize vector hacking service after engine is ready.
    
    Called by engine.create_app() after full initialization.
    """
    global vector_hacking_service

    logger.info("Initializing Vector Hacking Service...")

    # Initialize vector hacking service with Azure OpenAI client and EmbeddingService
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

    # Get EmbeddingService from engine (configured via manifest.json)
    embedding_service = eng.get_embedding_service(APP_SLUG)
    if not embedding_service:
        logger.error(
            "EmbeddingService not configured! Add 'embedding_config' to manifest.json."
        )
        raise RuntimeError("EmbeddingService not configured - required for vector hacking")

    # Get embedding model from app config
    app_config = eng.get_app(APP_SLUG)
    embedding_model = "text-embedding-3-small"
    temperature = 0.8

    if app_config and "embedding_config" in app_config:
        embedding_model = app_config["embedding_config"].get("default_embedding_model", embedding_model)

    logger.info(f"EmbeddingService initialized (model: {embedding_model})")

    logger.info(
        f"Vector hacking config: chat={deployment_name}, embedding={embedding_model}, temp={temperature}"
    )

    # Initialize vector hacking service
    vector_hacking_service = VectorHackingService(
        mongo_uri=mongo_uri,
        db_name=db_name,
        write_scope=APP_SLUG,
        read_scopes=[APP_SLUG],
        openai_client=openai_client,
        embedding_service=embedding_service,
        deployment_name=deployment_name,
        embedding_model=embedding_model,
        temperature=temperature,
    )
    logger.info("Vector hacking service initialized with Azure OpenAI and EmbeddingService")

    logger.info("Web application ready!")


async def on_shutdown(app, eng, manifest):
    """Cleanup vector hacking service before engine shuts down.
    
    Called by engine.create_app() before shutdown.
    """
    global vector_hacking_service

    if vector_hacking_service:
        try:
            await vector_hacking_service.stop_attack()
        except (RuntimeError, AttributeError):
            pass

    logger.info("Vector hacking service cleaned up")


# Create FastAPI app with automatic lifecycle management
# on_startup and on_shutdown callbacks run within the engine's lifespan context
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Vector Hacking - MDB_ENGINE Demo",
    description="A demonstration of vector inversion/hacking using LLMs",
    version="1.0.0",
    on_startup=on_startup,
    on_shutdown=on_shutdown,
)


# ============================================================================
# Routes
# ============================================================================


@app.get("/health", response_class=JSONResponse)
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service_initialized": vector_hacking_service is not None,
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the main page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/attack/start")
async def start_attack(request: StartAttackRequest):
    """Start a vector hacking attack"""
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    try:
        # Get target (either provided or generate random)
        target = request.target
        if request.generate_random or not target:
            target = await vector_hacking_service.generate_random_target()

        # Start the attack
        await vector_hacking_service.start_attack(target)

        return {
            "status": "started",
            "target": target,
            "message": f"Attack started against target: {target[:50]}..."
            if len(target) > 50
            else f"Attack started against target: {target}",
        }
    except (ValueError, RuntimeError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/attack/stop")
async def stop_attack():
    """Stop the current attack"""
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

    try:
        await vector_hacking_service.stop_attack()
        return {"status": "stopped", "message": "Attack stopped"}
    except (ValueError, RuntimeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/attack/status")
async def get_status():
    """Get current attack status"""
    if not vector_hacking_service:
        return {
            "is_running": False,
            "iteration": 0,
            "current_similarity": 0.0,
            "best_similarity": 0.0,
            "target": None,
            "current_text": None,
            "best_text": None,
            "history": [],
        }

    status = await vector_hacking_service.get_status()
    return status


@app.get("/api/attack/history")
async def get_history(limit: int = 100):
    """Get attack history from database"""
    if not vector_hacking_service:
        return {"history": [], "total": 0}

    try:
        history = await vector_hacking_service.get_history(limit=limit)
        return {"history": history, "total": len(history)}
    except (ValueError, RuntimeError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
