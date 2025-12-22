#!/usr/bin/env python3
"""
Conversations - AI Chat Application

A simple, beautiful conversation app with isolated context per user.
Each user can have multiple conversations using the abstracted LLM service.
"""
import os
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson.objectid import ObjectId

from fastapi import FastAPI, Request, HTTPException, status, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from mdb_engine import RuntimeEngine
from mdb_engine.auth.sub_auth import (
    get_app_sub_user,
    create_app_session,
)
from mdb_engine.auth.utils import (
    login_user,
    register_user,
    logout_user,
)
from mdb_engine.auth.decorators import (
    require_auth,
    rate_limit_auth,
    token_security,
)
from mdb_engine.auth.integration import (
    setup_auth_from_manifest,
    get_auth_config,
)
from mdb_engine.routing.websockets import broadcast_to_app, register_message_handler
from openai import AzureOpenAI
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Memory service is imported lazily when needed to avoid permission issues

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Conversations",
    description="AI Chat Application",
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

# Templates directory
templates_dir = Path("/app/templates") if Path("/app/templates").exists() else Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Global engine instance
engine: Optional[RuntimeEngine] = None
db = None

# Secret key for JWT
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "conversations_demo_secret_key_change_in_production")


@app.on_event("startup")
async def startup_event():
    """Initialize the runtime engine on startup."""
    global engine, db
    
    logger.info("Starting Conversations Application...")
    
    # Get MongoDB connection from environment
    # Default matches docker-compose.yml: mongodb service without authentication
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb://mongodb:27017/"
    )
    db_name = os.getenv("MONGO_DB_NAME", "conversations_db")
    
    logger.info(f"Connecting to MongoDB: {mongo_uri.replace('://', '://***@') if '@' in mongo_uri else mongo_uri} (db: {db_name})")
    
    # Initialize the runtime engine
    engine = RuntimeEngine(
        mongo_uri=mongo_uri,
        db_name=db_name
    )
    
    # Connect to MongoDB
    await engine.initialize()
    logger.info("Engine initialized successfully")
    
    # Load and register the app manifest
    manifest_path = Path(__file__).parent / "manifest.json"
    if manifest_path.exists():
        manifest = await engine.load_manifest(manifest_path)
        success = await engine.register_app(manifest, create_indexes=True)
        if success:
            logger.info(f"App '{manifest['slug']}' registered successfully")
        else:
            logger.warning("Failed to register app")
    
    # Set up enhanced auth system from manifest.json
    await setup_auth_from_manifest(app, engine, "conversations")
    
    # Set global engine for embedding dependency injection
    from mdb_engine.embeddings.dependencies import set_global_engine
    set_global_engine(engine, app_slug="conversations")
    
    # Initialize embedding service if configured in manifest.json
    try:
        from mdb_engine.embeddings import get_embedding_service
        app_config = engine.get_app("conversations")
        embedding_config = app_config.get("embedding_config", {}) if app_config else {}
        if embedding_config:
            embedding_service = get_embedding_service(config=embedding_config)
            logger.info("EmbeddingService initialized from manifest.json")
        else:
            logger.debug("No embedding_config found in manifest.json - embedding service not initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize EmbeddingService: {e}", exc_info=True)
    
    # Mark app as started
    app.state._started = True
    
    # Get scoped database
    db = engine.get_scoped_db("conversations")
    
    # Register WebSocket message handlers
    register_websocket_message_handlers()
    
    # Register WebSocket routes from manifest
    if engine:
        engine.register_websocket_routes(app, "conversations")
    
    logger.info("Conversations application ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global engine
    if engine:
        await engine.shutdown()
        logger.info("Cleaned up and shut down")


# Global exception handler to catch any unhandled exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all exceptions and return proper JSON responses"""
    # Only handle stats endpoint specially - let others use default behavior
    if request.url.path == "/api/memories/stats":
        logger.error(f"Global exception handler caught error in stats endpoint: {exc}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": f"Internal error: {str(exc)}",
            "error_type": type(exc).__name__,
            "stats": {
                "total_memories": 0,
                "memory_enabled": False,
                "inference_enabled": False,
                "graph_enabled": False,
                "conversation_memories": 0,
                "metadata_breakdown": {}
            }
        }, status_code=200)
    
    # For other endpoints, use default FastAPI behavior
    from fastapi.responses import JSONResponse
    if isinstance(exc, HTTPException):
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code
        )
    # For unexpected exceptions, return 500
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        {"detail": "Internal server error"},
        status_code=500
    )


