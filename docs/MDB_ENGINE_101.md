# MDB-ENGINE 101: Complete Guide for LLM-Assisted Development

**The Missing Engine for Your Python and MongoDB Projects**

This guide is designed to help Large Language Models understand and generate code using mdb-engine. It provides everything needed to leverage mdb-engine effectively, with clear examples and patterns for "vibe coding."

---

## When to Use What (Quick Reference)

### Pattern Selection

| Building... | Use This | Why |
|-------------|----------|-----|
| **New FastAPI app** | `engine.create_app()` | Zero boilerplate, automatic lifecycle |
| **Existing app** | `engine.lifespan()` or manual | Keep your existing setup |
| **CLI/Script** | `async with engine` | Simple, clean cleanup |
| **Multi-tenant SaaS** | Multi-app + `read_scopes` | Secure data isolation |
| **Microservices** | One engine per service | Independent scaling |

### Feature Activation Guide

| Feature | Activate When... | How to Enable |
|---------|-----------------|---------------|
| **Ray** | CPU-heavy tasks, distributed workers, isolated processing | `MongoDBEngine(..., enable_ray=True)` |
| **Multi-site** | Apps need cross-app data access | `"read_scopes": ["app1", "app2"]` in manifest |
| **App Secrets** | Production, need encrypted tokens | Set `MDB_ENGINE_MASTER_KEY` env var |
| **Casbin** | Simple RBAC (admin/user roles) | `"provider": "casbin"` in auth config |
| **OSO** | Complex policies (ownership, attributes) | `"provider": "oso"` in auth config |
| **Memory** | AI chatbots with conversation history | `"memory_config": {...}` in manifest |
| **Embeddings** | Semantic search, RAG, similarity | Use `EmbeddingService` |
| **WebSockets** | Real-time updates, live data | `"websockets": {...}` in manifest |
| **Auto-indexing** | Let engine create indexes from queries | Default ON, disable with `auto_index=False` |

### Authorization: Casbin vs OSO

| Use Casbin When... | Use OSO When... |
|-------------------|-----------------|
| Simple role-based access (admin, user, guest) | Complex attribute-based rules |
| Static permissions per role | Dynamic conditions (ownership, time-based) |
| Quick setup needed | Need Polar policy language |
| MongoDB-backed policies | Cloud-managed policies |

### Single-App vs Multi-App

| Single-App | Multi-App |
|------------|-----------|
| One manifest, one `create_app()` | Multiple manifests, multiple engines or apps |
| All data in one scope | Data isolated per app with `app_id` |
| Simple auth | Cross-app access via `read_scopes` |
| No app tokens needed | App tokens for secure cross-app access |
| **Use for:** Most applications | **Use for:** SaaS platforms, multi-tenant systems |

---

## Table of Contents

