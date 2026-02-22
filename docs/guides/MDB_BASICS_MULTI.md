# MDB-Engine Multi-App Basics

A practical guide to building multi-app platforms with `create_multi_app()`. Covers SSO authentication, dependency injection, scoped data access, service initialization, shared endpoints, and the full lifecycle of a mounted app -- all illustrated with the SSO multi-app example.

---

## Table of Contents

1. [Core Concept](#core-concept)
2. [Quick Start](#quick-start)
3. [How create_multi_app() Works](#how-create_multi_app-works)
4. [App Discovery and Route Import](#app-discovery-and-route-import)
5. [The on_startup Lifecycle Hook](#the-on_startup-lifecycle-hook)
6. [SSO Authentication](#sso-authentication)
7. [Dependency Injection](#dependency-injection)
8. [Scoped Data Access](#scoped-data-access)
9. [Service Initialization](#service-initialization)
10. [Shared Endpoints](#shared-endpoints)
11. [Manifest Anatomy (SSO App)](#manifest-anatomy-sso-app)
12. [Request State Reference](#request-state-reference)
13. [Common Patterns](#common-patterns)
14. [Deployment Options](#deployment-options)

---

## Core Concept

`create_multi_app()` takes multiple independent FastAPI apps, each with its own `manifest.json` and `web.py`, and mounts them under a single parent FastAPI instance at distinct path prefixes. One MongoDB connection pool, one auth layer, one process.

```
         Parent FastAPI (port 8000)
         ├── /auth-hub/*   → Auth Hub app
         ├── /pwd-zero/*   → Password Manager app
         ├── /flux/*        → Trading app
         ├── /member/*      → Cognitive Memory Showcase
         ├── /ai-chat/*     → AI Chat app
         ├── /health        → Unified health check
         ├── /docs          → Aggregated OpenAPI
         └── /_mdb/routes   → Route introspection
```

Each child app gets:
- Its own `FastAPI` instance (the injected `app` global)
- A scoped database wrapper (automatic `app_id` filtering)
- Its own services (memory, graph, profile, OSI) initialized from its manifest
- SSO authentication via shared middleware and user pool

---

## Quick Start

### Minimal Example (3 files)

**1. `manifest.json`**

```json
{
  "schema_version": "2.0",
  "slug": "my-app",
  "name": "My App",
  "status": "active",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["base_user"],
    "require_role": "base_user",
    "public_routes": ["/health"]
  }
}
```

**2. `web.py`**

```python
from fastapi import Request
from fastapi.responses import JSONResponse

# `app` and `engine` are injected by create_multi_app -- do NOT reassign them.

@app.get("/")
async def index(request: Request):
    user = getattr(request.state, "user", None)
    return JSONResponse({"hello": user["email"] if user else "anonymous"})

@app.get("/health")
async def health():
    return {"status": "healthy"}
```

**3. `main.py`**

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="mydb")

app = engine.create_multi_app(
    apps=[
        {"slug": "auth-hub", "manifest": Path("auth-hub/manifest.json"), "path_prefix": "/auth-hub"},
        {"slug": "my-app",   "manifest": Path("my-app/manifest.json"),   "path_prefix": "/my-app"},
    ],
    title="My Platform",
)
```

**Run it:**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Running the Full SSO Example

```bash
cd examples/advanced/sso-multi-app

# With Docker:
docker-compose up --build

# Without Docker:
cd apps
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DB="oblivio_apps"
export MDB_ENGINE_JWT_SECRET="dev-secret-change-in-prod"
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000 --reload
```

Apps become available at:
- `http://localhost:8000/auth-hub` -- register and login
- `http://localhost:8000/member` -- cognitive memory showcase
- `http://localhost:8000/ai-chat` -- AI chat with memory
- `http://localhost:8000/health` -- unified health check

---

## How create_multi_app() Works

### Signature

```python
def create_multi_app(
    self,
    apps: list[dict] | None = None,          # Programmatic app list
    multi_app_manifest: Path | None = None,   # JSON manifest alternative
    apps_dir: Path | None = None,             # Auto-discover from directory
    title: str = "Multi-App API",
    root_path: str = "",
    **fastapi_kwargs,
) -> FastAPI
```

### App Config Dict

Each entry in the `apps` list accepts:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `slug` | `str` | Yes | Unique app identifier |
| `manifest` | `Path` | Yes | Path to `manifest.json` |
| `path_prefix` | `str` | Yes | URL prefix (e.g. `"/member"`) |
| `on_startup` | `callable` | No | Custom startup hook (overrides `web.py`'s) |
| `on_shutdown` | `callable` | No | Custom shutdown hook |

### Lifecycle Sequence

```
create_multi_app() called
  │
  ├── 1. Parse app configs (from apps list, manifest, or directory scan)
  ├── 2. Validate path prefixes for conflicts
  ├── 3. Create parent FastAPI app with async lifespan
  │
  └── [lifespan starts on first request]
       │
       ├── 4. engine.initialize()  (MongoDB connection)
       │
       ├── 5. SharedUserPool created (if any app uses auth.mode: "shared")
       │      └── Stored on app.state.user_pool
       │
       ├── FOR EACH child app:
       │   ├── 6. engine.create_app(slug, manifest)
       │   │      ├── Register app in MongoDB
       │   │      ├── Create managed indexes
       │   │      ├── Seed initial_data
       │   │      └── Initialize services (OSI → Graph → Memory → Profile)
       │   │
       │   ├── 7. Import routes from web.py
       │   │      └── Inject `app` and `engine` as module globals
       │   │
       │   ├── 8. Call on_startup(app_instance, engine_ref, manifest)
       │   │
       │   ├── 9. Add middleware (AppContext, SharedAuth, CSRF, CORS)
       │   │
       │   └── 10. Mount child app at path_prefix on parent
       │
       └── 11. Register shared endpoints (/health, /docs, /_mdb/routes)
```

---

## App Discovery and Route Import

When `create_multi_app` processes each app, it looks for a route module in the manifest's directory:

1. **`web.py`** (preferred)
2. **`routes.py`** (fallback)

Before executing the module, two variables are injected into its namespace:

| Variable | Type | Description |
|----------|------|-------------|
| `app` | `FastAPI` | The child app instance. Use for route decorators. |
| `engine` | `MongoDBEngine` | The shared engine. Use for service access. |

**This is why route files use `@app.get(...)` without importing `app`:**

```python
# web.py -- `app` is already injected, no import needed

@app.get("/api/items")
async def list_items(request: Request):
    ...

@app.post("/api/items")
async def create_item(request: Request):
    ...
```

**Important rules:**
- Do NOT create your own `FastAPI()` instance
- Do NOT call `engine.create_app()` yourself
- Do NOT reassign `app` or `engine`
- Keep route registration at module level (not inside functions)

---

## The on_startup Lifecycle Hook

Define an `async def on_startup` in your `web.py` to run initialization after the engine and all services are ready.

### Signature

```python
async def on_startup(app_instance: FastAPI, engine_ref: MongoDBEngine, manifest: dict) -> None:
```

| Parameter | Description |
|-----------|-------------|
| `app_instance` | The child FastAPI app (same as the injected `app` global) |
| `engine_ref` | The shared MongoDBEngine instance |
| `manifest` | The parsed manifest.json dict |

### When It Runs

After:
- MongoDB connection is established
- App is registered in the database
- Indexes are created
- Services (memory, graph, profile, OSI) are initialized
- Routes are imported

Before:
- Middleware is added
- App is mounted on the parent

### Example

```python
cognitive_engine = None

async def on_startup(app_instance, engine_ref, manifest):
    global cognitive_engine

    from mdb_engine.llm import get_llm_service
    from mdb_engine.memory import CognitiveEngine

    llm_service = get_llm_service(config=manifest.get("llm_config", {}))
    memory_service = engine_ref.get_memory_service("my-app")

    if memory_service:
        scoped_db = await engine_ref.get_scoped_db("my-app")
        cognitive_engine = CognitiveEngine(
            app_slug="my-app",
            memory_service=memory_service,
            chat_history_collection=scoped_db["chat_history"],
            llm_service=llm_service,
            enable_context_engineering=True,
        )
```

---

## SSO Authentication

### Architecture

All apps with `"auth.mode": "shared"` share a single `SharedUserPool` instance backed by the `_mdb_engine_shared_users` collection. One app acts as the **auth hub** (handles registration and login); the others are **SSO clients** (validate tokens issued by the hub).

```
    User
     │
     ├── visits /member (no cookie)
     │    └── redirect → /auth-hub/login?redirect_to=/member/auth/callback
     │
     ├── logs in at /auth-hub/login
     │    ├── SharedUserPool.authenticate(email, password)
     │    ├── JWT token issued
     │    └── cookie "mdb_auth_token" set
     │
     ├── redirect → /member/auth/callback?token=<jwt>
     │    ├── SharedUserPool.validate_token(token)
     │    ├── cookie "mdb_auth_token" set for /member path
     │    └── redirect → /member/
     │
     └── visits /member/ (cookie present)
          ├── SharedAuthMiddleware reads cookie
          ├── SharedUserPool.validate_token(token)
          ├── Sets request.state.user and request.state.user_roles
          └── Route handler runs with authenticated user
```

### Auth Hub Manifest

```json
{
  "auth": {
    "mode": "shared",
    "roles": ["base_user", "viewer", "editor", "admin"],
    "default_role": "base_user",
    "public_routes": ["/", "/health", "/login", "/register"]
  }
}
```

The auth hub does NOT set `auth_hub_url` (it IS the auth hub).

### SSO Client Manifest

```json
{
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["base_user", "viewer", "editor", "admin"],
    "require_role": "base_user",
    "public_routes": ["/health", "/auth/callback"]
  }
}
```

Key fields:
- `auth_hub_url` points to the hub's path prefix
- `require_role` enforces a minimum role for access
- `/auth/callback` must be public (it receives the token before the cookie is set)

### Implementing the SSO Pattern in web.py

Every SSO client app needs three routes: `/login` (redirect), `/auth/callback` (token exchange), and `/logout` (revocation).

```python
from shared_security import get_cookie_settings, validate_jwt_token_format

def get_auth_hub_url(request: Request) -> str:
    """Get auth hub URL from middleware-injected request state."""
    return getattr(request.state, "auth_hub_url", None) or "/auth-hub"

def get_current_user(request: Request) -> dict | None:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


@app.get("/login")
async def login_redirect(request: Request):
    """Redirect to auth hub for login."""
    from urllib.parse import quote_plus

    auth_url = get_auth_hub_url(request)
    app_prefix = getattr(request.state, "app_base_path", "")
    callback_url = f"{app_prefix}/auth/callback"
    return RedirectResponse(
        url=f"{auth_url}/login?redirect_to={quote_plus(callback_url)}"
    )


@app.get("/auth/callback")
async def auth_callback(request: Request, token: str = None):
    """Receive JWT from auth hub, validate, set cookie."""
    from urllib.parse import unquote_plus

    token = unquote_plus(request.query_params.get("token", token or ""))
    auth_url = get_auth_hub_url(request)

    if not token or not validate_jwt_token_format(token):
        return RedirectResponse(url=f"{auth_url}/login?error=invalid_token")

    pool = getattr(request.app.state, "user_pool", None)
    user = await pool.validate_token(token) if pool else None
    if not user:
        return RedirectResponse(url=f"{auth_url}/login?error=invalid_token")

    response = RedirectResponse(url="/")
    cookie_settings = get_cookie_settings(request)
    response.set_cookie(
        key="mdb_auth_token",
        value=token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=86400,
        path="/",
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    """Revoke token and clear cookie."""
    pool = getattr(request.app.state, "user_pool", None)
    token = request.cookies.get("mdb_auth_token")
    if pool and token:
        await pool.revoke_token(token, reason="logout")

    auth_url = get_auth_hub_url(request)
    response = RedirectResponse(url=f"{auth_url}/login")
    response.delete_cookie("mdb_auth_token", path="/")
    return response
```

### Per-App Roles

Users can have different roles in different apps. Roles are stored in the user document under `app_roles`:

```json
{
  "email": "user@example.com",
  "app_roles": {
    "auth-hub": ["admin"],
    "member": ["base_user", "editor"],
    "flux": ["viewer"]
  }
}
```

Access the current user's roles for this app via:

```python
roles = getattr(request.state, "user_roles", [])
# or
roles = user.get("app_roles", {}).get(APP_SLUG, [])
```

---

## Dependency Injection

MDB-Engine provides FastAPI dependencies that resolve per-request using the app context set by middleware.

### Available Dependencies

Import from `mdb_engine.dependencies`:

| Dependency | Returns | Description |
|------------|---------|-------------|
| `get_scoped_db` | `ScopedMongoWrapper` | Scoped database for current app |
| `get_request_context` | `RequestContext` | All-in-one context (lazy-loads everything) |
| `get_memory_service` | `BaseMemoryService` | Memory service for current app |
| `get_profile_service` | `ProfileService` | Profile service for current app |
| `get_current_user` | `dict \| None` | Authenticated user or None |
| `require_user()` | `dict` | User or 401 error |
| `require_role(*roles)` | `dict` | User with required role or 403 error |

Import from `mdb_engine.osi.dependencies`:

| Dependency | Returns | Description |
|------------|---------|-------------|
| `get_osi_registry` | `OsiModelRegistry \| None` | OSI registry for current app |

### Usage Examples

**Scoped database access:**

```python
from fastapi import Depends
from mdb_engine.dependencies import get_scoped_db

@app.get("/api/items")
async def list_items(request: Request, db=Depends(get_scoped_db)):
    cursor = db["items"].find({}).limit(50)
    items = [doc async for doc in cursor]
    return {"items": items}
```

**Role-based access control:**

```python
from mdb_engine.dependencies import require_role

@app.delete("/api/items/{item_id}")
async def delete_item(
    item_id: str,
    request: Request,
    user=Depends(require_role("admin")),
    db=Depends(get_scoped_db),
):
    await db["items"].delete_one({"_id": item_id})
    return {"deleted": True}
```

**RequestContext (all-in-one):**

```python
from mdb_engine.dependencies import get_request_context

@app.post("/api/chat")
async def chat(request: Request, ctx=Depends(get_request_context)):
    # ctx.user       → authenticated user
    # ctx.memory     → memory service
    # ctx.llm        → LLM service
    # ctx.db         → scoped database
    # ctx.slug       → app slug
    # ctx.config     → manifest dict
    memories = await ctx.memory.search(query="...", user_id=ctx.user["email"])
    ...
```

---

## Scoped Data Access

Every app gets a `ScopedMongoWrapper` that transparently enforces data isolation.

### How It Works

When you access a collection through the scoped wrapper:

- **Reads** (`find`, `find_one`, `count_documents`, `aggregate`): automatically add `{"app_id": {"$in": read_scopes}}` to your filter
- **Writes** (`insert_one`, `insert_many`): automatically add `{"app_id": write_scope}` to every document
- **Vector search**: embeds scope filter in `$vectorSearch` stage

### Configuration

```json
{
  "data_access": {
    "read_scopes": ["my-app"],
    "write_scope": "my-app"
  }
}
```

For cross-app data access:

```json
{
  "data_access": {
    "read_scopes": ["my-app", "other-app"],
    "write_scope": "my-app",
    "cross_app_policy": "explicit"
  }
}
```

### In Practice

```python
@app.get("/api/notes")
async def list_notes(request: Request, db=Depends(get_scoped_db)):
    # This query is automatically scoped to the current app.
    # You do NOT need to add app_id to the filter.
    cursor = db["notes"].find({"user_id": user["email"]}).limit(50)
    ...

@app.post("/api/notes")
async def create_note(request: Request, db=Depends(get_scoped_db)):
    # app_id is automatically added to the inserted document.
    await db["notes"].insert_one({"user_id": user["email"], "text": "..."})
```

---

## Service Initialization

Services are initialized automatically during `create_app()` based on manifest configuration. The initialization order matters because services depend on each other.

### Initialization Order

```
1. OSI Registry        (if osi_config.enabled)
      ↓ provides entity resolution to...
2. Graph Service       (if graph_config.enabled)
      ↓ provides GraphRAG to...
3. Memory Service      (if memory_config.enabled)
      ↓ provides memories to...
4. Profile Service     (if profile_config.enabled)
```

### Accessing Services

After initialization, services are accessed via the engine:

```python
memory_service  = engine_ref.get_memory_service("my-app")
graph_service   = engine_ref.get_graph_service("my-app")
profile_service = engine_ref.get_profile_service("my-app")  # if available
osi_registry    = engine_ref.get_osi_registry("my-app")      # if available
```

Or via dependency injection in route handlers (see [Dependency Injection](#dependency-injection)).

### Manifest Triggers

| Manifest Section | Service Created | Key Fields |
|-----------------|----------------|------------|
| `memory_config.enabled: true` | Memory service | `collection_name`, `embedding_model`, `provider` |
| `graph_config.enabled: true` | Graph service | `collection_name`, `auto_extract`, `node_types` |
| `profile_config.enabled: true` | Profile service | `user_profiles`, `community_profile` |
| `osi_config.enabled: true` | OSI registry | `models_path`, `entity_resolution` |
| `llm_config.enabled: true` | LLM service | `providers`, `litellm_config` |
| `embedding_config.enabled: true` | Embedding service | `default_embedding_model` |

---

## Shared Endpoints

The parent app automatically provides these endpoints:

### `GET /health`

Unified health check aggregating status of the engine, MongoDB, and all mounted apps.

```json
{
  "status": "healthy",
  "engine": "connected",
  "apps": {
    "auth-hub": { "status": "mounted", "path": "/auth-hub" },
    "member":   { "status": "mounted", "path": "/member" },
    "ai-chat":  { "status": "mounted", "path": "/ai-chat" }
  }
}
```

### `GET /_mdb/routes`

Introspect all routes across all mounted apps:

```json
{
  "parent_routes": ["/health", "/docs", "/_mdb/routes"],
  "mounted_apps": {
    "member": {
      "prefix": "/member",
      "routes": ["/", "/api/chat", "/api/memories", "/api/graph/nodes", "..."]
    }
  }
}
```

### `GET /docs`

Aggregated OpenAPI documentation combining schemas from all child apps. Individual app docs are available at `/docs/{app_slug}`.

### `GET /metrics`

Operation metrics (counts, durations, error rates) collected across all apps.

---

## Manifest Anatomy (SSO App)

A complete manifest for an SSO client app with memory, graph, profile, and OSI features:

```json
{
  "schema_version": "2.0",
  "slug": "member",
  "name": "Member - Cognitive Memory Showcase",
  "status": "active",

  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["base_user", "viewer", "editor", "admin"],
    "require_role": "base_user",
    "public_routes": ["/health", "/auth/callback"],
    "csrf_protection": {
      "enabled": true,
      "exempt_routes": ["/health", "/auth/callback"],
      "token_ttl": 3600
    }
  },

  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "collection_name": "member_memories",
    "embedding_model": "text-embedding-3-small",
    "embedding_model_dims": 1536,
    "memory_llm_model": "gemini/gemini-2.5-flash-lite",
    "enable_cognitive": true,
    "memory_types": { "enabled": true, "auto_detect": true },
    "reflection": { "enabled": true, "interval_hours": 24 },
    "persona": {
      "enabled": true,
      "default_role": "My AI Companion",
      "default_traits": { "humor": 0.8, "empathy": 0.8 }
    }
  },

  "graph_config": {
    "enabled": true,
    "collection_name": "kg",
    "auto_extract": true,
    "node_types": ["actor", "movie", "director", "genre"]
  },

  "profile_config": {
    "enabled": true,
    "user_profiles": { "enabled": true, "collection_name": "user_profiles" },
    "community_profile": { "enabled": true, "collection_name": "community_profile" }
  },

  "osi_config": {
    "enabled": true,
    "models_path": "semantic_models/",
    "entity_resolution": true,
    "metric_routing": true,
    "export_enabled": true
  },

  "llm_config": {
    "enabled": true,
    "providers": {
      "chat": "gemini/gemini-2.5-flash-lite",
      "extraction": "gemini/gemini-2.5-flash-lite"
    }
  },

  "embedding_config": {
    "enabled": true,
    "default_embedding_model": "text-embedding-3-small"
  },

  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:8000"],
    "allow_credentials": true,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization", "X-CSRF-Token"]
  },

  "data_access": {
    "read_scopes": ["member"],
    "write_scope": "member"
  }
}
```

---

## Request State Reference

The `AppContextMiddleware` and `SharedAuthMiddleware` populate `request.state` on every request. These fields are available in all route handlers.

| Field | Type | Set By | Description |
|-------|------|--------|-------------|
| `request.state.app_base_path` | `str` | AppContextMiddleware | Path prefix (e.g. `"/member"`) |
| `request.state.auth_hub_url` | `str` | AppContextMiddleware | Auth hub URL from manifest |
| `request.state.app_slug` | `str` | AppContextMiddleware | Current app slug |
| `request.state.engine` | `MongoDBEngine` | AppContextMiddleware | Shared engine instance |
| `request.state.manifest` | `dict` | AppContextMiddleware | App's manifest.json |
| `request.state.mounted_apps` | `dict` | AppContextMiddleware | All mounted apps metadata |
| `request.state.user` | `dict \| None` | SharedAuthMiddleware | Authenticated user or None |
| `request.state.user_roles` | `list[str]` | SharedAuthMiddleware | User's roles for this app |

---

## Common Patterns

### Accessing the Engine from a Route

```python
def _get_engine(request: Request):
    """Get engine from request state or app state."""
    return (
        getattr(request.state, "engine", None)
        or getattr(request.app.state, "engine", None)
    )
```

### Requiring Authentication in a Route

```python
def require_user(request: Request) -> dict:
    """Get user or raise 401."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@app.get("/api/data")
async def get_data(request: Request):
    user = require_user(request)
    # user["email"] is guaranteed to exist here
    ...
```

### Accessing the SharedUserPool

```python
pool = getattr(request.app.state, "user_pool", None)

# Validate a token
user = await pool.validate_token(token)

# Revoke a token
await pool.revoke_token(token, reason="logout")
```

### Getting Services in on_startup

```python
async def on_startup(app_instance, engine_ref, manifest):
    memory_service = engine_ref.get_memory_service("my-app")
    graph_service  = engine_ref.get_graph_service("my-app")
    scoped_db      = await engine_ref.get_scoped_db("my-app")

    # Use services to initialize app-specific state
    ...
```

### Mounting OSI Routes

If your app has `osi_config.enabled: true`, mount the built-in OSI API router for free:

```python
try:
    from mdb_engine.osi.routes import router as osi_router
    app.include_router(osi_router)
except ImportError:
    pass
```

This adds 10 endpoints under `/api/osi/` (models, metrics, validate, import, export, concepts, etc.).

### Template Rendering with Auth Context

```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        auth_url = getattr(request.state, "auth_hub_url", "/auth-hub")
        return RedirectResponse(url=f"{auth_url}/login?redirect_to=/my-app/auth/callback")

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "app_name": "My App",
    })
```

---

## Deployment Options

| Approach | Command | Best For |
|----------|---------|----------|
| **Multi-app mounting** | `uvicorn multi_app_main:app` | Render.com, Railway, Heroku (single service) |
| **Docker Compose** | `docker-compose up --build` | Full setup with MongoDB + sample data |
| **Bundled container** | `docker-compose -f docker-compose.bundled.yml up` | Simpler Docker setup |
| **Local dev** | Run each `web.py` separately | Debugging individual apps |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MONGODB_URI` | Yes | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DB` | Yes | `oblivio_apps` | Database name |
| `MDB_ENGINE_JWT_SECRET` | **Production** | dev default | JWT signing secret (must match across all apps) |
| `AUTH_HUB_URL` | No | from manifest | Override auth hub URL at runtime |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ENVIRONMENT` | No | `development` | `"production"` enables secure cookies |

### Production Security Checklist

- [ ] Set `MDB_ENGINE_JWT_SECRET` to a strong random value: `python -c 'import secrets; print(secrets.token_urlsafe(32))'`
- [ ] Set `ENVIRONMENT=production` for secure cookie settings
- [ ] Use HTTPS (required for secure cookies)
- [ ] Restrict CORS origins to your actual domains
- [ ] Never commit secrets to version control
