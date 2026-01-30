# SSO Multi-App API Cheat Sheet

Quick reference for building SSO-enabled multi-app deployments with MDB-Engine.

## Quick Setup

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

# Initialize engine
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_apps_db"
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
    title="My Platform",
    version="1.0.0",
)
```

## Manifest Configuration

### Auth Hub Manifest
```json
{
  "schema_version": "2.0",
  "slug": "auth-hub",
  "name": "Auth Hub",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/", "/login", "/register"]
  },
  "data_access": {
    "read_scopes": ["auth-hub"],
    "write_scope": "auth-hub"
  }
}
```

### SSO App Manifest
```json
{
  "schema_version": "2.0",
  "slug": "app1",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",  // Use path prefix, not full URL
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

## Route Handlers

### Auth Hub Routes
```python
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from mdb_engine import get_shared_user_pool

router = APIRouter()

@router.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    pool = get_shared_user_pool(request)
    user = await pool.create_user(
        email=email,
        password=password,
        app_roles={"auth-hub": ["viewer"]}
    )
    token = await pool.authenticate(email=email, password=password)
    response = RedirectResponse(url="/auth-hub/", status_code=302)
    response.set_cookie(key="mdb_auth_token", value=token, httponly=True)
    return response

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    pool = get_shared_user_pool(request)
    token = await pool.authenticate(email=email, password=password)
    response = RedirectResponse(url="/auth-hub/", status_code=302)
    response.set_cookie(key="mdb_auth_token", value=token, httponly=True)
    return response
```

### SSO App Routes
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/")
async def index(request: Request):
    # User is automatically authenticated via SSO middleware
    user = getattr(request.state, "user", None)
    user_roles = getattr(request.state, "user_roles", [])
    
    if not user:
        # Redirect to auth hub if not authenticated
        from fastapi.responses import RedirectResponse
        auth_hub_url = getattr(request.state, "auth_hub_url", "/auth-hub")
        return RedirectResponse(url=f"{auth_hub_url}/login", status_code=302)
    
    return {"message": "Welcome", "user": user["email"], "roles": user_roles}
```

## Request State Helpers

```python
@app.get("/my-route")
async def my_route(request: Request):
    # App's path prefix (e.g., "/auth-hub")
    base_path = request.state.app_base_path
    
    # Auth hub URL from manifest
    auth_hub_url = request.state.auth_hub_url
    
    # Current app slug
    app_slug = request.state.app_slug
    
    # Authenticated user (if logged in)
    user = request.state.user
    
    # User roles for current app
    user_roles = request.state.user_roles
    
    # All mounted apps metadata
    mounted_apps = request.state.mounted_apps
    
    # MongoDBEngine instance
    engine = request.state.engine
    
    # App's manifest.json
    manifest = request.state.manifest
```

## Shared User Pool API

```python
from mdb_engine import get_shared_user_pool

# In route handler
pool = get_shared_user_pool(request)

# Create user
user = await pool.create_user(
    email="user@example.com",
    password="secure_password",
    app_roles={"app1": ["viewer"], "app2": ["editor"]}
)

# Authenticate
token = await pool.authenticate(email="user@example.com", password="password")

# Validate token
user = await pool.validate_token(token)

# Update roles
await pool.update_user_roles(
    email="user@example.com",
    app="app1",
    roles=["viewer", "editor"]
)

# Check role
has_role = pool.user_has_role(user, app="app1", role="admin")
```

## Configuration Methods

### Method 1: Programmatic
```python
app = engine.create_multi_app(
    apps=[
        {"slug": "app1", "manifest": Path("app1/manifest.json"), "path_prefix": "/app1"},
        {"slug": "app2", "manifest": Path("app2/manifest.json"), "path_prefix": "/app2"},
    ],
    title="Platform",
    validate=True,
)
```

### Method 2: Manifest-Based
```python
app = engine.create_multi_app(
    multi_app_manifest=Path("multi_app_manifest.json"),
    title="Platform",
)
```

### Method 3: Auto-Discovery
```python
app = engine.create_multi_app(
    apps_dir=Path("./apps"),
    path_prefix_template="/{slug}",  # or "/app-{index}"
    validate=True,
)
```

## Key Endpoints

- `GET /health` - Unified health check for all apps
- `GET /_mdb/routes` - List all routes from mounted apps
- `GET /docs` - Aggregated OpenAPI docs
- `GET /docs/{app_slug}` - Individual app docs

## Environment Variables

```bash
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=my_apps_db
MDB_ENGINE_JWT_SECRET=your-secret-key-here  # Required for SSO
```

## Running

```bash
# Development
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Production
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Common Patterns

### Redirect to Auth Hub
```python
from fastapi.responses import RedirectResponse

@app.get("/protected")
async def protected(request: Request):
    if not request.state.user:
        auth_hub_url = request.state.auth_hub_url
        return RedirectResponse(url=f"{auth_hub_url}/login", status_code=302)
    return {"data": "protected"}
```

### Role-Based Access
```python
from mdb_engine import require_role

@app.get("/admin")
@require_role("admin")
async def admin(request: Request):
    return {"message": "Admin only"}
```

### Public Routes
```json
{
  "auth": {
    "public_routes": ["/", "/health", "/api/public/*"]
  }
}
```

### Wildcard Public Routes
```json
{
  "auth": {
    "public_routes": ["/api/public/*", "/static/*"]
  }
}
```

## Cookie Configuration

```python
# Set cookie after login
response.set_cookie(
    key="mdb_auth_token",
    value=token,
    httponly=True,
    secure=True,  # Use True in production with HTTPS
    samesite="lax",
    max_age=86400  # 24 hours
)
```

## Troubleshooting

**SSO not working?**
- Check `MDB_ENGINE_JWT_SECRET` is set and same for all apps
- Verify `auth_hub_url` uses path prefix (`/auth-hub`), not full URL
- Check cookies are set correctly (browser DevTools)

**404 on mounted apps?**
- Ensure path prefixes start with `/`
- Check manifest paths are correct
- Verify apps mounted successfully (check logs)

**Auth middleware not working?**
- Ensure `"mode": "shared"` in manifest
- Check `public_routes` are correct
- Verify path prefix is stripped correctly (check `request.state.app_base_path`)

## Quick Reference

| Feature | Code |
|---------|------|
| Get user pool | `get_shared_user_pool(request)` |
| Get current user | `request.state.user` |
| Get user roles | `request.state.user_roles` |
| Get app slug | `request.state.app_slug` |
| Get base path | `request.state.app_base_path` |
| Get auth hub URL | `request.state.auth_hub_url` |
| Create user | `await pool.create_user(...)` |
| Authenticate | `await pool.authenticate(...)` |
| Validate token | `await pool.validate_token(token)` |

---

**See Also:**
- [Full SSO Multi-App Guide](../guides/SSO_MULTI_APP_SETUP.md)
- [Manifest Reference](../MANIFEST_REFERENCE.md)
- [Examples](../../examples/advanced/sso-multi-app/)