1. [Installation](#installation)
2. [Core Concepts](#core-concepts)
3. [Quick Start: Minimal Setup](#quick-start-minimal-setup)
4. [Manifest-Driven Configuration](#manifest-driven-configuration)
5. [Database Operations](#database-operations)
6. [Authentication & Authorization](#authentication--authorization)
7. [Index Management](#index-management)
8. [WebSockets](#websockets)
9. [Embeddings & Memory Services](#embeddings--memory-services)
10. [Observability](#observability)
11. [Common Patterns](#common-patterns)
12. [Best Practices](#best-practices)

---

## Installation

```bash
pip install mdb-engine
```

**Optional Dependencies:**
```bash
# For Casbin authorization
pip install mdb-engine[casbin]

# For OSO authorization
pip install mdb-engine[oso]

# For all optional features
pip install mdb-engine[all]

# For memory service (Mem0)
pip install mem0ai

# For development/testing
pip install mdb-engine[test]
```

**Environment Setup:**
```bash
# Required for JWT security
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')

# MongoDB connection (if not using defaults)
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB_NAME="my_database"
```

---

## Core Concepts

### What is MDB-ENGINE?

MDB-ENGINE is a "WordPress-like" platform for Python/MongoDB applications that handles:
- **Automatic Data Sandboxing**: All queries are automatically scoped to your app
- **Manifest-Driven Configuration**: Define your app's "DNA" in `manifest.json`
- **Zero-Boilerplate Auth**: Built-in authentication and authorization
- **Automatic Index Management**: Indexes created from manifest definitions
- **Built-in Services**: Embeddings, memory, WebSockets, observability

### Key Philosophy

**You write clean, naive code. The engine handles the complexity.**

```python
# YOU WRITE THIS (Clean, Naive Code):
await db.tasks.find({}).to_list(length=10)

# THE ENGINE EXECUTES THIS (Secure, Scoped Query):
# Collection: my_app_tasks
# Query: {"app_id": "my_app"}
```

### Two-Layer Scoping System

1. **Physical Scoping**: Collection names are prefixed (`db.users` → `db.my_app_users`)
2. **Logical Scoping**: All documents tagged with `app_id`, queries auto-filtered

---

## Quick Start: Minimal Setup

### Step 1: Create `manifest.json`

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My First App",
  "status": "active",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": false,
      "allow_anonymous": true,
      "authorization": {
        "model": "rbac",
        "link_users_roles": true
      }
    }
  },
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort"
      }
    ]
  }
}
```

### Step 2: Initialize Engine in FastAPI

**Option A: Simplified Pattern (Recommended)**

The easiest way - use `create_app()` for automatic lifecycle management:

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json")
)

# Use the scoped database
@app.post("/tasks")
async def create_task(task: dict):
    db = engine.get_scoped_db("my_app")
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

This automatically handles:
- Engine initialization on startup
- Manifest loading and app registration
- Multi-site mode auto-detection
- App token auto-retrieval
- Clean shutdown on app exit

**Option B: Manual Pattern (More Control)**

For more control over the lifecycle:

```python
from pathlib import Path
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

app = FastAPI()
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

@app.on_event("startup")
async def startup():
    # Initialize engine
    await engine.initialize()
    
    # Load and register app from manifest
    manifest_path = Path("manifest.json")
    manifest = await engine.load_manifest(manifest_path)
    await engine.register_app(manifest, create_indexes=True)

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()

# Use the scoped database
@app.post("/tasks")
async def create_task(task: dict):
    db = engine.get_scoped_db("my_app")
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

---

## Manifest-Driven Configuration

The `manifest.json` file is your app's "DNA" - it defines everything about your application.

### Basic Manifest Structure

```json
{
  "schema_version": "2.0",
  "slug": "my_app",                    // Unique app identifier
  "name": "My Application",            // Display name
  "status": "active",                  // active | inactive | archived
  "developer_id": "dev@example.com",  // Optional developer identifier
  
  // Authentication & Authorization
  "auth": { ... },
  
  // Index definitions
  "managed_indexes": { ... },
  
  // WebSocket endpoints
  "websockets": { ... },
  
  // Memory service configuration
  "memory_config": { ... },
  
  // Embedding service configuration
  "embedding_config": { ... },
  
  // CORS settings
  "cors": { ... }
}
```

### Loading and Validating Manifests

```python
from pathlib import Path
from mdb_engine import MongoDBEngine, ManifestValidator

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Load manifest
manifest_path = Path("manifest.json")
manifest = await engine.load_manifest(manifest_path)

# Validate manually (optional - register_app validates automatically)
validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)
if not is_valid:
    print(f"Validation error: {error} at {paths}")

# Register app (creates indexes, sets up services)
success = await engine.register_app(manifest, create_indexes=True)
```

### Reloading Apps

```python
# Reload all active apps from database
count = await engine.reload_apps()
print(f"Reloaded {count} apps")

# Get app configuration
app_config = engine.get_app("my_app")
```

---

## Database Operations

### Getting a Scoped Database

```python
# Basic usage - all operations automatically scoped to "my_app"
db = engine.get_scoped_db("my_app")

# Advanced: Custom read/write scopes
db = engine.get_scoped_db(
    app_slug="my_app",
    read_scopes=["my_app", "shared"],  # Can read from multiple scopes
    write_scope="my_app",              # Writes go to this scope
    auto_index=True                     # Auto-create indexes (default: True)
)
```

### CRUD Operations

```python
db = engine.get_scoped_db("my_app")

# CREATE
result = await db.tasks.insert_one({
    "title": "Complete project",
    "status": "pending",
    "created_at": datetime.utcnow()
})
task_id = result.inserted_id

# CREATE MANY
results = await db.tasks.insert_many([
    {"title": "Task 1", "status": "pending"},
    {"title": "Task 2", "status": "done"}
])

# READ ONE
task = await db.tasks.find_one({"_id": task_id})
task = await db.tasks.find_one({"status": "pending"})

# READ MANY
tasks = await db.tasks.find({"status": "pending"}).to_list(length=10)
tasks = await db.tasks.find({"status": "pending"}).sort("created_at", -1).to_list(length=10)

# COUNT
count = await db.tasks.count_documents({"status": "pending"})

# UPDATE ONE
result = await db.tasks.update_one(
    {"_id": task_id},
    {"$set": {"status": "done"}}
)

# UPDATE MANY
result = await db.tasks.update_many(
    {"status": "pending"},
    {"$set": {"status": "in_progress"}}
)

# DELETE ONE
result = await db.tasks.delete_one({"_id": task_id})

# DELETE MANY
result = await db.tasks.delete_many({"status": "done"})
```

### Aggregation Pipelines

```python
# Aggregation with automatic app_id filtering
pipeline = [
    {"$match": {"status": "pending"}},
    {"$group": {"_id": "$priority", "count": {"$sum": 1}}},
    {"$sort": {"_id": 1}}
]
results = await db.tasks.aggregate(pipeline).to_list(length=None)
```

### Important Notes

- **All queries are automatically filtered by `app_id`** - you don't need to add it manually
- **Collection names are prefixed** - `db.tasks` actually accesses `my_app_tasks`
- **Write operations automatically add `app_id`** - no need to include it in your documents
- **Standard Motor API** - works exactly like Motor, just with automatic scoping

---

## Authentication & Authorization

### Manifest Configuration

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",           // casbin | oso
      "required": true,               // Require auth for all routes
      "allow_anonymous": false,       // Allow anonymous access
      "authorization": {
        "model": "rbac",              // rbac | acl | custom path
        "policies_collection": "casbin_policies",
        "link_users_roles": true,     // Auto-link users to roles
        "default_roles": ["user"]    // Default roles for new users
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",        // app_users | global_users
      "collection_name": "users",
      "session_cookie_name": "my_app_session",
      "session_ttl_seconds": 86400,
      "allow_registration": true
    }
  },
  "token_management": {
    "enabled": true,
    "access_token_ttl": 900,          // 15 minutes
    "refresh_token_ttl": 604800,      // 7 days
    "token_rotation": true,
    "security": {
      "require_https": false,
      "csrf_protection": true,
      "rate_limiting": {
        "login": {"max_attempts": 5, "window_seconds": 300}
      }
    }
  }
}
```

### Setup in FastAPI

```python
from mdb_engine.auth import setup_auth_from_manifest

@app.on_event("startup")
async def startup():
    await engine.initialize()
    await engine.register_app(manifest, create_indexes=True)
    
    # Auto-setup auth from manifest
    await setup_auth_from_manifest(app, engine, "my_app")
```

### Using Authentication in Routes

```python
from fastapi import Depends
from mdb_engine.auth import get_current_user, get_authz_provider, require_admin

# Get current user
@app.get("/profile")
async def get_profile(user: dict = Depends(get_current_user)):
    return {"user_id": user["user_id"], "email": user.get("email")}

# Check authorization
@app.get("/admin")
async def admin_route(
    user: dict = Depends(get_current_user),
    authz = Depends(get_authz_provider)
):
    has_access = await authz.check(
        subject=user.get("email"),
        resource="my_app",
        action="admin_access"
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"message": "Admin access granted"}

# Require admin role
@app.delete("/users/{user_id}")
@require_admin
async def delete_user(user_id: str, user: dict = Depends(get_current_user)):
    # User is guaranteed to be admin here
    db = engine.get_scoped_db("my_app")
    await db.users.delete_one({"_id": user_id})
    return {"success": True}
```

### User Management

```python
from mdb_engine.auth.users import register_user, login_user, logout_user, get_app_user
from mdb_engine.auth.utils import create_app_session

# Registration
@app.post("/register")
async def register(credentials: dict):
    db = engine.get_scoped_db("my_app")
    app_config = engine.get_app("my_app")
    
    user = await register_user(
        db=db,
        email=credentials["email"],
        password=credentials["password"],
        config=app_config
    )
    return {"user_id": str(user["_id"])}

# Login
@app.post("/login")
async def login(credentials: dict, request: Request, response: Response):
    db = engine.get_scoped_db("my_app")
    app_config = engine.get_app("my_app")
    
    user = await login_user(
        db=db,
        email=credentials["email"],
        password=credentials["password"],
        request=request,
        response=response,
        config=app_config
    )
    return {"user_id": str(user["_id"]), "email": user["email"]}

# Logout
@app.post("/logout")
async def logout(request: Request, response: Response):
    await logout_user(request, response)
    return {"success": True}
```

### Optional Security Decorators

```python
from mdb_engine.auth.decorators import rate_limit_auth, token_security, require_auth

# Rate limiting
@app.post("/login")
@rate_limit_auth(max_attempts=5, window_seconds=300)
async def login(credentials: dict):
    # ... login logic
    pass

# CSRF protection and HTTPS enforcement
@app.post("/sensitive")
@token_security(enforce_https=True, check_csrf=True)
async def sensitive_operation(user: dict = Depends(get_current_user)):
    return {"data": "sensitive"}

# Require authentication
@app.get("/dashboard")
@require_auth(redirect_to="/login")
async def dashboard(request: Request):
    user = request.state.user
    return {"user": user}
```

---

## Index Management

### Defining Indexes in Manifest

```json
{
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"status": 1, "created_at": -1},
        "name": "status_sort",
        "options": {
          "background": true,
          "unique": false
        }
      },
      {
        "type": "regular",
        "keys": {"user_id": 1},
        "name": "user_idx"
      }
    ],
    "users": [
      {
        "type": "regular",
        "keys": {"email": 1},
        "name": "email_idx",
        "options": {
          "unique": true
        }
      }
    ]
  }
}
```

### Index Types

**Regular Indexes:**
```json
{
  "type": "regular",
  "keys": {"field": 1},  // 1 = ascending, -1 = descending
  "name": "field_idx"
}
```

**Text Indexes:**
```json
{
  "type": "text",
  "keys": {"title": "text", "description": "text"},
  "name": "text_search_idx"
}
```

**Geospatial Indexes:**
```json
{
  "type": "geospatial",
  "keys": {"location": "2dsphere"},
  "name": "location_idx"
}
```

**TTL Indexes:**
```json
{
  "type": "ttl",
  "keys": {"created_at": 1},
  "name": "ttl_idx",
  "options": {
    "expireAfterSeconds": 3600
  }
}
```

**Vector Indexes (Atlas Search):**
```json
{
  "type": "vector",
  "name": "vector_idx",
  "definition": {
    "fields": [
      {
        "type": "vector",
        "path": "embedding",
        "numDimensions": 1536,
        "similarity": "cosine"
      }
    ]
  }
}
```

### Automatic Index Creation

Indexes are automatically created when you register an app:

```python
# Indexes are created automatically
await engine.register_app(manifest, create_indexes=True)