def get_db():
    """Get the scoped database."""
    global engine
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return engine.get_scoped_db("conversations")


async def get_app_user(request: Request):
    """Helper to get app user."""
    db = get_db()
    
    # Get app config for sub_auth
    app_config = engine.get_app("conversations") if engine else None
    
    app_user = await get_app_sub_user(
        request=request,
        slug_id="conversations",
        db=db,
        config=app_config,
        allow_demo_fallback=False
    )
    
    # If no user but session cookie exists, it means the user was deleted
    # Mark this in request state so endpoints can clear the cookie
    if not app_user:
        cookie_name = "conversations_session_conversations"
        if request.cookies.get(cookie_name):
            request.state.clear_invalid_session = True
    
    return app_user


# ============================================================================
# Root Route
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Home page - redirects to conversations or login"""
    app_user = await get_app_user(request)
    
    if app_user:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# ============================================================================
# Authentication Routes
# ============================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    app_user = await get_app_user(request)
    
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
    auth_config = await get_auth_config("conversations", engine)
    token_config = auth_config.get("token_management", {})
    
    # Use login_user utility
    result = await login_user(
        request=request,
        email=email,
        password=password,
        db=db,
        config=token_config,
        redirect_url="/conversations"
    )
    
    if result["success"]:
        response = result["response"]
        
        # Create app-specific session
        user = result["user"]
        app_config = engine.get_app("conversations") if engine else None
        if app_config:
            try:
                session_token = await create_app_session(
                    request=request,
                    slug_id="conversations",
                    user_id=str(user["_id"]),
                    config=app_config,
                    response=response
                )
            except Exception as e:
                logger.warning(f"Failed to create app session: {e}")
        
        return response
    else:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": result.get("error", "Login failed")
            },
            status_code=status.HTTP_401_UNAUTHORIZED
        )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    app_user = await get_app_user(request)
    
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
    auth_config = await get_auth_config("conversations", engine)
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
        redirect_url="/conversations"
    )
    
    if result["success"]:
        response = result["response"]
        
        # Create app-specific session
        user = result["user"]
        app_config = engine.get_app("conversations") if engine else None
        if app_config:
            try:
                session_token = await create_app_session(
                    request=request,
                    slug_id="conversations",
                    user_id=str(user["_id"]),
                    config=app_config,
                    response=response
                )
            except Exception as e:
                logger.warning(f"Failed to create app session: {e}")
        
        return response
    else:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "error": result.get("error", "Registration failed")
            },
            status_code=status.HTTP_400_BAD_REQUEST
        )


@app.get("/logout")
async def logout(request: Request):
    """Handle logout"""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    # Use logout_user utility
    response = await logout_user(request, response)
    
    # Clear app-specific session cookie
    cookie_name = "conversations_session_conversations"
    response.delete_cookie(key=cookie_name)
    
    return response


# ============================================================================
# Conversation Routes
# ============================================================================

@app.get("/conversations", response_class=HTMLResponse)
@require_auth()
async def conversations_list(request: Request):
    """List all conversations for the user"""
    app_user = await get_app_user(request)
    
    if not app_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    db = get_db()
    user_id = str(app_user["_id"])
    
    # Get user's conversations
    conversations = await db.conversations.find(
        {"user_id": user_id}
    ).sort("updated_at", -1).to_list(length=100)
    
    return templates.TemplateResponse(request, "conversations.html", {
        "user": app_user,
        "conversations": conversations,
    })


@app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
@require_auth()
async def conversation_view(request: Request, conversation_id: str):
    """View a specific conversation"""
    app_user = await get_app_user(request)
    
    if not app_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    db = get_db()
    user_id = str(app_user["_id"])
    
    # Get conversation
    try:
        conversation = await db.conversations.find_one({
            "_id": ObjectId(conversation_id),
            "user_id": user_id
        })
    except Exception:
        conversation = None
    
    if not conversation:
        return RedirectResponse(url="/conversations", status_code=status.HTTP_302_FOUND)
    
    # Get messages
    messages = await db.messages.find(
        {"conversation_id": conversation_id}
    ).sort("created_at", 1).to_list(length=1000)
    
    return templates.TemplateResponse(request, "conversation.html", {
        "user": app_user,
        "conversation": conversation,
        "messages": messages,
    })


# ============================================================================
# API Routes
# ============================================================================

@app.post("/api/conversations", response_class=JSONResponse)
@require_auth()
async def create_conversation(request: Request):
    """Create a new conversation"""
    app_user = await get_app_user(request)
    
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
    
    return JSONResponse({
        "success": True,
        "conversation": {
            "_id": str(conversation["_id"]),
            "title": conversation["title"],
            "created_at": conversation["created_at"].isoformat(),
        }
    })


@app.post("/api/conversations/{conversation_id}/messages", response_class=JSONResponse)
@require_auth()
async def send_message(request: Request, conversation_id: str, message: str = Form(...)):
    """Send a message in a conversation"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db = get_db()
    user_id = str(app_user["_id"])
    
    # Verify conversation belongs to user
    try:
        conversation = await db.conversations.find_one({
            "_id": ObjectId(conversation_id),
            "user_id": user_id
        })
    except Exception:
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
    history = await db.messages.find(
        {"conversation_id": conversation_id}
    ).sort("created_at", 1).to_list(length=100)
    
    # Get Memory service from engine (if available)
    memory_service = engine.get_memory_service("conversations")
    
    # Initialize Azure OpenAI client
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    
    if not endpoint or not key:
        raise HTTPException(status_code=503, detail="Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY environment variables.")
    
    client = AzureOpenAI(
        api_version=api_version,
        azure_endpoint=endpoint,
        api_key=key
    )
    
    # Retrieve relevant memories for context (if memory service is available)
    # Do this BEFORE generating response so we can use memories as context
    context_memories = []
    memory_search_details = []
    if memory_service:
        try:
            # Search for relevant memories to add as context
            relevant_memories = memory_service.search(
                query=message,
                user_id=user_id,
                limit=3
            )
            if relevant_memories:
                # Extract memory text from results with details
                context_memories = []
                for m in relevant_memories:
                    if isinstance(m, dict):
                        # mem0 returns dicts with 'memory' field
                        memory_text = m.get("memory") or m.get("data", {}).get("memory", "")
                        if memory_text:
                            context_memories.append(memory_text)
                            memory_search_details.append({
                                "memory": memory_text,
                                "id": m.get("id") or m.get("_id"),
                                "score": m.get("score"),
                                "metadata": m.get("metadata", {})
                            })
                    elif isinstance(m, str):
                        context_memories.append(m)
                        memory_search_details.append({
                            "memory": m,
                            "score": None
                        })
        except Exception as e:
            logger.warning(f"Failed to retrieve memories: {e}")
    
    # Memory storage will happen AFTER AI response is generated
    # (moved below to include assistant response)
    
    # Format messages for LLM service
    messages = []
    
    # Add relevant memories as system context if available
    if context_memories:
        memory_context = "Relevant context from past conversations:\n" + "\n".join(f"- {m}" for m in context_memories)
        messages.append({
            "role": "system",
            "content": f"You are a helpful AI assistant. {memory_context}\n\nUse this context to provide more personalized and relevant responses."
        })
    
    # Add conversation history
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })
    # Add current user message
    messages.append({
        "role": "user",
        "content": message
    })
    
    # Get AI response using Azure OpenAI client
    try:
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=deployment_name,
            messages=messages,
            max_tokens=1000
        )
        ai_response = completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Azure OpenAI API error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"LLM service error: {str(e)}")
    
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
    # Mem0 needs both user message and assistant response to extract meaningful memories
    if memory_service:
        try:
            # Build memory messages with full conversation context including assistant response
            memory_messages = []
            
            # Convert full conversation history to message format
            for msg in history:
                memory_messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })
            
            # Add current user message
            memory_messages.append({
                "role": "user",
                "content": message
            })
            
            # Add assistant response (CRITICAL - mem0 needs this to extract memories)
            memory_messages.append({
                "role": "assistant",
                "content": ai_response
            })
            
            # Store in memory asynchronously (fire and forget)
            async def store_memory():
                try:
                    # Log the actual message content for debugging
                    message_preview = []
                    for i, msg in enumerate(memory_messages):
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        preview = content[:100] + "..." if len(content) > 100 else content
                        message_preview.append(f"{i+1}. {role}: {preview}")
                    
                    logger.info(
                        f"🔵 STORING MEMORY - user_id={user_id}, conversation_id={conversation_id}, messages={len(memory_messages)}, collection={memory_service.collection_name}",
                        extra={
                            "user_id": user_id,
                            "conversation_id": conversation_id,
                            "messages_count": len(memory_messages),
                            "has_assistant_response": any(m.get("role") == "assistant" for m in memory_messages),
                            "message_preview": "\n".join(message_preview),
                            "user_message": message[:200] if message else None,
                            "assistant_response": ai_response[:200] if ai_response else None,
                            "collection_name": memory_service.collection_name
                        }
                    )
                    
                    logger.info(f"🔵 About to call memory_service.add() for user_id={user_id}")
                    result = await asyncio.to_thread(
                        memory_service.add,
                        messages=memory_messages,
                        user_id=str(user_id),  # Ensure it's a string
                        metadata={
                            "conversation_id": conversation_id,
                            "source": "conversations_app"
                        }
                    )
                    logger.info(f"🔵 memory_service.add() completed, result type: {type(result).__name__}, length: {len(result) if isinstance(result, list) else 'N/A'}")
                    
                    logger.info(
                        f"STORAGE RESULT - user_id={user_id}, result_type={type(result).__name__}, result_length={len(result) if isinstance(result, list) else 'N/A'}",
                        extra={
                            "user_id": user_id,
                            "result_type": type(result).__name__,
                            "result_length": len(result) if isinstance(result, list) else 0,
                            "result_sample": result[0] if result and isinstance(result, list) and len(result) > 0 else None
                        }
                    )
                    
                    # Log what Mem0 extracted for visibility
                    if result and isinstance(result, list) and len(result) > 0:
                        logger.info(
                            f"✅ Mem0 extracted {len(result)} memories from conversation for user_id={user_id}",
                            extra={
                                "user_id": user_id,
                                "conversation_id": conversation_id,
                                "memory_count": len(result),
                                "messages_count": len(memory_messages),
                                "memory_ids": [m.get("id") or m.get("_id") for m in result if isinstance(m, dict)]
                            }
                        )
                        
                        # Extract memory text for real-time update
                        memory_texts = []
                        for m in result:
                            if isinstance(m, dict):
                                memory_text = m.get("memory") or m.get("data", {}).get("memory", "") or m.get("text", "")
                                if memory_text:
                                    memory_texts.append({
                                        "id": m.get("id") or m.get("_id"),
                                        "memory": memory_text,
                                        "metadata": m.get("metadata", {})
                                    })
                        
                        # Small delay before WebSocket broadcast to ensure memories are fully processed
                        await asyncio.sleep(0.3)  # Reduced delay for faster real-time updates
                        
                        # Fetch the actual memories from the service to include full data
                        try:
                            # Get fresh memories to include in broadcast
                            fresh_memories = await asyncio.to_thread(
                                memory_service.get_all,
                                user_id=str(user_id),
                                limit=50  # Get recent memories
                            )
                            
                            # Calculate stats for real-time update
                            total_memories = len(fresh_memories) if isinstance(fresh_memories, list) else 0
                            
                            # Broadcast memory update event via WebSocket with actual memory data
                            await broadcast_to_app(
                                "conversations",
                                {
                                    "type": "memory_stored",
                                    "user_id": user_id,
                                    "conversation_id": conversation_id,
                                    "memory_count": len(result),
                                    "total_memories": total_memories,
                                    "new_memories": memory_texts,  # Newly extracted memories
                                    "all_memories": fresh_memories[:20] if isinstance(fresh_memories, list) else [],  # Recent memories for UI update
                                    "stats": {
                                        "total_memories": total_memories,
                                        "new_count": len(result),
                                        "memory_enabled": True,
                                        "inference_enabled": True
                                    },
                                    "message": f"Mem0 extracted {len(result)} memories from conversation",
                                    "action": "refresh_memories"  # Signal frontend to refresh
                                },
                                user_id=user_id
                            )
                            logger.info(f"📡 Broadcasted memory update via WebSocket: {len(result)} new memories, {total_memories} total")
                        except Exception as ws_error:
                            logger.warning(f"Failed to broadcast memory event: {ws_error}", exc_info=True)
                    else:
                        logger.warning(
                            f"⚠️ Mem0 returned 0 memories for user_id={user_id}. This may be normal if the conversation doesn't contain extractable facts.",
                            extra={
                                "user_id": user_id,
                                "conversation_id": conversation_id,
                                "messages_count": len(memory_messages),
                                "user_message": message[:100] if message else None,
                                "assistant_response": ai_response[:100] if ai_response else None
                            }
                        )
                    
                    return result
                except Exception as e:
                    logger.error(
                        f"❌ Background memory storage FAILED: {e}",
                        exc_info=True,
                        extra={
                            "user_id": user_id,
                            "conversation_id": conversation_id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "traceback": str(e.__traceback__) if hasattr(e, '__traceback__') else None
                        }
                    )
                    return None
            
            # Schedule in background (fire and forget)
            asyncio.create_task(store_memory())
        except Exception as e:
            logger.warning(f"Failed to schedule memory storage: {e}", exc_info=True)
    
    # Update conversation timestamp
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$set": {"updated_at": datetime.utcnow()}}
    )
    
    # Update conversation title if it's still "New Conversation"
    if conversation.get("title") == "New Conversation" and len(message) > 0:
        # Use first 50 chars of first message as title
        title = message[:50] + ("..." if len(message) > 50 else "")
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {"$set": {"title": title}}
        )
    
    # Prepare response with memory info
    response_data = {
        "success": True,
        "message": {
            "role": "assistant",
            "content": ai_response,
            "created_at": ai_message["created_at"].isoformat(),
        }
    }
    
    # Add memory context info if available
    if memory_service:
        response_data["mem0_operations"] = {
            "memory_enabled": True,
            "search_performed": len(context_memories) > 0,
            "memories_found": len(context_memories),
            "storage_scheduled": True,
            "inference_enabled": memory_service.infer if hasattr(memory_service, 'infer') else False,
            "graph_enabled": memory_service.enable_graph if hasattr(memory_service, 'enable_graph') else False
        }
        
        if context_memories:
            response_data["memory_context"] = {
                "used_memories": len(context_memories),
                "memories": context_memories[:3],  # Limit to 3 for display
                "search_details": memory_search_details[:3]  # Include search scores and metadata
            }
        
        # Broadcast memory search event via WebSocket
        if context_memories:
            try:
                await broadcast_to_app(
                    "conversations",
                    {
                        "type": "memory_search",
                        "user_id": user_id,
                        "query": message,
                        "memories_found": len(context_memories),
                        "search_details": memory_search_details[:3]
                    },
                    user_id=user_id
                )
            except Exception as ws_error:
                logger.debug(f"Failed to broadcast memory search event: {ws_error}")
    
    return JSONResponse(response_data)


