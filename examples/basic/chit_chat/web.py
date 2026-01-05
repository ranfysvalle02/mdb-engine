#!/usr/bin/env python3
"""
Conversations - AI Chat Application

A simple, beautiful conversation app with isolated context per user.
Each user can have multiple conversations using the abstracted LLM service.

This example demonstrates the recommended `engine.create_app()` pattern
for FastAPI integration with MDB-Engine.
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from dotenv import load_dotenv
from fastapi import Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from openai import AzureOpenAI

from mdb_engine import MongoDBEngine
from mdb_engine.auth.decorators import rate_limit_auth, require_auth, token_security
from mdb_engine.auth.integration import get_auth_config
from mdb_engine.auth.users import create_app_session, get_app_user
from mdb_engine.auth.utils import login_user, logout_user, register_user
from mdb_engine.routing.websockets import broadcast_to_app, register_message_handler

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

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
SECRET_KEY = os.environ.get(
    "APP_SECRET_KEY", "conversations_demo_secret_key_change_in_production"
)

# Initialize the MongoDB Engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI", "mongodb://mongodb:27017/"),
    db_name=os.getenv("MONGO_DB_NAME", "conversations_db"),
)

# Create FastAPI app with automatic lifecycle management
# This automatically handles:
# - Engine initialization and shutdown
# - Manifest loading and validation
# - Auth setup from manifest (CORS, sessions, etc.)
# - WebSocket route registration
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="Conversations",
    description="AI Chat Application",
    version="1.0.0",
)


# Additional setup that runs after engine is initialized
@app.on_event("startup")
async def additional_startup():
    """Additional startup tasks beyond what create_app() handles."""
    # Set global engine for embedding dependency injection
    try:
        from mdb_engine.embeddings.dependencies import set_global_engine

        set_global_engine(engine, app_slug=APP_SLUG)
    except ImportError:
        logger.debug("Embedding dependencies not available")

    # Initialize embedding service if configured in manifest.json
    try:
        from mdb_engine.embeddings import get_embedding_service

        app_config = engine.get_app(APP_SLUG)
        embedding_config = app_config.get("embedding_config", {}) if app_config else {}
        if embedding_config:
            get_embedding_service(config=embedding_config)
            logger.info("EmbeddingService initialized from manifest.json")
        else:
            logger.debug(
                "No embedding_config found in manifest.json - embedding service not initialized"
            )
    except ImportError:
        logger.debug("EmbeddingService dependencies not available")

    # Register WebSocket message handlers
    register_websocket_message_handlers()

    logger.info("Conversations application ready!")


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
            db = engine.get_scoped_db(APP_SLUG)
            # Simple ping to verify connection
            await db.command("ping")
            health_status["database"] = "connected"
        except Exception as e:
            health_status["status"] = "degraded"
            health_status["database"] = f"error: {str(e)}"
    else:
        health_status["status"] = "starting"
        health_status["database"] = "not_connected"

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


def get_db():
    """Get the scoped database."""
    if not engine.initialized:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return engine.get_scoped_db(APP_SLUG)


async def get_current_app_user(request: Request):
    """Helper to get current app user for conversations app."""
    db = get_db()

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
    """Handle login"""
    db = get_db()

    # Get auth config from manifest
    auth_config = await get_auth_config(APP_SLUG, engine)
    token_config = auth_config.get("token_management", {})

    # Use login_user utility
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

        # Create app-specific session
        user = result["user"]
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

        return response
    else:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": result.get("error", "Login failed")},
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
    """Handle registration"""
    db = get_db()

    # Get auth config from manifest
    auth_config = await get_auth_config(APP_SLUG, engine)
    token_config = auth_config.get("token_management", {})

    # Use register_user utility
    result = await register_user(
        request=request,
        email=email,
        password=password,
        db=db,
        config=token_config,
        extra_data={
            "full_name": full_name,
        },
        redirect_url="/conversations",
    )

    if result["success"]:
        response = result["response"]

        # Create app-specific session
        user = result["user"]
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

        return response
    else:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": result.get("error", "Registration failed")},
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@app.get("/logout")
async def logout(request: Request):
    """Handle logout"""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    # Use logout_user utility
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

    db = get_db()
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

    db = get_db()
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

    return templates.TemplateResponse(
        request,
        "conversation.html",
        {
            "user": app_user,
            "conversation": conversation,
            "messages": messages,
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

    db = get_db()
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


@app.post("/api/conversations/{conversation_id}/messages", response_class=JSONResponse)
@require_auth()
async def send_message(request: Request, conversation_id: str, message: str = Form(...)):
    """Send a message in a conversation"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = get_db()
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

    # Save user message
    user_message = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": "user",
        "content": message,
        "created_at": datetime.utcnow(),
    }
    await db.messages.insert_one(user_message)

    # Get conversation history
    history = (
        await db.messages.find({"conversation_id": conversation_id})
        .sort("created_at", 1)
        .to_list(length=100)
    )

    # Get Memory service from engine (if available)
    memory_service = engine.get_memory_service(APP_SLUG)

    # Initialize Azure OpenAI client
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    if not endpoint or not key:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables.",
        )

    client = AzureOpenAI(api_version=api_version, azure_endpoint=endpoint, api_key=key)

    # Retrieve relevant memories for context (if memory service is available)
    context_memories = []
    memory_search_details = []
    if memory_service:
        relevant_memories = memory_service.search(query=message, user_id=user_id, limit=3)
        if relevant_memories:
            context_memories = []
            for m in relevant_memories:
                if isinstance(m, dict):
                    memory_text = m.get("memory") or m.get("data", {}).get("memory", "")
                    if memory_text:
                        context_memories.append(memory_text)
                        memory_search_details.append(
                            {
                                "memory": memory_text,
                                "id": m.get("id") or m.get("_id"),
                                "score": m.get("score"),
                                "metadata": m.get("metadata", {}),
                            }
                        )
                elif isinstance(m, str):
                    context_memories.append(m)
                    memory_search_details.append({"memory": m, "score": None})

    # Format messages for LLM service
    messages = []

    # Add relevant memories as system context if available
    if context_memories:
        memory_context = "Relevant context from past conversations:\n" + "\n".join(
            f"- {m}" for m in context_memories
        )
        messages.append(
            {
                "role": "system",
                "content": f"You are a helpful AI assistant. {memory_context}\n\nUse this context to provide more personalized and relevant responses.",
            }
        )

    # Add conversation history
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    # Add current user message
    messages.append({"role": "user", "content": message})

    # Get AI response using Azure OpenAI client
    completion = await asyncio.to_thread(
        client.chat.completions.create, model=deployment_name, messages=messages, max_tokens=1000
    )
    ai_response = completion.choices[0].message.content

    # Save AI message
    ai_message = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": "assistant",
        "content": ai_response,
        "created_at": datetime.utcnow(),
    }
    await db.messages.insert_one(ai_message)

    # Store conversation turn in memory (AFTER AI response is generated)
    if memory_service:
        memory_messages = []

        # Convert full conversation history to message format
        for msg in history:
            memory_messages.append(
                {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            )

        # Add current user message
        memory_messages.append({"role": "user", "content": message})

        # Add assistant response (CRITICAL - mem0 needs this to extract memories)
        memory_messages.append({"role": "assistant", "content": ai_response})

        # Store in memory asynchronously (fire and forget)
        async def store_memory():
            message_preview = []
            for i, msg in enumerate(memory_messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                preview = content[:100] + "..." if len(content) > 100 else content
                message_preview.append(f"{i+1}. {role}: {preview}")

            logger.info(
                f"🔵 STORING MEMORY - user_id={user_id}, conversation_id={conversation_id}, messages={len(memory_messages)}",
                extra={
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "messages_count": len(memory_messages),
                },
            )

            result = await asyncio.to_thread(
                memory_service.add,
                messages=memory_messages,
                user_id=str(user_id),
                metadata={"conversation_id": conversation_id, "source": "conversations_app"},
            )

            if result and isinstance(result, list) and len(result) > 0:
                logger.info(
                    f"✅ Mem0 extracted {len(result)} memories from conversation for user_id={user_id}"
                )

                memory_texts = []
                for m in result:
                    if isinstance(m, dict):
                        memory_text = (
                            m.get("memory")
                            or m.get("data", {}).get("memory", "")
                            or m.get("text", "")
                        )
                        if memory_text:
                            memory_texts.append(
                                {
                                    "id": m.get("id") or m.get("_id"),
                                    "memory": memory_text,
                                    "metadata": m.get("metadata", {}),
                                }
                            )

                await asyncio.sleep(0.3)

                fresh_memories = await asyncio.to_thread(
                    memory_service.get_all, user_id=str(user_id), limit=50
                )

                total_memories = len(fresh_memories) if isinstance(fresh_memories, list) else 0

                await broadcast_to_app(
                    APP_SLUG,
                    {
                        "type": "memory_stored",
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                        "memory_count": len(result),
                        "total_memories": total_memories,
                        "new_memories": memory_texts,
                        "all_memories": (
                            fresh_memories[:20] if isinstance(fresh_memories, list) else []
                        ),
                        "stats": {
                            "total_memories": total_memories,
                            "new_count": len(result),
                            "memory_enabled": True,
                            "inference_enabled": True,
                        },
                        "message": f"Mem0 extracted {len(result)} memories from conversation",
                        "action": "refresh_memories",
                    },
                    user_id=user_id,
                )
                logger.info(
                    f"📡 Broadcasted memory update via WebSocket: {len(result)} new memories, {total_memories} total"
                )
            else:
                logger.warning(
                    f"⚠️ Mem0 returned 0 memories for user_id={user_id}. This may be normal if the conversation doesn't contain extractable facts."
                )

            return result

        asyncio.create_task(store_memory())

    # Update conversation timestamp
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)}, {"$set": {"updated_at": datetime.utcnow()}}
    )

    # Update conversation title if it's still "New Conversation"
    if conversation.get("title") == "New Conversation" and len(message) > 0:
        title = message[:50] + ("..." if len(message) > 50 else "")
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)}, {"$set": {"title": title}}
        )

    # Prepare response with memory info
    response_data = {
        "success": True,
        "message": {
            "role": "assistant",
            "content": ai_response,
            "created_at": ai_message["created_at"].isoformat(),
        },
    }

    # Add memory context info if available
    if memory_service:
        response_data["mem0_operations"] = {
            "memory_enabled": True,
            "search_performed": len(context_memories) > 0,
            "memories_found": len(context_memories),
            "storage_scheduled": True,
            "inference_enabled": (
                memory_service.infer if hasattr(memory_service, "infer") else False
            ),
            "graph_enabled": (
                memory_service.enable_graph if hasattr(memory_service, "enable_graph") else False
            ),
        }

        if context_memories:
            response_data["memory_context"] = {
                "used_memories": len(context_memories),
                "memories": context_memories[:3],
                "search_details": memory_search_details[:3],
            }

            await broadcast_to_app(
                APP_SLUG,
                {
                    "type": "memory_search",
                    "user_id": user_id,
                    "query": message,
                    "memories_found": len(context_memories),
                    "search_details": memory_search_details[:3],
                },
                user_id=user_id,
            )

    return JSONResponse(response_data)