# Or create indexes manually
from mdb_engine.indexes import run_index_creation_for_collection

db = engine.get_scoped_db("my_app")
await run_index_creation_for_collection(
    collection=db.tasks,
    indexes=manifest["managed_indexes"]["tasks"],
    app_slug="my_app"
)
```

### Auto-Index Manager

The engine includes an **AutoIndexManager** that automatically creates indexes based on query patterns:

```python
# Enabled by default - no configuration needed!
# The engine watches your queries and creates indexes automatically
db = engine.get_scoped_db("my_app")

# This query might trigger automatic index creation
tasks = await db.tasks.find({"status": "pending"}).sort("created_at", -1).to_list(10)
```

---

## WebSockets

### Manifest Configuration

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": true,
        "allow_anonymous": false
      },
      "ping_interval": 30
    }
  }
}
```

### Setup

```python
from mdb_engine.routing.websockets import broadcast_to_app, register_message_handler

@app.on_event("startup")
async def startup():
    await engine.initialize()
    await engine.register_app(manifest, create_indexes=True)
    
    # Register WebSocket routes from manifest
    engine.register_websocket_routes(app, "my_app")
    
    # Register message handlers
    register_message_handler("my_app", handle_websocket_message)
```

### WebSocket Endpoint

```python
from fastapi import WebSocket, WebSocketDisconnect
from mdb_engine.routing.websockets import get_websocket_manager

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    manager = get_websocket_manager("my_app")
    
    # Get user from auth (if required)
    user = await get_app_user(request=websocket, ...)
    
    # Connect
    connection = await manager.connect(
        websocket,
        user_id=str(user["_id"]) if user else None,
        user_email=user.get("email") if user else None
    )
    
    try:
        while True:
            data = await websocket.receive_text()
            # Handle message
            await handle_message(connection, data)
    except WebSocketDisconnect:
        await manager.disconnect(connection)
```

