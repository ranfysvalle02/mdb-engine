#!/usr/bin/env python3
"""
Conversations - Enterprise AI Chat Application

This example demonstrates the RECOMMENDED approach for building AI chat applications
with MDB-Engine using CognitiveEngine for complete RAG pipeline orchestration.

Key Features:
- CognitiveEngine Integration: Complete STM + LTM orchestration via CognitiveEngine.chat()
- Automatic Memory Extraction: Facts are automatically extracted and stored to LTM
- Automatic Context Management: STM and LTM are managed automatically
- Best Practices: This is the gold standard example for MDB-Engine chat applications

This example demonstrates the recommended `engine.create_app()` pattern
for FastAPI integration with MDB-Engine.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mdb_engine.memory.base import BaseMemoryService

from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo.errors import PyMongoError
from fastapi import Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
# LLM Service imports (supports OpenAI, Azure OpenAI, and Gemini)
from mdb_engine.llm import get_llm_service

from mdb_engine import MongoDBEngine
from mdb_engine.auth.decorators import rate_limit_auth, require_auth, token_security
from mdb_engine.auth.integration import get_auth_config
from mdb_engine.auth.users import create_app_session, get_app_user
from mdb_engine.auth.utils import login_user, logout_user, register_user
from mdb_engine.routing.websockets import broadcast_to_app, register_message_handler
from mdb_engine.memory.orchestrator import CognitiveEngine

# Load environment variables
load_dotenv()

logger = logging.getLogger("conversations_app")

# App slug constant
APP_SLUG = "conversations"

# Templates directory
templates_dir = (
    Path("/app/templates")
    if Path("/app/templates").exists()
    else Path(__file__).parent / "templates"
)
templates = Jinja2Templates(directory=str(templates_dir))

# Secret key for JWT
from mdb_engine.env import get_jwt_secret, get_mongo_uri, get_db_name

SECRET_KEY = get_jwt_secret() or "conversations_demo_secret_key_change_in_production"

# Initialize the MongoDB Engine
engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(fallback="mongodb://mongodb:27017/"),
    db_name=get_db_name(fallback="conversations_db"),
)

# Global instances
cognitive_engine: Optional[CognitiveEngine] = None
llm_service = None

# -------------------------------------------------------------------------
# STARTUP LIFECYCLE
# -------------------------------------------------------------------------

async def on_startup(app, engine, manifest):
    """Enterprise Startup: Initialize Search, LLMs, and WebSocket Routes."""
    global cognitive_engine, llm_service
    
    # 1. Register Real-time capabilities
    register_websocket_message_handlers()
    try:
        engine.register_websocket_routes(app, APP_SLUG)
        logger.info("✅ WebSocket routes registered")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning(f"WebSocket route registration skipped: {e}", exc_info=True)

    # 2. Configure App-Level Auth Ticket Endpoint
    if engine.websocket_ticket_store:
        _configure_ticket_endpoint(app)

    # 3. Initialize Cognitive Services (LLM + Memory)
    memory_service = engine.get_memory_service(APP_SLUG)
    if memory_service:
        try:
            # Get LLM service from manifest config (supports OpenAI, Azure OpenAI, Gemini)
            llm_config = manifest.get("llm_config", {})
            llm_service = get_llm_service(config=llm_config)
            
            # Get collections from MDB-Engine connection manager
            motor_client = engine._connection_manager.mongo_client
            pymongo_client = motor_client.delegate  # Get underlying PyMongo client
            pymongo_db = pymongo_client[engine.db_name]
            
            # Initialize CognitiveEngine - THE recommended way to handle STM + LTM
            # This provides a complete RAG pipeline with automatic context management
            # Note: CognitiveEngine uses its own chat_history collection for STM
            # We'll sync messages to the messages collection for UI compatibility
            chat_history_collection = pymongo_db["chat_history"]
            
            cognitive_engine = CognitiveEngine(
                app_slug=APP_SLUG,
                memory_service=memory_service,
                chat_history_collection=chat_history_collection,
                stm_context_limit=10,  # Last 10 messages for context
                ltm_search_limit=5,    # Top 5 relevant memories
                auto_summarize_threshold=20,  # Summarize when session > 20 messages
                llm_service=llm_service,
            )
            logger.info("✅ Cognitive Engine Online: Complete RAG Pipeline Ready")
        except (ImportError, RuntimeError, OSError) as e:
            logger.error(
                f"❌ Failed to initialize CognitiveEngine: {e}", exc_info=True
            )
    else:
        logger.warning(
            f"⚠️ Memory service not found for app '{APP_SLUG}' - "
            f"Cognitive Engine disabled. Check if memory_config.enabled=true in manifest."
        )


def _configure_ticket_endpoint(app):
    """Sets up the auth ticket endpoint for WebSockets."""
    from fastapi import Request, status
    from fastapi.responses import JSONResponse
    from mdb_engine.auth.users import get_app_user
    
    # Remove existing /auth/ticket route if it exists (from default registration)
    routes_to_keep = []
    removed_count = 0
    
    for route in app.router.routes:
        route_path = getattr(route, 'path', None)
        if route_path == "/auth/ticket":
            route_methods = getattr(route, 'methods', set())
            if isinstance(route_methods, set) and 'POST' in route_methods:
                logger.info(f"Removing default ticket endpoint: {type(route).__name__} at {route_path}")
                removed_count += 1
                continue
            elif hasattr(route, 'methods') and route.methods:
                if isinstance(route.methods, (set, list)) and 'POST' in route.methods:
                    logger.info(f"Removing default ticket endpoint: {type(route).__name__} at {route_path}")
                    removed_count += 1
                    continue
        
        routes_to_keep.append(route)
    
    app.router.routes = routes_to_keep
    
    if removed_count > 0:
        logger.info(f"✅ Removed {removed_count} default ticket endpoint(s)")
    
    async def app_level_ticket_endpoint(request: Request):
        """Custom ticket endpoint that works with app-level auth."""
        logger.info(f"[Ticket Endpoint] 🎫 CUSTOM TICKET ENDPOINT CALLED - Path: {request.url.path}")
        
        db = engine.get_scoped_db(APP_SLUG)
        app_config = engine.get_app(APP_SLUG)
        
        app_user = await get_app_user(
            request=request,
            slug_id=APP_SLUG,
            db=db,
            config=app_config,
            allow_demo_fallback=False,
        )
        
        if not app_user:
            logger.warning(f"[Ticket Endpoint] No app user found - user not authenticated")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
            )
        
        logger.info(f"[Ticket Endpoint] App user found: {app_user.get('_id')}")
        
        user_id = str(app_user.get("_id") or app_user.get("user_id"))
        user_email = app_user.get("email")
        
        if not user_id:
            logger.error(f"[Ticket Endpoint] Invalid user data: {app_user}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid user data"},
            )
        
        ticket = engine.websocket_ticket_store.create_ticket(
            user_id=user_id,
            user_email=user_email,
            app_slug=APP_SLUG,
        )
        
        logger.info(f"✅ Generated WebSocket ticket for user '{user_id}' (app-level auth)")
        
        return JSONResponse({
            "ticket": ticket,
            "expires_in": engine.websocket_ticket_store.ticket_ttl,
        })
    
    app.add_api_route("/auth/ticket", app_level_ticket_endpoint, methods=["POST"])
    logger.info("✅ Custom app-level ticket endpoint registered at /auth/ticket")


# -------------------------------------------------------------------------
# FASTAPI APP
# -------------------------------------------------------------------------

app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Conversations",
    version="2.0.0",  # Enterprise Version
    on_startup=on_startup,
)


# Global exception handler to catch any unhandled exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all exceptions and return proper JSON responses"""
    # Only handle stats endpoint specially - let others use default behavior
    if request.url.path == "/api/memories/stats":
        logger.error(
            f"Global exception handler caught error in stats endpoint: {exc}", exc_info=True
        )
        return JSONResponse(
            {
                "success": False,
                "error": f"Internal error: {str(exc)}",
                "error_type": type(exc).__name__,
                "stats": {
                    "total_memories": 0,
                    "memory_enabled": False,
                    "inference_enabled": False,
                    "graph_enabled": False,
                    "conversation_memories": 0,
                    "metadata_breakdown": {},
                },
            },
            status_code=200,
        )

    # For other endpoints, use default FastAPI behavior
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    # For unexpected exceptions, return 500
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


