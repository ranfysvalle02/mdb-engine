#!/usr/bin/env python3
"""
FastAPI Web Application for Interactive RAG Example

This demonstrates MDB_ENGINE with:
- EmbeddingService for semantic text splitting and embeddings
- OpenAI SDK for chat completions
- Vector search with MongoDB Atlas Vector Search
- Knowledge base management with sessions
- Platform-level LLM abstractions via MongoDBEngine
"""
import os
import asyncio
import logging
import uuid
import tempfile
import warnings
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from bson.objectid import ObjectId

# Setup logger
logger = logging.getLogger(__name__)

# Handle Transformers/HF_HOME warnings early
# Set environment variables before any transformers imports
if not os.getenv("HF_HOME"):
    os.environ["HF_HOME"] = os.getenv("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
if not os.getenv("TRANSFORMERS_CACHE"):
    os.environ["TRANSFORMERS_CACHE"] = os.getenv("HF_HOME", "/app/.cache/huggingface")
if not os.getenv("HF_DATASETS_CACHE"):
    os.environ["HF_DATASETS_CACHE"] = os.getenv("HF_HOME", "/app/.cache/huggingface")

# Suppress the specific Transformers cache warning if it still appears
warnings.filterwarnings("ignore", message=".*TRANSFORMERS_CACHE.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Use `HF_HOME` instead.*", category=UserWarning)

# Removed WebSocket logging handler - using simple polling instead

from fastapi import FastAPI, Request, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Template
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mdb_engine import MongoDBEngine
from mdb_engine.embeddings import EmbeddingService, get_embedding_service
from mdb_engine.embeddings.dependencies import get_embedding_service_dep
from openai import AzureOpenAI
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Additional dependencies - with detailed error reporting
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError as e:
    DDGS_AVAILABLE = False
    logger.warning(f"ddgs not available (ImportError: {e}). Web search will be disabled.")

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError as e:
    DOCLING_AVAILABLE = False
    logger.warning(f"docling not available (ImportError: {e}). Document conversion will be limited.")

import requests

# Initialize FastAPI app
app = FastAPI(
    title="Interactive RAG - MDB_ENGINE Demo",
    description="An interactive RAG system with knowledge base management",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files directory - use explicit routes instead of mount for reliability
from fastapi.responses import FileResponse
# Get absolute path to static directory
try:
    _web_py_path = Path(__file__).resolve()
except NameError:
    # Fallback if __file__ is not available
    _web_py_path = Path.cwd() / "web.py"

static_dir = (_web_py_path.parent / "static").resolve()
if not static_dir.exists():
    # Try Docker path
    docker_static = Path("/app/static")
    if docker_static.exists():
        static_dir = docker_static.resolve()
    else:
        # Try current working directory
        cwd_static = Path.cwd() / "static"
        if cwd_static.exists():
            static_dir = cwd_static.resolve()

if static_dir.exists():
    static_dir_str = str(static_dir.absolute())
    logger.warning(f"✓ Static files directory configured: {static_dir_str}")
    logger.warning(f"  - styles.css exists: {(static_dir / 'styles.css').exists()}")
    logger.warning(f"  - script.js exists: {(static_dir / 'script.js').exists()}")
else:
    logger.error(f"✗ Static directory not found. Tried: {_web_py_path.parent / 'static'}, /app/static, {Path.cwd() / 'static'}")
    static_dir = None

# Templates directory - use relative path for local dev, absolute for Docker
template_dir = Path(__file__).parent / "templates"
if not template_dir.exists():
    template_dir = Path("/app/templates")
templates = Jinja2Templates(directory=str(template_dir))
logger.info(f"Templates directory: {template_dir}")

# Global engine instance (will be initialized in startup)
engine: Optional[MongoDBEngine] = None
db = None

# App configuration
APP_SLUG = "interactive_rag"
COLLECTION_NAME = "knowledge_base_sessions"
SESSION_FIELD = "session_id"

# In-memory state
chat_history: Dict[str, List[Dict[str, str]]] = {}
current_session: str = "default"
last_retrieved_sources: List[str] = []
last_retrieved_chunks: List[Dict[str, Any]] = []
background_tasks: Dict[str, Dict[str, Any]] = {}

_current_user_query: Optional[str] = None  # Store current user query for tool context

# Track which indexes are being created to prevent duplicate attempts
index_creation_in_progress: set = set()

# Application status tracking
app_status: Dict[str, Any] = {
    "initialized": False,
    "status": "initializing",
    "startup_time": None,
    "logs": [],
    "components": {
        "mongodb": {"status": "unknown", "message": ""},
        "engine": {"status": "unknown", "message": ""},
        "embedding_service": {"status": "unknown", "message": ""},
        "docling": {"status": "unknown", "message": ""},
        "rapidocr": {"status": "unknown", "message": ""}
    }
}

def add_status_log(message: str, level: str = "info", component: str = None):
    """Add a log entry to the status system"""
    timestamp = datetime.now().isoformat()
    log_entry = {
        "timestamp": timestamp,
        "level": level,
        "message": message,
        "component": component
    }
    app_status["logs"].append(log_entry)
    # Keep only last 100 logs
    if len(app_status["logs"]) > 100:
        app_status["logs"] = app_status["logs"][-100:]
    
    # Also log to standard logger
    if level == "error":
        logger.error(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)


# ============================================================================
# Dependency Injection
# ============================================================================

def get_db():
    """Get the scoped database"""
    global engine, db
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    if db is None:
        return engine.get_scoped_db(APP_SLUG)
    return db


def get_azure_openai_client() -> AzureOpenAI:
    """Get Azure OpenAI client configured from environment variables"""
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    
    if not endpoint or not key:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables."
        )
    
    return AzureOpenAI(
        api_version=api_version,
        azure_endpoint=endpoint,
        api_key=key
    )


async def embed_text(text: str, model: str = "text-embedding-3-small") -> List[float]:
    """Generate embedding for text using Azure OpenAI"""
    client = get_azure_openai_client()
    # Remove any model prefixes
    clean_model = model.replace("azure/", "").replace("openai/", "")
    
    try:
        response = await asyncio.to_thread(
            client.embeddings.create,
            model=clean_model,
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}", exc_info=True)
        raise


async def embed_chunks(texts: List[str], model: str = "text-embedding-3-small") -> List[List[float]]:
    """Generate embeddings for multiple texts using Azure OpenAI"""
    client = get_azure_openai_client()
    # Remove any model prefixes
    clean_model = model.replace("azure/", "").replace("openai/", "")
    
    try:
        response = await asyncio.to_thread(
            client.embeddings.create,
            model=clean_model,
            input=texts
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        logger.error(f"Failed to generate embeddings: {e}", exc_info=True)
        raise


def chunk_text(text: str, max_tokens: int = 1000, tokenizer_model: str = "gpt-3.5-turbo") -> List[str]:
    """Simple text chunking by tokens (approximate)"""
    from semantic_text_splitter import TextSplitter
    
    # Use semantic-text-splitter for better chunking
    splitter = TextSplitter.from_tiktoken_model(tokenizer_model, max_tokens)
    chunks = splitter.chunks(text)
    return list(chunks)


# ============================================================================
# Startup/Shutdown
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize the MongoDB Engine on startup"""
    global engine, db
    
    app_status["startup_time"] = datetime.now().isoformat()
    app_status["status"] = "initializing"
    add_status_log("🚀 Starting Interactive RAG Web Application...", "info", "startup")
    
    try:
        # Ensure cache directories exist and are writable
        cache_dirs = [
            "/app/.cache",
            "/app/.cache/huggingface",
            "/app/cache"
        ]
        for cache_dir in cache_dirs:
            os.makedirs(cache_dir, exist_ok=True)
            # Ensure writable permissions
            os.chmod(cache_dir, 0o755)
        add_status_log("✅ Cache directories initialized", "info", "startup")
        
        # Get MongoDB connection from environment
        mongo_uri = os.getenv(
            "MONGO_URI",
            "mongodb://admin:password@mongodb:27017/?authSource=admin"
        )
        db_name = os.getenv("MONGO_DB_NAME", "interactive_rag_db")
        
        # Initialize the MongoDB Engine
        add_status_log(f"Connecting to MongoDB: {db_name}...", "info", "mongodb")
        engine = MongoDBEngine(
            mongo_uri=mongo_uri,
            db_name=db_name
        )
        
        # Connect to MongoDB
        await engine.initialize()
        app_status["components"]["mongodb"]["status"] = "connected"
        app_status["components"]["mongodb"]["message"] = f"Connected to {db_name}"
        app_status["components"]["engine"]["status"] = "initialized"
        app_status["components"]["engine"]["message"] = "MongoDBEngine ready"
        add_status_log("✅ Engine initialized successfully", "info", "engine")
        
        # Load and register the app manifest
        manifest_path = Path("/app/manifest.json")
        if not manifest_path.exists():
            manifest_path = Path(__file__).parent / "manifest.json"
        
        if manifest_path.exists():
            manifest = await engine.load_manifest(manifest_path)
            success = await engine.register_app(manifest, create_indexes=True)
            if success:
                add_status_log(f"✅ App '{manifest['slug']}' registered successfully", "info", "engine")
            else:
                add_status_log("⚠️  Failed to register app", "warning", "engine")
        
        # Get scoped database and store globally
        db = engine.get_scoped_db(APP_SLUG)
        
        # Set global references
        global _global_db
        _global_db = db
        
        # Check Azure OpenAI configuration
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        key = os.getenv("AZURE_OPENAI_API_KEY")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        
        if endpoint and key:
            add_status_log(f"✅ Azure OpenAI configured: {deployment}", "info", "llm")
        else:
            add_status_log("⚠️  Azure OpenAI not configured", "warning", "llm")
        
        if DOCLING_AVAILABLE:
            app_status["components"]["docling"]["status"] = "available"
            app_status["components"]["docling"]["message"] = "Document conversion enabled"
            add_status_log("✅ Docling available", "info", "docling")
        else:
            app_status["components"]["docling"]["status"] = "unavailable"
            app_status["components"]["docling"]["message"] = "Docling not installed"
            add_status_log("⚠️  Docling not available", "warning", "docling")
        
        # Note: RapidOCR status will be updated when it's actually used
        app_status["components"]["rapidocr"]["status"] = "pending"
        app_status["components"]["rapidocr"]["message"] = "Will initialize on first use"
        
        # Set global engine for dependency injection
        from mdb_engine.embeddings.dependencies import set_global_engine
        set_global_engine(engine, app_slug=APP_SLUG)
        
        # Initialize global embedding service for tools
        # Uses manifest.json magic: works with or without memory_config
        global _global_embedding_service
        try:
            # Initialize embedding service using manifest.json config
            app_config = engine.get_app(APP_SLUG)
            embedding_config = app_config.get("embedding_config", {}) if app_config else {}
            
            # Note: Memory service (Mem0MemoryService) handles its own embeddings separately
            # EmbeddingService is for standalone embedding operations
            _global_embedding_service = get_embedding_service(config=embedding_config)
            app_status["components"]["embedding_service"]["status"] = "available"
            app_status["components"]["embedding_service"]["message"] = "EmbeddingService initialized (from manifest.json)"
            add_status_log("✅ EmbeddingService initialized (from manifest.json)", "info", "embedding")
        except Exception as e:
            logger.warning(f"Failed to initialize EmbeddingService: {e}", exc_info=True)
            app_status["components"]["embedding_service"]["status"] = "unavailable"
            app_status["components"]["embedding_service"]["message"] = f"Failed to initialize: {str(e)}"
            add_status_log(f"⚠️  Failed to initialize EmbeddingService: {e}", "warning", "embedding")
        
        app_status["initialized"] = True
        app_status["status"] = "ready"
        add_status_log("✅ Web application ready!", "info", "startup")
        
    except Exception as e:
        app_status["status"] = "error"
        app_status["initialized"] = False
        error_msg = f"Startup failed: {str(e)}"
        add_status_log(error_msg, "error", "startup")
        logger.error(error_msg, exc_info=True)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global engine
    if engine:
        await engine.shutdown()
        logger.info("Cleaned up and shut down")


# ============================================================================
# LangChain Tools (using platform services)
# ============================================================================

# Global references for tools (will be set during startup)
_global_db = None
_global_embedding_service: Optional[EmbeddingService] = None

def get_embedding_service_dependency() -> EmbeddingService:
    """FastAPI dependency to get the global embedding service"""
    global _global_embedding_service
    if not _global_embedding_service:
        # Try to get it from global engine if available
        try:
            from mdb_engine.embeddings.dependencies import _global_engine, _global_app_slug
            from mdb_engine.embeddings import get_embedding_service
            if _global_engine and _global_app_slug:
                app_config = _global_engine.get_app(_global_app_slug)
                embedding_config = app_config.get("embedding_config", {}) if app_config else {}
                _global_embedding_service = get_embedding_service(config=embedding_config)
        except Exception as e:
            logger.warning(f"Failed to get embedding service from global engine: {e}", exc_info=True)
    
    if not _global_embedding_service:
        raise HTTPException(
            status_code=503,
            detail="EmbeddingService unavailable. Please ensure embedding_config is set in manifest.json."
        )
    return _global_embedding_service

async def _get_vector_search_results_async(query: str, session_id: str, embedding_model: str, num_sources: int = 3) -> List[Dict]:
    """Async helper function for vector search (used by tools)"""
    global _global_embedding_service
    
    if not _global_db:
        return []
    
    # Get embedding service if not set (manifest.json magic - works with or without memory_config)
    if not _global_embedding_service:
        try:
            from mdb_engine.embeddings.dependencies import _global_engine, _global_app_slug
            from mdb_engine.embeddings import get_embedding_service
            if _global_engine:
                # Initialize standalone using manifest.json
                app_config = _global_engine.get_app(_global_app_slug)
                embedding_config = app_config.get("embedding_config", {}) if app_config else {}
                
                _global_embedding_service = get_embedding_service(config=embedding_config)
        except Exception as e:
            logger.warning(f"Failed to get embedding service: {e}", exc_info=True)
            return []
    
    if not _global_embedding_service:
        return []
    
    try:
        # Generate query embedding
        query_vector = await _global_embedding_service.embed_chunks([query], model=embedding_model)
        if not query_vector:
            return []
        
        # Vector search pipeline
        # Index name is prefixed with app slug by MongoDBEngine
        vector_index_name = f"{APP_SLUG}_embedding_vector_index"
        pipeline = [
            {
                "$vectorSearch": {
                    "index": vector_index_name,
                    "path": "embedding",
                    "queryVector": query_vector[0],
                    "numCandidates": num_sources * 10,
                    "limit": num_sources,
                    "filter": {
                        f"metadata.{SESSION_FIELD}": {"$eq": session_id}
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "content": "$text",
                    "source": "$metadata.source",
                    "score": {"$meta": "vectorSearchScore"}
                }
            }
        ]
        
        results = await _global_db.knowledge_base_sessions.aggregate(pipeline).to_list(length=num_sources)
        return results
    except Exception as e:
        logger.error(f"Error in vector search: {e}", exc_info=True)
        return []


# Note: LangChain tools removed - examples should implement their own LLM clients
# Tools functionality can be implemented using direct Azure OpenAI client
if False:  # Disabled - LangChain removed
    @tool
    async def search_knowledge_base(query: str, embedding_model: str = "text-embedding-3-small", num_sources: int = 3, max_chunk_length: int = 2000) -> str:
        """Query the knowledge base to find relevant chunks for `query`."""
        global current_session, last_retrieved_sources, _current_user_query
        
        try:
            logger.info(f"[INFO] Searching with '{embedding_model}' → top {num_sources}")
            results = await _get_vector_search_results_async(query, current_session, embedding_model, num_sources)
            
            if not results:
                last_retrieved_sources = []
                return f"No relevant info found in session '{current_session}'."
            
            # Remember sources
            found_sources = [r.get("source", "N/A") for r in results]
            last_retrieved_sources = list(set(found_sources))
            
            # Build a context string
            context_parts = []
            for r in results:
                text = r.get("content", "")
                src = r.get("source", "N/A")
                score = r.get("score", 0.0)
                if max_chunk_length and len(text) > max_chunk_length:
                    text = text[:max_chunk_length] + "... [truncated]"
                context_parts.append(f"Source: {src} (Score: {score:.4f})\nContent: {text}")
            
            context = "\n---\n".join(context_parts)
            return f"Retrieved from '{embedding_model}':\n{context}"
            
        except Exception as e:
            last_retrieved_sources = []
            logger.error(f"[ERROR] search_knowledge_base: {e}")
            return f"❌ Search error: {e}"
    
    @tool
    async def read_url(url: str, chunk_size: int = 1000, chunk_overlap: int = 150) -> str:
        """Adds a URL's content (via Firecrawl HTTP API) into the knowledge base."""
        global current_session, _global_db, _global_embedding_service
        
        try:
            if not _global_db or not _global_embedding_service:
                return "❌ Services not initialized."
            
            # Check if already exists
            existing = await _global_db.knowledge_base_sessions.count_documents(
                {"metadata.source": url, f"metadata.{SESSION_FIELD}": current_session},
                limit=1
            )
            
            if existing > 0:
                return f"❌ Source '{url}' already exists in session '{current_session}'."
            
            # Use Firecrawl HTTP API
            firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
            if not firecrawl_key:
                return "❌ FIRECRAWL_API_KEY not set."
            
            firecrawl_key = firecrawl_key.strip()
            if not firecrawl_key:
                return "❌ FIRECRAWL_API_KEY is empty or whitespace only."
            
            logger.info(f"[INFO] Scraping & ingesting URL via Firecrawl HTTP API: {url}")
            
            # Use Firecrawl HTTP API directly
            api_url = "https://api.firecrawl.dev/v2/scrape"
            headers = {
                "Authorization": f"Bearer {firecrawl_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": False
            }
            
            resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            scrape_result = resp.json()
            
            # Extract markdown content from response
            page_content = ""
            if "data" in scrape_result:
                if isinstance(scrape_result["data"], dict):
                    page_content = scrape_result["data"].get("markdown", "")
                elif isinstance(scrape_result["data"], str):
                    page_content = scrape_result["data"]
            elif "markdown" in scrape_result:
                page_content = scrape_result["markdown"]
            
            if not page_content:
                return f"❌ No markdown content returned from {url}."
            
            # Use EmbeddingService to process and store
            result = await _global_embedding_service.process_and_store(
                text_content=page_content,
                source_id=url,
                collection=_global_db.knowledge_base_sessions,
                max_tokens=chunk_size,
                metadata={
                    "source": url,
                    "source_type": "url",
                    SESSION_FIELD: current_session
                }
            )
            
            return f"✅ Ingested {result['chunks_created']} chunks from {url} into '{current_session}'."
            
        except Exception as e:
            logger.error(f"[ERROR] read_url: {e}", exc_info=True)
            return f"❌ Ingestion error: {e}"
    
    @tool
    async def update_chunk(chunk_id: str, new_content: str) -> str:
        """Updates chunk text (and embeddings) by chunk ID."""
        global _global_db, _global_embedding_service
        
        try:
            if not _global_db or not _global_embedding_service:
                return "❌ Services not initialized."
            
            # Re-embed the content
            vectors = await _global_embedding_service.embed_chunks([new_content])
            if not vectors:
                return "❌ Failed to generate embedding"
            
            # Update document
            result = await _global_db.knowledge_base_sessions.update_one(
                {"_id": ObjectId(chunk_id)},
                {"$set": {"text": new_content, "embedding": vectors[0]}}
            )
            
            if result.matched_count == 0:
                return f"❌ Could not find chunk with ID '{chunk_id}'."
            
            return f"✅ Chunk '{chunk_id}' updated (re-embedded)."
            
        except Exception as e:
            return f"❌ Failed to update chunk: {e}"
    
    @tool
    async def delete_chunk(chunk_id: str) -> str:
        """Deletes a chunk from the knowledge base by ID."""
        global _global_db
        
        try:
            if not _global_db:
                return "❌ Database not initialized."
            
            result = await _global_db.knowledge_base_sessions.delete_one({"_id": ObjectId(chunk_id)})
            if result.deleted_count == 0:
                return f"❌ Could not find chunk '{chunk_id}' to delete."
            return f"✅ Chunk '{chunk_id}' deleted."
            
        except Exception as e:
            return f"❌ Failed to delete chunk: {e}"
    
    @tool
    def switch_session(session_id: str) -> str:
        """Switch to another session in memory."""
        global current_session, chat_history
        current_session = session_id
        if session_id not in chat_history:
            chat_history[session_id] = []
        return f"✅ Switched to session: **{session_id}**."
    
    @tool
    def create_session(session_id: str) -> str:
        """Create a new session in memory only (no marker doc)."""
        global current_session, chat_history, _global_db
        
        try:
            if _global_db:
                # Use aggregation to get distinct session IDs
                field_path = f"metadata.{SESSION_FIELD}"
                pipeline = [
                    {"$group": {"_id": f"${field_path}"}},
                    {"$project": {"_id": 0, "value": "$_id"}}
                ]
                # Run aggregation synchronously (this is a sync function)
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, we can't use asyncio.run, need to handle differently
                    # For now, skip the check if loop is running
                    existing_sessions = []
                else:
                    cursor = _global_db.knowledge_base_sessions.aggregate(pipeline)
                    existing_sessions_list = asyncio.run(cursor.to_list(length=None))
                    existing_sessions = [item["value"] for item in existing_sessions_list if item.get("value")]
                if session_id in existing_sessions:
                    return f"❌ Session **'{session_id}'** already exists."
            
            current_session = session_id
            if session_id not in chat_history:
                chat_history[session_id] = []
            return f"✅ Created and switched to new session: **{session_id}**."
            
        except Exception as e:
            return f"❌ Failed to create session: {e}"
    
    @tool
    async def list_sources() -> str:
        """List all sources in the current session."""
        global current_session, _global_db
        
        try:
            if not _global_db:
                return "❌ Database not initialized."
            
            # Use aggregation to get distinct sources
            pipeline = [
                {"$match": {f"metadata.{SESSION_FIELD}": current_session}},
                {"$group": {"_id": "$metadata.source"}},
                {"$project": {"_id": 0, "value": "$_id"}},
                {"$sort": {"value": 1}}
            ]
            cursor = _global_db.knowledge_base_sessions.aggregate(pipeline)
            sources_list = await cursor.to_list(length=None)
            sources = [item["value"] for item in sources_list if item.get("value")]
            
            if not sources:
                return f"No sources found in session '{current_session}'."
            return "Sources:\n" + "\n".join(f"- {s}" for s in sources)
            
        except Exception as e:
            return f"❌ Error listing sources: {e}"
    
    @tool
    async def remove_all_sources() -> str:
        """Remove all docs from the current session."""
        global current_session, _global_db
        
        try:
            if not _global_db:
                return "❌ Database not initialized."
            
            result = await _global_db.knowledge_base_sessions.delete_many(
                {f"metadata.{SESSION_FIELD}": current_session}
            )
            return f"🗑 Removed all docs from session '{current_session}' (deleted {result.deleted_count})."
            
        except Exception as e:
            return f"❌ Error removing sources: {e}"
    
    # List of all tools
    LANGCHAIN_TOOLS = [
        search_knowledge_base,
        switch_session,
        create_session,
        list_sources,
        remove_all_sources,
        update_chunk,
        delete_chunk,
        read_url
    ]
else:
    LANGCHAIN_TOOLS = []


# ============================================================================
# LangChain Agent Setup
# ============================================================================

def create_agent_executor(client: AzureOpenAI) -> Optional[Any]:
    """Create LangGraph agent using platform's LLM service (modern LangGraph approach)"""
    # LangChain agents removed - examples should implement their own agent logic
    # This function is kept for compatibility but returns None
    logger.warning("[Agent] LangChain agents removed - implement your own agent logic")
    return None


# ============================================================================
# Routes
# ============================================================================

# Static file routes - must be BEFORE the root route
@app.get("/static/{file_path:path}")
async def serve_static_file(file_path: str):
    """Serve static files explicitly"""
    logger.info(f"[STATIC] Request for: {file_path}")
    
    if not static_dir or not static_dir.exists():
        logger.error(f"[STATIC] Static directory not found: {static_dir}")
        raise HTTPException(status_code=404, detail="Static directory not found")
    
    # Security: prevent directory traversal - normalize the path
    # Remove any leading slashes and normalize
    file_path = file_path.lstrip("/")
    if ".." in file_path:
        logger.warning(f"[STATIC] Invalid path (directory traversal attempt): {file_path}")
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    file_path_obj = static_dir / file_path
    # Ensure the resolved path is still within static_dir
    try:
        file_path_obj = file_path_obj.resolve()
        static_dir_resolved = static_dir.resolve()
        if not str(file_path_obj).startswith(str(static_dir_resolved)):
            logger.warning(f"[STATIC] Path traversal detected: {file_path_obj} not in {static_dir_resolved}")
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STATIC] Error resolving file path: {e}")
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    if not file_path_obj.exists() or not file_path_obj.is_file():
        logger.warning(f"[STATIC] File not found: {file_path_obj} (requested: {file_path}, static_dir: {static_dir})")
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    # Determine content type
    content_type = "application/octet-stream"
    if file_path.endswith(".css"):
        content_type = "text/css; charset=utf-8"
    elif file_path.endswith(".js"):
        content_type = "application/javascript; charset=utf-8"
    elif file_path.endswith(".png"):
        content_type = "image/png"
    elif file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif file_path.endswith(".svg"):
        content_type = "image/svg+xml"
    
    logger.info(f"[STATIC] Serving: {file_path_obj} (type: {content_type})")
    return FileResponse(
        str(file_path_obj),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"}
    )

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page"""
    return templates.TemplateResponse("index.html", {"request": request})



# ============================================================================
# Ingestion Endpoints
# ============================================================================

class IngestRequest(BaseModel):
    content: str
    source: str
    source_type: str = "unknown"
    session_id: str
    chunk_size: int = 1000
    chunk_overlap: int = 150


@app.post("/ingest")
async def start_ingestion_task(
    request: IngestRequest,
    db=Depends(get_db),
    embedding_service: EmbeddingService = Depends(get_embedding_service_dependency)
):
    """Start an ingestion task"""
    # Check duplicates
    existing = await db.knowledge_base_sessions.count_documents(
        {
            "metadata.source": request.source,
            f"metadata.{SESSION_FIELD}": request.session_id
        },
        limit=1
    )
    
    if existing > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Source '{request.source}' already exists in session '{request.session_id}'."
        )
    
    task_id = str(uuid.uuid4())
    background_tasks[task_id] = {"status": "pending"}
    
    # Start background task
    asyncio.create_task(
        run_ingestion_task(
            task_id,
            request.content,
            request.source,
            request.source_type,
            request.session_id,
            request.chunk_size,
            request.chunk_overlap,
            db,
            embedding_service
        )
    )
    
    return {"task_id": task_id}


@app.get("/ingest/status/{task_id}")
async def get_ingestion_status(task_id: str):
    """Get ingestion task status (for polling)"""
    if task_id not in background_tasks:
        return {"status": "not_found"}
    
    # Return full status with all fields for polling
    status = background_tasks[task_id].copy()
    return status


async def run_ingestion_task(
    task_id: str,
    content: str,
    source: str,
    source_type: str,
    session_id: str,
    chunk_size: int,
    chunk_overlap: int,
    db,
    embedding_service: EmbeddingService
):
    """Handles chunking & embedding in a background task with detailed polling-based progress updates"""
    import time
    start_time = time.time()
    
    def update_status(step, progress, chunks=0, embeddings=0, **kwargs):
        """Helper to update status with elapsed time"""
        background_tasks[task_id] = {
            "status": "processing",
            "step": step,
            "progress": progress,
            "chunks_created": chunks,
            "embeddings_generated": embeddings,
            "elapsed_time": round(time.time() - start_time, 2),
            **kwargs
        }
    
    try:
        # Step 0: Initialization
        update_status("Initializing ingestion...", 0)
        logger.info(f"[Task {task_id}] Starting ingestion for '{source}' (type: {source_type}, {len(content)} chars)")
        add_status_log(f"[Task {task_id}] Starting ingestion: {source}", "info", "ingestion")
        await asyncio.sleep(0.1)  # Small delay for UI update
        
        # Step 1: Analyzing content
        update_status(f"Analyzing content ({len(content):,} characters)...", 5)
        await asyncio.sleep(0.1)
        
        # Step 2: Chunking
        update_status(f"Chunking content (target: {chunk_size} tokens per chunk)...", 15)
        logger.info(f"[Task {task_id}] Chunking '{source}'...")
        await asyncio.sleep(0.1)
        
        # Step 3: Processing with EmbeddingService (chunking + embedding)
        update_status("Generating text chunks...", 25)
        await asyncio.sleep(0.1)
        
        # Actually perform chunking and embedding
        update_status("Creating semantic chunks...", 35)
        result = await embedding_service.process_and_store(
            text_content=content,
            source_id=source,
            collection=db.knowledge_base_sessions,
            max_tokens=chunk_size,
            tokenizer_model="gpt-3.5-turbo",
            metadata={
                "source": source,
                "source_type": source_type,
                SESSION_FIELD: session_id
            }
        )
        
        chunks_created = result.get('chunks_created', 0)
        
        # Step 4: Generating embeddings
        update_status(f"Generating embeddings for {chunks_created} chunks...", 60, chunks=chunks_created)
        await asyncio.sleep(0.1)
        
        # Step 5: Storing in database
        update_status(f"Storing {chunks_created} chunks in database...", 85, chunks=chunks_created, embeddings=chunks_created)
        await asyncio.sleep(0.1)
        
        # Step 6: Finalizing
        update_status("Finalizing ingestion...", 95, chunks=chunks_created, embeddings=chunks_created)
        await asyncio.sleep(0.1)
        
        # Complete
        final_message = f"Successfully ingested {chunks_created} chunks from source '{source}'."
        background_tasks[task_id] = {
            "status": "complete",
            "step": "Ingestion complete!",
            "message": final_message,
            "chunks_created": chunks_created,
            "embeddings_generated": chunks_created,
            "progress": 100,
            "elapsed_time": round(time.time() - start_time, 2)
        }
        
        logger.info(f"[Task {task_id}] {final_message}")
        add_status_log(f"[Task {task_id}] {final_message}", "info", "ingestion")
        
    except Exception as e:
        error_message = f"Ingestion failed: {str(e)}"
        logger.error(f"[Task {task_id}] [ERROR] {error_message}", exc_info=True)
        add_status_log(f"[Task {task_id}] [ERROR] {error_message}", "error", "ingestion")
        background_tasks[task_id] = {
            "status": "failed",
            "step": "Ingestion failed",
            "message": error_message,
            "progress": 100,
            "chunks_created": 0,
            "embeddings_generated": 0,
            "elapsed_time": round(time.time() - start_time, 2)
        }


# ============================================================================
# Chat Endpoint
# ============================================================================

class ChatRequest(BaseModel):
    query: str
    session_id: str
    embedding_model: str = "text-embedding-3-small"
    rag_params: Optional[Dict[str, Any]] = None


@app.post("/chat")
async def chat(
    request: ChatRequest,
    db=Depends(get_db)
):
    """Chat endpoint with RAG using direct Azure OpenAI client"""
    global current_session, chat_history, last_retrieved_sources, last_retrieved_chunks, _current_user_query
    
    if not request.query or not request.session_id:
        raise HTTPException(status_code=400, detail="Missing 'query' or 'session_id'")
    
    # Store the original user query so tools can reference it
    _current_user_query = request.query
    
    logger.info(f"\n--- Turn for session '{request.session_id}' ---\n")
    original_session = current_session
    
    try:
        # Switch session in memory
        current_session = request.session_id
        
        # Initialize chat history if needed
        if request.session_id not in chat_history:
            chat_history[request.session_id] = []
        
        # Shorten chat history if too long
        current_chat_history = chat_history[request.session_id]
        if len(current_chat_history) > 10:
            current_chat_history = current_chat_history[-10:]
        
        # Convert chat history to LangGraph format (list of tuples)
        langgraph_messages = []
        for msg in current_chat_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                langgraph_messages.append(("user", content))
            elif role == "assistant":
                langgraph_messages.append(("assistant", content))
        
        # Get Azure OpenAI client
        client = get_azure_openai_client()
        deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        
        # LangChain agents removed - use direct RAG
        if False:  # LangChain disabled
            logger.info(f"[Chat] LangChain agents removed - using direct RAG")
            agent_executor = None
            if agent_executor:
                # Use LangGraph agent
                # Add system instruction about embedding model
                agent_input_string = (
                    f"User query: '{request.query}'.\n\n"
                    f"IMPORTANT INSTRUCTION: When you call the 'search_knowledge_base' tool, "
                    f"you MUST set the 'embedding_model' parameter to '{request.embedding_model}'."
                )
                
                # LangGraph uses "messages" format with list of (role, content) tuples
                # Add system message to encourage markdown formatting
                system_message = (
                    "You are an AI assistant designed to answer questions using a private knowledge base. "
                    "**IMPORTANT:** Format your responses using Markdown for better readability. Use:\n"
                    "- Headers (##, ###) for sections\n"
                    "- **Bold** for emphasis\n"
                    "- Lists (bulleted or numbered) for multiple points\n"
                    "- Code blocks (```) for code or technical terms\n"
                    "- Tables for structured data\n"
                    "Your answers must be based ONLY on the context provided by the search_knowledge_base tool. "
                    "If no relevant information is found, state that clearly."
                )
                # Include system message, chat history + new user message
                messages = [("system", system_message)] + langgraph_messages + [("user", agent_input_string)]
                
                logger.info(f"[Chat] Invoking LangGraph agent with query: {request.query[:100]}...")
                response = await agent_executor.ainvoke({"messages": messages})
                
                # LangGraph returns the full state, extract the last message content
                if "messages" in response and len(response["messages"]) > 0:
                    # Get the last message (should be the assistant's response)
                    last_message = response["messages"][-1]
                    if hasattr(last_message, "content"):
                        response_text = last_message.content
                    elif isinstance(last_message, dict):
                        response_text = last_message.get("content", str(last_message))
                    else:
                        response_text = str(last_message)
                else:
                    # Fallback: try to get "output" if it exists
                    response_text = response.get("output", str(response))
                
                sources_used = last_retrieved_sources
                logger.info(f"[Chat] LangGraph agent response received ({len(response_text)} chars), sources: {sources_used}")
            else:
                # Use direct RAG (LangChain removed)
                logger.info("Using direct RAG chat")
                response_text = await _direct_rag_chat(
                    request, db, client, deployment_name, current_chat_history
                )
                sources_used = last_retrieved_sources
        else:
            # Use direct RAG
            logger.info("Using direct RAG chat")
            response_text = await _direct_rag_chat(
                request, db, client, deployment_name, current_chat_history
            )
            sources_used = last_retrieved_sources
        
        # Record conversation
        current_chat_history.extend([
            {"role": "user", "content": request.query},
            {"role": "assistant", "content": response_text}
        ])
        chat_history[request.session_id] = current_chat_history
        
        # Note: Chunks are retrieved by the agent tool, sources_used contains the sources
        
        # Get all sessions using aggregation
        session_field_path = f"$metadata.{SESSION_FIELD}"
        pipeline = [
            {"$group": {"_id": session_field_path}},
            {"$project": {"_id": 0, "value": "$_id"}}
        ]
        cursor = db.knowledge_base_sessions.aggregate(pipeline)
        db_sessions_list = await cursor.to_list(length=None)
        db_sessions = set([item["value"] for item in db_sessions_list if item.get("value")] or ["default"])
        mem_sessions = set(chat_history.keys())
        all_sessions = sorted(list(db_sessions.union(mem_sessions)))
        
        logger.info(f"[Chat] Returning response for query '{request.query}'")
        
        return {
            "messages": [{
                "type": "bot-message",
                "content": response_text,
                "sources": sources_used,
                "query": request.query,  # Include query for chunk inspection
                "chunks": last_retrieved_chunks  # Include retrieved chunks for inspection
            }],
            "session_update": {
                "all_sessions": all_sessions,
                "current_session": current_session
            }
        }
        
    except Exception as e:
        logger.error(f"[ERROR] chat endpoint: {e}", exc_info=True)
        error_detail = str(e)
        # Provide more helpful error messages for common issues
        if "azure" in error_detail.lower() or "api key" in error_detail.lower():
            error_detail = f"Azure OpenAI configuration error: {error_detail}. Please check your AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT environment variables."
        elif "model" in error_detail.lower() and "not found" in error_detail.lower():
            error_detail = f"Model configuration error: {error_detail}. Please check your AZURE_OPENAI_DEPLOYMENT_NAME matches your Azure deployment."
        raise HTTPException(status_code=500, detail=error_detail)
    finally:
        # Restore original session if there was an error
        if 'original_session' in locals():
            current_session = original_session


async def _direct_rag_chat(
    request: ChatRequest,
    db,
    client: AzureOpenAI,
    deployment_name: str,
    current_chat_history: List[Dict[str, str]]
) -> str:
    """Direct RAG chat using Azure OpenAI client"""
    global last_retrieved_sources, last_retrieved_chunks
    
    # Perform vector search
    rag_params = request.rag_params or {}
    num_sources = rag_params.get("num_sources", 3)
    max_chunk_length = rag_params.get("max_chunk_length", 2000)
    
    # Get embedding service if not set (manifest.json magic - works with or without memory_config)
    global _global_embedding_service
    if not _global_embedding_service:
        try:
            from mdb_engine.embeddings.dependencies import _global_engine, _global_app_slug
            from mdb_engine.embeddings import get_embedding_service
            if _global_engine:
                # Initialize standalone using manifest.json
                app_config = _global_engine.get_app(_global_app_slug)
                embedding_config = app_config.get("embedding_config", {}) if app_config else {}
                
                _global_embedding_service = get_embedding_service(config=embedding_config)
        except Exception as e:
            logger.error(f"Failed to initialize EmbeddingService: {e}", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="EmbeddingService unavailable. Cannot perform vector search."
            )
    
    if not _global_embedding_service:
        raise HTTPException(
            status_code=503,
            detail="EmbeddingService unavailable. Cannot perform vector search."
        )
    
    # Generate query embedding
    try:
        query_vector = await _global_embedding_service.embed_chunks([request.query], model=request.embedding_model)
    except Exception as e:
        logger.error(f"Failed to generate query embedding: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate query embedding: {str(e)}"
        )
    
    if not query_vector or len(query_vector) == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to generate query embedding: embed_chunks returned empty result"
        )
    
    # Debug: Check if documents exist for this session
    doc_count = await db.knowledge_base_sessions.count_documents(
        {f"metadata.{SESSION_FIELD}": request.session_id}
    )
    logger.info(f"[RAG] Found {doc_count} documents in session '{request.session_id}'")
    
    if doc_count == 0:
        logger.warning(f"[RAG] No documents found for session '{request.session_id}'. Make sure documents are embedded first.")
    
    # Vector search pipeline
    # Index name is prefixed with app slug by MongoDBEngine (e.g., "interactive_rag_embedding_vector_index")
    vector_index_name = f"{APP_SLUG}_embedding_vector_index"
    pipeline = [
        {
            "$vectorSearch": {
                "index": vector_index_name,
                "path": "embedding",
                "queryVector": query_vector[0],
                "numCandidates": num_sources * 10,
                "limit": num_sources,
                "filter": {
                    f"metadata.{SESSION_FIELD}": {"$eq": request.session_id}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "content": "$text",
                "source": "$metadata.source",
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    
    try:
        results = await db.knowledge_base_sessions.aggregate(pipeline).to_list(length=num_sources)
    except Exception as search_error:
        error_msg = str(search_error)
        logger.error(f"[RAG] Vector search failed: {error_msg}", exc_info=True)
        
        # Check for dimension mismatch error
        if "indexed with" in error_msg.lower() and "dimensions but queried with" in error_msg.lower():
            # Extract dimension information from error message
            import re
            dim_match = re.search(r'indexed with (\d+) dimensions but queried with (\d+)', error_msg.lower())
            if dim_match:
                indexed_dims = dim_match.group(1)
                queried_dims = dim_match.group(2)
                query_vector_dims = len(query_vector[0]) if query_vector and len(query_vector) > 0 else "unknown"
                raise HTTPException(
                    status_code=500,
                    detail=f"Vector dimension mismatch: The vector index '{vector_index_name}' was created with {indexed_dims}-dimensional vectors, but the query embedding model '{request.embedding_model}' produces {queried_dims}-dimensional vectors. Query vector has {query_vector_dims} dimensions. Please use the same embedding model for both indexing and querying, or recreate the index with the correct dimensions."
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Vector dimension mismatch: {error_msg}. Please ensure the embedding model used for queries matches the model used when creating the index."
                )
        # Check if it's a vector index issue
        elif "index" in error_msg.lower() or "vectorSearch" in error_msg.lower():
            logger.error(f"[RAG] Vector index may not exist. Check if '{vector_index_name}' is created and queryable.")
            raise HTTPException(
                status_code=500,
                detail=f"Vector search failed: Vector index '{vector_index_name}' may not exist or is not queryable. {error_msg}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Vector search failed: {error_msg}"
            )
    
    # Debug logging
    logger.info(f"[RAG] Vector search found {len(results)} results for query: '{request.query[:100]}...'")
    if results:
        logger.info(f"[RAG] Top result score: {results[0].get('score', 0.0):.4f}, source: {results[0].get('source', 'N/A')}")
    
    # Build context
    if results:
        context_parts = []
        sources_used = []
        for r in results:
            text = r.get("content", "")
            src = r.get("source", "N/A")
            score = r.get("score", 0.0)
            if max_chunk_length and len(text) > max_chunk_length:
                text = text[:max_chunk_length] + "... [truncated]"
            context_parts.append(f"Source: {src} (Score: {score:.4f})\nContent: {text}")
            if src not in sources_used:
                sources_used.append(src)
        
        context = "\n---\n".join(context_parts)
        last_retrieved_sources = sources_used
        # Transform chunks to match frontend expectations (text instead of content)
        last_retrieved_chunks = [
            {
                "text": r.get("content", ""),
                "source": r.get("source", "N/A"),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]
        logger.info(f"[RAG] Built context with {len(context_parts)} chunks, total length: {len(context)} chars, sources: {sources_used}")
    else:
        context = "No relevant information found in the knowledge base."
        last_retrieved_sources = []
        last_retrieved_chunks = []  # No chunks when no results
        logger.warning(f"[RAG] No results found for query in session '{request.session_id}'. Check if documents are embedded and vector index exists.")
        
        # Provide helpful context in the response
        if doc_count > 0:
            logger.warning(f"[RAG] Documents exist ({doc_count}) but vector search returned 0 results. "
                         f"This usually means the vector index 'embedding_vector_index' doesn't exist yet. "
                         f"Vector indexes are created asynchronously and may take a few minutes to build.")
    
    # Build prompt with context
    system_prompt = (
        "You are an AI assistant designed to answer questions using a private knowledge base. "
        "**IMPORTANT:** Format your responses using Markdown for better readability. Use:\n"
        "- Headers (##, ###) for sections\n"
        "- **Bold** for emphasis\n"
        "- Lists (bulleted or numbered) for multiple points\n"
        "- Code blocks (```) for code or technical terms\n"
        "- Tables for structured data\n"
        "Your answers must be based ONLY on the context provided below. "
        "If the context is insufficient, state that you could not find an answer in the knowledge base."
    )
    
    user_prompt = f"Context from knowledge base:\n\n{context}\n\nUser question: {request.query}"
    
    # Get LLM response using Azure OpenAI client
    messages = [{"role": "system", "content": system_prompt}]
    # Add recent chat history
    for msg in current_chat_history[-5:]:  # Last 5 messages for context
        messages.append(msg)
    messages.append({"role": "user", "content": user_prompt})
    
    try:
        logger.info(f"[RAG] Sending {len(messages)} messages to Azure OpenAI (model: {deployment_name})")
        logger.debug(f"[RAG] User prompt length: {len(user_prompt)} chars, context length: {len(context)} chars")
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=deployment_name,
            messages=messages,
            max_tokens=1000
        )
        response = completion.choices[0].message.content
        logger.info(f"[RAG] Received response ({len(response) if response else 0} chars): {response[:200] if response else 'None'}...")
        return response
    except Exception as e:
        logger.error(f"[RAG] LLM chat error: {e}", exc_info=True)
        # Provide helpful error message
        if "azure" in str(e).lower():
            raise Exception(f"Azure OpenAI error: {str(e)}. Please verify your Azure OpenAI configuration (endpoint, API key, deployment name).")
        raise


# ============================================================================
# Session / State Endpoints
# ============================================================================

@app.get("/status")
async def get_status():
    """Get application status and logs"""
    try:
        # Calculate uptime
        uptime_seconds = None
        if app_status.get("startup_time"):
            start_time = datetime.fromisoformat(app_status["startup_time"])
            uptime_seconds = int((datetime.now() - start_time).total_seconds())
        
        # Get recent logs (last 50)
        recent_logs = app_status["logs"][-50:] if len(app_status["logs"]) > 50 else app_status["logs"]
        
        return {
            "initialized": app_status["initialized"],
            "status": app_status["status"],
            "startup_time": app_status.get("startup_time"),
            "uptime_seconds": uptime_seconds,
            "components": app_status["components"],
            "logs": recent_logs,
            "total_logs": len(app_status["logs"])
        }
    except Exception as e:
        logger.error(f"Error in /status endpoint: {e}", exc_info=True)
        return {
            "initialized": False,
            "status": "error",
            "error": str(e),
            "components": {},
            "logs": []
        }


@app.get("/diagnostics")
async def get_diagnostics():
    """Get detailed diagnostics about installed packages and import status"""
    import subprocess
    import sys
    
    diagnostics = {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "environment_variables": {
            "HF_HOME": os.getenv("HF_HOME", "not set"),
            "TRANSFORMERS_CACHE": os.getenv("TRANSFORMERS_CACHE", "not set"),
            "HF_DATASETS_CACHE": os.getenv("HF_DATASETS_CACHE", "not set"),
            "PYTHONPATH": os.getenv("PYTHONPATH", "not set"),
        },
        "import_status": {
            "langchain": {
                "available": LANGCHAIN_AVAILABLE,
                "errors": LANGCHAIN_ERROR_DETAILS if not LANGCHAIN_AVAILABLE else []
            },
            "ddgs": {
                "available": DDGS_AVAILABLE
            },
            "docling": {
                "available": DOCLING_AVAILABLE
            }
        },
        "installed_packages": {}
    }
    
    # Check installed packages
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            import json
            packages = json.loads(result.stdout)
            # Filter for relevant packages
            relevant_packages = [
                "langchain", "langchain-community", "langchain-openai",
                "langchain-core", "langchain-mongodb",
                "langgraph", "ddgs", "docling", "mdb-engine"
            ]
            diagnostics["installed_packages"] = {
                pkg["name"]: pkg["version"]
                for pkg in packages
                if any(relevant in pkg["name"].lower() for relevant in relevant_packages)
            }
        else:
            diagnostics["installed_packages"] = {"error": f"pip list failed: {result.stderr}"}
    except Exception as e:
        diagnostics["installed_packages"] = {"error": str(e)}
    
    # Try to import each package individually to see which ones work
    import_tests = {}
    test_packages = [
        ("langchain", "langchain"),
        ("langgraph", "langgraph"),
        ("langgraph.prebuilt", "langgraph.prebuilt"),
        ("langchain_core", "langchain_core"),
        ("langchain_core.prompts", "langchain_core.prompts"),
        ("langchain_core.messages", "langchain_core.messages"),
        ("langchain_core.tools", "langchain_core.tools"),
        ("langchain_openai", "langchain_openai"),
        ("ddgs", "ddgs"),
        ("docling", "docling"),
    ]
    
    for module_name, display_name in test_packages:
        try:
            __import__(module_name)
            import_tests[display_name] = {"status": "success", "error": None}
        except ImportError as e:
            import_tests[display_name] = {"status": "failed", "error": str(e)}
        except Exception as e:
            import_tests[display_name] = {"status": "error", "error": str(e)}
    
    diagnostics["import_tests"] = import_tests
    
    return diagnostics


@app.get("/state")
async def get_state(session_id: str = "default", db=Depends(get_db)):
    """Get application state"""
    try:
        # Get sessions from database
        db_sessions = {"default"}
        try:
            # Check if collection exists and has documents
            collection = db.knowledge_base_sessions
            count = await collection.count_documents({})
            if count > 0:
                # Use aggregation to get distinct session IDs
                session_field_path = f"$metadata.{SESSION_FIELD}"
                pipeline = [
                    {"$group": {"_id": session_field_path}},
                    {"$project": {"_id": 0, "value": "$_id"}}
                ]
                cursor = collection.aggregate(pipeline)
                db_sessions_list_raw = await cursor.to_list(length=None)
                db_sessions_list = [item["value"] for item in db_sessions_list_raw if item.get("value")]
                if db_sessions_list:
                    db_sessions = set(db_sessions_list)
        except Exception as e:
            logger.warning(f"Could not get distinct sessions from DB: {e}")
            # Continue with default session
        
        # Get sessions from memory
        mem_sessions = set(chat_history.keys()) if chat_history else set()
        all_sessions = sorted(list(db_sessions.union(mem_sessions)))
        if not all_sessions:
            all_sessions = ["default"]
        
        # If session_id parameter is provided and not in all_sessions, add it
        # This handles the case where a session was just created but not yet in chat_history
        if session_id and session_id not in all_sessions:
            logger.info(f"[State] Adding session_id parameter '{session_id}' to all_sessions (session may have just been created)")
            all_sessions.append(session_id)
            all_sessions.sort()
        
        # Determine current_session: use session_id parameter if it's valid, otherwise use global
        # This ensures the state reflects the session the client is asking about
        if session_id and session_id in all_sessions:
            effective_current_session = session_id
        else:
            effective_current_session = current_session
        
        # Ensure default is always in the list
        if "default" not in all_sessions:
            all_sessions.insert(0, "default")
        
        logger.info(f"[State] Returning state: all_sessions={all_sessions}, current_session={effective_current_session}, session_id_param={session_id}, global_current_session={current_session}, chat_history_keys={list(chat_history.keys()) if chat_history else []}")
        
        # Get available embedding models (from manifest.json)
        available_models = []
        if engine:
            try:
                app_config = engine.get_app(APP_SLUG)
                if app_config:
                    # Check embedding_config for embedding model
                    if "embedding_config" in app_config:
                        model = app_config["embedding_config"].get("default_embedding_model")
                        if model and model not in available_models:
                            available_models.append(model)
            except Exception as e:
                logger.warning(f"Could not get embedding model from config: {e}")
        
        # Default to a common embedding model if none found
        if not available_models:
            available_models = ["text-embedding-3-small"]
        
        # Get index status for each model
        index_status = {}
        for model in available_models:
            try:
                # Count documents with embeddings for this session
                doc_count = await db.knowledge_base_sessions.count_documents({
                    f"metadata.{SESSION_FIELD}": session_id,
                    "embedding": {"$exists": True}
                })
                
                # Check index status using index manager
                vector_index_name = f"{APP_SLUG}_embedding_vector_index"
                index_queryable = False
                index_status_str = "UNKNOWN"
                
                # Use index manager to get actual index status
                try:
                    collection_wrapper = db.knowledge_base_sessions
                    if hasattr(collection_wrapper, 'index_manager') and collection_wrapper.index_manager:
                        index_info = await collection_wrapper.index_manager.get_search_index(vector_index_name)
                        if index_info:
                            status = index_info.get("status", "UNKNOWN")
                            queryable = index_info.get("queryable", False)
                            index_status_str = status.upper()
                            index_queryable = queryable
                        else:
                            index_status_str = "NOT_FOUND"
                    else:
                        # Fallback: try dummy query
                        try:
                            dummy_vector = [0.0] * 1024
                            test_pipeline = [
                                {
                                    "$vectorSearch": {
                                        "index": vector_index_name,
                                        "path": "embedding",
                                        "queryVector": dummy_vector,
                                        "numCandidates": 1,
                                        "limit": 1
                                    }
                                }
                            ]
                            await db.knowledge_base_sessions.aggregate(test_pipeline).to_list(length=1)
                            index_queryable = True
                            index_status_str = "READY"
                        except Exception:
                            index_status_str = "NOT_FOUND"
                except Exception:
                    index_status_str = "NOT_FOUND"
                
                index_status[model] = {
                    "document_count": doc_count,
                    "index_queryable": index_queryable,
                    "index_status": index_status_str,
                    "index_ready": index_queryable and doc_count > 0
                }
            except Exception as e:
                logger.warning(f"Could not get index status for {model}: {e}")
                index_status[model] = {
                    "document_count": 0,
                    "index_queryable": False,
                    "index_status": "ERROR",
                    "index_ready": False
                }
        
        return {
            "all_sessions": all_sessions,
            "current_session": effective_current_session,
            "available_embedding_models": available_models,
            "index_status": index_status
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /state endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/index_status")
async def get_index_status(
    session_id: str = "default",
    embedding_model: str = "text-embedding-3-small",
    auto_create: bool = False,
    db=Depends(get_db)
):
    """Get detailed index status for a session"""
    try:
        # Count documents with embeddings
        doc_count = await db.knowledge_base_sessions.count_documents({
            f"metadata.{SESSION_FIELD}": session_id,
            "embedding": {"$exists": True}
        })
        
        # Check index status using index manager
        vector_index_name = f"{APP_SLUG}_embedding_vector_index"
        index_queryable = False
        index_status_str = "UNKNOWN"
        
        # Use index manager to get actual index status
        try:
            collection_wrapper = db.knowledge_base_sessions
            if hasattr(collection_wrapper, 'index_manager') and collection_wrapper.index_manager:
                index_info = await collection_wrapper.index_manager.get_search_index(vector_index_name)
                if index_info:
                    # Get actual status from MongoDB
                    status = index_info.get("status", "UNKNOWN")
                    queryable = index_info.get("queryable", False)
                    
                    index_status_str = status.upper()  # BUILDING, READY, FAILED, etc.
                    index_queryable = queryable
                    
                    # Map MongoDB status to our status strings
                    if status == "READY" and queryable:
                        index_status_str = "READY"
                    elif status == "BUILDING":
                        index_status_str = "BUILDING"
                    elif status == "FAILED":
                        index_status_str = "FAILED"
                    else:
                        index_status_str = status.upper()
                else:
                    index_status_str = "NOT_FOUND"
            else:
                # Fallback: try dummy query if index manager not available
                try:
                    dummy_vector = [0.0] * 1024
                    test_pipeline = [
                        {
                            "$vectorSearch": {
                                "index": vector_index_name,
                                "path": "embedding",
                                "queryVector": dummy_vector,
                                "numCandidates": 1,
                                "limit": 1
                            }
                        }
                    ]
                    await db.knowledge_base_sessions.aggregate(test_pipeline).to_list(length=1)
                    index_queryable = True
                    index_status_str = "READY"
                except Exception as e:
                    error_str = str(e).lower()
                    if "index" in error_str or "not found" in error_str:
                        index_status_str = "NOT_FOUND"
                    else:
                        index_status_str = "ERROR"
        except Exception as e:
            logger.warning(f"Error checking index status: {e}")
            # Fallback to NOT_FOUND if we can't check
            index_status_str = "NOT_FOUND"
        
        # Auto-create index if missing and we have documents
        # Note: Index creation is handled by MongoDBEngine during app registration
        # If status is BUILDING, that means the index is being created - no need to trigger again
        if index_status_str == "NOT_FOUND" and doc_count > 0 and auto_create:
            if vector_index_name not in index_creation_in_progress:
                logger.info(f"Index '{vector_index_name}' not found but {doc_count} documents exist. Index should be created automatically by MongoDBEngine.")
                index_creation_in_progress.add(vector_index_name)
                # Don't set status to CREATING - let MongoDBEngine handle it
                # The next check will show BUILDING or READY when the index is actually being created/ready
        
        return {
            "session_id": session_id,
            "embedding_model": embedding_model,
            "document_count": doc_count,
            "index_name": vector_index_name,
            "index_queryable": index_queryable,
            "index_status": index_status_str,
            "index_ready": index_queryable and doc_count > 0,
            "ready_for_search": index_queryable and doc_count > 0
        }
    except Exception as e:
        logger.error(f"Error in /index_status endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/indexes/create")
async def create_indexes(db=Depends(get_db)):
    """Create or update all vector search indexes"""
    try:
        logger.info("Manual index creation requested...")
        # Index creation is handled by MongoDBEngine during app registration
        # This endpoint just returns success - indexes are created automatically
        return {
            "status": "success",
            "message": "Index creation is handled automatically by MongoDBEngine. Check index status for progress."
        }
    except Exception as e:
        logger.error(f"Failed to create indexes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class ClearHistoryRequest(BaseModel):
    session_id: str

@app.post("/history/clear")
async def clear_history(request: ClearHistoryRequest):
    """Clear chat history for a session"""
    if request.session_id in chat_history:
        chat_history[request.session_id] = []
        return {"status": "success", "message": f"Chat history for '{request.session_id}' cleared."}
    raise HTTPException(status_code=404, detail=f"Session '{request.session_id}' not found.")


# ============================================================================
# Search / Preview Endpoints
# ============================================================================

class PreviewSearchRequest(BaseModel):
    query: str
    session_id: str
    embedding_model: str = "text-embedding-3-small"
    num_sources: int = 3


class SearchRequest(BaseModel):
    query: str
    num_results: int = 5


@app.post("/preview_search")
async def preview_search(
    request: PreviewSearchRequest,
    db=Depends(get_db),
    embedding_service: EmbeddingService = Depends(get_embedding_service_dependency)
):
    """Preview vector search results"""
    # Generate query embedding
    query_vector = await embedding_service.embed_chunks([request.query], model=request.embedding_model)
    if not query_vector:
        raise HTTPException(status_code=500, detail="Failed to generate query embedding")
    
    # Vector search pipeline
    # Index name is prefixed with app slug by MongoDBEngine
    vector_index_name = f"{APP_SLUG}_embedding_vector_index"
    pipeline = [
        {
            "$vectorSearch": {
                "index": vector_index_name,
                "path": "embedding",
                "queryVector": query_vector[0],
                "numCandidates": request.num_sources * 10,
                "limit": request.num_sources,
                "filter": {
                    f"metadata.{SESSION_FIELD}": {"$eq": request.session_id}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "content": "$text",
                "source": "$metadata.source",
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    
    results = await db.knowledge_base_sessions.aggregate(pipeline).to_list(length=request.num_sources)
    return results


@app.post("/preview_file")
async def preview_file(file: UploadFile = File(...)):
    """Preview file content"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No selected file")
    
    _, extension = os.path.splitext(file.filename.lower())
    MAX_PREVIEW = 50000
    
    if extension in [".txt", ".md"]:
        text_data = (await file.read()).decode("utf-8", errors="replace")
        if len(text_data) > MAX_PREVIEW:
            text_data = text_data[:MAX_PREVIEW] + "\n\n[TRUNCATED]"
        return {"content": text_data, "filename": file.filename}
    
    # Use docling for other file types
    if not DOCLING_AVAILABLE:
        raise HTTPException(status_code=400, detail="Document conversion not available. Install docling.")
    
    temp_file_path = ""
    try:
        logger.info(f"[Preview] Starting file conversion for {file.filename} (type: {extension})")
        add_status_log(f"📄 Previewing file: {file.filename}", "info", "file")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            content = await file.read()
            tmp.write(content)
            temp_file_path = tmp.name
        
        logger.info(f"[Preview] Initializing Docling converter (this may trigger RapidOCR model downloads)...")
        add_status_log("🔄 Initializing document converter...", "info", "docling")
        
        try:
            # Try to initialize converter with OCR disabled to avoid model download issues
            # If that fails, try with default settings
            try:
                # First try without OCR to avoid network issues
                # Disable OCR completely to prevent RapidOCR from trying to download models
                converter = DocumentConverter(
                    pipeline_options={
                        "do_ocr": False,
                        "do_table_structure": False  # Also disable table structure which may use OCR
                    }
                )
                logger.info(f"[Preview] Using converter without OCR to avoid model download issues")
            except (TypeError, ValueError) as init_error:
                # If pipeline_options doesn't work, try creating converter and disabling OCR after
                logger.warning(f"[Preview] Failed to initialize with options: {init_error}, trying alternative approach")
                try:
                    # Try to set environment variable to disable OCR before initialization
                    os.environ["DOCLING_DISABLE_OCR"] = "1"
                    converter = DocumentConverter()
                    logger.info(f"[Preview] Using converter with OCR disabled via environment variable")
                except Exception:
                    # Last resort: try default but catch the download error
                    converter = DocumentConverter()
            
            logger.info(f"[Preview] Converting document: {temp_file_path}")
            add_status_log(f"📖 Converting document...", "info", "docling")
            
            result = converter.convert(temp_file_path)
            doc_text = result.document.export_to_markdown()
            
            logger.info(f"[Preview] Conversion complete: {len(doc_text)} characters extracted")
            add_status_log(f"✅ Document converted: {len(doc_text)} characters extracted", "info", "docling")
            
            if len(doc_text) > MAX_PREVIEW:
                doc_text = doc_text[:MAX_PREVIEW] + "\n\n[TRUNCATED]"
            
            return {"content": doc_text, "filename": file.filename}
            
        except Exception as conversion_error:
            error_msg = str(conversion_error)
            logger.error(f"[Preview] Document conversion failed: {error_msg}", exc_info=True)
            
            # Check if it's a network/download error
            if "modelscope.cn" in error_msg or "Failed to download" in error_msg or "name resolution" in error_msg.lower():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Document conversion failed: Unable to download OCR models (network issue). "
                        "This usually happens when the OCR library tries to download models from modelscope.cn. "
                        "You can still ingest the file directly - it will extract text without OCR. "
                        f"Error: {error_msg[:200]}"
                    )
                )
            elif "OCR" in error_msg or "rapidocr" in error_msg.lower():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "OCR initialization failed. You can still ingest the file directly - "
                        "it will extract text from the PDF without OCR. "
                        f"Error: {error_msg[:200]}"
                    )
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Document conversion failed: {error_msg[:300]}"
                )
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


