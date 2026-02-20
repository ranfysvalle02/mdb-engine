# SSO Multi-App Deep Dive: The Complete Mechanics Guide

A comprehensive technical deep-dive into how MDB-Engine's SSO multi-app system actually works under the hood --- from JWT token lifecycle to memory service integration, middleware execution order, data isolation, and the common gotchas that will trip you up.

---

## Table of Contents

- [1. Architecture Overview](#1-architecture-overview)
- [2. The Engine Lifecycle](#2-the-engine-lifecycle)
- [3. How SSO Actually Works](#3-how-sso-actually-works)
- [4. The Middleware Stack (Execution Order Matters)](#4-the-middleware-stack-execution-order-matters)
- [5. Manifest Configuration In Depth](#5-manifest-configuration-in-depth)
- [6. Integrating Memory Service Across Apps](#6-integrating-memory-service-across-apps)
- [7. Data Isolation and Scoped Collections](#7-data-isolation-and-scoped-collections)
- [8. WebSocket Authentication in Multi-App](#8-websocket-authentication-in-multi-app)
- [9. The RequestContext and Dependency Injection](#9-the-requestcontext-and-dependency-injection)
- [10. CORS, CSRF, and Cookie Mechanics](#10-cors-csrf-and-cookie-mechanics)
- [11. Route Auto-Import and the `app` Injection](#11-route-auto-import-and-the-app-injection)
- [12. CognitiveEngine + SSO: Per-User Memory Across Apps](#12-cognitiveengine--sso-per-user-memory-across-apps)
- [13. Common Issues and How to Fix Them](#13-common-issues-and-how-to-fix-them)
- [14. Full Working Example](#14-full-working-example)
- [15. Production Checklist](#15-production-checklist)

---

## 1. Architecture Overview

When you call `engine.create_multi_app()`, MDB-Engine creates a **parent FastAPI application** that mounts multiple **child FastAPI applications** at distinct path prefixes. All children share a single `MongoDBEngine` instance, a single MongoDB connection pool, and (when SSO is enabled) a single `SharedUserPool`.

```
                          ┌─────────────────────────────┐
                          │     Parent FastAPI App       │
                          │                             │
                          │  /health  /metrics  /docs   │
                          │  /auth/ticket               │
                          │  /auth/websocket-session    │
                          │  /_mdb/routes               │
                          │                             │
                          │  Middleware Stack:           │
                          │   1. DiagnosticMiddleware    │
                          │   2. RequestScopeMiddleware  │
                          │   3. CSRFMiddleware          │
                          │   4. DynamicCORSMiddleware   │
                          └──────┬──────────┬───────────┘
                                 │          │
                    ┌────────────┘          └────────────┐
                    ▼                                    ▼
         ┌──────────────────┐                ┌──────────────────┐
         │  /auth-hub       │                │  /my-app         │
         │  (Child App)     │                │  (Child App)     │
         │                  │                │                  │
         │  Auth middleware  │                │  Auth middleware  │
         │  Context middleware│               │  Context middleware│
         │  Routes (web.py) │                │  Routes (web.py) │
         │                  │                │  Memory service   │
         └──────────────────┘                └──────────────────┘
                    │                                    │
                    └───────────┬─────────────────────────┘
                                ▼
                    ┌──────────────────────┐
                    │   MongoDBEngine      │
                    │                      │
                    │  SharedUserPool      │
                    │  Connection Pool     │
                    │  Memory Services     │
                    │  Embedding Services  │
                    │  WebSocket Tickets   │
                    └──────┬───────────────┘
                           ▼
                    ┌──────────────────────┐
                    │   MongoDB Atlas      │
                    │                      │
                    │  _mdb_engine_shared_ │
                    │    users             │
                    │  _mdb_engine_token_  │
                    │    blacklist         │
                    │  auth_hub_*          │
                    │  my_app_*            │
                    │  my_app_memories     │
                    └──────────────────────┘
```

### Key Insight: One Process, Many Apps

Every app runs in the **same Python process**. This means:

- Shared memory (user pool, ticket stores, caches)
- Shared event loop
- Shared MongoDB connection pool (typically 10-100 connections)
- No network hops between apps for internal calls
- A crash in one app's route handler does NOT crash other apps (FastAPI isolates mounted apps)

---

## 2. The Engine Lifecycle

Understanding the lifecycle is critical because **service initialization order matters**.

### Phase 1: `create_multi_app()` Returns (Synchronous-ish)

When you call `engine.create_multi_app(apps=[...])`, the following happens **before** the server starts:

1. App configs are parsed and validated
2. Path prefixes are checked for conflicts
3. Manifests are read to detect which apps use `auth.mode: "shared"`
4. WebSocket ticket store is initialized (in-memory)
5. Parent FastAPI app is created with a lifespan context manager
6. Middleware is added to parent app (CORS, CSRF, diagnostics)
7. Health, metrics, and route introspection endpoints are registered
8. **The parent app is returned** --- but nothing is mounted yet!

### Phase 2: Lifespan Startup (When Server Starts)

When Uvicorn starts the app, the lifespan context manager fires:

```
Lifespan Startup Order:
  1. engine.initialize()           → MongoDB connection established
  2. WebSocket ticket/session endpoints registered on parent
  3. SharedUserPool initialized    → _mdb_engine_shared_users indexes created
  4. Demo users seeded (if configured)
  5. AuthAuditLog initialized
  6. FOR EACH child app:
     a. Manifest loaded
     b. child_app = engine.create_app(slug, manifest, is_sub_app=True)
     c. engine + app_slug set on child_app.state
     d. Routes auto-imported from web.py / routes.py
     e. user_pool shared to child_app.state
     f. WebSocket session manager + ticket store shared
     g. AppContextMiddleware added to child
     h. WebSocket routes registered on PARENT (not child!)
     i. Memory service initialized (if memory_config.enabled)
     j. child_app mounted at path_prefix
     k. CORS config merged from child to parent
  7. app.state.mounted_apps updated with final statuses
```

### Why This Matters

- **Memory service initialization happens inside the lifespan**, not at import time. If your memory config references an LLM provider and the API key is missing, the app will still start --- memory just won't be available for that app.
- **Route auto-import happens BEFORE mounting**. Your `web.py` gets the `app` variable injected, and decorators execute against the child app.
- **WebSocket routes go on the parent**, not the child. This is because Starlette's `Mount` doesn't forward WebSocket upgrade requests properly.

---

## 3. How SSO Actually Works

### The Token Flow

```
                    ┌──────────┐
                    │  Browser  │
                    └─────┬────┘
                          │
           1. POST /auth-hub/login
              {email, password}
                          │
                          ▼
                ┌─────────────────┐
                │   Auth Hub App   │
                │                 │
                │  SharedUserPool │◄── Single instance shared
                │  .authenticate()│    across ALL apps
                │                 │
                │  Returns JWT    │
                └────────┬────────┘
                         │
          2. Set-Cookie: mdb_auth_token=<JWT>
             HttpOnly, SameSite=Lax, Secure (prod)
             Max-Age: 86400 (24h default)
                         │
                         ▼
                    ┌──────────┐
                    │  Browser  │  Cookie stored
                    └─────┬────┘
                          │
           3. GET /my-app/dashboard
              Cookie: mdb_auth_token=<JWT>
                          │
                          ▼
                ┌─────────────────────────┐
                │  Parent App Middleware    │
                │  (CORS, CSRF validated)  │
                └────────┬────────────────┘
                         │
                         ▼
                ┌─────────────────────────┐
                │  Child App: /my-app      │
                │                         │
                │  LazySharedAuthMiddleware│
                │   1. Extract JWT from   │
                │      cookie             │
                │   2. user_pool          │
                │      .validate_token()  │
                │   3. Check blacklist    │
                │   4. Fetch fresh user   │
                │      from MongoDB       │
                │   5. Check app roles    │
                │   6. Set request.state  │
                │      .user             │
                │      .user_roles       │
                └────────┬────────────────┘
                         │
                         ▼
                ┌─────────────────────────┐
                │  Route Handler           │
                │                         │
                │  user = request.state   │
                │          .user          │
                │  # Fully authenticated! │
                └─────────────────────────┘
```

### JWT Token Anatomy

When `SharedUserPool.authenticate()` succeeds, it generates a JWT with these claims:

```json
{
  "sub": "65a1b2c3d4e5f6a7b8c9d0e1",   // User's MongoDB _id
  "email": "user@example.com",
  "jti": "aB3cD4eF5gH6iJ7k",            // Unique token ID (for revocation)
  "iat": 1708041600,                      // Issued at
  "exp": 1708128000,                      // Expires (iat + token_expiry_hours)
  "ip": "192.168.1.1",                    // Optional: session-bound IP
  "fp": "sha256_of_user_agent_etc"        // Optional: device fingerprint
}
```

### Token Validation Pipeline

Every request to a protected route goes through this pipeline:

```python
# Inside LazySharedAuthMiddleware.dispatch()

# 1. Extract token
token = request.cookies.get("mdb_auth_token")
#   or: Authorization: Bearer <token>

# 2. Decode and verify signature
payload = jwt.decode(token, verification_key, algorithms=["HS256"])

# 3. Check blacklist (revoked tokens)
jti = payload["jti"]
is_revoked = await blacklist_collection.find_one({"jti": jti})
# If blacklist DB is down:
#   blacklist_fail_closed=True  → REJECT (secure default)
#   blacklist_fail_closed=False → ALLOW (availability mode)

# 4. Fetch FRESH user data (roles may have changed since token was issued)
user = await shared_users_collection.find_one({
    "_id": ObjectId(payload["sub"]),
    "is_active": True
})

# 5. Check app-specific roles
user_roles = user["app_roles"].get("my-app", [])
# e.g., ["viewer", "editor"]

# 6. Check required role with hierarchy
# If roles=["viewer", "editor", "admin"], then admin > editor > viewer
has_access = "viewer" in user_roles  # or via hierarchy
```

### Per-App Role Model

Users have roles scoped to specific apps:

```json
{
  "email": "user@example.com",
  "app_roles": {
    "auth-hub": ["admin"],
    "my-app": ["editor"],
    "another-app": ["viewer"]
  }
}
```

This means:
- The same user can be `admin` on Auth Hub but only `viewer` on another app
- Roles are checked at the **middleware level** per-app
- Role hierarchy is auto-generated from the manifest's `auth.roles` array (ordered least → most privileged)

---

## 4. The Middleware Stack (Execution Order Matters)

Middleware executes in **reverse addition order** (last added = outermost = runs first). Here's the actual execution order for an incoming request:

### Parent App Middleware (runs for ALL requests)

```
REQUEST ENTERS
    │
    ▼
1. DynamicCORSMiddleware (outermost)
   - Handles OPTIONS preflight
   - Sets Access-Control-* headers
   - Reads config from app.state.cors_config (dynamic!)
    │
    ▼
2. CSRFMiddleware (if shared auth enabled)
   - Validates Origin header for state-changing requests
   - Exempts public routes + WebSocket endpoints
   - Uses parent app's CORS allowed origins
    │
    ▼
3. RequestScopeMiddleware
   - Begins DI request scope (ScopeManager.begin_request())
   - Ensures cleanup on response (ScopeManager.end_request())
    │
    ▼
4. DiagnosticMiddleware (innermost on parent)
   - Logs all requests (especially WebSocket upgrades)
   - Debug-level, minimal overhead
    │
    ▼
[Request is routed to child app based on path prefix]
```

### Child App Middleware (runs for that app's routes only)

```
    │
    ▼
5. AppContextMiddleware
   - Sets request.state.app_base_path
   - Sets request.state.auth_hub_url
   - Sets request.state.app_slug
   - Sets request.state.engine
   - Sets request.state.manifest
   - Sets request.state.mounted_apps
    │
    ▼
6. LazySharedAuthMiddleware (if auth.mode="shared")
   - Gets user_pool from app.state (lazy — set during lifespan)
   - Extracts JWT from cookie/header
   - Validates token
   - Checks roles
   - Sets request.state.user and request.state.user_roles
    │
    ▼
7. [Additional child middleware: rate limiting, etc.]
    │
    ▼
ROUTE HANDLER EXECUTES
```

### Critical Gotcha: Middleware on Parent vs Child

Middleware added to the **parent** app runs for ALL requests including all child apps. Middleware added to a **child** app runs only for that child's routes.

This is why:
- CORS → parent (needs to handle all origins)
- CSRF → parent (needs to validate all state-changing requests)
- Auth → child (each child has its own role requirements)
- Rate limiting → child (per-app rate limits)

---

## 5. Manifest Configuration In Depth

### Auth Hub Manifest (`auth-hub/manifest.json`)

The auth hub is the **central authentication app**. It needs public routes for login/register and typically has `admin` as its `require_role`:

```json
{
  "schema_version": "2.0",
  "slug": "auth_hub",
  "name": "Auth Hub",
  "auth": {
    "mode": "shared",
    "require_role": "admin",
    "default_role": "admin",
    "auto_assign_default_role": true,
    "roles": ["viewer", "editor", "admin"],
    "public_routes": [
      "/",
      "/login",
      "/register",
      "/api/login",
      "/api/register",
      "/api/logout",
      "/health",
      "/docs",
      "/openapi.json",
      "/static/*"
    ],
    "session_binding": {
      "bind_ip": false,
      "bind_fingerprint": true,
      "strict_fingerprint": false
    },
    "users": {
      "demo_user_seed_strategy": "auto",
      "demo_users": [
        {
          "email": "admin@example.com",
          "password": "changeme",
          "app_roles": {
            "auth_hub": ["admin"],
            "my_app": ["admin"]
          }
        }
      ]
    },
    "audit": {
      "enabled": true,
      "retention_days": 90
    }
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000", "http://localhost:8000"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

### Consumer App Manifest (`my-app/manifest.json`)

A consumer app that uses SSO and has memory service enabled:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "require_role": "viewer",
    "roles": ["viewer", "editor", "admin"],
    "public_routes": [
      "/",
      "/health",
      "/docs",
      "/openapi.json",
      "/static/*"
    ],
    "session_binding": {
      "bind_ip": false,
      "bind_fingerprint": true,
      "strict_fingerprint": false
    }
  },
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "extraction": {
      "enabled": true,
      "model": "gpt-4o-mini"
    },
    "scoring": {
      "importance_weight": 0.4,
      "recency_weight": 0.3,
      "access_weight": 0.3
    },
    "embedding": {
      "model": "text-embedding-3-small",
      "dimensions": 1536
    }
  },
  "embedding_config": {
    "enabled": true,
    "provider": "openai",
    "model": "text-embedding-3-small"
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000", "http://localhost:8000"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

### Key Configuration Rules

1. **`auth.mode: "shared"` on at least one app** triggers SSO. If ANY app uses shared mode, the SharedUserPool is initialized for the whole deployment.

2. **`auth.public_routes`** are relative to the app, not the parent. If your app is mounted at `/my-app` and you list `/login`, the middleware matches requests to `/my-app/login`.

3. **`auth.require_role`** is checked AFTER token validation. If a valid user doesn't have the required role for this app, they get a 403, not a 401.

4. **`auto_assign_default_role: true`** automatically grants the `default_role` to users who authenticate but have NO roles for that app. Use carefully --- only enable on the auth hub or apps where all authenticated users should have base access.

5. **`memory_config.enabled: true`** triggers memory service initialization during lifespan. The memory collection is named `{app_slug}_memories` and is automatically scoped.

---

## 6. Integrating Memory Service Across Apps

This is where things get interesting. Each app can have its **own memory service**, and because of SSO, the same `user_id` is consistent across all apps.

### How Memory Service Gets Initialized

During the lifespan startup, for each child app:

```python
# Inside create_multi_app lifespan
memory_config = app_manifest_data.get("memory_config")
if memory_config and memory_config.get("enabled", False):
    await engine._service_initializer.initialize_memory_service(
        slug,           # "my_app"
        memory_config   # from manifest
    )
```

This calls `ServiceInitializer.initialize_memory_service()` which:

1. Creates a `ScopedCollectionWrapper` for `{slug}_memories`
2. Creates an `EmbeddingService` (using configured provider)
3. Creates an `LLMService` (for fact extraction)
4. Calls `get_memory_service()` factory which builds a `CognitiveMemoryService`
5. Stores the service in `engine._memory_services[slug]`

### Accessing Memory in Route Handlers

There are three ways to access the memory service in your routes:

#### Method 1: Via RequestContext (Recommended)

```python
from fastapi import Depends, Request
from mdb_engine.dependencies import RequestContext, get_request_context

@app.post("/chat")
async def chat(request: Request, ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()  # Raises 401 if not authenticated
    memory = ctx.memory         # CognitiveMemoryService or None

    if memory is None:
        raise HTTPException(503, "Memory service not configured")

    # Add a memory for this user
    await memory.add(
        content="User prefers dark mode",
        user_id=user["_id"],           # SSO user ID (consistent across apps!)
        metadata={
            "category": "preference",
            "source": "chat",
        }
    )

    # Search memories
    results = await memory.search(
        query="What does the user prefer?",
        user_id=user["_id"],
        limit=5,
    )

    return {"memories": results}
```

#### Method 2: Via Dependency Injection

```python
from fastapi import Depends
from mdb_engine.dependencies import get_memory_service, get_current_user
from mdb_engine.memory import BaseMemoryService

@app.get("/memories")
async def list_memories(
    memory: BaseMemoryService = Depends(get_memory_service),
    user: dict = Depends(require_user()),
):
    return await memory.list(user_id=user["_id"])
```

#### Method 3: Direct Engine Access

```python
@app.post("/remember")
async def remember(request: Request):
    engine = request.state.engine
    slug = request.state.app_slug
    memory = engine.get_memory_service(slug)

    user = request.state.user
    await memory.add(
        content="Important fact to remember",
        user_id=user["_id"],
    )
```

### Cross-App Memory: Same User, Different Apps

Because SSO uses a **centralized user pool**, the `user["_id"]` is the same MongoDB ObjectId regardless of which app the user is accessing. This means:

```
User logs into Auth Hub → user._id = "65a1b2c3..."

Visits /my-app:
  memory.add(content="likes pizza", user_id="65a1b2c3...")
  → Stored in collection: my_app_memories

Visits /another-app:
  memory.add(content="allergic to nuts", user_id="65a1b2c3...")
  → Stored in collection: another_app_memories

# Each app has ISOLATED memory storage
# But the user_id is CONSISTENT
```

If you want to **share memories across apps**, you have two approaches:

#### Approach A: Cross-App Read Scopes

Configure `data_access` in your manifest to allow one app to read another's data:

```json
{
  "data_access": {
    "read_scopes": ["other_app"]
  }
}
```

Then query the other app's memory collection directly:

```python
# In my_app, reading from another_app's memories
other_memory = engine.get_memory_service("another_app")
if other_memory:
    results = await other_memory.search(
        query="What is the user allergic to?",
        user_id=user["_id"],
    )
```

#### Approach B: Shared Memory Bucket

Use a shared `bucket_id` across apps:

```python
# Both apps use the same bucket_id convention
SHARED_BUCKET = "shared_user_profile"

# In my_app
await memory.add(
    content="User speaks Spanish",
    user_id=user["_id"],
    metadata={"bucket_id": SHARED_BUCKET, "bucket_type": "profile"}
)

# In another_app (if it has its own memory service)
results = await memory.search(
    query="languages",
    user_id=user["_id"],
    filter={"metadata.bucket_id": SHARED_BUCKET}
)
```

### CognitiveEngine Integration

For chat-based apps that need both short-term (conversation) and long-term (facts) memory:

```python
from mdb_engine.memory.orchestrator import CognitiveEngine

# Initialize in your app's startup or route
cognitive_engine = CognitiveEngine(
    app_slug="my_app",
    memory_service=ctx.memory,                    # Long-term memory (vectors)
    chat_history_collection=chat_collection,       # Short-term memory (messages)
    memory_collection=memory_collection,           # For direct memory ops
    stm_context_limit=10,                          # Last 10 messages in context
    ltm_search_limit=5,                            # Top 5 relevant memories
    auto_summarize_threshold=20,                   # Summarize after 20 messages
    llm_service=llm_service,                       # For generating responses
    enable_context_engineering=True,                # Smart context assembly
)

# Chat with full cognitive architecture
response = await cognitive_engine.chat(
    user_id=user["_id"],
    session_id="session_abc",
    message="What do you know about me?",
    system_prompt="You are a helpful assistant with access to user memories.",
)
# response includes:
#   - AI response text
#   - Retrieved memories used for context
#   - Any new facts extracted from the conversation
```

### Memory Service Configuration Reference

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",

    "extraction": {
      "enabled": true,
      "model": "gpt-4o-mini",
      "max_facts_per_message": 5
    },

    "scoring": {
      "importance_weight": 0.4,
      "recency_weight": 0.3,
      "access_weight": 0.3,
      "decay_half_life_hours": 168
    },

    "reinforcement": {
      "enabled": true,
      "similarity_threshold": 0.85
    },

    "merging": {
      "enabled": true,
      "similarity_threshold": 0.90
    },

    "embedding": {
      "model": "text-embedding-3-small",
      "dimensions": 1536
    },

    "search": {
      "default_limit": 10,
      "min_score": 0.5
    }
  }
}
```

---

## 7. Data Isolation and Scoped Collections

MDB-Engine uses `ScopedCollectionWrapper` and `ScopedMongoWrapper` to automatically isolate data between apps.

### How It Works

When a child app queries MongoDB, all operations go through the scoped wrapper:

```python
# Your code:
await db["users"].find({"name": "Alice"})

# What actually executes:
await raw_db["my_app_users"].find({"name": "Alice", "app_id": "my_app"})
```

The scoping is automatic:
- **Collection names** are prefixed: `users` → `my_app_users`
- **Queries** get `app_id` injected: `{"name": "Alice"}` → `{"name": "Alice", "app_id": "my_app"}`
- **Inserts** get `app_id` added automatically
- **Indexes** are created on the prefixed collection

### What's NOT Scoped

These collections are intentionally shared (no app prefix):

| Collection | Purpose | Why Shared |
|---|---|---|
| `_mdb_engine_shared_users` | User accounts | SSO requires single user store |
| `_mdb_engine_token_blacklist` | Revoked tokens | Tokens are app-agnostic |
| `_mdb_engine_audit_log` | Auth audit trail | Centralized compliance |

### Memory Collection Scoping

Memory collections are scoped by app slug:

```
my_app       → my_app_memories
another_app  → another_app_memories
auth_hub     → auth_hub_memories (if memory enabled)
```

Each memory document also stores the `app_id`:

```json
{
  "_id": "...",
  "app_id": "my_app",
  "user_id": "65a1b2c3...",
  "content": "User prefers dark mode",
  "embedding": [0.1, 0.2, ...],
  "importance": 0.85,
  "access_count": 3,
  "metadata": {
    "category": "preference",
    "bucket_id": "user_prefs",
    "bucket_type": "profile"
  }
}
```

---

## 8. WebSocket Authentication in Multi-App

WebSocket auth in multi-app is more complex because:
1. Browsers don't send custom headers on WebSocket upgrade
2. Cookies may not be sent depending on SameSite policy
3. WebSocket routes must be on the **parent** app

### The Ticket-Based Flow

```
1. Client has valid JWT (from SSO login)
   │
   ▼
2. POST /auth/ticket
   Cookie: mdb_auth_token=<JWT>
   → Validates JWT
   → Creates short-lived ticket (10s TTL, single-use)
   → Returns { "ticket": "abc123" }
   │
   ▼
3. WebSocket connect: ws://host/my-app/ws?ticket=abc123
   → Parent app receives upgrade request
   → WebSocket handler validates ticket
   → Ticket consumed (cannot be reused)
   → Connection established with user context
```

### Manifest WebSocket Config

```json
{
  "websockets": {
    "chat": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": true
      },
      "ping_interval": 30,
      "ticket_ttl_seconds": 10
    }
  }
}
```

### Critical: MDB_ENGINE_MASTER_KEY

If your WebSocket endpoints use `csrf_required: true`, you MUST set the `MDB_ENGINE_MASTER_KEY` environment variable. This key is used for envelope encryption of WebSocket session keys:

```bash
# Generate a secure master key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Set it
export MDB_ENGINE_MASTER_KEY="your-generated-key"
```

Without this, you'll get:

```
RuntimeError: WebSocket routes cannot be registered for app 'my_app':
websocket_session_manager is not available.
Set MDB_ENGINE_MASTER_KEY environment variable to enable session manager.
```

---

## 9. The RequestContext and Dependency Injection

`RequestContext` is your all-in-one dependency that lazily loads everything:

```python
from mdb_engine.dependencies import RequestContext, get_request_context

@app.post("/do-stuff")
async def do_stuff(ctx: RequestContext = Depends(get_request_context)):
    # Auth
    user = ctx.user              # dict | None (from middleware)
    roles = ctx.user_roles       # list[str]
    user = ctx.require_user()    # raises 401 if None
    ctx.require_role("editor")   # raises 403 if missing

    # Database
    db = await ctx.get_db()      # ScopedMongoWrapper
    uow = await ctx.get_uow()   # UnitOfWork (repository pattern)

    # AI Services
    memory = ctx.memory          # CognitiveMemoryService | None
    embedding = ctx.embedding_service  # EmbeddingService | None
    llm = ctx.llm                # OpenAI | AzureOpenAI | None
    model = ctx.llm_model        # "gpt-4o" etc.

    # App context
    slug = ctx.slug              # "my_app"
    config = ctx.config          # manifest dict
    engine = ctx.engine          # MongoDBEngine instance
```

### How User Identity Flows Through

```
Cookie/Header → SharedAuthMiddleware → request.state.user → RequestContext.user
```

The `request.state.user` dict contains:

```python
{
    "_id": "65a1b2c3d4e5f6a7b8c9d0e1",  # String (ObjectId converted)
    "email": "user@example.com",
    "app_roles": {
        "auth_hub": ["admin"],
        "my_app": ["editor"],
    },
    "is_active": True,
    "created_at": datetime(...),
    "last_login": datetime(...),
    # password_hash is NEVER included (sanitized)
}
```

---

## 10. CORS, CSRF, and Cookie Mechanics

### Why CORS Matters for SSO

SSO relies on cookies being sent cross-origin (or same-origin with different paths). The CORS config must:

1. **Allow credentials**: `allow_credentials: true` (required for cookies)
2. **Specific origins**: When credentials are true, you CANNOT use `"*"` as origin. You must list specific origins.
3. **Match cookie domain**: Cookies set by `/auth-hub` must be readable by `/my-app`

### How Cookie Sharing Works

Because all apps are under the **same origin** (same host:port, different paths), cookies "just work":

```
Set-Cookie from /auth-hub/api/login:
  mdb_auth_token=<JWT>;
  Path=/;                 ← Cookie available to ALL paths
  HttpOnly;               ← Not accessible via JavaScript
  SameSite=Lax;           ← Sent on same-site navigations
  Secure;                 ← HTTPS only (production)
  Max-Age=86400;          ← 24 hours
```

The `Path=/` is critical. If the cookie were set with `Path=/auth-hub`, other apps wouldn't receive it.

### CORS Config Merging

Each child app can define its own CORS config in its manifest. During mounting, these are **merged** into the parent's config:

```
Parent CORS: { origins: ["*"], credentials: false }
  + Auth Hub: { origins: ["http://localhost:3000"], credentials: true }
  = Merged:   { origins: ["http://localhost:3000"], credentials: true }

  + My App:   { origins: ["http://localhost:8080"], credentials: true }
  = Merged:   { origins: ["http://localhost:3000", "http://localhost:8080"], credentials: true }
```

### CSRF Protection

CSRF middleware on the parent validates the `Origin` header for state-changing requests (POST, PUT, DELETE, PATCH). It ensures the origin matches one of the allowed CORS origins.

Exempt routes (no CSRF check):
- All `public_routes` from all child apps (with path prefixes)
- `/health`, `/docs`, `/openapi.json`, `/_mdb/routes`
- `/auth/ticket`, `/auth/websocket-session`
- GET/HEAD/OPTIONS requests

---

## 11. Route Auto-Import and the `app` Injection

When `create_multi_app` mounts a child app, it automatically discovers and imports route modules.

### Discovery Order

For each child app, it looks for:
1. `routes_module` field in manifest (if specified)
2. `web.py` in the manifest's directory
3. `routes.py` in the manifest's directory

### The Injection Mechanism

Before your module is loaded, MDB-Engine injects two variables:

```python
# Your web.py gets these injected:
app    # → The child FastAPI app instance
engine # → The MongoDBEngine instance
```

This means your `web.py` should use the injected `app`, not create a new one:

```python
# web.py (CORRECT)
from fastapi import Request, Depends
from mdb_engine.dependencies import RequestContext, get_request_context

# 'app' is already available — injected by MDB-Engine
# Do NOT do: app = FastAPI()

@app.get("/")
async def home(request: Request):
    return {"app": request.state.app_slug}

@app.get("/dashboard")
async def dashboard(ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()
    return {"user": user["email"]}

@app.post("/chat")
async def chat(
    message: str,
    ctx: RequestContext = Depends(get_request_context),
):
    user = ctx.require_user()
    memory = ctx.memory

    if memory:
        # Search for relevant memories
        context = await memory.search(
            query=message,
            user_id=user["_id"],
            limit=5,
        )
        return {"memories": context, "message": message}

    return {"message": message, "memories": []}
```

### Common Mistake: Creating a New App

```python
# web.py (WRONG — DO NOT DO THIS)
from fastapi import FastAPI

app = FastAPI()  # This OVERWRITES the injected app!

@app.get("/")   # Routes registered on wrong app instance
async def home():
    return {"error": "This route may not be reachable"}
```

If your module creates its own `FastAPI()` instance, MDB-Engine will log a warning:

```
WARNING: Route module 'web.py' for app 'my_app' created its own app instance.
Routes defined before app creation are registered, but routes defined after may not be.
```

---

## 12. CognitiveEngine + SSO: Per-User Memory Across Apps

The most powerful pattern: using `CognitiveEngine` with SSO to build apps that **remember** each user across sessions.

### Setup Pattern

```python
# web.py for a chat app with persistent memory

from fastapi import Depends, Request
from mdb_engine.dependencies import RequestContext, get_request_context
from mdb_engine.memory.orchestrator import CognitiveEngine

# Cache cognitive engines per user session
_engines: dict[str, CognitiveEngine] = {}

async def get_cognitive_engine(
    ctx: RequestContext = Depends(get_request_context),
) -> CognitiveEngine:
    """Get or create a CognitiveEngine for the current user."""
    user = ctx.require_user()
    user_id = user["_id"]
    slug = ctx.slug

    cache_key = f"{slug}:{user_id}"
    if cache_key not in _engines:
        db = await ctx.get_db()
        memory = ctx.memory

        if not memory:
            raise HTTPException(503, "Memory service not configured")

        chat_collection = db["chat_history"]
        memory_collection = db["memories"]

        _engines[cache_key] = CognitiveEngine(
            app_slug=slug,
            memory_service=memory,
            chat_history_collection=chat_collection,
            memory_collection=memory_collection,
            stm_context_limit=15,
            ltm_search_limit=5,
            auto_summarize_threshold=25,
            llm_service=ctx.llm,
            enable_context_engineering=True,
        )

    return _engines[cache_key]


@app.post("/chat")
async def chat(
    message: str,
    session_id: str = "default",
    ctx: RequestContext = Depends(get_request_context),
    cognitive: CognitiveEngine = Depends(get_cognitive_engine),
):
    user = ctx.require_user()

    response = await cognitive.chat(
        user_id=user["_id"],
        session_id=session_id,
        message=message,
        system_prompt=(
            "You are a helpful assistant. You have access to the user's "
            "long-term memory and can recall facts about them. Use this "
            "context to personalize your responses."
        ),
    )

    return {
        "response": response.text,
        "memories_used": response.context_memories,
        "facts_extracted": response.new_facts,
    }
```

### What Happens During a Chat

```
User sends: "I just moved to Austin, TX"
    │
    ▼
CognitiveEngine.chat():
    │
    ├─ 1. Save message to STM (chat_history collection)
    │
    ├─ 2. Search LTM: "moved to Austin TX"
    │     → Finds: "User lives in San Francisco" (old fact)
    │     → Finds: "User works remotely" (relevant)
    │
    ├─ 3. Get last 15 STM messages for context
    │
    ├─ 4. Assemble prompt:
    │     System: "You are a helpful assistant..."
    │     [Long-term memory context]:
    │       - User lives in San Francisco
    │       - User works remotely
    │     [Recent conversation]:
    │       - User: "How's the weather?"
    │       - AI: "It's sunny in SF!"
    │       - User: "I just moved to Austin, TX"
    │
    ├─ 5. LLM generates response:
    │     "Welcome to Austin! Since you work remotely..."
    │
    ├─ 6. Save AI response to STM
    │
    └─ 7. Background: Extract facts from conversation
          → New fact: "User moved to Austin, TX"
          → Importance: 0.9 (high — location change)
          → Conflict detected: "User lives in San Francisco"
          → Resolution: Old fact marked as superseded
```

---

## 13. Common Issues and How to Fix Them

### Issue: "Memory service not configured for app 'my_app'"

**Cause**: Memory service wasn't initialized during lifespan.

**Fix checklist**:
1. Check `memory_config.enabled: true` in manifest
2. Check that `OPENAI_API_KEY` is set (required for embeddings)
3. Check logs for initialization errors during startup
4. Ensure MongoDB Atlas has vector search capability (required for semantic search)

```bash
# Check your environment
echo $OPENAI_API_KEY  # Must be set
echo $MONGODB_URI     # Must point to Atlas (for vector search)
```

### Issue: "Authentication required" on routes that should work

**Cause**: Route not listed in `public_routes`, or cookie not being sent.

**Fix**:
1. Add the route to `auth.public_routes` in manifest
2. For API calls, ensure you're sending the cookie or Bearer token
3. Check that `Path=/` is set on the cookie (not scoped to auth hub path)

```python
# In your auth hub login handler, ensure cookie path is "/"
from mdb_engine.auth.cookie_utils import set_auth_cookies

response = JSONResponse({"message": "Login successful"})
set_auth_cookies(response, access_token=token, request=request)
# This sets Path=/ by default
```

### Issue: "JWT secret required for SharedUserPool"

**Cause**: No JWT secret configured and not in dev mode.

**Fix**:
```bash
# Generate a secure secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Set it
export MDB_ENGINE_JWT_SECRET="your-secret-here"

# For development only (insecure, ephemeral):
export MDB_ENGINE_ENV="development"
# Or pass allow_insecure_dev=True
```

### Issue: "Role 'viewer' required for this app" (403)

**Cause**: User exists in SharedUserPool but doesn't have the required role for this app.

**Fix**: Either:
1. Add the role to the user:
   ```python
   await user_pool.update_user_roles("user@example.com", "my_app", ["viewer"])
   ```
2. Enable auto-assignment in manifest:
   ```json
   {
     "auth": {
       "require_role": "viewer",
       "default_role": "viewer",
       "auto_assign_default_role": true
     }
   }
   ```
3. Seed demo users with proper roles:
   ```json
   {
     "auth": {
       "users": {
         "demo_users": [{
           "email": "user@example.com",
           "password": "pass",
           "app_roles": {
             "my_app": ["viewer"]
           }
         }]
       }
     }
   }
   ```

### Issue: WebSocket connection refused / 403

**Cause**: CSRF validation failing, or missing ticket/session setup.

**Fix**:
1. Set `MDB_ENGINE_MASTER_KEY` for WebSocket session encryption
2. Ensure CORS origins include your frontend's origin
3. Use the ticket-based auth flow (POST /auth/ticket first)
4. Check that WebSocket route is configured in manifest

### Issue: Memory search returns empty results

**Cause**: Vector search index not created, or embeddings not generated.

**Fix**:
1. Ensure MongoDB Atlas vector search index exists on the memories collection
2. Check that embeddings are being generated (requires OpenAI API key)
3. Verify the memory was added with the correct `user_id`
4. Check minimum score threshold (default 0.5 may be too high)

---

## 14. Full Working Example

### Project Structure

```
my-project/
├── server.py                    # Entry point
├── .env                         # Environment variables
├── auth-hub/
│   ├── manifest.json
│   └── web.py                   # Auth hub routes
└── my-app/
    ├── manifest.json
    └── web.py                   # App routes with memory
```

### `server.py`

```python
import asyncio
from pathlib import Path
from mdb_engine import MongoDBEngine

async def create_app():
    engine = MongoDBEngine(
        mongo_uri="mongodb+srv://...",  # Or from MONGODB_URI env
        db_name="my_platform",
    )

    app = engine.create_multi_app(
        apps=[
            {
                "slug": "auth_hub",
                "manifest": Path("./auth-hub/manifest.json"),
                "path_prefix": "/auth-hub",
            },
            {
                "slug": "my_app",
                "manifest": Path("./my-app/manifest.json"),
                "path_prefix": "/my-app",
            },
        ],
        title="My Platform API",
    )

    return app

app = asyncio.run(create_app())

# Run with: uvicorn server:app --reload
```

### `auth-hub/web.py`

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from mdb_engine.auth.cookie_utils import set_auth_cookies, clear_auth_cookies

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email")
    password = body.get("password")

    user_pool = request.app.state.user_pool
    if not user_pool:
        return JSONResponse({"error": "Auth not initialized"}, 503)

    result = await user_pool.authenticate(
        email=email,
        password=password,
        app_slug="auth_hub",
    )

    if result is None:
        return JSONResponse({"error": "Invalid credentials"}, 401)

    # Handle tuple return (jwt_token, websocket_session_key)
    if isinstance(result, tuple):
        token, ws_session = result
    else:
        token = result
        ws_session = None

    response = JSONResponse({
        "message": "Login successful",
        "email": email,
        "websocket_session": ws_session,
    })

    set_auth_cookies(response, access_token=token, request=request)
    return response


@app.post("/api/register")
async def register(request: Request):
    body = await request.json()
    user_pool = request.app.state.user_pool

    try:
        user = await user_pool.create_user(
            email=body["email"],
            password=body["password"],
            app_roles={
                "auth_hub": ["admin"],
                "my_app": ["viewer"],  # Grant base access to consumer app
            },
        )
        return {"message": "User created", "user_id": user["_id"]}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("mdb_auth_token")
    user_pool = request.app.state.user_pool

    if token and user_pool:
        await user_pool.revoke_token(token, reason="logout")

    response = JSONResponse({"message": "Logged out"})
    clear_auth_cookies(response, request=request)
    return response


@app.get("/api/me")
async def me(request: Request):
    user = request.state.user
    if not user:
        return JSONResponse({"error": "Not authenticated"}, 401)
    return {
        "email": user["email"],
        "roles": user.get("app_roles", {}),
    }
```

### `my-app/web.py`

```python
from fastapi import Depends, HTTPException, Request
from mdb_engine.dependencies import RequestContext, get_request_context


@app.get("/")
async def home():
    return {"app": "my_app", "status": "running"}


@app.get("/dashboard")
async def dashboard(ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()
    return {
        "message": f"Welcome, {user['email']}!",
        "roles": ctx.user_roles,
    }


@app.post("/memories")
async def add_memory(
    content: str,
    category: str = "general",
    ctx: RequestContext = Depends(get_request_context),
):
    user = ctx.require_user()
    memory = ctx.memory

    if not memory:
        raise HTTPException(503, "Memory service not available")

    result = await memory.add(
        content=content,
        user_id=user["_id"],
        metadata={"category": category},
    )
    return {"stored": True, "memory_id": str(result)}


@app.get("/memories/search")
async def search_memories(
    query: str,
    limit: int = 5,
    ctx: RequestContext = Depends(get_request_context),
):
    user = ctx.require_user()
    memory = ctx.memory

    if not memory:
        raise HTTPException(503, "Memory service not available")

    results = await memory.search(
        query=query,
        user_id=user["_id"],
        limit=limit,
    )
    return {"results": results, "count": len(results)}


@app.get("/memories")
async def list_memories(ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()
    memory = ctx.memory

    if not memory:
        raise HTTPException(503, "Memory service not available")

    results = await memory.list(user_id=user["_id"])
    return {"memories": results}
```

### `.env`

```bash
# MongoDB
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MDB_DB_NAME=my_platform

# JWT Secret (REQUIRED for production)
MDB_ENGINE_JWT_SECRET=your-secure-secret-here

# LLM (for memory extraction + chat)
OPENAI_API_KEY=sk-...

# WebSocket encryption (REQUIRED if using WebSockets with CSRF)
MDB_ENGINE_MASTER_KEY=your-master-key-here

# Development mode (optional - relaxes security)
# MDB_ENGINE_ENV=development
```

### Running

```bash
# Install dependencies
pip install mdb-engine[all] uvicorn python-dotenv

# Run
uvicorn server:app --reload --port 8000

# Test
# 1. Register: POST http://localhost:8000/auth-hub/api/register
# 2. Login:    POST http://localhost:8000/auth-hub/api/login
# 3. Access:   GET  http://localhost:8000/my-app/dashboard
# 4. Memory:   POST http://localhost:8000/my-app/memories
# 5. Search:   GET  http://localhost:8000/my-app/memories/search?query=...
```

---

## 15. Production Checklist

Before deploying your SSO multi-app:

### Security

- [ ] `MDB_ENGINE_JWT_SECRET` set to a strong, random value (not the dev default)
- [ ] `MDB_ENGINE_ENV` is NOT `development` in production
- [ ] `MDB_ENGINE_MASTER_KEY` set (if using WebSockets)
- [ ] CORS `allow_origins` lists specific origins (not `*` with credentials)
- [ ] `session_binding.bind_fingerprint: true` enabled
- [ ] `auth.audit.enabled: true` for compliance logging
- [ ] All API keys (OpenAI, etc.) are in environment variables, not in code
- [ ] HTTPS enabled (Secure cookies require it)
- [ ] Demo users removed or passwords changed

### Memory Service

- [ ] MongoDB Atlas used (required for vector search)
- [ ] Vector search index created on `{app_slug}_memories` collection
- [ ] `OPENAI_API_KEY` set (or alternate embedding provider configured)
- [ ] `memory_config.enabled: true` in manifests that need it
- [ ] Memory extraction model configured (gpt-4o-mini recommended for cost)
- [ ] Importance scoring weights tuned for your use case

### Multi-App

- [ ] Path prefixes don't conflict (validated automatically, but double-check)
- [ ] Public routes are correctly listed in each app's manifest
- [ ] Role assignments are correct for demo/seed users
- [ ] Auth hub is the first app listed (initialized first = pool created first)
- [ ] Health endpoint (`GET /health`) returns healthy for all apps
- [ ] CORS origins include your frontend URLs

### Infrastructure

- [ ] MongoDB connection string uses `+srv` for Atlas
- [ ] Connection pool size appropriate for expected load
- [ ] Uvicorn workers configured (typically 1 per CPU core)
- [ ] Graceful shutdown configured (SIGTERM handling)
- [ ] Log level set appropriately (INFO for prod, DEBUG for troubleshooting)
- [ ] Monitoring/alerting on `/health` and `/metrics` endpoints

---

## Quick Reference: Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MONGODB_URI` | Yes | - | MongoDB connection string |
| `MDB_DB_NAME` | No | `mdb_engine` | Database name |
| `MDB_ENGINE_JWT_SECRET` | Yes (prod) | Auto-gen (dev) | JWT signing secret |
| `MDB_ENGINE_JWT_PUBLIC_KEY` | No | Derived | Public key for RS256/ES256 |
| `MDB_ENGINE_MASTER_KEY` | If WebSockets | - | Encryption key for WS sessions |
| `MDB_ENGINE_ENV` | No | - | `development` for dev mode |
| `OPENAI_API_KEY` | If memory/LLM | - | OpenAI API key |
| `AUTH_HUB_URL` | No | `/auth-hub` | Auth hub path for redirects |
| `DEBUG` | No | `false` | Enable debug mode |

---

*This guide reflects mdb-engine v0.7.x. For updates, check the [changelog](../../CHANGELOG.md).*
