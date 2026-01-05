# MDB Engine Best Practices

This guide covers best practices for building applications with `mdb-engine`, focusing on dependency injection, database access, and service integration patterns.

---

## Quick Reference

| Pattern | Use Case | Import |
|---------|----------|--------|
| `Depends(get_scoped_db)` | Simple database access | `from mdb_engine.dependencies import get_scoped_db` |
| `Depends(get_embedding_service)` | Text chunking & embeddings | `from mdb_engine.dependencies import get_embedding_service` |
| `Depends(get_llm_client)` | Chat completions | `from mdb_engine.dependencies import get_llm_client` |
| `Depends(get_memory_service)` | Semantic memory (Mem0) | `from mdb_engine.dependencies import get_memory_service` |
| `AppContext = Depends()` | Multiple services at once | `from mdb_engine.dependencies import AppContext` |

---

## Table of Contents

1. [Dependency Injection](#dependency-injection)
2. [Database Access](#database-access)
3. [AI/ML Services](#aiml-services)
4. [Authentication & Authorization](#authentication--authorization)
5. [Common Patterns](#common-patterns)
6. [Anti-Patterns to Avoid](#anti-patterns-to-avoid)
7. [Migration from Old Patterns](#migration-from-old-patterns)

---

## Dependency Injection

### Individual Dependencies

Use individual dependencies when you only need one or two services:

```python
from fastapi import Depends
from mdb_engine.dependencies import get_scoped_db, get_embedding_service

@app.get("/documents")
async def list_documents(db=Depends(get_scoped_db)):
    """Simple route - only needs database."""
    docs = await db.documents.find({}).to_list(100)
    return {"documents": docs}

@app.post("/embed")
async def embed_text(
    text: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    """Route needs both database and embedding service."""
    result = await embedding_service.process_and_store(
        text_content=text,
        source_id="user_input",
        collection=db.knowledge_base,
    )
    return {"chunks_created": result["chunks_created"]}
```

### AppContext All-in-One

Use `AppContext` when you need multiple services or want cleaner code:

```python
from fastapi import Depends
from mdb_engine.dependencies import AppContext

@app.post("/ai-chat")
async def chat(query: str, ctx: AppContext = Depends()):
    """
    AppContext provides:
    - ctx.db          - Scoped database
    - ctx.slug        - App identifier
    - ctx.config      - Manifest configuration
    - ctx.engine      - MongoDBEngine instance
    - ctx.embedding_service - EmbeddingService (None if not configured)
    - ctx.memory      - Mem0 memory service (None if not configured)
    - ctx.llm         - OpenAI/AzureOpenAI client (None if not configured)
    - ctx.llm_model   - Model/deployment name
    - ctx.user        - Current authenticated user (None if not logged in)
    - ctx.user_roles  - User's roles for this app
    - ctx.authz       - Authorization provider (None if not configured)
    """
    # Require authentication
    user = ctx.require_user()  # Raises 401 if not logged in
    
    # Optional: require specific role
    # ctx.require_role("editor")  # Raises 403 if missing
    
    # Get context from memory (if configured)
    context = []
    if ctx.memory:
        results = ctx.memory.search(query=query, user_id=user["email"], limit=3)
        context = [r.get("memory") for r in results if r.get("memory")]
    
    # Generate response with LLM (if configured)
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[
                {"role": "system", "content": f"Context: {context}"},
                {"role": "user", "content": query},
            ],
        )
        return {"response": response.choices[0].message.content}
    
    return {"error": "LLM not configured"}
```

### When to Use Which

There are **two valid patterns** for accessing mdb-engine services. Use the right one for your context:

#### Pattern 1: FastAPI Dependencies (Route Handlers)

Use `Depends()` in route handlers - it's clean, testable, and auto-resolves the app slug:

```python
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)
```

#### Pattern 2: Direct Engine Access (Outside Request Context)

Use direct engine methods when you're **not in a request context**:

```python
@app.on_event("startup")
async def startup():
    db = engine.get_scoped_db(APP_SLUG)
    await db.items.create_index("name")
```

#### Decision Table

| Context | Pattern | Why |
|---------|---------|-----|
| Route handlers | `db=Depends(get_scoped_db)` | Clean DI, testable, auto-resolves slug |
| Startup/shutdown hooks | `engine.get_scoped_db(APP_SLUG)` | No request context available |
| Custom decorators | `engine.get_scoped_db(APP_SLUG)` | Runs before route DI |
| Middleware | `engine.get_scoped_db(APP_SLUG)` | Runs before route DI |
| WebSocket setup | `engine.get_scoped_db(APP_SLUG)` | Before connection established |
| Background tasks | `engine.get_scoped_db(APP_SLUG)` | No request context |
| Testing with mocks | `Depends()` pattern | Easy to override |

#### Quick Decision

```
Are you inside a route handler?
├─ YES → Use Depends(get_scoped_db) or AppContext
└─ NO  → Use engine.get_scoped_db(APP_SLUG)
```

---

## Database Access

### Always Use Scoped Database

The scoped database automatically:
- Prefixes collection names with your app slug
- Adds `app_id` filter to all queries
- Ensures data isolation between apps

```python
# ✅ GOOD: Use dependency injection
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)

# ✅ GOOD: AppContext for multiple services
@app.get("/dashboard")
async def dashboard(ctx: AppContext = Depends()):
    items = await ctx.db.items.find({}).to_list(100)
    users = await ctx.db.users.count_documents({})
    return {"items": len(items), "users": users}
```

### Collection Operations

```python
@app.post("/items")
async def create_item(item: dict, db=Depends(get_scoped_db)):
    # Insert
    result = await db.items.insert_one(item)
    return {"id": str(result.inserted_id)}

@app.get("/items/{item_id}")
async def get_item(item_id: str, db=Depends(get_scoped_db)):
    from bson import ObjectId
    item = await db.items.find_one({"_id": ObjectId(item_id)})
    if not item:
        raise HTTPException(404, "Item not found")
    item["_id"] = str(item["_id"])
    return item

@app.put("/items/{item_id}")
async def update_item(item_id: str, updates: dict, db=Depends(get_scoped_db)):
    from bson import ObjectId
    result = await db.items.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": updates}
    )
    return {"modified": result.modified_count > 0}

@app.delete("/items/{item_id}")
async def delete_item(item_id: str, db=Depends(get_scoped_db)):
    from bson import ObjectId
    result = await db.items.delete_one({"_id": ObjectId(item_id)})
    return {"deleted": result.deleted_count > 0}
```

---

## AI/ML Services

### Embedding Service

For text chunking and vector embeddings:

```python
from mdb_engine.dependencies import get_scoped_db, get_embedding_service

@app.post("/ingest")
async def ingest_document(
    content: str,
    source_id: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    """Process and store document with embeddings."""
    result = await embedding_service.process_and_store(
        text_content=content,
        source_id=source_id,
        collection=db.knowledge_base,
        metadata={"ingested_at": datetime.utcnow().isoformat()},
    )
    return {
        "chunks_created": result["chunks_created"],
        "source_id": source_id,
    }

@app.post("/search")
async def semantic_search(
    query: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    """Search using vector similarity."""
    query_embedding = await embedding_service.embed_chunks([query])
    
    # Use MongoDB Atlas Vector Search
    results = await db.knowledge_base.aggregate([
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_embedding[0],
                "numCandidates": 100,
                "limit": 10,
            }
        },
        {"$project": {"text": 1, "score": {"$meta": "vectorSearchScore"}}}
    ]).to_list(10)
    
    return {"results": results}
```

### LLM Client

Auto-configured OpenAI/AzureOpenAI client:

```python
from mdb_engine.dependencies import get_llm_client, get_llm_model_name

@app.post("/chat")
async def chat(
    message: str,
    llm=Depends(get_llm_client),
):
    """Simple chat endpoint."""
    response = llm.chat.completions.create(
        model=get_llm_model_name(),  # Auto-detects deployment/model name
        messages=[{"role": "user", "content": message}],
    )
    return {"response": response.choices[0].message.content}

@app.post("/chat-with-history")
async def chat_with_history(
    message: str,
    history: list,
    ctx: AppContext = Depends(),
):
    """Chat with conversation history."""
    if not ctx.llm:
        raise HTTPException(503, "LLM not configured")
    
    messages = history + [{"role": "user", "content": message}]
    
    response = ctx.llm.chat.completions.create(
        model=ctx.llm_model,
        messages=messages,
        temperature=0.7,
    )
    
    return {
        "response": response.choices[0].message.content,
        "model": ctx.llm_model,
    }
```

### Memory Service (Mem0)

For persistent semantic memory:

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/remember")
async def remember(
    content: str,
    user_id: str,
    memory=Depends(get_memory_service),
):
    """Store a memory for a user."""
    if not memory:
        raise HTTPException(503, "Memory service not configured")
    
    result = memory.add(
        messages=[{"role": "user", "content": content}],
        user_id=user_id,
    )
    return {"stored": True, "memory_id": result.get("id")}

@app.get("/recall")
async def recall(
    query: str,
    user_id: str,
    memory=Depends(get_memory_service),
):
    """Recall relevant memories."""
    if not memory:
        return {"memories": [], "configured": False}
    
    results = memory.search(query=query, user_id=user_id, limit=5)
    return {
        "memories": [r.get("memory") for r in results if r.get("memory")],
        "configured": True,
    }
```

---

## Authentication & Authorization

### Getting Current User

```python
from mdb_engine.dependencies import get_current_user, get_user_roles

@app.get("/me")
async def get_me(user=Depends(get_current_user)):
    """Get current user info."""
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {
        "email": user.get("email"),
        "id": str(user.get("_id", user.get("id"))),
    }

@app.get("/my-roles")
async def get_my_roles(
    user=Depends(get_current_user),
    roles=Depends(get_user_roles),
):
    """Get current user's roles."""
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"user": user.get("email"), "roles": roles}
```

### Using AppContext for Auth

AppContext provides convenient auth helpers:

```python
from mdb_engine.dependencies import AppContext

@app.get("/profile")
async def profile(ctx: AppContext = Depends()):
    """Require authentication."""
    user = ctx.require_user()  # Raises 401 if not logged in
    return {"email": user["email"]}

@app.get("/admin")
async def admin_only(ctx: AppContext = Depends()):
    """Require admin role."""
    user = ctx.require_role("admin")  # Raises 403 if not admin
    return {"admin": user["email"]}

@app.get("/editor-or-admin")
async def editor_or_admin(ctx: AppContext = Depends()):
    """Require editor OR admin role."""
    user = ctx.require_role("editor", "admin")  # Any of these roles
    return {"user": user["email"], "roles": ctx.user_roles}

@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, ctx: AppContext = Depends()):
    """Check fine-grained permission."""
    ctx.require_user()
    
    # Check specific permission
    if not await ctx.check_permission("documents", "delete"):
        raise HTTPException(403, "Cannot delete documents")
    
    await ctx.db.documents.delete_one({"_id": doc_id})
    return {"deleted": True}
```

### Authorization Provider

For direct access to Casbin/OSO:

```python
from mdb_engine.dependencies import get_authz_provider, get_current_user

@app.get("/can-access")
async def check_access(
    resource: str,
    action: str,
    user=Depends(get_current_user),
    authz=Depends(get_authz_provider),
):
    """Check if user can perform action on resource."""
    if not user:
        return {"allowed": False, "reason": "not_authenticated"}
    
    if not authz:
        return {"allowed": True, "reason": "no_authz_configured"}
    
    allowed = await authz.check(user.get("email"), resource, action)
    return {"allowed": allowed}
```

### CSRF Protection for Frontend

When building web applications with JavaScript frontends, all state-changing requests (POST, PUT, DELETE) must include a CSRF token. MDB_ENGINE automatically sets a `csrf_token` cookie when CSRF protection is enabled.

#### Reading the CSRF Token

Add this helper function to your JavaScript:

```javascript
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}
```

#### Including CSRF Token in Requests

Include the `X-CSRF-Token` header in all state-changing requests:

```javascript
// POST request with CSRF token
async function createItem(data) {
    const response = await fetch('/api/items', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin',
        body: JSON.stringify(data)
    });
    return response.json();
}

// DELETE request with CSRF token
async function deleteItem(id) {
    const response = await fetch(`/api/items/${id}`, {
        method: 'DELETE',
        headers: {
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin'
    });
    return response.json();
}
```

#### AJAX Login/Logout Pattern

For authentication endpoints, use JSON responses instead of redirects:

```javascript
// Login with CSRF token
async function login(email, password) {
    const formData = new FormData();
    formData.append('email', email);
    formData.append('password', password);
    
    const response = await fetch('/login', {
        method: 'POST',
        headers: {
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin',
        body: formData
    });
    
    const result = await response.json();
    if (result.success) {
        window.location.href = result.redirect || '/';
    } else {
        showError(result.detail || 'Login failed');
    }
}

// Logout (must be POST, not GET)
async function logout() {
    const response = await fetch('/logout', {
        method: 'POST',
        headers: {
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin'
    });
    
    const result = await response.json();
    if (result.success) {
        window.location.href = result.redirect || '/login';
    }
}
```

#### Backend Pattern for AJAX Auth

Return JSON responses from login/register/logout endpoints:

```python
from fastapi.responses import JSONResponse

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    """Login endpoint returning JSON for JavaScript frontend."""
    result = await authenticate_user(email, password)
    
    if result["success"]:
        json_response = JSONResponse({"success": True, "redirect": "/dashboard"})
        # Copy auth cookies from the result response
        for key, value in result["response"].headers.items():
            if key.lower() == "set-cookie":
                json_response.headers.append(key, value)
        return json_response
    
    return JSONResponse(
        {"success": False, "detail": result.get("error", "Login failed")},
        status_code=401
    )

@app.post("/logout")
async def logout(request: Request):
    """Logout endpoint - must be POST with CSRF token."""
    response = JSONResponse({"success": True, "redirect": "/login"})
    response = await logout_user(request, response)
    return response
```

---

## Common Patterns

### RAG (Retrieval Augmented Generation)

```python
@app.post("/rag")
async def rag_query(query: str, ctx: AppContext = Depends()):
    """Complete RAG pipeline."""
    user = ctx.require_user()
    
    if not ctx.embedding_service or not ctx.llm:
        raise HTTPException(503, "RAG services not configured")
    
    # 1. Embed the query
    query_embedding = await ctx.embedding_service.embed_chunks([query])
    
    # 2. Search for relevant documents
    docs = await ctx.db.knowledge_base.aggregate([
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_embedding[0],
                "numCandidates": 100,
                "limit": 5,
            }
        }
    ]).to_list(5)
    
    context = "\n".join([d.get("text", "") for d in docs])
    
    # 3. Generate response with context
    response = ctx.llm.chat.completions.create(
        model=ctx.llm_model,
        messages=[
            {"role": "system", "content": f"Use this context:\n{context}"},
            {"role": "user", "content": query},
        ],
    )
    
    return {
        "response": response.choices[0].message.content,
        "sources": len(docs),
    }
```

### Chat with Memory

```python
@app.post("/chat-with-memory")
async def chat_with_memory(
    message: str,
    user_id: str,
    ctx: AppContext = Depends(),
):
    """Chat that remembers past conversations."""
    if not ctx.llm:
        raise HTTPException(503, "LLM not configured")
    
    # Retrieve relevant memories
    memories = []
    if ctx.memory:
        results = ctx.memory.search(query=message, user_id=user_id, limit=3)
        memories = [r.get("memory") for r in results if r.get("memory")]
    
    # Build messages with memory context
    messages = []
    if memories:
        messages.append({
            "role": "system",
            "content": f"Previous context: {'; '.join(memories)}"
        })
    messages.append({"role": "user", "content": message})
    
    # Generate response
    response = ctx.llm.chat.completions.create(
        model=ctx.llm_model,
        messages=messages,
    )
    
    reply = response.choices[0].message.content
    
    # Store this interaction as memory
    if ctx.memory:
        ctx.memory.add(
            messages=[
                {"role": "user", "content": message},
                {"role": "assistant", "content": reply},
            ],
            user_id=user_id,
        )
    
    return {"response": reply, "memories_used": len(memories)}
```

### Health Check with Services

```python
@app.get("/health")
async def health(ctx: AppContext = Depends()):
    """Comprehensive health check."""
    health = {
        "status": "healthy",
        "app": ctx.slug,
        "services": {},
    }
    
    # Check database via engine health
    try:
        engine_health = await ctx.engine.get_health_status()
        health["services"]["database"] = engine_health.get("mongodb", "unknown")
    except (ConnectionError, TimeoutError, OSError):
        health["services"]["database"] = "connection_failed"
        health["status"] = "degraded"
    
    # Check optional services - graceful None handling
    health["services"]["embedding"] = "ok" if ctx.embedding_service else "not_configured"
    health["services"]["memory"] = "ok" if ctx.memory else "not_configured"
    health["services"]["llm"] = "ok" if ctx.llm else "not_configured"
    health["services"]["authz"] = "ok" if ctx.authz else "not_configured"
    
    status_code = 200 if health["status"] == "healthy" else 503
    return JSONResponse(health, status_code=status_code)
```

---

## Anti-Patterns to Avoid

### ❌ Don't Create Direct MongoDB Connections

```python
# ❌ BAD: Bypasses connection pooling and scoping
from motor.motor_asyncio import AsyncIOMotorClient

@app.get("/items")
async def get_items():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["my_database"]
    items = await db.items.find({}).to_list(100)
    client.close()
    return items

# ✅ GOOD: Use dependency injection
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)
```

### ❌ Don't Use Global State

```python
# ❌ BAD: Global state causes issues with testing and multiple workers
_global_db = None

@app.on_event("startup")
async def startup():
    global _global_db
    _global_db = engine.get_scoped_db("my_app")

@app.get("/items")
async def get_items():
    return await _global_db.items.find({}).to_list(100)

# ✅ GOOD: Request-scoped dependencies
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)
```

### ❌ Don't Hardcode App Slugs in Dependencies

```python
# ❌ BAD: Hardcoded slug
@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("my_app")  # Hardcoded
    return await db.items.find({}).to_list(100)

# ✅ GOOD: Let dependency get slug from app.state
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):  # Auto-resolved
    return await db.items.find({}).to_list(100)
```

### ❌ Don't Manually Initialize Services in Routes

```python
# ❌ BAD: Creating service instances in routes
from mdb_engine.embeddings import EmbeddingService

@app.post("/embed")
async def embed(text: str):
    service = EmbeddingService(config={...})  # New instance every request!
    return await service.embed_chunks([text])

# ✅ GOOD: Use dependency injection
@app.post("/embed")
async def embed(text: str, embedding_service=Depends(get_embedding_service)):
    return await embedding_service.embed_chunks([text])
```

### ❌ Don't Use Generic Exception Handling

```python
# ❌ BAD: Catches everything, hides bugs
try:
    await db.command("ping")
except Exception as e:
    logger.error(f"Error: {e}")

# ✅ GOOD: Be specific about what can fail
try:
    await db.command("ping")
except (ConnectionError, TimeoutError, OSError):
    logger.error("Database connection failed")
```

### ❌ Don't Ignore Missing Services

```python
# ❌ BAD: Crashes if service not configured
@app.post("/embed")
async def embed(text: str, ctx: AppContext = Depends()):
    return await ctx.embedding_service.embed_chunks([text])  # None if not configured!

# ✅ GOOD: Graceful handling of optional services
@app.post("/embed")
async def embed(text: str, ctx: AppContext = Depends()):
    if not ctx.embedding_service:
        raise HTTPException(503, "Embedding service not configured")
    return await ctx.embedding_service.embed_chunks([text])
```

---

## Migration from Old Patterns

### From `set_global_engine` to Dependencies

**Before (old pattern):**
```python
from mdb_engine.embeddings import set_global_engine, get_embedding_service_dependency

@app.on_event("startup")
async def startup():
    await engine.initialize()
    set_global_engine(engine, app_slug=APP_SLUG)

@app.post("/embed")
async def embed(text: str, service=Depends(get_embedding_service_dependency)):
    return await service.embed_chunks([text])
```

**After (new pattern):**
```python
from mdb_engine.dependencies import get_embedding_service

# No startup code needed - create_app() handles everything

@app.post("/embed")
async def embed(text: str, service=Depends(get_embedding_service)):
    return await service.embed_chunks([text])
```

### From Local `get_db()` to `get_scoped_db`

**Before:**
```python
def get_db():
    if not engine.initialized:
        raise HTTPException(503, "Engine not initialized")
    return engine.get_scoped_db(APP_SLUG)

@app.get("/items")
async def get_items(db=Depends(get_db)):
    return await db.items.find({}).to_list(100)
```

**After:**
```python
from mdb_engine.dependencies import get_scoped_db

@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)
```

### From Manual Auth Checks to AppContext

**Before:**
```python
@app.get("/admin")
async def admin(request: Request):
    user = request.state.user
    if not user:
        raise HTTPException(401, "Not authenticated")
    if "admin" not in request.state.user_roles:
        raise HTTPException(403, "Admin required")
    return {"admin": user["email"]}