### Broadcasting Messages

```python
from mdb_engine.routing.websockets import broadcast_to_app

# Broadcast to all connections in an app
await broadcast_to_app("my_app", {
    "type": "update",
    "data": {"task_id": "123", "status": "done"}
})

# Broadcast to specific user
manager = get_websocket_manager("my_app")
await manager.send_to_user(user_id="user123", message={"type": "notification"})
```

### Message Handlers

```python
async def handle_websocket_message(connection, message: dict):
    """Handle incoming WebSocket messages"""
    msg_type = message.get("type")
    
    if msg_type == "subscribe":
        # Subscribe to updates
        await subscribe_to_updates(connection, message["channel"])
    elif msg_type == "ping":
        # Respond to ping
        await connection.websocket.send_json({"type": "pong"})
```

---

## Embeddings & Memory Services

### Embedding Service

**Purpose**: Semantic text splitting and embedding generation for RAG applications.

**Manifest Configuration:**
```json
{
  "embedding_config": {
    "enabled": true,
    "default_max_tokens": 1000,
    "default_tokenizer_model": "gpt-3.5-turbo"
  }
}
```

**Environment Variables:**
```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# Azure OpenAI
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2024-02-15-preview"
```

**Usage:**
```python
from mdb_engine.embeddings import get_embedding_service

# Initialize (auto-detects provider from env vars)
embedding_service = get_embedding_service()

# Process and store text with embeddings
db = engine.get_scoped_db("my_app")
await embedding_service.process_and_store(
    text_content="Your long document here...",
    source_id="doc_101",
    collection=db.knowledge_base,
    max_tokens_per_chunk=1000
)

# Generate embeddings manually
embeddings = await embedding_service.embed(["Text 1", "Text 2"])
```

