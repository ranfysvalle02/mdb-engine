---
name: mdb-multi-app
description: Guide for building multi-app platforms with mdb-engine, with and without SSO. Use when creating multi-app setups, configuring SharedUserPool, setting up auth hubs, mounting apps with path prefixes, or managing cross-app data access.
---

# MDB-Engine Multi-App Guide

## 1. Architecture

Multi-app mounts multiple FastAPI apps under one engine, each with its own manifest, database scope, and optional services.

```
Platform (engine)
├── /auth-hub   → Auth Hub app (SSO login, registration)
├── /app1       → App 1 (memory, graph, etc.)
└── /app2       → App 2 (independent features)
```

Each app gets its own:
- `app_id` scope (automatic data isolation)
- Collection prefix (`{slug}_memories`, `{slug}_kg`, etc.)
- Services (memory, graph, LLM, embedding — shared instances when possible)

---

## 2. Without SSO (Independent Auth)

Each app handles its own authentication independently.

### main.py

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="platform")

app = engine.create_multi_app(
    apps=[
        {"slug": "app1", "manifest": Path("apps/app1/manifest.json"), "path_prefix": "/app1"},
        {"slug": "app2", "manifest": Path("apps/app2/manifest.json"), "path_prefix": "/app2"},
    ],
    title="My Platform",
)
```

### App manifest (apps/app1/manifest.json)

```json
{
  "schema_version": "2.0",
  "slug": "app1",
  "name": "App One",
  "auth": {
    "mode": "app",
    "users": {"enabled": true, "allow_registration": true}
  },
  "memory_config": true
}
```

Each app has its own user collection and JWT scope.

---

## 3. With SSO (Shared Auth)

All apps share a single user pool. An auth hub handles login/registration.

### main.py

```python
app = engine.create_multi_app(
    apps=[
        {"slug": "auth-hub", "manifest": Path("apps/auth-hub/manifest.json"), "path_prefix": "/auth-hub"},
        {"slug": "chat", "manifest": Path("apps/chat/manifest.json"), "path_prefix": "/chat"},
        {"slug": "admin", "manifest": Path("apps/admin/manifest.json"), "path_prefix": "/admin"},
    ],
    title="SSO Platform",
)
```

### Auth hub manifest

```json
{
  "schema_version": "2.0",
  "slug": "auth-hub",
  "name": "Auth Hub",
  "auth": {
    "mode": "shared",
    "roles": ["base_user", "viewer", "editor", "admin"],
    "default_role": "base_user",
    "public_routes": ["/", "/health", "/login", "/register"],
    "users": {"enabled": true, "strategy": "app_users"}
  },
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:8000"],
    "allow_credentials": true
  }
}
```

### App manifest (SSO consumer)

```json
{
  "schema_version": "2.0",
  "slug": "chat",
  "name": "Chat App",
  "auth": {
    "mode": "shared",
    "require_role": "base_user",
    "public_routes": ["/health"]
  },
  "memory_config": "smart",
  "graph_config": {
    "node_types": ["person", "topic", "project"]
  }
}
```

Key: all SSO apps use `"mode": "shared"`. The JWT secret (`MDB_JWT_SECRET` env var) must be the same across all apps.

---

## 4. SharedUserPool

For SSO, use `SharedUserPool` in the auth hub:

```python
from mdb_engine.auth import SharedUserPool

pool = SharedUserPool(db=engine._db, jwt_secret=os.getenv("MDB_JWT_SECRET"))

@app.post("/register")
async def register(email: str, password: str):
    user = await pool.register(email=email, password=password, app_slug="auth-hub")
    return {"user_id": str(user["_id"])}

@app.post("/login")
async def login(email: str, password: str):
    token = await pool.authenticate(email=email, password=password, app_slug="auth-hub")
    return {"token": token}