# ============================================================================
# Health Check Endpoint (for container healthchecks)
# ============================================================================


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container orchestration."""
    health_status = {
        "status": "healthy",
        "app": APP_SLUG,
        "engine_initialized": engine.initialized,
    }

    # Check MongoDB connection if engine is initialized
    if engine.initialized:
        try:
            engine_health = await engine.get_health_status()
            health_status["database"] = engine_health.get("mongodb", "unknown")
        except (ConnectionError, TimeoutError, OSError):
            health_status["status"] = "degraded"
            health_status["database"] = "connection_failed"
    else:
        health_status["status"] = "starting"
        health_status["database"] = "not_connected"

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


async def get_current_app_user(request: Request):
    """Helper to get current app user for conversations app."""
    if not engine.initialized:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    db = engine.get_scoped_db(APP_SLUG)

    # Get app config for auth.users
    app_config = engine.get_app(APP_SLUG)

    app_user = await get_app_user(
        request=request,
        slug_id=APP_SLUG,
        db=db,
        config=app_config,
        allow_demo_fallback=False,
    )

    # If no user but session cookie exists, it means the user was deleted
    # Mark this in request state so endpoints can clear the cookie
    if not app_user:
        cookie_name = f"{APP_SLUG}_session_{APP_SLUG}"
        if request.cookies.get(cookie_name):
            request.state.clear_invalid_session = True

    return app_user


# ============================================================================
# Root Route
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Home page - redirects to conversations or login"""
    app_user = await get_current_app_user(request)

    if app_user:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# ============================================================================
# Authentication Routes
# ============================================================================


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    app_user = await get_current_app_user(request)

    if app_user:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