### Memory Service (Mem0)

**Purpose**: Intelligent memory management for AI applications - stores and retrieves user memories.

**Manifest Configuration:**
```json
{
  "memory_config": {
    "enabled": true,
    "collection_name": "user_memories",
    "enable_graph": true,
    "infer": true,
    "async_mode": true
  }
}
```

**Environment Variables:**
```bash
# Same as embedding service - auto-detects provider
export OPENAI_API_KEY="sk-..."
# OR
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://..."
```

**Usage:**
```python
# Get memory service (automatically configured from manifest)
memory_service = engine.get_memory_service("my_app")

# Add memory from conversation
memory = await memory_service.add(
    messages=[
        {"role": "user", "content": "I love Python programming"},
        {"role": "assistant", "content": "That's great!"}
    ],
    user_id="user123"
)

# Search memories
memories = await memory_service.search(
    query="What does the user like?",
    user_id="user123",
    limit=5
)

# Get all memories for a user
all_memories = await memory_service.get_all(user_id="user123")

# Update memory
await memory_service.update(
    memory_id=memory["id"],
    memory={"content": "Updated memory"}
)

# Delete memory
await memory_service.delete(memory_id=memory["id"])
```

**Integration Example:**
```python
@app.post("/chat")
async def chat(message: str, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    memory_service = engine.get_memory_service("my_app")
    
    # Get relevant memories
    memories = await memory_service.search(
        query=message,
        user_id=str(user["_id"]),
        limit=3
    )
    
    # Build context from memories
    context = "\n".join([m["memory"] for m in memories])
    
    # Generate response (using your LLM)
    response = await generate_llm_response(message, context)
    
    # Store conversation as memory
    await memory_service.add(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ],
        user_id=str(user["_id"])
    )
    
    return {"response": response}
```

---

## Observability

### Health Checks

```python
# Check engine health
health = await engine.get_health_status()
print(health["status"])  # "healthy" | "degraded" | "unhealthy"
print(health["mongodb"]["status"])
print(health["pool"]["status"])

# Health check endpoint
@app.get("/health")
async def health_check():
    return await engine.get_health_status()
```

### Metrics

```python
# Get metrics summary
metrics = engine.get_metrics()
print(metrics["summary"])

# Metrics endpoint
@app.get("/metrics")
async def metrics_endpoint():
    return engine.get_metrics()
```

### Logging

```python
from mdb_engine.observability import get_logger, set_correlation_id

# Set correlation ID for request tracking
correlation_id = set_correlation_id()

# Get contextual logger (automatically includes app_id, correlation_id)
logger = get_logger(__name__)
logger.info("Operation completed")  # Includes correlation_id automatically
logger.error("Error occurred", exc_info=True)
```

---

## Common Patterns

### Pattern 1: FastAPI Application with Auth

