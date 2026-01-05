#!/usr/bin/env python3
"""
FastAPI Web Application for Vector Hacking Example

This demonstrates MDB_ENGINE with a vector hacking demo including:
- Vector inversion attack visualization
- Real-time status updates
- Modern, responsive UI

Uses engine.create_app() for automatic lifecycle management.
"""
import logging
import os
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

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Vector Hacking - MDB_ENGINE Demo",
    description="A demonstration of vector inversion/hacking using LLMs",
    version="1.0.0",
)


class StartAttackRequest(BaseModel):
    target: Optional[str] = None
    generate_random: bool = False  # If True, generate random target using LLM


# Templates directory - works in both Docker (/app) and local development
templates_dir = Path("/app/templates")
if not templates_dir.exists():
    templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Vector hacking service instance (initialized on startup)
vector_hacking_service: Optional[VectorHackingService] = None


def get_db(request: Request):
    """Get the scoped database from app state"""
    return request.app.state.engine.get_scoped_db(APP_SLUG)


@app.on_event("startup")
async def post_startup():
    """Initialize vector hacking service after engine is ready"""
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

    # Get EmbeddingService - try memory service first, fallback to standalone
    app_config = engine.get_app(APP_SLUG)
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
    embedding_service = None
    memory_service = engine.get_memory_service(APP_SLUG)
    if memory_service:
        from mdb_engine.embeddings import get_embedding_service

        embedding_service = get_embedding_service(config={})
        logger.info("EmbeddingService initialized with mem0 (via memory service)")

    # Fallback: initialize standalone using manifest.json config
    if not embedding_service:
        embedding_config = app_config.get("embedding_config", {}) if app_config else {}

        config = {}
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


@app.on_event("shutdown")
async def pre_shutdown():
    """Cleanup vector hacking service before engine shuts down"""
    global vector_hacking_service

    if vector_hacking_service:
        try:
            await vector_hacking_service.stop_attack()
        except (RuntimeError, AttributeError):
            pass

    logger.info("Vector hacking service cleaned up")


# ============================================================================
# Routes
# ============================================================================


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container healthchecks"""
    health = await engine.get_health_status()
    status_code = 200 if health.get("status") == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Home page - shows the vector hacking interface"""
    if vector_hacking_service:
        try:
            # Render the index page using the service's template rendering
            html_content = await vector_hacking_service.render_index()
            return HTMLResponse(content=html_content)
        except (AttributeError, RuntimeError):
            # Fallback to template
            try:
                return templates.TemplateResponse(request, "index.html")
            except (RuntimeError, FileNotFoundError):
                return HTMLResponse(
                    content="<h1>Vector Hacking Demo</h1><p>Template rendering failed. Check logs.</p>"
                )
    else:
        # Fallback if service not available
        try:
            return templates.TemplateResponse(request, "index.html")
        except (RuntimeError, FileNotFoundError):
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

    status_data = await vector_hacking_service.get_status()
    return status_data


@app.post("/api/generate-target", response_class=JSONResponse)
async def generate_random_target():
    """Generate a random target phrase using LLM"""
    if not vector_hacking_service:
        raise HTTPException(status_code=503, detail="Vector hacking service not initialized")

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

    await vector_hacking_service.stop_attack()
    await vector_hacking_service.reset_attack()

    return {"status": "ready", "reload": True, "message": "State reset. Page will reload."}


@app.get("/api/health", response_class=JSONResponse)
async def health_api():
    """Health status API endpoint (legacy - use /health instead)"""
    health = await engine.get_health_status()
    return health


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, ws="auto")