@rate_limit_auth(endpoint="login")
@token_security()
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Handle login - returns JSON for JavaScript frontend"""
    db = engine.get_scoped_db(APP_SLUG)
    auth_config = await get_auth_config(APP_SLUG, engine)
    token_config = auth_config.get("token_management", {})

    result = await login_user(
        request=request,
        email=email,
        password=password,
        db=db,
        config=token_config,
        redirect_url="/conversations",
    )

    if result["success"]:
        response = result["response"]
        user = result["user"]
        
        # Create app-specific session
        app_config = engine.get_app(APP_SLUG)
        if app_config:
            try:
                await create_app_session(
                    request=request,
                    slug_id=APP_SLUG,
                    user_id=str(user["_id"]),
                    config=app_config,
                    response=response,
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to create app session: {e}", exc_info=True)

        # Return JSON with cookies
        json_response = JSONResponse({"success": True, "redirect": "/conversations"})
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                json_response.headers.append(key, value)
        return json_response
    
    return JSONResponse(
        {"success": False, "detail": result.get("error", "Login failed")},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    app_user = await get_current_app_user(request)

    if app_user:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request, "register.html", {})


@app.post("/register")
@rate_limit_auth(endpoint="register")
@token_security()
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
):
    """Handle registration - returns JSON for JavaScript frontend"""
    db = engine.get_scoped_db(APP_SLUG)
    auth_config = await get_auth_config(APP_SLUG, engine)
    token_config = auth_config.get("token_management", {})

    result = await register_user(
        request=request,
        email=email,
        password=password,
        db=db,
        config=token_config,
        extra_data={"full_name": full_name},
        redirect_url="/conversations",
    )

    if result["success"]:
        response = result["response"]
        user = result["user"]
        
        # Create app-specific session
        app_config = engine.get_app(APP_SLUG)
        if app_config:
            try:
                await create_app_session(
                    request=request,
                    slug_id=APP_SLUG,
                    user_id=str(user["_id"]),
                    config=app_config,
                    response=response,
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to create app session: {e}", exc_info=True)

        # Return JSON with cookies
        json_response = JSONResponse({"success": True, "redirect": "/conversations"})
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                json_response.headers.append(key, value)
        return json_response
    
    return JSONResponse(
        {"success": False, "detail": result.get("error", "Registration failed")},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@app.post("/logout")
async def logout(request: Request):
    """Handle logout - returns JSON for JavaScript frontend"""
    response = JSONResponse(content={"success": True})
    response = await logout_user(request, response)

    # Clear app-specific session cookie
    cookie_name = f"{APP_SLUG}_session_{APP_SLUG}"
    response.delete_cookie(key=cookie_name)

    return response


# ============================================================================
# Conversation Routes
# ============================================================================


@app.get("/conversations", response_class=HTMLResponse)
@require_auth()
async def conversations_list(request: Request):
    """List all conversations for the user"""
    app_user = await get_current_app_user(request)

    if not app_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    db = engine.get_scoped_db(APP_SLUG)
    user_id = str(app_user["_id"])

    # Get user's conversations
    conversations = (
        await db.conversations.find({"user_id": user_id}).sort("updated_at", -1).to_list(length=100)
    )

    return templates.TemplateResponse(
        request,
        "conversations.html",
        {
            "user": app_user,
            "conversations": conversations,
        },
    )


@app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
@require_auth()
async def conversation_view(request: Request, conversation_id: str):
    """View a specific conversation"""
    app_user = await get_current_app_user(request)

    if not app_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    db = engine.get_scoped_db(APP_SLUG)
    user_id = str(app_user["_id"])

    # Get conversation
    try:
        conversation = await db.conversations.find_one(
            {"_id": ObjectId(conversation_id), "user_id": user_id}
        )
    except (ValueError, TypeError):
        # Invalid ObjectId format, redirect to conversations
        conversation = None

    if not conversation:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)

    # Get messages
    messages = (
        await db.messages.find({"conversation_id": conversation_id})
        .sort("created_at", 1)
        .to_list(length=1000)
    )

    # Get last active context from conversation document (persisted from last message)
    last_active_context = conversation.get("last_active_context", [])

    return templates.TemplateResponse(
        request,
        "conversation.html",
        {
            "user": app_user,
            "conversation": conversation,
            "messages": messages,
            "last_active_context": last_active_context,
        },
    )


# ============================================================================
# API Routes
# ============================================================================


@app.post("/api/conversations", response_class=JSONResponse)
@require_auth()
async def create_conversation(request: Request):
    """Create a new conversation"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = engine.get_scoped_db(APP_SLUG)
    user_id = str(app_user["_id"])

    # Create conversation
    conversation = {
        "user_id": user_id,
        "title": "New Conversation",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await db.conversations.insert_one(conversation)
    conversation["_id"] = result.inserted_id

    return JSONResponse(
        {
            "success": True,
            "conversation": {
                "_id": str(conversation["_id"]),
                "title": conversation["title"],
                "created_at": conversation["created_at"].isoformat(),
            },
        }
    )


# -------------------------------------------------------------------------
# ENTERPRISE CHAT LOGIC
# -------------------------------------------------------------------------

@app.post("/api/conversations/{conversation_id}/messages", response_class=JSONResponse)
@require_auth()
async def send_message(request: Request, conversation_id: str, message: str = Form(...)):
    """
    Enterprise Message Handler using CognitiveEngine:
    Complete RAG pipeline with automatic STM + LTM orchestration.
    
    PERFORMANCE OPTIMIZATION: Returns response immediately, extracts memories in background.
    This provides fast response times while memory extraction happens asynchronously.
    The UI is updated via WebSocket when memory extraction completes.
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db = engine.get_scoped_db(APP_SLUG)
    user_id = str(app_user["_id"])
    memory_service = engine.get_memory_service(APP_SLUG)

    # Verify conversation belongs to user
    try:
        conversation = await db.conversations.find_one(
            {"_id": ObjectId(conversation_id), "user_id": user_id}
        )
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Track if background extraction will run
    extraction_pending = False

    # Use CognitiveEngine for complete RAG pipeline
    if cognitive_engine and memory_service:
        try:
            # Use conversation_id as session_id (they're the same concept)
            session_id = conversation_id
            
            # PERFORMANCE OPTIMIZATION: Set extract_facts=False for immediate response
            # Memory extraction is moved to a background task below
            # CognitiveEngine handles:
            # 1. Saves user message to STM
            # 2. Searches LTM for relevant memories
            # 3. Retrieves STM context
            # 4. Generates LLM response with context
            # 5. Saves AI response to STM
            # (Memory extraction moved to background task)
            result = await cognitive_engine.chat(
                user_id=user_id,
                session_id=session_id,
                user_query=message,
                system_prompt="You are a helpful AI assistant. Use the provided context to give personalized responses.",
                extract_facts=False,  # FAST: Skip extraction here, do it in background
            )
            
            ai_response_content = result["response"]
            stm_context = result.get("stm_context", [])
            ltm_memories = result.get("ltm_memories", [])
            session_message_count = result.get("session_message_count", 0)
            
            # Format memories for UI response
            context_memories = [
                {
                    "id": m.get("id"),
                    "memory": m.get("memory"),
                    "score": m.get("score", m.get("similarity", 0.0)),
                    "metadata": m.get("metadata", {})
                }
                for m in ltm_memories
                if isinstance(m, dict) and m.get("memory")
            ]
            
            # Sync messages to messages collection for UI compatibility
            # CognitiveEngine stores in chat_history with session_id, but UI expects messages with conversation_id
            user_msg_doc = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "user",
                "content": message,
                "created_at": datetime.utcnow()
            }
            ai_msg_doc = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "assistant",
                "content": ai_response_content,
                "created_at": datetime.utcnow()
            }
            await db.messages.insert_many([user_msg_doc, ai_msg_doc])
            
            logger.info(
                f"✅ CognitiveEngine processed message (fast mode): "
                f"STM={len(stm_context)} messages, LTM={len(context_memories)} memories"
            )
            
            # BACKGROUND MEMORY EXTRACTION: Extract facts after returning response
            # This provides fast response times while memory extraction happens asynchronously
            # The UI will be notified via WebSocket when extraction completes
            if memory_service and message.strip():
                extraction_pending = True
                logger.info(
                    f"📡 [Background Extraction] Scheduling memory extraction: "
                    f"user_id={user_id}, session_id={session_id}"
                )
                extraction_task = asyncio.create_task(
                    _extract_memories_background(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        message=message,
                        memory_service=memory_service,
                        ai_response=ai_response_content,
                    )
                )
                # Add error callback to log task failures without disrupting main flow
                extraction_task.add_done_callback(
                    lambda t: logger.error(
                        f"❌ Background extraction task failed: {t.exception()}"
                    ) if t.exception() else None
                )
            
        except (PyMongoError, ValueError, KeyError) as e:
            logger.error(f"❌ CognitiveEngine chat failed: {e}", exc_info=True)
            # Fallback to basic response
            ai_response_content = await _generate_llm_response([
                {"role": "user", "content": message}
            ])
            context_memories = []
            stm_context = []
    else:
        # Fallback if CognitiveEngine not available
        logger.warning("⚠️ CognitiveEngine not available, using basic chat")
        ai_response_content = await _generate_llm_response([
            {"role": "user", "content": message}
        ])
        context_memories = []
        stm_context = []
        
        # Still save messages for UI
        user_msg_doc = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "user",
            "content": message,
            "created_at": datetime.utcnow()
        }
        await db.messages.insert_one(user_msg_doc)
        
        ai_msg_doc = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "assistant",
            "content": ai_response_content,
            "created_at": datetime.utcnow()
        }
        await db.messages.insert_one(ai_msg_doc)

    # 7. Update conversation metadata and persist active context for page refresh
    update_fields = {"updated_at": datetime.utcnow()}
    
    # Persist active context to database (survives page refresh across devices)
    if context_memories:
        update_fields["last_active_context"] = context_memories
    
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)}, {"$set": update_fields}
    )

    # Update conversation title if it's still "New Conversation"
    if conversation.get("title") == "New Conversation" and len(message) > 0:
        title = message[:50] + ("..." if len(message) > 50 else "")
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)}, {"$set": {"title": title}}
        )

    # 8. Return Response to UI IMMEDIATELY (memory extraction continues in background)
    return JSONResponse({
        "success": True,
        "message": {
            "role": "assistant",
            "content": ai_response_content,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
        # Enterprise Grade: We explicitly tell the UI what we found
        "memory_context": {
            "query": message,
            "used_memories": len(context_memories),
            "memories": context_memories[:3], 
            "search_details": context_memories,
        },
        "memory_operations": {
            "search_performed": True,
            "memories_found": len(context_memories),
            "extraction_pending": extraction_pending,  # NEW: Indicates background extraction is running
            "extraction_performed": False,  # Will be true when WebSocket event arrives
            "vector_search_used": memory_service is not None,
            "cognitive_engine_used": cognitive_engine is not None
        }
    })


async def _generate_llm_response(messages):
    """Helper to call LLM safely using LLMService."""
    global llm_service
    if not llm_service:
        return "I am currently unable to access my language model. Please check configuration."
    
    try:
        # Use named provider from llm_config.providers
        response = await llm_service.chat_completion(
            messages=messages,
            provider_name="chat",
            temperature=1.0
        )
        return response
    except (RuntimeError, ValueError, OSError) as e:
        logger.error(f"LLM Generation failed: {e}", exc_info=True)
        return "I encountered an error generating a response."


# Note: Memory extraction is now handled automatically by CognitiveEngine.chat()
# The extract_facts=True parameter ensures facts are extracted and stored to LTM
# during the chat() call. No separate background processing needed.


# Constants for memory broadcasting
VECTOR_INDEX_UPDATE_DELAY_SECONDS = 1.5  # Delay to allow MongoDB Atlas vector index to update
MAX_MEMORIES_TO_FETCH = 50  # Maximum number of memories to fetch for broadcast
MAX_NEW_MEMORIES_TO_DISPLAY = 5  # Maximum number of new memories to include in event


async def _send_extraction_status(
    user_id: str,
    conversation_id: str,
    stage: str,
    message: str,
    progress: int = 0,
    details: Optional[Dict[str, Any]] = None,
    filename: Optional[str] = None,
    fact_number: Optional[int] = None,
    total_facts: Optional[int] = None,
) -> None:
    """Send a status update via WebSocket during memory extraction.
    
    Sends both memory_extraction_status and memory_progress events for full transparency.
    """
    try:
        # Send memory_extraction_status event (for chat status indicator)
        payload_status = {
            "type": "memory_extraction_status",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "stage": stage,
            "message": message,
            "progress": progress,  # 0-100
            "details": details or {},
        }
        await broadcast_to_app(APP_SLUG, payload_status, user_id=user_id)
        
        # Also send memory_progress event (for progress bars and modals)
        payload_progress = {
            "type": "memory_progress",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "stage": stage,
            "message": message,
            "progress": progress,
            "details": details or {},
            "filename": filename,
            "fact_number": fact_number,
            "total_facts": total_facts,
        }
        await broadcast_to_app(APP_SLUG, payload_progress, user_id=user_id)
        
        logger.debug(f"📡 Sent extraction status: stage={stage}, progress={progress}%")
    except (RuntimeError, OSError) as e:
        logger.warning(f"Failed to send extraction status: {e}")


async def _extract_memories_background(
    user_id: str,
    conversation_id: str,
    message: str,
    memory_service: "BaseMemoryService",
    ai_response: Optional[str] = None,
) -> None:
    """
    Extract memories from user message in background after response is returned.
    
    This function provides fast response times by decoupling memory extraction from
    the main request/response cycle. The UI is notified via WebSocket with step-by-step
    status updates showing exactly what's happening.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for metadata
        message: The user message to extract facts from
        memory_service: Memory service instance for storage
        ai_response: The AI response text (optional). If provided, it allows
                    extracting memories from the full interaction context.
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id or not message.strip():
        logger.warning("⚠️ _extract_memories_background called with empty user_id or message, skipping")
        return
    
    if not memory_service:
        logger.warning("⚠️ _extract_memories_background called with None memory_service, skipping")
        return
    
    try:
        # Stage 1: Starting
        await _send_extraction_status(
            user_id, conversation_id,
            stage="starting",
            message="🔄 Starting memory extraction...",
            progress=10,
        )
        
        logger.info(
            f"🧠 [Background Extraction] Starting memory extraction: "
            f"user_id={user_id}, message='{message[:50]}...'"
        )
        
        # Stage 2: Analyzing
        await _send_extraction_status(
            user_id, conversation_id,
            stage="analyzing",
            message="🔍 Analyzing message for memorable facts...",
            progress=30,
            details={"message_length": len(message)},
        )
        
        # Build storage metadata
        storage_bucket_id = f"session:{conversation_id}"
        storage_metadata = {
            "source": "chat_session",
            "session_id": conversation_id,
            "associated_bucket_id": storage_bucket_id,
            "raw_input": message,
            "raw_output": ai_response,
        }
        
        # Combine user message and AI response for extraction context
        extraction_text = message
        if ai_response:
            extraction_text = f"User: {message}\nAI: {ai_response}"
        
        # Stage 3: Preparing extraction
        await _send_extraction_status(
            user_id, conversation_id,
            stage="preparing_extraction",
            message="🔧 Preparing AI extraction pipeline...",
            progress=40,
        )
        await asyncio.sleep(0.1)  # Brief pause for UI update
        
        # Stage 4: Extracting (the LLM call happens here)
        await _send_extraction_status(
            user_id, conversation_id,
            stage="extracting",
            message="🧠 Analyzing message with AI...",
            progress=45,
        )
        
        # Send periodic updates during extraction (heartbeat)
        extraction_start = asyncio.get_event_loop().time()
        heartbeat_task = None
        
        async def send_extraction_heartbeat():
            """Send periodic progress updates during extraction."""
            heartbeat_count = 0
            while True:
                await asyncio.sleep(0.8)  # Update every 800ms
                elapsed = asyncio.get_event_loop().time() - extraction_start
                heartbeat_count += 1
                
                # Gradually increase progress from 45% to 65% during extraction
                # This prevents the UI from appearing stuck
                progress_increment = min(20, heartbeat_count * 2)  # Max 20% increase
                current_progress = 45 + progress_increment
                
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage="extracting",
                    message=f"🧠 AI is analyzing... ({int(elapsed)}s)",
                    progress=current_progress,
                    details={"elapsed_seconds": int(elapsed)},
                )
        
        # Start heartbeat
        heartbeat_task = asyncio.create_task(send_extraction_heartbeat())
        
        try:
            # Extract and store memories (this uses LLM for fact extraction)
            stored = await memory_service.add(
                messages=extraction_text,
                user_id=user_id,
                metadata=storage_metadata,
                bucket_id=storage_bucket_id,
                bucket_type="conversation",
            )
        finally:
            # Stop heartbeat
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
        
        # Stage 5: Processing results
        await _send_extraction_status(
            user_id, conversation_id,
            stage="processing_results",
            message="📊 Processing extracted facts...",
            progress=70,
        )
        await asyncio.sleep(0.1)  # Brief pause for UI update
        
        memories_count = len(stored) if isinstance(stored, list) else 0
        
        # Stage 6: Storing
        if memories_count > 0:
            await _send_extraction_status(
                user_id, conversation_id,
                stage="storing",
                message=f"💾 Storing {memories_count} new memories...",
                progress=75,
                details={"count": memories_count},
            )
            await asyncio.sleep(0.1)
            
            # Send progress updates for each memory being stored
            for idx, memory in enumerate(stored[:memories_count]):
                if idx > 0:  # Skip first update (already sent above)
                    progress = 75 + int((idx / memories_count) * 15)  # 75% to 90%
                    await _send_extraction_status(
                        user_id, conversation_id,
                        stage="storing_memories",
                        message=f"💾 Storing memory {idx + 1}/{memories_count}...",
                        progress=progress,
                        details={"count": memories_count, "current": idx + 1},
                        fact_number=idx + 1,
                        total_facts=memories_count,
                    )
                    await asyncio.sleep(0.05)  # Small delay between updates
            
            logger.info(
                f"✅ [Background Extraction] Stored {memories_count} memories "
                f"for user_id={user_id}"
            )
            
            # Brief delay to show the storing message
            await asyncio.sleep(0.2)
            
            # Stage 5: Complete with memories
            memory_previews = [
                m.get("memory", "")[:60] + "..." if len(m.get("memory", "")) > 60 else m.get("memory", "")
                for m in (stored[:3] if isinstance(stored, list) else [])
            ]
            
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message=f"✅ Extracted {memories_count} new memories!",
                progress=100,
                details={
                    "count": memories_count,
                    "previews": memory_previews,
                },
            )
            
            # Broadcast memory_stored event to update Memory Explorer
            await _broadcast_memory_stored(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_service=memory_service,
                new_memories=stored,
            )
        else:
            # Stage 5: Complete with no memories
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message="ℹ️ No new facts to remember from this message",
                progress=100,
                details={"count": 0},
            )
            
            logger.info(
                f"ℹ️ [Background Extraction] No new facts extracted from message "
                f"(user_id={user_id})"
            )
            
            # Still broadcast to signal extraction completed
            await _broadcast_memory_stored(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_service=memory_service,
                new_memories=[],
            )
            
    except asyncio.CancelledError:
        logger.debug(f"🔄 Background extraction task cancelled for user_id={user_id}")
        raise
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Stage: Error
        await _send_extraction_status(
            user_id, conversation_id,
            stage="error",
            message=f"❌ Memory extraction failed: {str(e)[:50]}",
            progress=0,
            details={"error": str(e)},
        )
        
        logger.error(
            f"❌ [Background Extraction] Failed: user_id={user_id}, error={e}",
            exc_info=True
        )
        # Don't re-raise - background task failures shouldn't affect main flow


