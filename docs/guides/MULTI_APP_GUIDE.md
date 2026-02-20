# Multi-App Deployment Guide

Complete guide to deploying multiple apps with SSO support using MDB-Engine's `create_multi_app()`.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration Methods](#configuration-methods)
4. [SSO Setup](#sso-setup)
5. [New Features](#new-features)
6. [Built-in Helpers](#built-in-helpers)
7. [API Endpoints](#api-endpoints)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)

---

## Overview

MDB-Engine's multi-app deployment allows you to:

- **Mount multiple apps** under a single FastAPI instance
- **Share authentication** across apps (SSO)
- **Auto-discover apps** from a directory
- **Validate configurations** before deployment
- **Access app context** via built-in helpers
- **Monitor health** with unified endpoints
- **Introspect routes** across all apps

### Architecture

```
┌─────────────────────────────────────────────────┐
│         Parent FastAPI App                       │
│  (main.py - engine.create_multi_app()          │
│                                                  │
│  Routes:                                         │
│  ├── /health (unified health check)            │
│  ├── /_mdb/routes (route introspection)        │
│  ├── /docs (aggregated OpenAPI docs)            │
│  ├── /auth-hub/* → Auth Hub App                │
│  ├── /app1/* → App 1                           │
│  └── /app2/* → App 2                            │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  Shared MongoDBEngine                     │  │
│  │  - Single connection pool                 │  │
│  │  - Shared user pool (SSO)                 │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Install MDB-Engine

```bash
pip install mdb-engine
```

### 2. Create Your Apps

Each app needs a `manifest.json`:

**apps/auth-hub/manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "auth-hub",
  "name": "Auth Hub",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "admin"],
    "require_role": "viewer",
    "public_routes": ["/", "/login", "/register"]
  }
}
```

**apps/app1/manifest.json:**
```json
{
  "schema_version": "2.0",
  "slug": "app1",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["viewer", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  }
}
```

### 3. Create Multi-App

**main.py:**
```python
from pathlib import Path
from mdb_engine import MongoDBEngine
import os

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "my_apps_db"),
)

# Create multi-app with auto-discovery
app = engine.create_multi_app(
    apps_dir=Path("./apps"),
    path_prefix_template="/{slug}",
    validate=True,  # Validate all manifests
    title="My Multi-App Platform",
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 4. Run

```bash
python main.py
```

Your apps are now available at:
- `http://localhost:8000/auth-hub/` - Auth Hub
- `http://localhost:8000/app1/` - App 1
- `http://localhost:8000/health` - Health check
- `http://localhost:8000/docs` - API documentation

---

## Configuration Methods

### Method 1: Programmatic Configuration

**Best for:** Dynamic configuration, full control

```python
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./apps/auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",
            "on_startup": my_startup_hook,  # Optional
            "on_shutdown": my_shutdown_hook,  # Optional
        },
        {
            "slug": "app1",
            "manifest": Path("./apps/app1/manifest.json"),
            "path_prefix": "/app1",
        },
    ],
    validate=True,
    strict=False,
    title="My Platform",
)
```

### Method 2: Manifest-Based Configuration

**Best for:** Version-controlled, declarative setup

**multi_app_manifest.json:**
```json
{
  "schema_version": "2.0",
  "multi_app": {
    "enabled": true,
    "apps": [
      {
        "slug": "auth-hub",
        "manifest": "./apps/auth-hub/manifest.json",
        "path_prefix": "/auth-hub"
      },
      {
        "slug": "app1",
        "manifest": "./apps/app1/manifest.json",
        "path_prefix": "/app1"
      }
    ]
  }
}
```

**Usage:**
```python
app = engine.create_multi_app(
    multi_app_manifest=Path("./multi_app_manifest.json"),
    validate=True,
)
```

### Method 3: Auto-Discovery

**Best for:** Development, rapid prototyping

```python
app = engine.create_multi_app(
    apps_dir=Path("./apps"),  # Recursively scans for manifest.json
    path_prefix_template="/app-{index}",  # Auto-generate prefixes
    # OR use slug: path_prefix_template="/{slug}"
    validate=True,
)
```

**Auto-discovery:**
- Scans `apps_dir` recursively for all `manifest.json` files
- Extracts slug from each manifest
- Auto-generates path prefixes using template
- `{index}` = app index (1, 2, 3...)
- `{slug}` = app slug from manifest

---

## SSO Setup

### Step 1: Configure Shared Auth

All apps using SSO must have `"auth": {"mode": "shared"}` in their manifests:

**auth-hub/manifest.json:**
```json
{
  "slug": "auth-hub",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/", "/login", "/register", "/health"]
  }
}
```

**app1/manifest.json:**
```json
{
  "slug": "app1",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",  // Path prefix, not full URL
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  }
}
```

### Step 2: Set Environment Variables

```bash
# All apps must use the same JWT secret
export MDB_ENGINE_JWT_SECRET="your-secret-key-here-min-32-chars"

# All apps must use the same database
export MONGODB_DB="my_apps_db"
```

### Step 3: Create Multi-App

```python
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": Path("./apps/auth-hub/manifest.json"),
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "app1",
            "manifest": Path("./apps/app1/manifest.json"),
            "path_prefix": "/app1",
        },
    ],
)
```

### Step 4: How SSO Works

1. **User logs in** at `/auth-hub/login`
2. **Auth hub** creates JWT token and sets cookie
3. **User visits** `/app1/` (or any SSO app)
4. **Middleware** validates token automatically
5. **User authenticated** - no login needed!

**Key Points:**
- JWT token stored in cookie (`mdb_auth_token`)
- Cookie shared across all apps (same domain)
- Token validated on every request
- User roles checked per app

---

## New Features

### 1. Built-in App Context Helpers

Every mounted app automatically has access to helpful context via `request.state`:

```python
@app.get("/my-route")
async def my_route(request: Request):
    # App's path prefix (e.g., "/auth-hub")
    base_path = request.state.app_base_path
    
    # Auth hub URL from manifest or env
    auth_hub_url = request.state.auth_hub_url
    
    # Current app's slug
    app_slug = request.state.app_slug
    
    # All mounted apps metadata
    all_apps = request.state.mounted_apps
    # Returns: {
    #   "auth-hub": {
    #     "slug": "auth-hub",
    #     "path_prefix": "/auth-hub",
    #     "status": "mounted"
    #   },
    #   "app1": {...}
    # }
    
    # MongoDBEngine instance
    engine = request.state.engine
    
    # App's manifest.json (full config)
    manifest = request.state.manifest
    
    return {
        "base_path": base_path,
        "auth_url": auth_hub_url,
        "slug": app_slug,
        "all_apps": list(all_apps.keys(),
    }
```

### 2. Auto-Discovery

Automatically find and mount all apps:

```python
app = engine.create_multi_app(
    apps_dir=Path("./apps"),
    path_prefix_template="/{slug}",  # or "/app-{index}"
)
```

### 3. Validation Mode

Validate all manifests before mounting:

```python
app = engine.create_multi_app(
    apps=[...],
    validate=True,   # Validate all manifests
    strict=True,     # Fail fast on any error
)
```

### 4. Enhanced Health Check

Unified health check with per-app status:

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "engine": {
    "status": "healthy",
    "message": "Engine is operational",
    "response_time_ms": 12
  },
  "mongodb": {
    "status": "healthy",
    "message": "MongoDB connection active"
  },
  "apps": {
    "auth-hub": {
      "path_prefix": "/auth-hub",
      "status": "healthy",
      "route_count": 15
    },
    "app1": {
      "path_prefix": "/app1",
      "status": "healthy",
      "route_count": 8
    }
  }
}
```

### 5. Route Introspection

List all routes from all apps:

```bash
curl http://localhost:8000/_mdb/routes
```

**Response:**
```json
{
  "parent_app": {
    "routes": [
      {
        "path": "/health",
        "methods": ["GET"],
        "name": "health_check"
      }
    ]
  },
  "mounted_apps": {
    "auth-hub": {
      "path_prefix": "/auth-hub",
      "status": "mounted",
      "routes": [
        {
          "path": "/auth-hub/login",
          "relative_path": "/login",
          "methods": ["GET", "POST"],
          "name": "login"
        }
      ],
      "route_count": 15
    }
  }
}
```

### 6. OpenAPI Docs Aggregation

- **`/docs`** - Combined docs from all apps
- **`/docs/{app_slug}`** - Individual app docs

### 7. App Metadata Access

```python
# Get all mounted apps
mounted_apps = engine.get_mounted_apps(app)

for app_info in mounted_apps:
    print(f"App: {app_info['slug']}")
    print(f"Path: {app_info['path_prefix']}")
    print(f"Status: {app_info['status']}")
```

---

## Built-in Helpers

### Available in `request.state`

| Helper | Type | Description |
|--------|------|-------------|
| `app_base_path` | `str` | Path prefix (e.g., "/auth-hub") |
| `auth_hub_url` | `str` | Auth hub URL from manifest or env |
| `app_slug` | `str` | Current app's slug |
| `mounted_apps` | `dict` | All mounted apps metadata |
| `engine` | `MongoDBEngine` | MongoDBEngine instance |
| `manifest` | `dict` | App's manifest.json |

### Example Usage

```python
from fastapi import Request

@app.get("/dashboard")
async def dashboard(request: Request):
    # Get current app info
    slug = request.state.app_slug
    base_path = request.state.app_base_path
    
    # Get auth hub URL for redirects
    auth_hub = request.state.auth_hub_url
    
    # List all available apps
    all_apps = request.state.mounted_apps
    
    # Access engine
    engine = request.state.engine
    db = engine.get_scoped_db(slug)
    
    # Access manifest config
    manifest = request.state.manifest
    app_name = manifest.get("name", slug)
    
    return {
        "app": app_name,
        "base_path": base_path,
        "available_apps": list(all_apps.keys(),
    }
```

---

## API Endpoints

### Parent App Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Unified health check |
| `/_mdb/routes` | GET | List all routes |
| `/docs` | GET | Aggregated OpenAPI docs |
| `/docs/{app_slug}` | GET | Individual app docs |
| `/openapi.json` | GET | Aggregated OpenAPI JSON |

### Child App Endpoints

Each mounted app's routes are accessible at:
- `{path_prefix}/{route}`

Example:
- `/auth-hub/login` → Auth Hub's login route
- `/app1/dashboard` → App 1's dashboard route

---

## Best Practices

### 1. Path Prefixes

✅ **Good:**
```python
path_prefix="/auth-hub"
path_prefix="/app1"
path_prefix="/api/v1"
```

❌ **Bad:**
```python
path_prefix="auth-hub"  # Missing leading slash
path_prefix="/app"      # Conflicts with /app/v2
path_prefix="/health"   # Reserved path
```

### 2. Manifest Organization

```
project/
├── apps/
│   ├── auth-hub/
│   │   └── manifest.json
│   ├── app1/
│   │   └── manifest.json
│   └── app2/
│       └── manifest.json
├── multi_app_manifest.json  # Optional
└── main.py
```

### 3. Environment Variables

```bash
# Required for SSO
MDB_ENGINE_JWT_SECRET=your-secret-key-here-min-32-chars
MONGODB_DB=my_apps_db

# Optional
AUTH_HUB_URL=/auth-hub
MONGODB_URI=mongodb://localhost:27017
```

### 4. Validation

Always validate in production:

```python
app = engine.create_multi_app(
    apps=[...],
    validate=True,   # Validate manifests
    strict=True,     # Fail fast on errors
)
```

### 5. Error Handling

```python
try:
    app = engine.create_multi_app(
        apps=[...],
        validate=True,
        strict=True,
    )
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)
```

### 6. Logging

Enable detailed logging:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

---

## WebSocket Support (v0.7.0+)

Multi-app deployments fully support WebSocket connections with **FastAPI's native `APIRouter` approach**:

### Benefits

- ✅ **Full FastAPI Integration**: Dependency injection, OpenAPI docs, request/response models
- ✅ **Consistent Behavior**: Same registration pattern for single-app and multi-app modes
- ✅ **Route Priority**: WebSocket routes registered before mounted apps ensure proper routing
- ✅ **Best Practices**: Follows FastAPI's recommended WebSocket patterns

### Configuration

WebSocket endpoints are automatically registered from your app's `manifest.json`:

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true
      }
    }
  }
}
```

Routes are registered on the parent app with the full path (e.g., `/app-slug/ws`), ensuring they're checked before mounted app routes.

### Example

```python
# Multi-app automatically handles WebSocket registration
app = engine.create_multi_app(
    apps=[
        {
            "slug": "chat-app",
            "manifest": Path("apps/chat-app/manifest.json"),
            "path_prefix": "/chat-app"
        }
    ]
)

# WebSocket route automatically available at /chat-app/ws
# Uses FastAPI APIRouter for full feature support
```

See [WebSocket Security Guide](./WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md) for detailed security configuration.

---

## Troubleshooting

### SSO Not Working

**Problem:** Users need to login to each app separately.

**Solutions:**
1. **Check JWT Secret:** All apps must use the same `MDB_ENGINE_JWT_SECRET`
   ```bash
   echo $MDB_ENGINE_JWT_SECRET
   ```

2. **Check Database:** All apps must use the same `MONGODB_DB`
   ```bash
   echo $MONGODB_DB
   ```

3. **Check Auth Hub URL:** Use path prefix, not full URL
   ```json
   {
     "auth": {
       "auth_hub_url": "/auth-hub"  // ✅ Correct
       // NOT: "http://localhost:8000/auth-hub"  // ❌ Wrong
     }
   }
   ```

4. **Check Cookies:** Verify cookie is set and shared
   - Browser DevTools → Application → Cookies
   - Verify `mdb_auth_token` exists
   - Check cookie domain matches deployment domain

### Apps Not Mounting

**Problem:** Getting errors when mounting apps.

**Solutions:**
1. **Check Manifest Paths:** Ensure paths are correct
   ```python
   manifest=Path("./apps/auth-hub/manifest.json")  # ✅ Absolute or relative
   ```

2. **Check Path Prefixes:** No conflicts or reserved paths
   ```python
   path_prefix="/auth-hub"  # ✅ Starts with /, not reserved
   ```

3. **Enable Validation:** Use `validate=True` to catch errors early
   ```python
   app = engine.create_multi_app(..., validate=True)
   ```

### Health Check Failing

**Problem:** `/health` returns unhealthy status.

**Solutions:**
1. **Check MongoDB Connection:** Verify `MONGODB_URI` is correct
2. **Check Engine Status:** Look at `engine.status` in response
3. **Check App Status:** Look at `apps.{app_slug}.status` in response

### Route Introspection Empty

**Problem:** `/_mdb/routes` shows no routes.

**Solutions:**
1. **Check App Status:** Ensure apps are mounted successfully
2. **Check Routes:** Verify child apps have routes defined
3. **Check Path Prefixes:** Ensure prefixes are correct

---

## Examples

### Complete Example

**main.py:**
```python
from pathlib import Path
from mdb_engine import MongoDBEngine
import os
import logging

logging.basicConfig(level=logging.INFO)

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "my_apps_db"),
)

# Create multi-app
app = engine.create_multi_app(
    apps_dir=Path("./apps"),
    path_prefix_template="/{slug}",
    validate=True,
    strict=False,
    title="My Multi-App Platform",
    description="SSO-enabled multi-app deployment",
    version="1.0.0",
)

# Add root route
@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth-hub", status_code=302)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
```

### Using App Context Helpers

**apps/app1/web.py:**
```python
from fastapi import Request, APIRouter

router = APIRouter()

@router.get("/info")
async def app_info(request: Request):
    """Get app information using built-in helpers."""
    return {
        "app_slug": request.state.app_slug,
        "base_path": request.state.app_base_path,
        "auth_hub_url": request.state.auth_hub_url,
        "mounted_apps": list(request.state.mounted_apps.keys(),
        "manifest_name": request.state.manifest.get("name"),
    }

@router.get("/navigate/{target_app}")
async def navigate_to_app(request: Request, target_app: str):
    """Navigate to another app using mounted_apps."""
    all_apps = request.state.mounted_apps
    
    if target_app not in all_apps:
        return {"error": f"App '{target_app}' not found"}
    
    target_path = all_apps[target_app]["path_prefix"]
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=target_path, status_code=302)
```

---

## Next Steps

- [SSO Multi-App Setup Guide](./SSO_MULTI_APP_SETUP.md) - Detailed SSO setup
- [Manifest Reference](../MANIFEST_REFERENCE.md) - Complete manifest documentation
- [Architecture Guide](../ARCHITECTURE.md) - System architecture
- [Examples](../../examples/) - Real-world examples

---

**Happy Building! 🚀**
