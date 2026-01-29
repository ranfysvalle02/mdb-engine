# SSO Multi-App Setup Guide: FastAPI Magic ✨

Complete guide to setting up Single Sign-On (SSO) multi-app deployments using MDB-Engine's FastAPI multi-app mounting feature. Perfect for deploying multiple apps under a single service (Render.com, Railway, Heroku, etc.).

## Table of Contents

- [Overview](#overview)
- [What You'll Build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Step-by-Step Setup](#step-by-step-setup)
- [Configuration Methods](#configuration-methods)
- [Running Your Multi-App](#running-your-multi-app)
- [Deployment](#deployment)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Architecture Deep Dive](#architecture-deep-dive)

---

## Overview

**SSO Multi-App** allows you to deploy multiple FastAPI applications under a single FastAPI instance, all sharing:

- ✅ **Single Sign-On (SSO)**: Login once, access all apps
- ✅ **Shared Authentication**: Centralized user pool and JWT tokens
- ✅ **Path-Based Routing**: Each app accessible via path prefix (e.g., `/auth-hub`, `/app1`)
- ✅ **Unified Health Checks**: Single `/health` endpoint for all apps
- ✅ **Resource Efficiency**: Shared MongoDB connection pool and engine instance

### Perfect For

- **Render.com**: Single-service deployments
- **Railway**: Single-service deployments
- **Heroku**: Single dyno deployments
- **Development**: Simplified local development
- **Small-Medium Deployments**: Resource-efficient setups

---

## What You'll Build

You'll create a multi-app platform with:

1. **Auth Hub** (`/auth-hub`): Central authentication service
   - User registration
   - Login/logout
   - Role management
   - User dashboard

2. **App 1** (`/pwd-zero`): SSO-enabled app
   - Data viewing
   - Requires `viewer` role

3. **App 2** (`/flux`): SSO-enabled app
   - Data editing
   - Requires `viewer` role (access), `editor`/`admin` (edit)

All apps share:
- Same MongoDB database
- Same JWT secret
- Same user pool
- Single FastAPI instance

---

## Prerequisites

### Required

- **Python 3.11+**
- **MongoDB** (local or Atlas)
- **MDB-Engine** installed: `pip install -e ".[casbin]"`
- **FastAPI dependencies**: `pip install uvicorn fastapi jinja2 python-multipart`

### Optional

- **Docker** (for containerized MongoDB)
- **Render.com/Railway account** (for deployment)

---

## Quick Start

### 1. Install Dependencies

```bash
# Install MDB-Engine with Casbin support
pip install -e ".[casbin]"

# Install FastAPI and web dependencies
pip install uvicorn fastapi jinja2 python-multipart
```

### 2. Set Environment Variables

```bash
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DB="my_apps_db"
export MDB_ENGINE_JWT_SECRET="your-secret-key-here"
```

**Generate a secure JWT secret:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3. Start MongoDB

```bash
# Using Docker
docker run -d -p 27017:27017 --name mongodb mongo:7

# Or use MongoDB Atlas (set MONGODB_URI to your Atlas connection string)
```

### 4. Create Your Multi-App

```python
# main.py
from mdb_engine import MongoDBEngine
from pathlib import Path
import os

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "my_apps_db"),
)

# Create multi-app
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
    title="My Multi-App Platform",
)
```

### 5. Run It!

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Access your apps:**
- Auth Hub: http://localhost:8000/auth-hub
- App 1: http://localhost:8000/app1
- Health: http://localhost:8000/health

---

## Step-by-Step Setup

### Step 1: Project Structure

Create the following directory structure:

```
my-multi-app/
├── main.py                 # Multi-app entry point
├── multi_app_manifest.json # Optional: manifest-based config
├── .env                    # Environment variables
└── apps/
    ├── auth-hub/
    │   ├── manifest.json
    │   ├── web.py
    │   └── templates/
    ├── app1/
    │   ├── manifest.json
    │   ├── web.py
    │   └── templates/
    └── app2/
        ├── manifest.json
        ├── web.py
        └── templates/
```

### Step 2: Create App Manifests

Each app needs a `manifest.json` file. Here's an example for an SSO-enabled app:

**`apps/app1/manifest.json`:**
```json
{
  "schema_version": "2.0",
  "slug": "app1",
  "name": "My App 1",
  "description": "First SSO-enabled app",
  "status": "active",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  },
  "data_access": {
    "read_scopes": ["app1"],
    "write_scope": "app1"
  }
}
```

**`apps/auth-hub/manifest.json`:**
```json
{
  "schema_version": "2.0",
  "slug": "auth-hub",
  "name": "Auth Hub",
  "description": "Central authentication service",
  "status": "active",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health", "/register", "/login"]
  },
  "data_access": {
    "read_scopes": ["auth-hub"],
    "write_scope": "auth-hub"
  }
}
```

**Key Points:**
- `"mode": "shared"` enables SSO
- `"auth_hub_url": "/auth-hub"` points to the auth hub (use path prefix, not full URL)
- `"public_routes"` lists routes that don't require authentication

### Step 3: Create App Code

**`apps/auth-hub/web.py`:**
```python
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mdb_engine import get_shared_user_pool

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """User dashboard."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse(url="/auth-hub/login", status_code=302)
    
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Register a new user."""
    pool = get_shared_user_pool(request)
    
    try:
        user = await pool.create_user(
            email=email,
            password=password,
            app_roles={"auth-hub": ["viewer"]},
        )
        # Auto-login after registration
        token = await pool.authenticate(email=email, password=password)
        response = RedirectResponse(url="/auth-hub/", status_code=302)
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
        )
        return response
    except ValueError as e:
        # User already exists
        return RedirectResponse(url="/auth-hub/login?error=exists", status_code=302)

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Login user."""
    pool = get_shared_user_pool(request)
    
    try:
        token = await pool.authenticate(email=email, password=password)
        response = RedirectResponse(url="/auth-hub/", status_code=302)
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=False,  # Set to True in production with HTTPS
        )
        return response
    except ValueError:
        return RedirectResponse(url="/auth-hub/login?error=invalid", status_code=302)
```

**`apps/app1/web.py`:**
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """App 1 homepage."""
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})
```

### Step 4: Create Multi-App Entry Point

**`main.py`:**
```python
#!/usr/bin/env python3
"""
Multi-App Main Entry Point
==========================

Creates a single FastAPI app that mounts multiple child apps.
Perfect for single-service deployments (Render.com, Railway, etc.).
"""

import logging
import os
import sys
from pathlib import Path

from mdb_engine import MongoDBEngine

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
db_name = os.getenv("MONGODB_DB", "my_apps_db")
APPS_DIR = Path(__file__).parent / "apps"

# Initialize engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)

# Create multi-app
app = engine.create_multi_app(
    apps=[
        {
            "slug": "auth-hub",
            "manifest": APPS_DIR / "auth-hub" / "manifest.json",
            "path_prefix": "/auth-hub",
        },
        {
            "slug": "app1",
            "manifest": APPS_DIR / "app1" / "manifest.json",
            "path_prefix": "/app1",
        },
    ],
    title="My Multi-App Platform",
    description="SSO-enabled multi-app deployment",
    version="1.0.0",
)

# Optional: Add root route
@app.get("/")
async def root():
    """Root endpoint - redirects to auth hub."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth-hub", status_code=302)

logger.info("Multi-app created successfully!")
logger.info("Apps accessible at:")
logger.info("  - /auth-hub/*  (Auth Hub)")
logger.info("  - /app1/*      (App 1)")
logger.info("  - /health      (Health check)")
```

### Step 5: Include Routers in Child Apps

Each child app needs to include its router. Update your child app creation to include routers:

**In your child app's `web.py` or initialization:**
```python
# This is handled automatically by MDB-Engine when creating the app
# But if you need to add custom routes, you can access the app like this:

from mdb_engine import get_app_state

# In a route handler:
app_state = get_app_state(request)
child_app = app_state.get("child_app")  # Access the child FastAPI app
child_app.include_router(router)  # Add your router
```

Actually, the better approach is to include routers when creating the app. MDB-Engine automatically includes routers defined in your child app files. Just make sure your routers are imported and included in the child app's module.

---

## Configuration Methods

MDB-Engine supports two ways to configure multi-app mounting:

### Method 1: Programmatic Configuration

**Pros:**
- Full Python control
- Dynamic configuration
- Easy to debug

**Example:**
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
    title="My Platform",
)
```

### Method 2: Manifest-Based Configuration

**Pros:**
- Declarative configuration
- Version-controlled
- No code changes needed

**`multi_app_manifest.json`:**
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
    ],
    "shared_middleware": {
      "cors": true,
      "rate_limiting": true,
      "health_checks": true
    }
  }
}
```

**Usage:**
```python
app = engine.create_multi_app(
    multi_app_manifest=Path("./multi_app_manifest.json"),
    title="My Platform",
)
```

---

## Running Your Multi-App

### Development Mode

```bash
# With auto-reload
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Production-like
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Using Python Directly

```python
# main.py
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

Then run:
```bash
python main.py
```

### Environment Variables

Create a `.env` file:
```bash
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=my_apps_db
MDB_ENGINE_JWT_SECRET=your-secret-key-here
LOG_LEVEL=INFO
```

Load with `python-dotenv`:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Deployment

### Render.com Deployment

**1. Create a Render Web Service**

**2. Set Build Command:**
```bash
pip install -e ".[casbin]" && pip install uvicorn fastapi jinja2 python-multipart
```

**3. Set Start Command:**
```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

**4. Set Environment Variables:**
- `MONGODB_URI`: Your MongoDB connection string
- `MONGODB_DB`: Your database name
- `MDB_ENGINE_JWT_SECRET`: Your JWT secret (generate with `secrets.token_urlsafe(32)`)

**5. Deploy!**

All apps will be accessible under your Render URL:
- `https://your-app.onrender.com/auth-hub`
- `https://your-app.onrender.com/app1`
- `https://your-app.onrender.com/health`

### Railway Deployment

**1. Create a Railway Project**

**2. Set Start Command:**
```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

**3. Set Environment Variables** (same as Render.com)

**4. Deploy!**

### Docker Deployment

**`Dockerfile`:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**`requirements.txt`:**
```
mdb-engine[casbin]
uvicorn[standard]
fastapi
jinja2
python-multipart
python-dotenv
```

**Build and run:**
```bash
docker build -t my-multi-app .
docker run -p 8000:8000 \
  -e MONGODB_URI="mongodb://host.docker.internal:27017" \
  -e MONGODB_DB="my_apps_db" \
  -e MDB_ENGINE_JWT_SECRET="your-secret" \
  my-multi-app
```

---

## Testing

### Manual Testing

1. **Start the app:**
   ```bash
   uvicorn main:app --reload
   ```

2. **Register a user:**
   - Visit http://localhost:8000/auth-hub/register
   - Enter email and password
   - Submit

3. **Access SSO apps:**
   - Visit http://localhost:8000/app1
   - Should be automatically authenticated (no login needed)

4. **Check health:**
   ```bash
   curl http://localhost:8000/health
   ```

### Automated Testing

```python
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test_multi_app_health():
    """Test unified health check."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "mounted_apps" in data

@pytest.mark.asyncio
async def test_auth_hub_access():
    """Test auth hub is accessible."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/auth-hub/")
        assert response.status_code in [200, 302]  # 302 if redirect to login
```

---

## Troubleshooting

### SSO Not Working

**Problem:** Users need to login to each app separately.

**Solutions:**
1. **Check JWT Secret:** All apps must use the same `MDB_ENGINE_JWT_SECRET`
   ```bash
   # Verify environment variable is set
   echo $MDB_ENGINE_JWT_SECRET
   ```

2. **Check Database:** All apps must use the same `MONGODB_DB`
   ```bash
   # Verify database name matches
   echo $MONGODB_DB
   ```

3. **Check Cookies:** Ensure cookies are set correctly
   - Check browser DevTools → Application → Cookies
   - Verify `access_token` cookie exists
   - Check cookie domain matches your deployment domain

4. **Check Auth Hub URL:** In child app manifests, use path prefix:
   ```json
   {
     "auth": {
       "auth_hub_url": "/auth-hub"  // ✅ Correct (path prefix)
       // NOT: "http://localhost:8000/auth-hub"  // ❌ Wrong
     }
   }
   ```

### Apps Not Accessible

**Problem:** Getting 404 when accessing mounted apps.

**Solutions:**
1. **Check Path Prefixes:** Ensure path prefixes start with `/`
   ```python
   "path_prefix": "/app1"  // ✅ Correct
   "path_prefix": "app1"   // ❌ Wrong (missing leading slash)
   ```

2. **Check Manifest Paths:** Ensure manifest paths are correct
   ```python
   # Use absolute paths or paths relative to multi_app_manifest.json
   "manifest": "./apps/app1/manifest.json"  // ✅ Relative to manifest
   "manifest": "apps/app1/manifest.json"    // ✅ Also works
   ```

3. **Check Logs:** Look for mounting errors in startup logs
   ```bash
   # Check for errors like:
   # "Failed to load manifest: ..."
   # "Path prefix conflict: ..."
   ```

### Health Check Failing

**Problem:** `/health` endpoint returns errors.

**Solutions:**
1. **Check MongoDB Connection:** Verify MongoDB is accessible
   ```bash
   # Test connection
   mongosh "mongodb://localhost:27017"
   ```

2. **Check Engine Initialization:** Look for initialization errors in logs

3. **Check Mounted Apps:** Verify all apps mounted successfully
   ```bash
   curl http://localhost:8000/health | jq '.mounted_apps'
   ```

### Environment Variables Not Loading

**Problem:** App can't find environment variables.

**Solutions:**
1. **Use `.env` file:** Create `.env` and load with `python-dotenv`
   ```python
   from dotenv import load_dotenv
   load_dotenv()
   ```

2. **Check Deployment Platform:** Verify env vars set in Render.com/Railway dashboard

3. **Use Defaults:** Provide defaults in code
   ```python
   mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
   ```

---

## Architecture Deep Dive

### How Multi-App Mounting Works

```
┌─────────────────────────────────────────────────┐
│         Parent FastAPI App                       │
│  (main.py - engine.create_multi_app())          │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  Shared MongoDBEngine                     │  │
│  │  - Single connection pool                 │  │
│  │  - Shared user pool (SSO)                 │  │
│  └──────────────────────────────────────────┘  │
│                                                  │
│  Routes:                                         │
│  ├── /health (unified health check)            │
│  ├── /auth-hub/* → Mounted App 1               │
│  ├── /app1/* → Mounted App 2                   │
│  └── /app2/* → Mounted App 3                   │
│                                                  │
│  ┌──────────────┐  ┌──────────────┐           │
│  │  Child App 1 │  │  Child App 2 │           │
│  │  (auth-hub)  │  │  (app1)      │           │
│  └──────────────┘  └──────────────┘           │
│         │                  │                    │
│         └──────────┬───────┘                    │
│                    │                             │
│         ┌──────────▼──────────┐                 │
│         │  SharedUserPool     │                 │
│         │  (SSO)              │                 │
│         └──────────────────────┘                 │
└─────────────────────────────────────────────────┘
```

### SSO Flow

1. **User registers/logs in** on Auth Hub (`/auth-hub/login`)
2. **Auth Hub authenticates** via `SharedUserPool`
3. **JWT token issued** and stored in cookie
4. **User visits SSO app** (`/app1`)
5. **SSO middleware validates token** automatically
6. **User authenticated** - no login needed!

### Path Prefix Routing

- **Parent app** handles routing at `/health`, `/info`, etc.
- **Child apps** handle all routes under their path prefix
- **FastAPI's `app.mount()`** handles the routing automatically

### Shared State

- **MongoDBEngine**: Single instance shared across all apps
- **SharedUserPool**: Single user pool for SSO
- **Connection Pool**: Single MongoDB connection pool
- **App State**: Each child app has access to parent's state

---

## Next Steps

- **Add More Apps:** Mount additional apps by adding to the `apps` list
- **Custom Middleware:** Add custom middleware at parent or child level
- **Advanced Routing:** Use FastAPI's routing features for complex paths
- **Monitoring:** Add Prometheus metrics or logging
- **Scaling:** Consider splitting apps if you need independent scaling

---

## Resources

- [MDB-Engine Documentation](../../README.md)
- [Manifest Reference](../../MANIFEST_REFERENCE.md)
- [Architecture Guide](../../ARCHITECTURE.md)
- [SSO Multi-App Example](../../../examples/advanced/sso-multi-app/README.md)

---

## Support

For issues or questions:
- Check [Troubleshooting](#troubleshooting) section
- Review [MDB-Engine FAQ](../../FAQ.md)
- Open an issue on GitHub

---

**Happy Building! 🚀**