```python
from fastapi import FastAPI, Depends, HTTPException
from mdb_engine import MongoDBEngine
from mdb_engine.auth import setup_auth_from_manifest, get_current_user
from pathlib import Path

app = FastAPI()
engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="my_db")

@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest, create_indexes=True)
    await setup_auth_from_manifest(app, engine, "my_app")

@app.get("/tasks")
async def get_tasks(user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    tasks = await db.tasks.find({"user_id": str(user["_id"])}).to_list(10)
    return {"tasks": tasks}
```

### Pattern 2: CRUD API

```python
from fastapi import FastAPI, Depends
from mdb_engine import MongoDBEngine
from mdb_engine.auth import get_current_user
from bson import ObjectId

app = FastAPI()
engine = MongoDBEngine(...)

@app.post("/items")
async def create_item(item: dict, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    item["user_id"] = str(user["_id"])
    result = await db.items.insert_one(item)
    return {"id": str(result.inserted_id)}

@app.get("/items/{item_id}")
async def get_item(item_id: str, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    item = await db.items.find_one({
        "_id": ObjectId(item_id),
        "user_id": str(user["_id"])
    })
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.put("/items/{item_id}")
async def update_item(item_id: str, updates: dict, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    result = await db.items.update_one(
        {"_id": ObjectId(item_id), "user_id": str(user["_id"])},
        {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True}

@app.delete("/items/{item_id}")
async def delete_item(item_id: str, user: dict = Depends(get_current_user)):
    db = engine.get_scoped_db("my_app")
    result = await db.items.delete_one({
        "_id": ObjectId(item_id),
        "user_id": str(user["_id"])
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True}
```

### Pattern 3: Real-time Updates with WebSockets

```python
from fastapi import WebSocket
from mdb_engine.routing.websockets import get_websocket_manager, broadcast_to_app

@app.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str):
    db = engine.get_scoped_db("my_app")
    await db.tasks.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {"status": "completed"}}
    )
    
    # Broadcast update to all connected clients
    await broadcast_to_app("my_app", {
        "type": "task_completed",
        "task_id": task_id
    })
    
    return {"success": True}
```

### Pattern 4: RAG Application

```python
from mdb_engine.embeddings import get_embedding_service

@app.post("/documents")
async def add_document(document: dict):
    db = engine.get_scoped_db("my_app")
    embedding_service = get_embedding_service()
    
    # Process and store with embeddings
    await embedding_service.process_and_store(
        text_content=document["content"],
        source_id=str(document["_id"]),
        collection=db.knowledge_base,
        max_tokens_per_chunk=1000
    )
    
    return {"success": True}

@app.post("/search")
async def search(query: str):
    db = engine.get_scoped_db("my_app")
    embedding_service = get_embedding_service()
    
    # Generate query embedding
    query_embedding = await embedding_service.embed([query])[0]
    
    # Vector search (using aggregation pipeline)
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_idx",
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": 100,
                "limit": 10
            }
        }
    ]
    
    results = await db.knowledge_base.aggregate(pipeline).to_list(10)
    return {"results": results}
```

### Pattern 5: Context Manager Usage

```python
async with MongoDBEngine(mongo_uri, db_name) as engine:
    await engine.reload_apps()
    db = engine.get_scoped_db("my_app")
    # ... use engine
    # Automatic cleanup on exit
```

### Pattern 6: FastAPI with create_app()

The simplest pattern using automatic lifecycle management:

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)

# Automatic initialization, manifest loading, and cleanup
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json"),
    title="My Application"
)

@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("my_app")
    return await db.items.find({}).to_list(10)
```

---

## Optional Ray Support

Ray integration enables distributed processing with isolated app environments. Ray is **optional** - the engine works perfectly without it.

### Enable Ray

```python
from mdb_engine import MongoDBEngine

# Enable Ray (only activates if Ray is installed)
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    enable_ray=True,
    ray_namespace="my_namespace"
)

await engine.initialize()

# Check if Ray is available
if engine.has_ray:
    print(f"Ray initialized in namespace: {engine.ray_namespace}")
else:
    print("Ray not available - running in standard mode")
```

### Ray with FastAPI

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    enable_ray=True
)

app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.get("/status")
async def status():
    return {
        "app": "my_app",
        "ray_enabled": engine.has_ray,
        "ray_namespace": engine.ray_namespace if engine.has_ray else None
    }
```

### Install Ray (Optional)

```bash
pip install ray
```

Ray features:
- **Isolated environments**: Each app can run in its own Ray actor
- **Distributed processing**: Offload heavy computations
- **Graceful degradation**: Works without Ray installed

---

## Best Practices