async def _broadcast_memory_stored(
    user_id: str,
    conversation_id: str,
    memory_service: "BaseMemoryService",
    new_memories: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Broadcast memory_stored event via WebSocket after a delay to allow vector index to update.
    
    This function ensures the Memory Explorer UI automatically refreshes when new memories
    are stored. It includes proper error handling, input validation, and structured logging.
    
    The delay is necessary because MongoDB Atlas vector indexes update asynchronously, and
    we want to ensure the frontend receives consistent data when it queries for memories.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for filtering new memories
        memory_service: Memory service instance (must implement BaseMemoryService interface)
        new_memories: Optional list of newly stored memories from CognitiveEngine.
                     If provided, these are used for immediate display. Otherwise, memories
                     are filtered by conversation_id from fresh database query.
    
    Raises:
        Logs errors but does not raise exceptions to prevent disrupting the main request flow.
        Background tasks should fail gracefully.
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id:
        logger.warning("⚠️ _broadcast_memory_stored called with empty user_id, skipping broadcast")
        return
    
    if not conversation_id:
        logger.warning("⚠️ _broadcast_memory_stored called with empty conversation_id, skipping broadcast")
        return
    
    if not memory_service:
        logger.warning("⚠️ _broadcast_memory_stored called with None memory_service, skipping broadcast")
        return
    
    try:
        # Wait for vector index to update (MongoDB Atlas indexes update asynchronously)
        # This delay ensures the vector index is consistent for search operations
        await asyncio.sleep(VECTOR_INDEX_UPDATE_DELAY_SECONDS)
        
        # Fetch fresh memories to broadcast (ensures we have the latest state)
        try:
            fresh_memories = await memory_service.get_all(
                user_id=str(user_id),
                limit=MAX_MEMORIES_TO_FETCH
            )
        except (PyMongoError, ValueError, KeyError) as fetch_error:
            logger.error(
                f"❌ Failed to fetch fresh memories for broadcast (user_id={user_id}): {fetch_error}",
                exc_info=True
            )
            # Continue with provided new_memories if available, otherwise abort
            if not new_memories:
                logger.warning("⚠️ No fresh memories and no new_memories provided, aborting broadcast")
                return
            fresh_memories = []
        
        # Format memories for broadcast with validation
        formatted_memories: List[Dict[str, Any]] = []
        if isinstance(fresh_memories, list):
            for m in fresh_memories:
                if isinstance(m, dict) and m.get("memory"):
                    formatted_memories.append({
                        "id": str(m.get("id", "")),  # Ensure ID is string
                        "memory": str(m.get("memory", "")),  # Ensure memory is string
                        "metadata": m.get("metadata", {}),  # Preserve metadata dict
                    })
        
        # Determine new memories to highlight
        formatted_new: List[Dict[str, Any]] = []
        new_memories_count = 0
        
        if new_memories and isinstance(new_memories, list):
            # Use provided new_memories from CognitiveEngine result (preferred)
            for m in new_memories:
                if isinstance(m, dict) and m.get("memory"):
                    formatted_new.append({
                        "id": str(m.get("id", "")),
                        "memory": str(m.get("memory", "")),
                        "metadata": m.get("metadata", {}),
                    })
            new_memories_count = len(formatted_new)
        else:
            # Fallback: filter by conversation_id from fresh_memories
            # This is less reliable but provides a backup if new_memories not provided
            for m in formatted_memories:
                metadata = m.get("metadata", {})
                if isinstance(metadata, dict):
                    if (metadata.get("session_id") == conversation_id or
                        metadata.get("conversation_id") == conversation_id):
                        formatted_new.append(m)
            new_memories_count = len(formatted_new)
        
        # Prepare broadcast payload with validation
        broadcast_payload: Dict[str, Any] = {
            "type": "memory_stored",
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "memory_count": len(formatted_memories),
            "new_memories": formatted_new[:MAX_NEW_MEMORIES_TO_DISPLAY],
            "all_memories": formatted_memories,
            "message": f"🧠 Extracted {new_memories_count} new memories"
        }
        
        # Broadcast to frontend with error handling
        try:
            await broadcast_to_app(
                APP_SLUG,
                broadcast_payload,
                user_id=str(user_id)
            )
            
            logger.info(
                f"📡 Successfully broadcasted memory_stored event: "
                f"user_id={user_id}, conversation_id={conversation_id}, "
                f"new_memories={new_memories_count}, total_memories={len(formatted_memories)}"
            )
        except (RuntimeError, OSError) as broadcast_error:
            logger.error(
                f"❌ Failed to broadcast WebSocket event (user_id={user_id}): {broadcast_error}",
                exc_info=True
            )
            # Don't re-raise - background task failures shouldn't affect main flow
            
    except asyncio.CancelledError:
        # Task was cancelled - this is expected behavior, don't log as error
        logger.debug(f"🔄 Memory broadcast task cancelled for user_id={user_id}")
        raise  # Re-raise to properly handle cancellation
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Catch-all for any unexpected errors
        logger.error(
            f"❌ Unexpected error in _broadcast_memory_stored (user_id={user_id}, "
            f"conversation_id={conversation_id}): {e}",
            exc_info=True
        )
        # Don't re-raise - background task failures shouldn't affect main flow


# -------------------------------------------------------------------------
# STANDARD BOILERPLATE (Auth, Helpers, etc.)
# -------------------------------------------------------------------------


@app.delete("/api/conversations/{conversation_id}", response_class=JSONResponse)
@require_auth()
async def delete_conversation(request: Request, conversation_id: str):
    """Delete a conversation"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = engine.get_scoped_db(APP_SLUG)
    user_id = str(app_user["_id"])

    # Verify conversation belongs to user
    try:
        conversation = await db.conversations.find_one(
            {"_id": ObjectId(conversation_id), "user_id": user_id}
        )
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Delete conversation and all messages
    await db.conversations.delete_one({"_id": ObjectId(conversation_id)})
    await db.messages.delete_many({"conversation_id": conversation_id})

    return JSONResponse({"success": True})


# ============================================================================
# GraphRAG API Routes
# ============================================================================


@app.get("/api/graph/stats", response_class=JSONResponse)
@require_auth()
async def get_graph_stats(request: Request):
    """Get graph store statistics"""
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    graph_service = getattr(memory_service, "_graph_service", None) if memory_service else None
    
    if not graph_service:
        return JSONResponse(
            {"success": True, "enabled": False, "total_nodes": 0, "total_edges": 0}
        )

    stats = await graph_service.get_stats()
    return JSONResponse({"success": True, **stats})


@app.get("/api/graph/search", response_class=JSONResponse)
@require_auth()
async def graph_hybrid_search(
    request: Request,
    query: str,
    max_depth: int = 2,
    limit: int = 10,
):
    """Hybrid search combining vector similarity and graph traversal"""
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    graph_service = getattr(memory_service, "_graph_service", None) if memory_service else None
    
    if not graph_service:
        return JSONResponse(
            {"success": True, "query_type": "none", "entry_nodes": [], "graph_context": [], "total_nodes": 0}
        )

    user_id = str(app_user["_id"])
    
    # Use GraphRAG query classification
    query_type = graph_service.classify_query(query)
    
    # Route to appropriate search method
    if query_type == "local":
        results = await graph_service.local_search(query=query, user_id=user_id, max_depth=max_depth)
    elif query_type == "global":
        results = await graph_service.global_search(query=query, user_id=user_id, max_communities=limit)
    elif query_type == "drift":
        results = await graph_service.drift_search(query=query, user_id=user_id, max_depth=max_depth)
    else:
        results = await graph_service.hybrid_search(query=query, user_id=user_id, max_depth=max_depth, vector_limit=limit)
    
    # Ensure query_type is included
    if results and "query_type" not in results:
        results["query_type"] = query_type
    
    return JSONResponse({"success": True, **results})


@app.get("/api/graph/traverse", response_class=JSONResponse)
@require_auth()
async def graph_traverse(
    request: Request,
    node_id: str,
    max_depth: int = 2,
):
    """Traverse the graph from a specific node"""
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    graph_service = getattr(memory_service, "_graph_service", None) if memory_service else None
    
    if not graph_service:
        return JSONResponse({"success": True, "nodes": []})

    results = await graph_service.traverse(
        start_id=node_id,
        max_depth=max_depth
    )
    
    return JSONResponse({"success": True, "nodes": results})


@app.get("/api/graph/nodes", response_class=JSONResponse)
@require_auth()
async def list_graph_nodes(
    request: Request,
    node_type: Optional[str] = None,
    limit: int = 50,
):
    """List graph nodes for the user"""
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    graph_service = getattr(memory_service, "_graph_service", None) if memory_service else None
    
    if not graph_service:
        return JSONResponse({"success": True, "nodes": []})

    user_id = str(app_user["_id"])
    
    nodes = await graph_service.list_nodes(
        node_type=node_type,
        user_id=user_id,
        limit=limit
    )
    
    return JSONResponse({"success": True, "nodes": nodes})


# ============================================================================
# Memory API Routes
# ============================================================================


@app.get("/api/memories", response_class=JSONResponse)
@require_auth()
async def get_all_memories(request: Request, limit: int = 20):
    """Get all memories for the current user"""
    app_user = await get_current_app_user(request)

    if not app_user:
        response = JSONResponse({"error": "Authentication required"}, status_code=401)
        if getattr(request.state, "clear_invalid_session", False):
            cookie_name = f"{APP_SLUG}_session_{APP_SLUG}"
            response.delete_cookie(key=cookie_name)
        return response

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": True, "memories": [], "count": 0, "memory_service_available": False}
        )

    user_id = str(app_user["_id"])
    memory_collection = getattr(memory_service, 'collection_name', 'unknown')
    memory_db = getattr(memory_service, 'db_name', 'unknown')
    memory_service_id = id(memory_service)

    logger.info(
        f"🔍 [API get_all_memories] FETCHING MEMORIES - user_id={user_id}, limit={limit}, "
        f"collection={memory_collection}, db={memory_db}, service_id={memory_service_id}",
        extra={"user_id": user_id, "limit": limit},
    )
    memories = await memory_service.get_all(user_id=str(user_id), limit=limit)
    logger.info(
        f"🔍 RETRIEVED MEMORIES - user_id={user_id}, count={len(memories) if isinstance(memories, list) else 0}"
    )

    # Service returns our MongoDB structure: {id, memory, metadata, user_id, created_at, updated_at}
    # Use directly - no normalization needed
    normalized_memories = []
    if isinstance(memories, list):
        for mem in memories:
            if isinstance(mem, dict) and mem.get("memory") and mem.get("id"):
                normalized_memories.append({
                    "id": mem.get("id"),
                    "memory": mem.get("memory"),
                    "metadata": mem.get("metadata", {}) if isinstance(mem.get("metadata"), dict) else {}
                })

    logger.info(f"Returning {len(normalized_memories)} memories for user {user_id}")

    return JSONResponse(
        {"success": True, "memories": normalized_memories, "count": len(normalized_memories)}
    )


@app.get("/api/memories/search", response_class=JSONResponse)
@require_auth()
async def search_memories(
    request: Request,
    query: str,
    limit: int = 5,
    filters: Optional[str] = None,
    version: Optional[str] = None,
):
    """Search memories using semantic search.
    
    Uses MongoDB Atlas Vector Search for semantic search with advanced filtering.
    Supports filter syntax including metadata filtering.
    Example filters: {"metadata": {"bucket_id": "conversation:123"}}
    """
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": True, "results": [], "count": 0, "query": query, "filters": None}
        )

    user_id = str(app_user["_id"])

    # Parse filters if provided
    search_filters = None
    if filters:
        try:
            search_filters = json.loads(filters) if isinstance(filters, str) else filters
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="Invalid filters format. Expected JSON string."
            )

    results = await memory_service.search(
        query=query,
        user_id=user_id,
        limit=limit,
        filters=search_filters,
        version=version,
    )

    # Service returns our MongoDB structure - use directly
    normalized_results = []
    if isinstance(results, list):
        for res in results:
            if isinstance(res, dict):
                memory_text = res.get("memory") or ""
                normalized_results.append(
                    {
                        "memory": memory_text,
                        "id": res.get("id"),
                        "metadata": res.get("metadata", {}),
                        "score": res.get("score"),
                    }
                )
            elif isinstance(res, str):
                normalized_results.append({"memory": res})

        return JSONResponse(
            {
                "success": True,
                "results": normalized_results,
                "count": len(normalized_results),
                "query": query,
                "filters": search_filters,
                "version": version,
            }
        )


@app.get("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def get_memory(request: Request, memory_id: str):
    """Get a single memory by ID"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available", "memory": None},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    memory = await memory_service.get(memory_id=memory_id, user_id=user_id)

    if not memory:
        return JSONResponse(
            {"success": False, "error": "Memory not found", "memory": None},
            status_code=404,
        )

    # Service returns our MongoDB structure: {id, memory, metadata, user_id, ...}
    normalized_memory = {
        "memory": memory.get("memory", ""),
        "id": memory.get("id") or memory_id,
        "metadata": memory.get("metadata", {}),
        "user_id": memory.get("user_id", user_id),
    }

    return JSONResponse({"success": True, "memory": normalized_memory})