```

**After:**
```python
from mdb_engine.dependencies import AppContext

@app.get("/admin")
async def admin(ctx: AppContext = Depends()):
    user = ctx.require_role("admin")  # One line!
    return {"admin": user["email"]}
```

---

## Summary

1. **Use `Depends(get_scoped_db)`** for simple database access in routes
2. **Use `AppContext`** when you need multiple services or auth helpers
3. **Use `engine.get_scoped_db()`** for startup, decorators, and background tasks
4. **Never create direct MongoDB connections** - always use scoped database
5. **Let `create_app()` handle initialization** - no manual startup code needed
6. **Use `ctx.require_user()` and `ctx.require_role()`** for clean auth checks

For more examples, see the [examples directory](../examples/).

---

## Appendix: Engine API Reference

Direct engine methods available for use outside request context:

### Database Access

```python
# Get scoped database for an app
db = engine.get_scoped_db(app_slug)

# Collections are auto-prefixed with app slug
await db.items.find({}).to_list(100)
```

### Services

```python
# Embedding service for text chunking & vectors
embedding_service = engine.get_embedding_service(app_slug)

# Memory service for semantic memory (Mem0)
memory_service = engine.get_memory_service(app_slug)
```

### App Configuration

```python
# Get full app config (from manifest)
config = engine.get_app(app_slug)

# Access specific config sections
auth_config = config.get("auth", {})
embedding_config = config.get("embedding_config", {})
memory_config = config.get("memory_config", {})
```

### Lifecycle

```python
# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="my_app",
    manifest=Path(__file__).parent / "manifest.json",
    title="My App",
)

# Manual initialization (rarely needed - create_app handles this)
await engine.initialize()

# Manual shutdown (rarely needed - create_app handles this)
await engine.shutdown()
```

### Full Example: Startup Hook

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
)

app = engine.create_app(
    slug="my_app",
    manifest=Path(__file__).parent / "manifest.json",
)

@app.on_event("startup")
async def additional_setup():
    """Custom startup logic using direct engine access."""
    db = engine.get_scoped_db("my_app")
    
    # Create indexes
    await db.items.create_index("created_at")
    await db.items.create_index([("name", 1), ("status", 1)])
    
    # Initialize embedding service if needed
    embedding_service = engine.get_embedding_service("my_app")
    if embedding_service:
        print("Embedding service ready")
```