### 1. Always Use Scoped Database

```python
# ✅ GOOD: Use scoped database
db = engine.get_scoped_db("my_app")
await db.tasks.find({}).to_list(10)

# ❌ BAD: Raw database access is no longer available
# engine.mongo_db has been removed for security - use scoped databases only
```

### 2. Initialize Engine Once

```python
# ✅ GOOD: Initialize once in startup
@app.on_event("startup")
async def startup():
    await engine.initialize()
    await engine.register_app(manifest, create_indexes=True)

# ❌ BAD: Don't initialize in every request
@app.get("/tasks")
async def get_tasks():
    await engine.initialize()  # Don't do this!
```

### 3. Use Manifest for Configuration

```python
# ✅ GOOD: Configure in manifest.json
{
  "managed_indexes": {
    "tasks": [...]
  }
}

# ❌ BAD: Don't create indexes manually
await db.tasks.create_index("status")  # Use manifest instead
```

### 4. Handle Errors Gracefully

```python
from mdb_engine.exceptions import MongoDBEngineError

try:
    db = engine.get_scoped_db("my_app")
    await db.tasks.insert_one(task)
except MongoDBEngineError as e:
    logger.error(f"Database error: {e}")
    raise HTTPException(status_code=500, detail="Database error")
```

### 5. Use Dependency Injection for Database

```python
from fastapi import Depends

def get_db():
    return engine.get_scoped_db("my_app")

@app.get("/tasks")
async def get_tasks(db = Depends(get_db)):
    return await db.tasks.find({}).to_list(10)
```

### 6. Leverage Auto-Indexing

```python
# ✅ GOOD: Let the engine auto-create indexes
# Just write your queries - indexes are created automatically

# ❌ BAD: Don't manually create indexes unless necessary
# The AutoIndexManager handles this for you
```

### 7. Use Context Managers for Cleanup

```python
# ✅ GOOD: Use context manager for automatic cleanup
async with MongoDBEngine(mongo_uri, db_name) as engine:
    # ... use engine

# ✅ ALSO GOOD: Manual cleanup in shutdown
@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()
```

---

## Complete Example: Todo App

```python
from fastapi import FastAPI, Depends, HTTPException
from mdb_engine import MongoDBEngine
from mdb_engine.auth import setup_auth_from_manifest, get_current_user
from pathlib import Path
from bson import ObjectId
from datetime import datetime

app = FastAPI(title="Todo App")
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="todo_db"
)

@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest = await engine.load_manifest(Path("manifest.json"))
    await engine.register_app(manifest, create_indexes=True)
    await setup_auth_from_manifest(app, engine, "todo_app")

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()

def get_db():
    return engine.get_scoped_db("todo_app")

@app.post("/todos")
async def create_todo(todo: dict, user: dict = Depends(get_current_user), db = Depends(get_db)):
    todo["user_id"] = str(user["_id"])
    todo["created_at"] = datetime.utcnow()
    todo["completed"] = False
    result = await db.todos.insert_one(todo)
    return {"id": str(result.inserted_id)}

@app.get("/todos")
async def list_todos(user: dict = Depends(get_current_user), db = Depends(get_db)):
    todos = await db.todos.find({"user_id": str(user["_id"])}).sort("created_at", -1).to_list(100)
    return {"todos": todos}

@app.put("/todos/{todo_id}")
async def update_todo(todo_id: str, updates: dict, user: dict = Depends(get_current_user), db = Depends(get_db)):
    result = await db.todos.update_one(
        {"_id": ObjectId(todo_id), "user_id": str(user["_id"])},
        {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"success": True}

@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str, user: dict = Depends(get_current_user), db = Depends(get_db)):
    result = await db.todos.delete_one({"_id": ObjectId(todo_id), "user_id": str(user["_id"])})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"success": True}
```

**manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "todo_app",
  "name": "Todo Application",
  "status": "active",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "allow_anonymous": false,
      "authorization": {
        "model": "rbac",
        "link_users_roles": true
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "allow_registration": true
    }
  },
  "managed_indexes": {
    "todos": [
      {
        "type": "regular",
        "keys": {"user_id": 1, "created_at": -1},
        "name": "user_todos_idx"
      },
      {
        "type": "regular",
        "keys": {"completed": 1},
        "name": "completed_idx"
      }
    ]
  }
}
```

---

## Summary

**MDB-ENGINE provides:**

1. ✅ **Automatic Data Sandboxing** - All queries scoped to your app
2. ✅ **Manifest-Driven Configuration** - Define everything in `manifest.json`
3. ✅ **Zero-Boilerplate Auth** - Built-in authentication and authorization
4. ✅ **Automatic Index Management** - Indexes created from manifest
5. ✅ **Built-in Services** - Embeddings, memory, WebSockets, observability
6. ✅ **Standard Motor API** - Works exactly like Motor, just better

**Key Workflow:**

1. Install: `pip install mdb-engine`
2. Create `manifest.json` with your app configuration
3. Initialize engine in FastAPI startup
4. Use `engine.get_scoped_db("app_slug")` for all database operations
5. Everything else is automatic!

**For LLMs generating code:**

- Always use `engine.get_scoped_db()` - raw database access has been removed for security
- Use `collection.index_manager` for index operations - never access `.database` property
- `engine.mongo_client` is for observability/health checks only - NOT for data access
- Configure everything in `manifest.json` - don't create indexes manually
- Use `setup_auth_from_manifest()` for authentication setup
- Leverage built-in services (embeddings, memory) when needed
- Follow the patterns above for common use cases

## Security Best Practices

### Query Security

The engine automatically validates all queries for security:

**Dangerous Operators Blocked:**
- `$where` - JavaScript execution (security risk)
- `$eval` - JavaScript evaluation (deprecated, security risk)
- `$function` - JavaScript functions (security risk)
- `$accumulator` - Can be abused for code execution

```python
# ❌ These will raise QueryValidationError:
db.collection.find({"$where": "this.status === 'active'"})
db.collection.aggregate([{"$match": {"$eval": "code"}}])

# ✅ Use safe alternatives:
db.collection.find({"status": "active"})
db.collection.find({"age": {"$gt": 18}})
```

**Query Limits:**
- Maximum query depth: 10 levels (prevents deeply nested queries)
- Maximum pipeline stages: 50 (prevents complex aggregation pipelines)
- Maximum regex length: 1000 characters (prevents ReDoS attacks)
- Maximum regex complexity: 50 (prevents ReDoS attacks)

### Resource Limits

All operations have automatic resource limits:

**Query Timeouts:**
- Default timeout: 30 seconds
- Maximum timeout: 5 minutes
- Automatically enforced on all queries

```python
# Timeout is automatically added:
db.collection.find({"status": "active"})  # Has maxTimeMS=30000

# You can set custom timeout (capped to 5 minutes):
db.collection.find({"status": "active"}, maxTimeMS=60000)  # 60 seconds
```

**Result Size Limits:**
- Maximum result size: 10,000 documents
- Maximum batch size: 1,000 documents
- Automatically enforced

```python
# Limits are automatically enforced:
db.collection.find({}, limit=20000)  # Capped to 10,000
```

**Document Size Limits:**
- Maximum document size: 16MB (MongoDB limit)
- Validated before insert

```python
# Document size is validated:
large_doc = {"data": "x" * (20 * 1024 * 1024)}  # 20MB
await db.collection.insert_one(large_doc)  # ❌ Raises ResourceLimitExceeded
```

### Collection Naming

- Use valid MongoDB collection names: alphanumeric, underscore, dot, hyphen
- Start with a letter or underscore (not a number)
- Avoid reserved names: `apps_config`, `system.*`, `admin.*`, `config.*`, `local.*`
- Use descriptive names: `user_profiles`, `order_items`, `product_catalog`

```python
# ✅ Good collection names
db.user_profiles
db.order_items
db.product_catalog_v2

# ❌ Bad collection names
db.system_users      # Reserved prefix
db.apps_config       # Reserved name
db["../other"]       # Path traversal attempt
db["123users"]       # Starts with number
```

### Cross-App Access

- Only grant `read_scopes` to apps that need cross-app data
- Validate that cross-app access is necessary before granting
- Monitor audit logs for unauthorized access attempts

```python
# ✅ Explicit cross-app access
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_data_app"]  # Explicitly authorized
)

# ❌ Overly permissive access
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["*"]  # Don't do this - be explicit
)
```

### Scope Validation

- Always validate `read_scopes` and `write_scope` when creating scoped databases
- Use non-empty, valid app slugs
- The engine validates scopes automatically - invalid scopes raise `ValueError`

### Security Monitoring

- Review security logs regularly for:
  - Invalid collection name attempts
  - Unauthorized cross-app access attempts
  - Reserved name/prefix access attempts
- Set up alerts for repeated security violations

---

**Stop building scaffolding. Start building features.**