@app.post("/api/memories/inject", response_class=JSONResponse)
@require_auth()
async def inject_memory(request: Request):
    """
    Manually inject a memory without LLM inference.
    
    Supports advanced memory customization:
    - category: Memory category (biographical, preferences, temporal, relational, general)
    - importance: Manual importance score (0.1-1.0)
    - bucket_id: Bucket for grouping related memories
    - bucket_type: Type of bucket (general, conversation, file, category)
    - metadata: Additional custom metadata
    
    Example request:
    {
        "memory": "User prefers dark mode interfaces",
        "category": "preferences",
        "importance": 0.8,
        "bucket_id": "settings",
        "metadata": {"source": "manual", "verified": true}
    }
    """
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {
                "success": False,
                "error": "Memory service not available",
                "memory": None,
            },
            status_code=503,
        )

    user_id = str(app_user["_id"])

    try:
        body = await request.json()
        memory_content = body.get("memory")
        
        # Enhanced memory customization
        category = body.get("category", "general")  # Memory category
        importance = body.get("importance")  # Optional manual importance (0.1-1.0)
        bucket_id = body.get("bucket_id")  # Optional bucket grouping
        bucket_type = body.get("bucket_type", "general")  # Bucket type
        metadata = body.get("metadata", {})  # Additional metadata

        if not memory_content:
            raise HTTPException(
                status_code=400, detail="Missing 'memory' field in request body"
            )

        # Validate category
        valid_categories = [
            "biographical", "preferences", "temporal", 
            "relational", "general", "work", "health", "finance"
        ]
        if category not in valid_categories:
            category = "general"

        # Validate importance
        if importance is not None:
            try:
                importance = float(importance)
                importance = max(0.1, min(1.0, importance))
            except (ValueError, TypeError):
                importance = None

        # Build enhanced metadata
        if not isinstance(metadata, dict):
            metadata = {}
        
        metadata["category"] = category
        metadata["source"] = metadata.get("source", "manual_injection")
        if importance is not None:
            metadata["manual_importance"] = importance

        # Auto-construct bucket_id from category if not provided
        if not bucket_id and category != "general":
            bucket_id = f"category:{category}:{user_id}"

        logger.info(
            f"💉 Injecting memory for user {user_id}",
            extra={
                "user_id": user_id,
                "category": category,
                "importance": importance,
                "bucket_id": bucket_id,
                "bucket_type": bucket_type,
            },
        )

        # Inject memory without inference
        injected_memory = await memory_service.inject(
            memory=memory_content,
            user_id=user_id,
            metadata=metadata,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )

        if not injected_memory:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Failed to inject memory",
                    "memory": None,
                },
                status_code=500,
            )

        # Normalize memory format with enhanced fields
        if isinstance(injected_memory, dict):
            normalized_memory = {
                "memory": injected_memory.get("memory", ""),
                "id": injected_memory.get("id"),
                "category": category,
                "importance": importance or injected_memory.get("importance", 0.5),
                "metadata": injected_memory.get("metadata", {}),
                "user_id": injected_memory.get("user_id", user_id),
            }
        else:
            normalized_memory = {
                "memory": str(injected_memory),
                "id": None,
                "category": category,
            }

        logger.info(
            f"✅ Successfully injected memory with id={normalized_memory.get('id')} "
            f"category={category} for user {user_id}"
        )

        return JSONResponse({"success": True, "memory": normalized_memory})
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.exception(f"Error injecting memory: {e}")
        return JSONResponse(
            {
                "success": False,
                "error": f"Failed to inject memory: {str(e)}",
                "memory": None,
            },
            status_code=500,
        )