@app.delete("/api/conversations/{conversation_id}", response_class=JSONResponse)
@require_auth()
async def delete_conversation(request: Request, conversation_id: str):
    """Delete a conversation"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db = get_db()
    user_id = str(app_user["_id"])
    
    # Verify conversation belongs to user
    try:
        conversation = await db.conversations.find_one({
            "_id": ObjectId(conversation_id),
            "user_id": user_id
        })
    except Exception:
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
    app_user = await get_app_user(request)
    
    if not app_user:
        # Clear invalid session cookie if marked
        response = JSONResponse({"error": "Authentication required"}, status_code=401)
        if getattr(request.state, "clear_invalid_session", False):
            cookie_name = "conversations_session_conversations"
            response.delete_cookie(key=cookie_name)
        return response
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return empty response instead of 503 to avoid breaking frontend
        # Memory service is optional - app can work without it
        return JSONResponse({
            "success": True,
            "memories": [],
            "count": 0,
            "memory_service_available": False
        })
    
    user_id = str(app_user["_id"])
    
    try:
        # Run synchronous get_all in thread pool
        logger.info(
            f"🔍 FETCHING MEMORIES - user_id={user_id} (type: {type(user_id).__name__}), limit={limit}",
            extra={"user_id": user_id, "user_id_type": type(user_id).__name__, "user_id_repr": repr(user_id), "limit": limit}
        )
        memories = await asyncio.to_thread(memory_service.get_all, user_id=str(user_id), limit=limit)  # Ensure string
        logger.info(
            f"🔍 RETRIEVED MEMORIES - user_id={user_id}, count={len(memories) if isinstance(memories, list) else 0}",
            extra={
                "user_id": user_id,
                "memory_count": len(memories) if isinstance(memories, list) else 0,
                "sample_memory": memories[0] if memories and isinstance(memories, list) and len(memories) > 0 else None
            }
        )
        
        # Normalize memory format for frontend
        # Handle Mem0 v2 API format: memories should already be a list from service layer
        normalized_memories = []
        if isinstance(memories, list):
            for mem in memories:
                if isinstance(mem, dict):
                    # Mem0 v2 format: {"id": "...", "memory": "...", "created_at": "...", "updated_at": "...", "metadata": {...}}
                    # Prioritize "memory" field (v2 format) over nested structures
                    memory_text = (
                        mem.get("memory") or  # v2 format - primary
                        mem.get("text") or   # Alternative text field
                        (mem.get("data", {}).get("memory") if isinstance(mem.get("data"), dict) else None) or  # Nested format (legacy)
                        str(mem)  # Fallback
                    )
                    
                    # Extract ID - v2 format uses "id" field
                    memory_id = mem.get("id") or mem.get("_id") or None
                    
                    # Extract metadata - v2 format has direct "metadata" field
                    metadata = mem.get("metadata", {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    
                    if memory_text:  # Only add if we have memory text
                        normalized_memories.append({
                            "memory": memory_text,
                            "id": memory_id,
                            "metadata": metadata
                        })
                elif isinstance(mem, str):
                    # Handle string format memories
                    normalized_memories.append({
                        "memory": mem,
                        "id": None,
                        "metadata": {}
                    })
        
        logger.info(
            f"Returning {len(normalized_memories)} normalized memories for user {user_id}",
            extra={
                "user_id": user_id,
                "raw_count": len(memories) if isinstance(memories, list) else 0,
                "normalized_count": len(normalized_memories)
            }
        )
        
        return JSONResponse({
            "success": True,
            "memories": normalized_memories,
            "count": len(normalized_memories)
        })
    except Exception as e:
        logger.error(f"Failed to get memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve memories: {str(e)}")


@app.get("/api/memories/search", response_class=JSONResponse)
@require_auth()
async def search_memories(request: Request, query: str, limit: int = 5, metadata: Optional[str] = None):
    """Search memories for the current user with optional metadata filtering"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return empty results instead of 503 to avoid breaking frontend
        # Memory service is optional - app can work without it
        return JSONResponse({
            "success": True,
            "results": [],
            "count": 0,
            "query": query,
            "metadata_filter": None
        })
    
    user_id = str(app_user["_id"])
    
    try:
        # Parse metadata filter if provided
        metadata_filter = None
        if metadata:
            try:
                import json
                metadata_filter = json.loads(metadata) if isinstance(metadata, str) else metadata
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid metadata format. Expected JSON string.")
        
        # Run synchronous search in thread pool with metadata filter
        results = await asyncio.to_thread(
            memory_service.search,
            query=query,
            user_id=user_id,
            limit=limit,
            metadata=metadata_filter
        )
        
        # Normalize results format
        normalized_results = []
        if isinstance(results, list):
            for res in results:
                if isinstance(res, dict):
                    memory_text = (
                        res.get("memory") or 
                        res.get("data", {}).get("memory") or 
                        res.get("text") or
                        str(res)
                    )
                    normalized_results.append({
                        "memory": memory_text,
                        "id": res.get("id") or res.get("_id"),
                        "metadata": res.get("metadata", {}),
                        "score": res.get("score")
                    })
                elif isinstance(res, str):
                    normalized_results.append({"memory": res})
        
        return JSONResponse({
            "success": True,
            "results": normalized_results,
            "count": len(normalized_results),
            "query": query,
            "metadata_filter": metadata_filter
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to search memories: {str(e)}")


@app.get("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def get_memory(request: Request, memory_id: str):
    """Get a single memory by ID"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return error response when memory service not available
        return JSONResponse({
            "success": False,
            "error": "Memory service not available",
            "memory": None
        }, status_code=503)
    
    user_id = str(app_user["_id"])
    
    try:
        # Run synchronous get in thread pool
        memory = await asyncio.to_thread(memory_service.get, memory_id=memory_id, user_id=user_id)
        
        # Normalize memory format
        if isinstance(memory, dict):
            memory_text = (
                memory.get("memory") or 
                memory.get("data", {}).get("memory") or 
                memory.get("text") or
                str(memory)
            )
            normalized_memory = {
                "memory": memory_text,
                "id": memory.get("id") or memory.get("_id") or memory_id,
                "metadata": memory.get("metadata", {}),
                "user_id": memory.get("user_id", user_id)
            }
        else:
            normalized_memory = {"memory": str(memory), "id": memory_id}
        
        return JSONResponse({
            "success": True,
            "memory": normalized_memory
        })
    except Exception as e:
        logger.error(f"Failed to get memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get memory: {str(e)}")


@app.put("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def update_memory(request: Request, memory_id: str):
    """Update a memory by ID"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return error response when memory service not available
        return JSONResponse({
            "success": False,
            "error": "Memory service not available",
            "memory": None
        }, status_code=503)
    
    user_id = str(app_user["_id"])
    
    try:
        # Parse request body
        body = await request.json()
        data = body.get("data")
        metadata = body.get("metadata")
        
        if not data:
            raise HTTPException(status_code=400, detail="Missing 'data' field in request body")
        
        # Run synchronous update in thread pool
        updated_memory = await asyncio.to_thread(
            memory_service.update,
            memory_id=memory_id,
            data=data,
            user_id=user_id,
            metadata=metadata
        )
        
        # Normalize memory format
        if isinstance(updated_memory, dict):
            memory_text = (
                updated_memory.get("memory") or 
                updated_memory.get("data", {}).get("memory") or 
                updated_memory.get("text") or
                str(updated_memory)
            )
            normalized_memory = {
                "memory": memory_text,
                "id": updated_memory.get("id") or updated_memory.get("_id") or memory_id,
                "metadata": updated_memory.get("metadata", metadata or {}),
                "user_id": updated_memory.get("user_id", user_id)
            }
        else:
            normalized_memory = {"memory": str(updated_memory), "id": memory_id}
        
        return JSONResponse({
            "success": True,
            "memory": normalized_memory
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update memory: {str(e)}")


@app.delete("/api/memories/{memory_id}", response_class=JSONResponse)
@require_auth()
async def delete_memory(request: Request, memory_id: str):
    """Delete a single memory by ID"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return error response when memory service not available
        return JSONResponse({
            "success": False,
            "error": "Memory service not available",
            "message": "Memory service not available"
        }, status_code=503)
    
    user_id = str(app_user["_id"])
    
    try:
        # Run synchronous delete in thread pool
        success = await asyncio.to_thread(memory_service.delete, memory_id=memory_id, user_id=user_id)
        
        return JSONResponse({
            "success": success,
            "message": f"Memory {memory_id} deleted successfully" if success else "Failed to delete memory"
        })
    except Exception as e:
        logger.error(f"Failed to delete memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete memory: {str(e)}")


@app.delete("/api/memories", response_class=JSONResponse)
@require_auth()
async def delete_all_memories(request: Request):
    """Delete all memories for the current user"""
    app_user = await get_app_user(request)
    
    if not app_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    memory_service = engine.get_memory_service("conversations")
    if not memory_service:
        # Return empty response instead of 503 to avoid breaking frontend
        # Memory service is optional - app can work without it
        return JSONResponse({
            "success": True,
            "memories": [],
            "count": 0,
            "memory_service_available": False
        })
    
    user_id = str(app_user["_id"])
    
    try:
        # Get count before deletion for response
        all_memories = await asyncio.to_thread(memory_service.get_all, user_id=user_id, limit=1000)
        memory_count = len(all_memories) if isinstance(all_memories, list) else 0
        
        # Run synchronous delete_all in thread pool
        success = await asyncio.to_thread(memory_service.delete_all, user_id=user_id)
        
        return JSONResponse({
            "success": success,
            "deleted_count": memory_count,
            "message": f"Deleted {memory_count} memories for user {user_id}" if success else "Failed to delete memories"
        })
    except Exception as e:
        logger.error(f"Failed to delete all memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete all memories: {str(e)}")


@app.get("/api/memories/stats", response_class=JSONResponse)
@require_auth()
async def get_memory_stats(request: Request):
    """Get memory statistics for the current user - BULLETPROOF VERSION"""
    # Default response - always return 200, never raise exceptions
    default_stats = {
        "success": True,
        "stats": {
            "total_memories": 0,
            "memory_enabled": False,
            "inference_enabled": False,
            "graph_enabled": False,
            "conversation_memories": 0,
            "metadata_breakdown": {}
        }
    }
    
    try:
        # Check engine is available
        global engine
        if not engine:
            logger.warning("Engine not initialized in get_memory_stats")
            return JSONResponse(default_stats, status_code=200)
        
        # Get app user - wrap in try-except in case get_app_user fails
        try:
            app_user = await get_app_user(request)
        except Exception as user_error:
            logger.warning(f"Failed to get app user in stats: {user_error}")
            return JSONResponse(default_stats, status_code=200)
        
        if not app_user:
            return JSONResponse(default_stats, status_code=200)
        
        # Get memory service - wrap in try-except
        try:
            memory_service = engine.get_memory_service("conversations")
        except Exception as service_error:
            logger.warning(f"Failed to get memory service in stats: {service_error}")
            return JSONResponse(default_stats, status_code=200)
        
        if not memory_service:
            return JSONResponse(default_stats, status_code=200)
        
        user_id = str(app_user.get("_id", "")) if app_user else ""
        if not user_id:
            logger.warning(f"get_memory_stats: No user_id available")
            return JSONResponse(default_stats, status_code=200)
        
        logger.info(
            f"📊 FETCHING STATS - user_id={user_id} (type: {type(user_id).__name__})",
            extra={"user_id": user_id, "user_id_type": type(user_id).__name__, "user_id_repr": repr(user_id)}
        )
        
        # Try to get memories - wrap in try-except with timeout
        try:
            logger.debug(f"Fetching memory stats for user {user_id}")
            # DEBUG: Check what's actually in MongoDB collection
            try:
                db = get_db()
                collection_name = getattr(memory_service, 'collection_name', 'user_memories')
                logger.info(f"🔍 DEBUG: Checking MongoDB collection '{collection_name}' for user_id='{user_id}'")
                
                # Query MongoDB directly to see what's stored
                collection = db[collection_name]
                direct_query = await collection.find({"user_id": user_id}).limit(5).to_list(length=5)
                logger.info(
                    f"🔍 DIRECT MONGODB QUERY: Found {len(direct_query)} documents with user_id='{user_id}'",
                    extra={
                        "user_id": user_id,
                        "collection": collection_name,
                        "direct_count": len(direct_query),
                        "sample_doc": direct_query[0] if direct_query else None
                    }
                )
                
                # Also check for any documents (to see user_id format)
                any_docs = await collection.find({}).limit(3).to_list(length=3)
                if any_docs:
                    logger.info(
                        f"🔍 SAMPLE DOCS: Found {len(any_docs)} docs, sample user_ids: {[doc.get('user_id') for doc in any_docs]}",
                        extra={
                            "collection": collection_name,
                            "sample_user_ids": [doc.get('user_id') for doc in any_docs]
                        }
                    )
            except Exception as db_error:
                logger.warning(f"Could not query MongoDB directly: {db_error}")
            
            # Add timeout to prevent hanging
            logger.info(f"📊 CALLING get_all - user_id={user_id}, type={type(user_id).__name__}")
            all_memories = await asyncio.wait_for(
                asyncio.to_thread(memory_service.get_all, user_id=str(user_id), limit=1000),  # Ensure string
                timeout=5.0
            )
            logger.info(
                f"📊 RETRIEVED MEMORIES - user_id={user_id}, count={len(all_memories) if isinstance(all_memories, list) else 0}",
                extra={
                    "user_id": user_id,
                    "memory_count": len(all_memories) if isinstance(all_memories, list) else 0,
                    "sample_memory": all_memories[0] if all_memories and isinstance(all_memories, list) and len(all_memories) > 0 else None
                }
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting memories for stats (user: {user_id})")
            return JSONResponse(default_stats, status_code=200)
        except Exception as mem_error:
            logger.warning(f"Failed to get memories for stats: {mem_error}")
            return JSONResponse(default_stats, status_code=200)
        
        # Safely process memories
        try:
            memory_count = len(all_memories) if isinstance(all_memories, list) else 0
            
            # Analyze metadata to show breakdown
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
                except Exception:
                    continue  # Skip invalid memory entries
            
            # Safely get memory service attributes
            inference_enabled = False
            graph_enabled = False
            try:
                inference_enabled = getattr(memory_service, 'infer', False)
                graph_enabled = getattr(memory_service, 'enable_graph', False)
            except Exception:
                pass  # Use defaults
            
            return JSONResponse({
                "success": True,
                "stats": {
                    "total_memories": memory_count,
                    "memory_enabled": True,
                    "inference_enabled": inference_enabled,
                    "graph_enabled": graph_enabled,
                    "conversation_memories": conversation_memories,
                    "metadata_breakdown": metadata_breakdown
                }
            }, status_code=200)
        except Exception as process_error:
            logger.warning(f"Failed to process memory stats: {process_error}")
            return JSONResponse(default_stats, status_code=200)
            
    except Exception as e:
        # Catch EVERYTHING - this should never happen but just in case
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
    
    register_message_handler("conversations", "realtime", handle_message)
