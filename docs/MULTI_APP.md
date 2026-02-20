# Multi-App Architecture

The complete reference for building multi-app deployments with MDB-Engine. One FastAPI process, multiple apps, shared authentication, zero boilerplate.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [How It Works](#how-it-works)
4. [Child App Pattern](#child-app-pattern)
5. [What the Framework Does Automatically](#what-the-framework-does-automatically)
6. [SSO and Shared Authentication](#sso-and-shared-authentication)
7. [Parent-Level Endpoints](#parent-level-endpoints)
8. [Manifest Configuration](#manifest-configuration)
9. [Templates and URL Handling](#templates-and-url-handling)
10. [WebSocket Authentication](#websocket-authentication)
11. [Docker Deployment](#docker-deployment)
12. [API Reference](#api-reference)

---

## Overview

`create_multi_app()` creates a single FastAPI application that mounts multiple child apps at distinct path prefixes. All children share one engine, one MongoDB connection pool, and (with SSO) one user pool.

```
http://localhost:8000/
  /auth-hub/*     -> Auth Hub (login, register, dashboard)
  /pwd-zero/*     -> Password Generator
  /flux/*         -> Paper Trading
  /member/*       -> Cognitive Memory Showcase
  /ai-chat/*      -> AI Chat with Perfect Recall
  /health         -> Unified health check
  /auth/ticket    -> WebSocket ticket endpoint
```

All apps authenticate via a single `mdb_auth_token` cookie set at `Path=/`. Login once at `/auth-hub/login`, access everything.

---

## Quick Start

### Entry Point

```python
# multi_app_main.py
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_apps",
)

app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "my-app",
            "manifest": Path("./my-app/manifest.json"),
            "path_prefix": "/my-app",
        },
    ],
    title="My Platform",
)
```

```bash
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000
```

`create_multi_app()` is **synchronous**. It returns a FastAPI instance immediately. All async work (engine init, memory service, auth pool) runs in the lifespan when uvicorn starts. See [SYNC_CREATE_APP.md](guides/SYNC_CREATE_APP.md) for why.

### Child App

```python
# my-app/web.py

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

APP_SLUG = "my-app"
templates = Jinja2Templates(directory="templates")

# `app` and `engine` are injected by create_multi_app before this runs.
# Do NOT create your own MongoDBEngine or call engine.create_app().

@app.get("/")
async def index(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login")  # auto-rewritten to /my-app/login
    return templates.TemplateResponse("index.html", {"request": request, "user": user})
```

That's it. No engine creation, no `_base(request)` helper, no manual `base_path` passing. The framework handles everything.

---

## How It Works

### Lifecycle

```
1. create_multi_app() returns        [SYNC - module import time]
   - Read manifests (json.load)
   - Validate path prefixes
   - Build parent FastAPI app
   - Register middleware (CORS, CSRF, diagnostics)
   - Register parent endpoints (/health, etc.)

2. Lifespan startup                   [ASYNC - server start time]
   - engine.initialize() (MongoDB connection)
   - SharedUserPool initialized (if any app uses shared auth)
   - FOR EACH child app:
     a. child_app = engine.create_app(slug, manifest, is_sub_app=True)
     b. Route auto-import (web.py or routes.py)
     c. Jinja2 template globals injected (base_path, auth_hub_url, app_slug)
     d. user_pool, WebSocket managers shared to child state
     e. Memory service initialized (if enabled)
     f. on_startup(app, engine, manifest) called (if exported)
     g. Child app mounted at path prefix

3. Serving requests                   [ASYNC - runtime]
   - AppContextMiddleware sets request.state on every request
   - SharedAuthMiddleware validates JWT tokens
   - Redirect URLs auto-rewritten with mount prefix
   - Templates use injected globals
```

---

## Child App Pattern

### What's Injected

Before your `web.py` module executes, `create_multi_app` injects two variables into its namespace:

| Variable | Type | Description |
|----------|------|-------------|
| `app` | `FastAPI` | The child app instance. Use `@app.get()`, etc. |
| `engine` | `MongoDBEngine` | The shared engine. Use for `engine.get_memory_service()`, etc. |

**Never create your own `MongoDBEngine` or call `engine.create_app()` in a child module.** This overwrites the injected variables and breaks routing.

### What You Export

| Export | Required | Description |
|--------|----------|-------------|
| Route decorators | Yes | `@app.get("/")`, `@app.post("/api/data")`, etc. |
| `on_startup` | Optional | `async def on_startup(app, engine, manifest)` -- called during lifespan |
| `on_shutdown` | Optional | `async def on_shutdown(app, engine, manifest)` -- called on shutdown |
| `templates` | Optional | `Jinja2Templates` instance -- framework injects globals into it |

### on_startup

If your module exports an `async def on_startup(app, engine, manifest)`, the framework calls it automatically during the lifespan after:
- Engine is initialized
- MongoDB is connected
- Memory service is ready
- User pool is shared

This is the right place to initialize CognitiveEngine, LLM services, or any app-specific setup:

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
        )
```

### request.state

The `AppContextMiddleware` sets these on every request:

| Attribute | Type | Description |
|-----------|------|-------------|
| `request.state.app_base_path` | `str` | Mount prefix (e.g., `"/my-app"`) |
| `request.state.auth_hub_url` | `str` | Auth hub path prefix (e.g., `"/auth-hub"`) |
| `request.state.app_slug` | `str` | App slug (e.g., `"my-app"`) |
| `request.state.engine` | `MongoDBEngine` | Shared engine instance |
| `request.state.manifest` | `dict` | App's manifest.json |
| `request.state.user` | `dict or None` | Authenticated user (set by SharedAuthMiddleware) |
| `request.state.user_roles` | `list[str]` | User's roles for this app |
| `request.state.mounted_apps` | `dict` | All mounted apps with status |

---

## What the Framework Does Automatically

### 1. Template Globals

If your module has a `templates` variable (Jinja2Templates instance), the framework injects these as Jinja2 environment globals:

| Global | Value | Usage in Template |
|--------|-------|-------------------|
| `base_path` | `"/my-app"` | `{{ base_path }}` or `const BASE = '{{ base_path }}'` |
| `auth_hub_url` | `"/auth-hub"` | `{{ auth_hub_url }}` |
| `app_slug` | `"my-app"` | `{{ app_slug }}` |

You never need to pass these in `TemplateResponse` context. They're always available.

### 2. Redirect Rewriting

The framework automatically prepends your app's mount prefix to redirect URLs.

```python
# You write:
return RedirectResponse("/login")

# Framework rewrites to:
# Location: /my-app/login
```

Rules:
- Only rewrites paths starting with `/`
- Skips paths already starting with the app's prefix (no double-prefixing)
- Skips cross-app redirects (e.g., `/auth-hub/login` stays as-is)

### 3. on_startup Detection

The framework checks your imported module for an `on_startup` function and calls it during the lifespan. No registration needed -- just export it.

### 4. Route Auto-Import

The framework looks for `web.py` or `routes.py` in your manifest's directory and imports it. Route decorators (`@app.get()`, etc.) execute against the injected `app`.

---

## SSO and Shared Authentication

### How It Works

All apps using `"auth": {"mode": "shared"}` in their manifest share a single `SharedUserPool`. JWT tokens are stored in an httpOnly cookie (`mdb_auth_token`) at `Path=/`, making them accessible to all apps on the same origin.

### Login Flow

```
User -> /auth-hub/login
         |
         v
   SharedUserPool.authenticate(email, password)
         |
         v
   JWT token generated (HS256, 24h expiry)
         |
         v
   Set-Cookie: mdb_auth_token=<JWT>; Path=/; HttpOnly; SameSite=Lax
         |
         v
User -> /my-app/dashboard
         |
         v
   SharedAuthMiddleware extracts JWT from cookie
         |
         v
   SharedUserPool.validate_token(token) -> user dict
         |
         v
   Check user.app_roles["my-app"] contains required role
         |
         v
   request.state.user = user  (route handler gets it)
```

### Per-App Roles

Users have roles scoped to each app:

```json
{
    "email": "user@example.com",
    "app_roles": {
        "auth-hub": ["admin"],
        "my-app": ["viewer", "editor"],
        "another-app": ["viewer"]
    }
}
```

Each app's manifest specifies `require_role`:

```json
{
    "auth": {
        "mode": "shared",
        "require_role": "viewer",
        "roles": ["viewer", "editor", "admin"],
        "public_routes": ["/health", "/auth/callback"]
    }
}
```

### Token Exchange (Cross-App SSO)

When a user hits a child app without being logged in:

1. Child app redirects to `/auth-hub/login?redirect_to=/my-app/auth/callback`
2. Auth hub authenticates user, generates JWT
3. Auth hub redirects to `/my-app/auth/callback?token=<JWT>`
4. Child app validates token, sets cookie, redirects to `/`

### Logout

```python
@app.post("/logout")
async def logout(request: Request):
    pool = getattr(request.app.state, "user_pool", None)
    token = request.cookies.get("mdb_auth_token")
    if pool and token:
        await pool.revoke_token(token, reason="logout")
    response = RedirectResponse("/auth-hub/login")  # cross-app, not rewritten
    response.delete_cookie("mdb_auth_token", path="/")
    return response
```

---

## Parent-Level Endpoints

These are registered on the parent app (not on any child):

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Unified health check for all apps |
| `/auth/ticket` | POST | WebSocket ticket generation (requires JWT cookie) |
| `/auth/websocket-session` | GET | WebSocket session key generation |
| `/_mdb/routes` | GET | Route introspection for all mounted apps |
| `/docs` | GET | Aggregated OpenAPI documentation |
| `/docs/{app_slug}` | GET | Per-app OpenAPI documentation |
| `/metrics` | GET | Operation metrics |

### Health Check Response

```json
{
    "status": "healthy",
    "engine": {"status": "healthy", "response_time_ms": 12},
    "mongodb": {"status": "healthy"},
    "apps": {
        "auth-hub": {"path_prefix": "/auth-hub", "status": "healthy"},
        "my-app": {"path_prefix": "/my-app", "status": "healthy"}
    }
}
```

---

## Manifest Configuration

Each child app needs a `manifest.json`. Minimal example:

```json
{
    "schema_version": "2.0",
    "slug": "my-app",
    "name": "My App",
    "auth": {
        "mode": "shared",
        "require_role": "viewer",
        "roles": ["viewer", "editor", "admin"],
        "public_routes": ["/health", "/auth/callback"]
    }
}
```

With memory service:

```json
{
    "schema_version": "2.0",
    "slug": "my-app",
    "name": "My App",
    "auth": { "mode": "shared", "require_role": "viewer" },
    "memory_config": {
        "enabled": true,
        "provider": "cognitive",
        "embedding": { "model": "text-embedding-3-small" }
    },
    "llm_config": {
        "enabled": true,
        "providers": { "chat": "gpt-4o" }
    }
}
```

---

## Templates and URL Handling

### The Rule

Child apps write **bare paths**. The framework adds the prefix.

| What you write | What the browser sees |
|---|---|
| `RedirectResponse("/login")` | `Location: /my-app/login` |
| `href="/dashboard"` in Jinja | `href="/dashboard"` (use `{{ base_path }}/dashboard`) |
| `fetch('/api/data')` in JS | Needs `fetch(BASE + '/api/data')` |

**Redirects** are auto-rewritten. **Template hrefs and JS fetches** use the `base_path` / `BASE` global.

### Template Pattern

```html
<!-- base_path, auth_hub_url, app_slug are auto-injected by the framework -->
<script>
    const BASE = '{{ base_path }}';  // e.g., '/my-app'
</script>

<a href="{{ base_path }}/dashboard">Dashboard</a>
<a href="/auth-hub/login">Login</a>  <!-- cross-app: use absolute path -->

<script>
    // Same-app API call: use BASE
    const res = await fetch(BASE + '/api/data');

    // Parent-level endpoint: use absolute path (no BASE)
    const ticket = await fetch('/auth/ticket', { method: 'POST' });

    // Cross-app redirect: use absolute path
    window.location.href = '/auth-hub/login';
</script>
```

### Three URL Types

| Type | Prefix | Example |
|------|--------|---------|
| Same-app route | `BASE + '/...'` | `fetch(BASE + '/api/chat')` |
| Cross-app route | Absolute path | `href="/auth-hub/login"` |
| Parent endpoint | Absolute path | `fetch('/auth/ticket')` |

---

## WebSocket Authentication

WebSocket connections use ticket-based auth because browsers can't send custom headers on upgrade requests.

### Flow

```
1. Client has JWT cookie (from SSO login)
2. POST /auth/ticket (parent endpoint, sends JWT cookie)
   -> Returns { "ticket": "abc123" } (10s TTL, single-use)
3. WebSocket connect: ws://host/my-app/ws?ticket=abc123
   -> Ticket validated and consumed
   -> Connection established with user context
```

### Manifest Config

```json
{
    "websockets": {
        "realtime": {
            "path": "/ws",
            "auth": { "required": true },
            "ping_interval": 30
        }
    }
}
```

WebSocket routes are registered on the **parent** app at the full path (`/my-app/ws`). The framework handles this automatically from the manifest.

### Client Code

```javascript
const ticket = await (await fetch('/auth/ticket', {
    method: 'POST',
    credentials: 'include'
})).json();

const ws = new WebSocket(
    `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${BASE}/ws?ticket=${ticket.ticket}`
);
```

---

## Docker Deployment

### Single Container

```yaml
services:
  mongodb:
    image: mongodb/mongodb-atlas-local:latest
    healthcheck:
      test: echo 'db.runCommand("ping").ok' | mongosh "mongodb://localhost:27017/?directConnection=true" --quiet

  sso-platform:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      sh -c "cd /app/apps && exec uvicorn multi_app_main:app --host 0.0.0.0 --port 8000"
    ports:
      - "8000:8000"
    environment:
      - MONGODB_URI=mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true
      - MONGODB_DB=my_apps
      - MDB_ENGINE_JWT_SECRET=your-secret-here
    depends_on:
      mongodb:
        condition: service_healthy
```

All apps run in one process on one port. No inter-service networking needed.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MONGODB_URI` | Yes | MongoDB connection string |
| `MONGODB_DB` | No | Database name (default: `mdb_engine`) |
| `MDB_ENGINE_JWT_SECRET` | Yes (prod) | JWT signing secret |
| `MDB_ENGINE_MASTER_KEY` | If WebSockets | Encryption key for WS sessions |
| `MDB_ENGINE_ENV` | No | `development` for dev mode |

---

## API Reference

### `engine.create_multi_app()`

```python
def create_multi_app(
    self,
    apps: list[dict] | None = None,
    multi_app_manifest: Path | None = None,
    apps_dir: Path | None = None,
    path_prefix_template: str | None = None,
    validate: bool = False,
    strict: bool = False,
    title: str = "Multi-App API",
    root_path: str = "",
    **fastapi_kwargs,
) -> FastAPI
```

**Returns**: FastAPI application with all child apps mounted.

**App config dict**:

```python
{
    "slug": "my-app",              # Required
    "manifest": Path("..."),        # Required
    "path_prefix": "/my-app",      # Optional (defaults to /{slug})
    "on_startup": async_fn,         # Optional (also auto-detected from module)
    "on_shutdown": async_fn,        # Optional
}
```

### Auto-Discovery

```python
app = engine.create_multi_app(
    apps_dir=Path("./apps"),
    path_prefix_template="/{slug}",
)
```

Scans `apps_dir` recursively for `manifest.json` files, extracts slugs, generates prefixes.

### Manifest-Based

```python
app = engine.create_multi_app(
    multi_app_manifest=Path("./multi_app_manifest.json"),
)
```

```json
{
    "multi_app": {
        "enabled": true,
        "apps": [
            { "slug": "auth-hub", "manifest": "./auth-hub/manifest.json", "path_prefix": "/auth-hub" },
            { "slug": "my-app", "manifest": "./my-app/manifest.json", "path_prefix": "/my-app" }
        ]
    }
}
```

---

*This document reflects mdb-engine v0.7.9.*