@app.get("/api/memories/categories", response_class=JSONResponse)
@require_auth()
async def get_memory_categories(request: Request):
    """
    Get available memory categories for the UI.
    
    Returns standard categories and any custom categories from configuration.
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Standard categories with descriptions
    categories = [
        {"id": "biographical", "name": "Biographical", "icon": "👤", "description": "Personal info: name, age, occupation, family, location"},
        {"id": "preferences", "name": "Preferences", "icon": "❤️", "description": "Likes, dislikes, preferences, favorites"},
        {"id": "temporal", "name": "Temporal", "icon": "📅", "description": "Current projects, deadlines, short-term goals"},
        {"id": "relational", "name": "Relational", "icon": "👥", "description": "Relationships, feelings about others"},
        {"id": "work", "name": "Work", "icon": "💼", "description": "Job-related information and projects"},
        {"id": "health", "name": "Health", "icon": "🏥", "description": "Health conditions, medications, fitness"},
        {"id": "finance", "name": "Finance", "icon": "💰", "description": "Financial preferences and goals"},
        {"id": "general", "name": "General", "icon": "📝", "description": "Other facts and information"},
    ]

    return JSONResponse({
        "success": True,
        "categories": categories,
    })


@app.put("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def update_memory(request: Request, memory_id: str):
    """Update a memory by ID"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available", "memory": None},
            status_code=503,
        )

    user_id = str(app_user["_id"])

    body = await request.json()
    data = body.get("data")

    if not data:
        raise HTTPException(status_code=400, detail="Missing 'data' field in request body")

    logger.info(
        f"🔄 Updating memory {memory_id} for user {user_id}",
        extra={
            "memory_id": memory_id,
            "user_id": user_id,
        },
    )

    try:
        # Update memory content - automatically regenerates embeddings if text changes
        # Metadata is preserved and merged with existing metadata
        updated_memory = await memory_service.update(memory_id=memory_id, memory=data, user_id=user_id)

        logger.debug(
            f"Memory update result for {memory_id}: {type(updated_memory)}, "
            f"is_none={updated_memory is None}"
        )

        # Handle case where memory is not found (update returns None)
        if updated_memory is None:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Memory {memory_id} not found or could not be updated",
                    "memory": None,
                },
                status_code=404,
            )

        # Service returns our MongoDB structure: {id, memory, metadata, user_id, ...}
        normalized_memory = {
            "memory": updated_memory.get("memory", ""),
            "id": updated_memory.get("id") or memory_id,
            "metadata": updated_memory.get("metadata", {}),
            "user_id": updated_memory.get("user_id", user_id),
        }

        return JSONResponse({"success": True, "memory": normalized_memory})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.exception(f"Error updating memory {memory_id}: {e}")
        return JSONResponse(
            {
                "success": False,
                "error": f"Failed to update memory: {str(e)}",
                "memory": None,
            },
            status_code=500,
        )