```

Consumer apps validate the same JWT tokens automatically via the shared auth middleware.

---

## 5. Data Access & Cross-App Scoping

### Default: Each app sees only its own data

```json
{"data_access": {"read_scopes": ["my_app"], "write_scope": "my_app"}}
```

### Cross-app reading

Allow one app to read another app's data:

```json
{
  "data_access": {
    "read_scopes": ["my_app", "shared_app"],
    "write_scope": "my_app"
  }
}
```

### App.state services

After initialization, all services are available on each app's state:

```python
request.app.state.engine           # MongoDBEngine
request.app.state.app_slug         # str
request.app.state.manifest         # dict
request.app.state.memory_service   # MemoryService | None
request.app.state.graph_service    # GraphService | None
request.app.state.embedding_service  # EmbeddingService | None
request.app.state.llm_service     # LLMService | None
```

---

## 6. Per-App on_startup Hook

Each app can define an `on_startup` callback:

```python
async def on_startup(app, engine, manifest):
    """Called after the app is initialized but before it starts serving."""
    # Custom initialization logic here
    pass

# In create_multi_app:
{"slug": "chat", "manifest": ..., "on_startup": on_startup}
```

Or define `on_startup` in the app's `web.py` module (auto-discovered).

---

## 7. WebSocket Support

```json
{
  "websockets": {
    "chat": {
      "path": "/ws",
      "auth": {"required": true}
    }
  }
}
```

```python
from mdb_engine.routing.websockets import authenticated_websocket

@app.websocket("/ws")
@authenticated_websocket
async def ws_chat(websocket, user):
    ...
```

---

## 8. Platform Navigation

Use `get_platform_info` to build navigation across mounted apps:

```python
from mdb_engine.dependencies import get_platform_info

@app.get("/nav")
async def get_nav(platform=Depends(get_platform_info)):
    return platform.apps  # List of {slug, name, path_prefix}
```

---

## 9. Service Initialization Order

For each mounted app, the engine initializes services in this guaranteed order:

1. Shared LLM + embedding services (`_ensure_shared_services`)
2. Graph service (`initialize_graph_service`)
3. Memory service (`initialize_memory_service`) -- graph is injected here
4. Perfect Brain (`initialize_perfect_brain`) -- uses all of the above
5. Store all services on `app.state`

This happens automatically in both `create_app` and `create_multi_app`. No manual `on_startup` wiring is needed for any service.

---

## 10. Framework Base Template in Multi-App

The engine auto-registers its `mdb_engine/templates/` directory in each child app's Jinja2 loader. This means `{% extends "mdb_base.html" %}` works in any app without configuration.

Each app's `base.html` should extend `mdb_base.html` and override blocks for its own visual design:

```html
{% extends "mdb_base.html" %}
{% block head %}<style>/* app CSS */</style>{% block extra_css %}{% endblock %}{% endblock %}
{% block body %}<header>...</header><main>{% block content %}{% endblock %}</main>{% endblock %}
{% block base_js %}<script>/* app-level JS like logout */</script>{% endblock %}
```

Page templates extend the app's `base.html` and put scripts in `{% block extra_js %}`. **Never** put scripts that use `BASE`/`MDB` in `{% block content %}` — they run before `MDB` is defined.

### Scaffolding

```bash
mdb-engine new-app my-app --services memory
# Creates: my-app/manifest.json, web.py, templates/base.html, templates/index.html
```

The generated `base.html` extends `mdb_base.html` out of the box.

---

## 11. Rules

- **DO** use `"mode": "shared"` for SSO apps, `"mode": "app"` for independent.
- **DO** set `MDB_JWT_SECRET` env var (same across all SSO apps).
- **DO** give each app a unique `slug` and `path_prefix`.
- **DO NOT** access raw `engine._db` — use `get_scoped_db` or `RequestContext`.
- **DO NOT** hardcode `app_id` in queries — scoping is automatic.
- **DO NOT** manually initialize graph/memory in `on_startup` — the engine handles it.
- **DO** use `public_routes` for unauthenticated endpoints in SSO apps.
- **DO** extend `mdb_base.html` in app base templates for correct JS context ordering.
- **DO** put page scripts in `{% block extra_js %}`, app scripts in `{% block base_js %}`.
- **DO NOT** put `<script>` tags that use `BASE`/`MDB` inside `{% block content %}`.
- Services (memory, graph, LLM, embedding) are initialized per-app and shared via `app.state`.