@app.get("/preview_url")
async def preview_url(url: str):
    """Preview URL content (using Firecrawl HTTP API)"""
    # Check for Firecrawl API key
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    if not firecrawl_key:
        firecrawl_key_raw = os.getenv("FIRECRAWL_API_KEY", "NOT_SET")
        error_msg = f"FIRECRAWL_API_KEY not set. (Key present in env: {firecrawl_key_raw != 'NOT_SET'}, Key length: {len(firecrawl_key_raw) if firecrawl_key_raw != 'NOT_SET' else 0})"
        logger.error(f"[ERROR] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    
    firecrawl_key = firecrawl_key.strip()
    if not firecrawl_key:
        error_msg = "FIRECRAWL_API_KEY is empty or whitespace only."
        logger.error(f"[ERROR] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    
    try:
        logger.info(f"[INFO] Previewing URL via Firecrawl HTTP API: {url}")
        
        # Use Firecrawl HTTP API directly
        api_url = "https://api.firecrawl.dev/v2/scrape"
        headers = {
            "Authorization": f"Bearer {firecrawl_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": False
        }
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        scrape_result = response.json()
        
        # Extract markdown content from response
        # Firecrawl API response structure: {"data": {"markdown": "..."}}
        page_content = ""
        if "data" in scrape_result:
            if isinstance(scrape_result["data"], dict):
                page_content = scrape_result["data"].get("markdown", "")
            elif isinstance(scrape_result["data"], str):
                # Sometimes the API returns data as a string directly
                page_content = scrape_result["data"]
        elif "markdown" in scrape_result:
            page_content = scrape_result["markdown"]
        
        if not page_content:
            logger.warning(f"[WARN] Firecrawl response structure: {list(scrape_result.keys())}")
            error_msg = f"No markdown content returned from {url}. Response: {str(scrape_result)[:200]}"
            logger.error(f"[ERROR] {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        
        MAX_PREVIEW = 50000
        if len(page_content) > MAX_PREVIEW:
            page_content = page_content[:MAX_PREVIEW] + "\n\n[TRUNCATED]"
        
        return {"markdown": page_content}
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Error fetching URL content via Firecrawl HTTP API: {e}"
        logger.error(f"[ERROR] {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"[ERROR] {error_msg}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=error_msg)


class IngestURLRequest(BaseModel):
    url: str
    session_id: str
    chunk_size: int = 1000
    chunk_overlap: int = 150


@app.post("/ingest_url")
async def ingest_url(
    request: IngestURLRequest,
    db=Depends(get_db),
    embedding_service: EmbeddingService = Depends(get_embedding_service_dependency)
):
    """Ingest URL content directly"""
    # Check duplicates
    existing = await db.knowledge_base_sessions.count_documents(
        {
            "metadata.source": request.url,
            f"metadata.{SESSION_FIELD}": request.session_id
        },
        limit=1
    )
    
    if existing > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Source '{request.url}' already exists in session '{request.session_id}'."
        )
    
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    if not firecrawl_key:
        raise HTTPException(status_code=500, detail="FIRECRAWL_API_KEY not set.")
    
    firecrawl_key = firecrawl_key.strip()
    if not firecrawl_key:
        raise HTTPException(status_code=500, detail="FIRECRAWL_API_KEY is empty or whitespace only.")
    
    try:
        logger.info(f"[INFO] Reading & ingesting URL via Firecrawl HTTP API: {request.url}")
        
        # Use Firecrawl HTTP API directly
        api_url = "https://api.firecrawl.dev/v2/scrape"
        headers = {
            "Authorization": f"Bearer {firecrawl_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "url": request.url,
            "formats": ["markdown"],
            "onlyMainContent": False
        }
        
        resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        scrape_result = resp.json()
        
        # Extract markdown content from response
        page_content = ""
        if "data" in scrape_result:
            if isinstance(scrape_result["data"], dict):
                page_content = scrape_result["data"].get("markdown", "")
            elif isinstance(scrape_result["data"], str):
                page_content = scrape_result["data"]
        elif "markdown" in scrape_result:
            page_content = scrape_result["markdown"]
        
        if not page_content:
            raise HTTPException(status_code=400, detail=f"No meaningful content from {request.url}.")
        
        # Use EmbeddingService to process and store
        result = await embedding_service.process_and_store(
            text_content=page_content,
            source_id=request.url,
            collection=db.knowledge_base_sessions,
            max_tokens=request.chunk_size,
            metadata={
                "source": request.url,
                "source_type": "url",
                SESSION_FIELD: request.session_id
            }
        )
        
        return {
            "status": "success",
            "message": f"✅ Ingested {result['chunks_created']} chunks from {request.url} into '{request.session_id}'.",
            "chunks_created": result['chunks_created']
        }
        
    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"[ERROR] URL ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching URL content via Firecrawl: {e}")
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        logger.error(f"[ERROR] {error_msg}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=error_msg)


# ============================================================================
# Chunk Editing
# ============================================================================

@app.delete("/chunk/{chunk_id}")
async def api_delete_chunk(chunk_id: str, db=Depends(get_db)):
    """Delete a chunk"""
    result = await db.knowledge_base_sessions.delete_one({"_id": ObjectId(chunk_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Could not find chunk '{chunk_id}' to delete.")
    return {"status": "success", "message": f"Chunk '{chunk_id}' deleted."}


@app.put("/chunk/{chunk_id}")
async def api_update_chunk(
    chunk_id: str,
    content: str = Form(...),
    db=Depends(get_db),
    embedding_service: EmbeddingService = Depends(get_embedding_service_dependency)
):
    """Update a chunk and re-embed"""
    # Re-embed the content
    vectors = await embedding_service.embed_chunks([content])
    if not vectors:
        raise HTTPException(status_code=500, detail="Failed to generate embedding")
    
    # Update document
    update_payload = {
        "$set": {
            "text": content,
            "embedding": vectors[0]
        }
    }
    
    result = await db.knowledge_base_sessions.update_one({"_id": ObjectId(chunk_id)}, update_payload)
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"Could not find chunk with ID '{chunk_id}'.")
    
    return {"status": "success", "message": f"Chunk '{chunk_id}' updated (re-embedded)."}


# ============================================================================
# Source Browsing
# ============================================================================


@app.get("/sources")
async def get_sources(session_id: str = "default", db=Depends(get_db)):
    """Get all sources for a session"""
    try:
        collection = db.knowledge_base_sessions
        
        # Check if collection exists and has documents
        try:
            count = await collection.count_documents({f"metadata.{SESSION_FIELD}": session_id})
            if count == 0:
                return []
        except Exception as e:
            logger.warning(f"Could not count documents: {e}")
            return []
        
        pipeline = [
            {"$match": {f"metadata.{SESSION_FIELD}": session_id}},
            {
                "$group": {
                    "_id": "$metadata.source",
                    "source_type": {"$first": "$metadata.source_type"},
                    "chunk_count": {"$sum": 1}
                }
            },
            {
                "$project": {
                    "name": "$_id",
                    "type": {"$ifNull": ["$source_type", "unknown"]},
                    "chunk_count": "$chunk_count",
                    "_id": 0
                }
            },
            {"$sort": {"name": 1}}
        ]
        results = await collection.aggregate(pipeline).to_list(length=1000)
        return results if results else []
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /sources endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/chunks")
async def get_chunks(session_id: str = "default", source_url: str = None, db=Depends(get_db)):
    """Get chunks for a source"""
    if not source_url:
        raise HTTPException(status_code=400, detail="source_url required")
    
    cursor = db.knowledge_base_sessions.find(
        {"metadata.source": source_url, f"metadata.{SESSION_FIELD}": session_id},
        {"_id": 1, "text": 1}
    )
    results = await cursor.to_list(length=1000)
    return [
        {"_id": str(doc["_id"]), "text": doc["text"]}
        for doc in results
    ]


@app.get("/source_content")
async def get_source_content(session_id: str, source: str, db=Depends(get_db)):
    """Get full content for a source as HTML"""
    chunks_cursor = db.knowledge_base_sessions.find(
        {
            f"metadata.{SESSION_FIELD}": session_id,
            "metadata.source": source
        },
        {"text": 1, "_id": 0}
    ).sort("metadata.chunk_index", 1)
    
    chunks = await chunks_cursor.to_list(length=10000)
    full_content = "".join([chunk.get('text', '') for chunk in chunks])
    
    if not full_content:
        raise HTTPException(status_code=404, detail="Source not found or has no content.")
    
    # Return HTML page
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{{ source_name }}</title>
        <style>
            body { 
                background-color: #121826; 
                color: #e5e7eb; 
                font-family: sans-serif; 
                line-height: 1.6;
                margin: 0;
                padding: 2rem; 
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
            pre { 
                white-space: pre-wrap; 
                word-wrap: break-word; 
                font-family: monospace; 
                font-size: 1rem; 
                background-color: #1d2333;
                padding: 1.5rem;
                border-radius: 8px;
                border: 1px solid #333c51;
            }
            h1 { color: #00ED64; word-break: break-all; }
            a { color: #00ED64; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Source</h1>
            <p style="word-break: break-all;">
                <a href="{{ source_name }}" target="_blank">{{ source_name }}</a>
            </p>
            <hr style="border-color: #333c51; margin: 1.5rem 0;">
            <pre>{{ content }}</pre>
        </div>
    </body>
    </html>
    """
    template = Template(html_template)
    html_content = template.render(source_name=source, content=full_content)
    return HTMLResponse(content=html_content)


# ============================================================================
# Additional Endpoints
# ============================================================================

@app.post("/search")
async def search_web(request: SearchRequest):
    """Web search using DuckDuckGo"""
    if not DDGS_AVAILABLE:
        raise HTTPException(status_code=503, detail="Web search not available. Install ddgs.")
    
    if not request.query:
        raise HTTPException(status_code=400, detail="Query is required")
    
    try:
        logger.info(f"[INFO] Web search for: '{request.query}'")
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(request.query, max_results=request.num_results)]
        return {"status": "success", "results": results}
    except Exception as e:
        logger.error(f"[ERROR] Web search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Web search error: {str(e)}")


@app.post("/chunk_preview")
async def chunk_preview(
    content: str = Form(...),
    chunk_size: int = Form(1000),
    chunk_overlap: int = Form(150),
    embedding_service: EmbeddingService = Depends(get_embedding_service_dependency)
):
    """Preview how content will be chunked with detailed information"""
    logs = []
    
    def log_message(msg: str, level: str = "info"):
        """Helper to collect log messages"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logs.append({"timestamp": timestamp, "level": level, "message": msg})
        if level == "info":
            logger.info(msg)
        elif level == "error":
            logger.error(msg)
        elif level == "warning":
            logger.warning(msg)
    
    if not content:
        log_message("Content validation failed: Content is required", "error")
        raise HTTPException(status_code=400, detail="Content is required")
    
    if chunk_overlap >= chunk_size:
        log_message(f"Validation failed: Chunk overlap ({chunk_overlap}) must be smaller than chunk size ({chunk_size})", "error")
        raise HTTPException(status_code=400, detail="Chunk overlap must be smaller than chunk size.")
    
    try:
        log_message(f"Starting chunk preview: content_length={len(content)}, chunk_size={chunk_size}, chunk_overlap={chunk_overlap}")
        log_message("Initializing text splitter with tokenizer model: gpt-3.5-turbo")
        
        # Get chunks (chunk_text is async, must await)
        chunks = await embedding_service.chunk_text(
            content,
            max_tokens=chunk_size,
            tokenizer_model="gpt-3.5-turbo"
        )
        
        log_message(f"Generated {len(chunks)} chunks using semantic-text-splitter")
        
        # Calculate chunk positions and overlaps
        # The semantic-text-splitter handles overlap internally, so we need to find
        # where chunks actually appear in the original text
        chunk_details = []
        search_start = 0
        
        for i, chunk in enumerate(chunks):
            # Find chunk position in original text starting from where we left off
            # This handles overlap correctly since chunks will overlap in the text
            chunk_clean = chunk.strip()
            start_pos = content.find(chunk_clean, search_start)
            
            if start_pos == -1:
                # Try without cleaning
                start_pos = content.find(chunk, search_start)
            
            if start_pos == -1:
                # If still not found, try from beginning
                start_pos = content.find(chunk_clean)
                if start_pos == -1:
                    start_pos = content.find(chunk)
            
            if start_pos == -1:
                # Fallback: use estimated position
                if i > 0 and chunk_details:
                    prev_end = chunk_details[-1]["end_pos"]
                    # Estimate: previous end minus overlap
                    start_pos = max(0, prev_end - chunk_overlap)
                else:
                    start_pos = 0
            
            end_pos = start_pos + len(chunk)
            
            # Calculate actual overlap with previous chunk
            overlap_start = None
            overlap_end = None
            overlap_text = None
            
            if i > 0:
                prev_chunk = chunk_details[i - 1]
                prev_start = prev_chunk["start_pos"]
                prev_end = prev_chunk["end_pos"]
                
                # Find actual overlap region (where chunks intersect)
                if start_pos < prev_end:
                    overlap_start = start_pos
                    overlap_end = min(end_pos, prev_end)
                    if overlap_end > overlap_start:
                        overlap_text = content[overlap_start:overlap_end]
                    else:
                        overlap_start = None
                        overlap_end = None
            
            # Update search start for next iteration (account for overlap)
            search_start = max(0, start_pos + 1)
            
            # Estimate token count (rough approximation)
            # Using ~4 characters per token as a rough estimate
            estimated_tokens = len(chunk) // 4
            
            overlap_length = (overlap_end - overlap_start) if overlap_start and overlap_end else 0
            
            chunk_info = {
                "index": i,
                "text": chunk,
                "start_pos": start_pos,
                "end_pos": end_pos,
                "length": len(chunk),
                "estimated_tokens": estimated_tokens,
                "overlap_start": overlap_start,
                "overlap_end": overlap_end,
                "overlap_text": overlap_text,
                "overlap_length": overlap_length
            }
            chunk_details.append(chunk_info)
            
            overlap_info = f"overlap={overlap_length} chars" if overlap_start else "no overlap"
            log_message(f"Chunk {i}: position={start_pos}-{end_pos}, length={len(chunk)}, tokens≈{estimated_tokens}, {overlap_info}")
        
        # Calculate total overlap percentage
        total_overlap_chars = sum(
            (c["overlap_end"] - c["overlap_start"]) 
            for c in chunk_details 
            if c["overlap_start"] and c["overlap_end"]
        )
        total_content_length = len(content)
        overlap_percentage = (total_overlap_chars / total_content_length * 100) if total_content_length > 0 else 0
        
        log_message(f"Chunking complete: {len(chunks)} chunks, total overlap: {total_overlap_chars} chars ({overlap_percentage:.1f}%)")
        
        return {
            "chunks": [c["text"] for c in chunk_details],  # Keep backward compatibility
            "chunk_details": chunk_details,
            "summary": {
                "total_chunks": len(chunks),
                "total_length": len(content),
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "total_overlap_chars": total_overlap_chars,
                "overlap_percentage": round(overlap_percentage, 2),
                "avg_chunk_length": round(sum(c["length"] for c in chunk_details) / len(chunk_details)) if chunk_details else 0,
                "avg_estimated_tokens": round(sum(c["estimated_tokens"] for c in chunk_details) / len(chunk_details)) if chunk_details else 0
            },
            "logs": logs,
            "original_content": content  # Include for visualization
        }
    except Exception as e:
        log_message(f"Chunk preview failed: {str(e)}", "error")
        logger.error(f"[ERROR] Chunk preview failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "logs": logs
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)