@app.delete("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def delete_memory(request: Request, memory_id: str):
    """Delete a single memory by ID"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {
                "success": False,
                "error": "Memory service not available",
                "message": "Memory service not available",
            },
            status_code=503,
        )

    user_id = str(app_user["_id"])
    success = await memory_service.delete(memory_id=memory_id, user_id=user_id)

    return JSONResponse(
        {
            "success": success,
            "message": (
                f"Memory {memory_id} deleted successfully" if success else "Failed to delete memory"
            ),
        }
    )


@app.delete("/api/memories", response_class=JSONResponse)
@require_auth()
async def delete_all_memories(request: Request):
    """Delete all memories for the current user"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": True, "memories": [], "count": 0, "memory_service_available": False}
        )

    user_id = str(app_user["_id"])

    all_memories = await memory_service.get_all(user_id=user_id, limit=1000)
    memory_count = len(all_memories) if isinstance(all_memories, list) else 0

    # Hard delete for user-initiated deletion (GDPR compliant)
    success = await memory_service.delete_all(
        user_id=user_id,
        hard_delete=True
    )

    return JSONResponse(
        {
            "success": success,
            "deleted_count": memory_count,
            "message": (
                f"Deleted {memory_count} memories for user {user_id}"
                if success
                else "Failed to delete memories"
            ),
        }
    )


@app.get("/api/memories/stats", response_class=JSONResponse)
@require_auth()
async def get_memory_stats(request: Request):
    """Get memory statistics for the current user"""
    default_stats = {
        "success": True,
        "stats": {
            "total_memories": 0,
            "memory_enabled": False,
            "inference_enabled": False,
            "graph_enabled": False,
            "conversation_memories": 0,
            "metadata_breakdown": {},
        },
    }

    try:
        if not engine.initialized:
            logger.warning("Engine not initialized in get_memory_stats")
            return JSONResponse(default_stats, status_code=200)

        try:
            app_user = await get_current_app_user(request)
        except (AttributeError, RuntimeError, ValueError, TypeError, KeyError) as user_error:
            logger.warning(f"Failed to get app user in stats: {user_error}")
            return JSONResponse(default_stats, status_code=200)

        if not app_user:
            return JSONResponse(default_stats, status_code=200)

        try:
            memory_service = engine.get_memory_service(APP_SLUG)
        except (AttributeError, RuntimeError, ValueError, KeyError) as service_error:
            logger.warning(f"Failed to get memory service in stats: {service_error}")
            return JSONResponse(default_stats, status_code=200)

        if not memory_service:
            return JSONResponse(default_stats, status_code=200)

        user_id = str(app_user.get("_id", "")) if app_user else ""
        if not user_id:
            logger.warning("get_memory_stats: No user_id available")
            return JSONResponse(default_stats, status_code=200)

        logger.info(f"📊 FETCHING STATS - user_id={user_id}")

        try:
            all_memories = await asyncio.wait_for(
                memory_service.get_all(user_id=str(user_id), limit=1000),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting memories for stats (user: {user_id})")
            return JSONResponse(default_stats, status_code=200)
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError) as mem_error:
            logger.warning(f"Failed to get memories for stats: {mem_error}")
            return JSONResponse(default_stats, status_code=200)

        try:
            memory_count = len(all_memories) if isinstance(all_memories, list) else 0

            metadata_breakdown = {}
            conversation_memories = 0
            for mem in all_memories:
                try:
                    if isinstance(mem, dict):
                        metadata = mem.get("metadata", {})
                        if isinstance(metadata, dict):
                            source = metadata.get("source", "unknown")
                            metadata_breakdown[source] = metadata_breakdown.get(source, 0) + 1
                            if metadata.get("conversation_id"):
                                conversation_memories += 1
                except (KeyError, TypeError, AttributeError):
                    continue

            inference_enabled = False
            try:
                inference_enabled = getattr(memory_service, "infer", False)
            except AttributeError:
                pass

            return JSONResponse(
                {
                    "success": True,
                    "stats": {
                        "total_memories": memory_count,
                        "memory_enabled": True,
                        "inference_enabled": inference_enabled,
                        "graph_enabled": False,  # Not supported in CognitiveMemoryService
                        "conversation_memories": conversation_memories,
                        "metadata_breakdown": metadata_breakdown,
                    },
                },
                status_code=200,
            )
        except (ValueError, TypeError, AttributeError, KeyError) as process_error:
            logger.warning(f"Failed to process memory stats: {process_error}")
            return JSONResponse(default_stats, status_code=200)

    except (AttributeError, RuntimeError, ValueError, TypeError, KeyError) as e:
        logger.error(f"Unexpected error in get_memory_stats: {e}", exc_info=True)
        return JSONResponse(default_stats, status_code=200)