@app.delete("/api/conversations/{conversation_id}", response_class=JSONResponse)
@require_auth()
async def delete_conversation(request: Request, conversation_id: str):
    """Delete a conversation"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = get_db()
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
# Memory API Routes (Mem0 Showcase)
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

    logger.info(
        f"🔍 FETCHING MEMORIES - user_id={user_id}, limit={limit}",
        extra={"user_id": user_id, "limit": limit},
    )
    memories = await asyncio.to_thread(memory_service.get_all, user_id=str(user_id), limit=limit)
    logger.info(
        f"🔍 RETRIEVED MEMORIES - user_id={user_id}, count={len(memories) if isinstance(memories, list) else 0}"
    )

    # Normalize memory format for frontend
    normalized_memories = []
    if isinstance(memories, list):
        for mem in memories:
            if isinstance(mem, dict):
                memory_text = (
                    mem.get("memory")
                    or mem.get("text")
                    or (
                        mem.get("data", {}).get("memory")
                        if isinstance(mem.get("data"), dict)
                        else None
                    )
                    or str(mem)
                )
                memory_id = mem.get("id") or mem.get("_id") or None
                metadata = mem.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}

                if memory_text:
                    normalized_memories.append(
                        {"memory": memory_text, "id": memory_id, "metadata": metadata}
                    )
            elif isinstance(mem, str):
                normalized_memories.append({"memory": mem, "id": None, "metadata": {}})

    logger.info(f"Returning {len(normalized_memories)} normalized memories for user {user_id}")

    return JSONResponse(
        {"success": True, "memories": normalized_memories, "count": len(normalized_memories)}
    )


@app.get("/api/memories/search", response_class=JSONResponse)
@require_auth()
async def search_memories(
    request: Request, query: str, limit: int = 5, metadata: Optional[str] = None
):
    """Search memories for the current user with optional metadata filtering"""
    app_user = await get_current_app_user(request)

    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    memory_service = engine.get_memory_service(APP_SLUG)
    if not memory_service:
        return JSONResponse(
            {"success": True, "results": [], "count": 0, "query": query, "metadata_filter": None}
        )

    user_id = str(app_user["_id"])

    # Parse metadata filter if provided
    metadata_filter = None
    if metadata:
        try:
            import json

            metadata_filter = json.loads(metadata) if isinstance(metadata, str) else metadata
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400, detail="Invalid metadata format. Expected JSON string."
            )

    results = await asyncio.to_thread(
        memory_service.search, query=query, user_id=user_id, limit=limit, metadata=metadata_filter
    )

    # Normalize results format
    normalized_results = []
    if isinstance(results, list):
        for res in results:
            if isinstance(res, dict):
                memory_text = (
                    res.get("memory")
                    or res.get("data", {}).get("memory")
                    or res.get("text")
                    or str(res)
                )
                normalized_results.append(
                    {
                        "memory": memory_text,
                        "id": res.get("id") or res.get("_id"),
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
            "metadata_filter": metadata_filter,
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
    memory = await asyncio.to_thread(memory_service.get, memory_id=memory_id, user_id=user_id)

    # Normalize memory format
    if isinstance(memory, dict):
        memory_text = (
            memory.get("memory")
            or memory.get("data", {}).get("memory")
            or memory.get("text")
            or str(memory)
        )
        normalized_memory = {
            "memory": memory_text,
            "id": memory.get("id") or memory.get("_id") or memory_id,
            "metadata": memory.get("metadata", {}),
            "user_id": memory.get("user_id", user_id),
        }
    else:
        normalized_memory = {"memory": str(memory), "id": memory_id}

    return JSONResponse({"success": True, "memory": normalized_memory})


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
    metadata = body.get("metadata")

    if not data:
        raise HTTPException(status_code=400, detail="Missing 'data' field in request body")

    updated_memory = await asyncio.to_thread(
        memory_service.update, memory_id=memory_id, data=data, user_id=user_id, metadata=metadata
    )

    # Normalize memory format
    if isinstance(updated_memory, dict):
        memory_text = (
            updated_memory.get("memory")
            or updated_memory.get("data", {}).get("memory")
            or updated_memory.get("text")
            or str(updated_memory)
        )
        normalized_memory = {
            "memory": memory_text,
            "id": updated_memory.get("id") or updated_memory.get("_id") or memory_id,
            "metadata": updated_memory.get("metadata", metadata or {}),
            "user_id": updated_memory.get("user_id", user_id),
        }
    else:
        normalized_memory = {"memory": str(updated_memory), "id": memory_id}

    return JSONResponse({"success": True, "memory": normalized_memory})


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
    success = await asyncio.to_thread(memory_service.delete, memory_id=memory_id, user_id=user_id)

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

    all_memories = await asyncio.to_thread(memory_service.get_all, user_id=user_id, limit=1000)
    memory_count = len(all_memories) if isinstance(all_memories, list) else 0

    success = await asyncio.to_thread(memory_service.delete_all, user_id=user_id)

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
                asyncio.to_thread(memory_service.get_all, user_id=str(user_id), limit=1000),
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
            graph_enabled = False
            try:
                inference_enabled = getattr(memory_service, "infer", False)
                graph_enabled = getattr(memory_service, "enable_graph", False)
            except AttributeError:
                pass

            return JSONResponse(
                {
                    "success": True,
                    "stats": {
                        "total_memories": memory_count,
                        "memory_enabled": True,
                        "inference_enabled": inference_enabled,
                        "graph_enabled": graph_enabled,
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
# WebSocket Handlers
# ============================================================================


def register_websocket_message_handlers():
    """Register WebSocket message handlers"""

    async def handle_message(websocket, message: Dict[str, Any]):
        """Handle incoming WebSocket messages"""
        # Messages are broadcast via the send_message endpoint
        # This handler can process incoming client messages if needed
        pass

    register_message_handler(APP_SLUG, "realtime", handle_message)