# ============================================================================
# Cognitive Memory API Routes (Advanced Features)
# ============================================================================


@app.get("/api/memories/analytics", response_class=JSONResponse)
@require_auth()
async def get_memory_analytics(request: Request):
    """
    Get cognitive memory analytics for the current user.
    
    Returns metrics useful for understanding memory usage and decay patterns:
    - active_memories: Number of active memories
    - cold_storage_memories: Number of pruned memories in cold storage
    - average_strength: Average retrieval strength (Ebbinghaus decay)
    - average_stability: Average stability (half-life in hours)
    - average_emotion: Average emotional intensity
    - weak_memories: Count of memories with strength < 0.3
    - strong_memories: Count of memories with strength > 0.7
    - categories: Breakdown by memory category
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        # Check if analytics method is available
        if not hasattr(memory_service, 'get_memory_analytics'):
            return JSONResponse({
                "success": False,
                "error": "Analytics not available for this memory provider",
            }, status_code=501)
        
        analytics = await memory_service.get_memory_analytics(
            user_id=user_id,
        )
        
        return JSONResponse({
            "success": True,
            "analytics": analytics,
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Analytics not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get memory analytics: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.get("/api/memories/cold-storage", response_class=JSONResponse)
@require_auth()
async def get_cold_storage(request: Request, limit: int = 50):
    """
    Get memories from cold storage (pruned/inactive memories).
    
    Cold storage contains memories that have been soft-deleted, providing:
    - Audit trail for what was forgotten
    - Analytics on user memory patterns
    - Recovery capability if needed
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        if not hasattr(memory_service, 'get_cold_storage'):
            return JSONResponse({
                "success": False,
                "error": "Cold storage not available for this memory provider",
            }, status_code=501)
        
        cold_memories = await memory_service.get_cold_storage(
            user_id=user_id,
            limit=limit,
            include_reason=True,
        )
        
        return JSONResponse({
            "success": True,
            "memories": cold_memories,
            "count": len(cold_memories),
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Cold storage not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get cold storage: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/{memory_id}/restore", response_class=JSONResponse)
@require_auth()
async def restore_from_cold_storage(request: Request, memory_id: str):
    """
    Restore a memory from cold storage to active status.
    
    This allows recovery of accidentally pruned memories or memories
    that the user wants to bring back to active use.
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        if not hasattr(memory_service, 'restore_from_cold_storage'):
            return JSONResponse({
                "success": False,
                "error": "Restore not available for this memory provider",
            }, status_code=501)
        
        restored_memory = await memory_service.restore_from_cold_storage(
            memory_id=memory_id,
            user_id=user_id,
        )
        
        if restored_memory:
            return JSONResponse({
                "success": True,
                "memory": restored_memory,
                "message": f"Memory {memory_id} restored successfully",
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "Memory not found in cold storage",
            }, status_code=404)
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Restore not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to restore memory: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/check-conflict", response_class=JSONResponse)
@require_auth()
async def check_knowledge_conflict(request: Request):
    """
    Check if new information conflicts with existing knowledge.
    
    This implements the "Integrity Layer" that prevents the AI from developing
    "digital dementia" - holding contradictory facts as equally true.
    
    Request body:
    {
        "fact": "User is allergic to penicillin"
    }
    
    Response:
    {
        "success": true,
        "has_conflict": false,
        "conflict_description": null
    }
    
    Or if conflict detected:
    {
        "success": true,
        "has_conflict": true,
        "conflict_description": "This conflicts with existing knowledge that user takes penicillin daily."
    }
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        body = await request.json()
        new_fact = body.get("fact")
        
        if not new_fact:
            raise HTTPException(status_code=400, detail="Missing 'fact' field in request body")
        
        conflict = await memory_service.detect_knowledge_conflict(
            user_id=user_id,
            new_fact=new_fact,
        )
        
        return JSONResponse({
            "success": True,
            "has_conflict": conflict is not None,
            "conflict_description": conflict,
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Conflict detection not supported by this memory provider",
        }, status_code=501)
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to check conflict: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/prune", response_class=JSONResponse)
@require_auth()
async def trigger_pruning(request: Request):
    """
    Manually trigger memory pruning for the current user.
    
    This soft-deletes the weakest memories based on retrieval strength,
    moving them to cold storage for potential recovery.
    
    Request body (optional):
    {
        "max_capacity": 100,  // Override max capacity
        "reason": "manual_cleanup"  // Custom reason for audit trail
    }
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        body = {}
        try:
            body = await request.json()
        except (ValueError, TypeError) as exc:
            logger.debug("No JSON body provided (ok for this endpoint): %s", exc)
        
        max_capacity = body.get("max_capacity")
        reason = body.get("reason", "manual_trigger")
        
        if not hasattr(memory_service, 'prune_memories'):
            return JSONResponse({
                "success": False,
                "error": "Pruning not available for this memory provider",
            }, status_code=501)
        
        pruned_count = await memory_service.prune_memories(
            user_id=user_id,
            max_capacity=max_capacity,
            reason=reason,
        )
        
        return JSONResponse({
            "success": True,
            "pruned_count": pruned_count,
            "message": f"Pruned {pruned_count} memories to cold storage",
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Pruning not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to prune memories: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# ============================================================================
# REFLECTION ENDPOINTS
# ============================================================================


@app.get("/api/reflections", response_class=JSONResponse)
@require_auth()
async def get_reflections(request: Request, limit: int = 20):
    """
    Get memory reflections (consolidated summaries) for the current user.
    
    Reflections are periodic consolidations of atomic memories into narrative summaries.
    """
    app_user = await get_current_app_user(request)
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(app_user["_id"])
    
    try:
        # Check if reflection service is available
        reflection_service = getattr(memory_service, 'reflection_service', None)
        if not reflection_service:
            return JSONResponse({
                "success": True,
                "enabled": False,
                "reflections": [],
                "message": "Reflection service not enabled for this app",
            })
        
        # Get reflections collection directly
        reflections_col = getattr(reflection_service, 'reflections_collection', None)
        if not reflections_col:
            return JSONResponse({
                "success": True,
                "enabled": True,
                "reflections": [],
            })
        
        reflections = list(
            reflections_col.find(
                {"user_id": user_id},
                sort=[("created_at", -1)],
                limit=limit,
            )
        )
        
        # Convert ObjectId to string
        for r in reflections:
            r["_id"] = str(r["_id"])
        
        return JSONResponse({
            "success": True,
            "enabled": True,
            "reflections": reflections,
            "count": len(reflections),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get reflections: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# Register WebSocket Message Handler
def register_websocket_message_handlers():
    async def handle_message(ws, msg): 
        pass
    register_message_handler(APP_SLUG, "realtime", handle_message)
