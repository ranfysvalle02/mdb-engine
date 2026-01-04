# MDB_ENGINE Quick Start Guide

## Installation

```bash
pip install mdb-engine
```

Or install from source:

```bash
pip install -e .
```

---

## When to Use What

### Choosing Your Pattern

| Scenario | Recommended Approach |
|----------|---------------------|
| **New FastAPI app** | `engine.create_app()` - automatic lifecycle |
| **Existing FastAPI app** | `engine.lifespan()` or manual `initialize()`/`shutdown()` |
| **Script or CLI tool** | Direct engine usage with `async with` |
| **Multiple apps** | Multi-app with `read_scopes` in manifest |
| **Heavy computation** | Enable Ray with `enable_ray=True` |

### Feature Guide

| Feature | When to Use | Configuration |
|---------|------------|---------------|
| **`create_app()`** | New FastAPI apps - handles everything automatically | `engine.create_app(slug, manifest)` |
| **`lifespan()`** | Custom FastAPI apps - just lifecycle management | `FastAPI(lifespan=engine.lifespan(...))` |
| **Ray Support** | Distributed processing, isolated app actors | `enable_ray=True` in constructor |
| **Multi-site Mode** | Apps sharing data across boundaries | `read_scopes` in manifest |
| **App Tokens** | Production security, encrypted secrets | Set `MDB_ENGINE_MASTER_KEY` env var |
| **Shared Auth (SSO)** | Multi-app with single sign-on | `"auth": {"mode": "shared"}` in manifest |
| **Per-App Auth** | Isolated auth per app (default) | `"auth": {"mode": "app"}` in manifest |
| **Casbin Auth** | Simple RBAC (roles like admin, user) | `"provider": "casbin"` in manifest |
| **OSO Auth** | Complex permission rules (policies) | `"provider": "oso"` in manifest |
| **Memory Service** | AI chat apps with persistent memory | `memory_config` in manifest |
| **Embeddings** | Vector search, RAG applications | Use `EmbeddingService` |

### Quick Decision Tree

```
Building a web app?
├── YES → Use create_app() for automatic lifecycle
│         │
│         └── Need multiple apps sharing data?
│             ├── YES → Need SSO (login once, access all apps)?
│             │         ├── YES → Use auth.mode="shared" 
│             │         └── NO  → Use auth.mode="app" + read_scopes
│             └── NO  → Single app is fine
│
└── NO  → Use engine directly:
          async with MongoDBEngine(...) as engine:
              db = engine.get_scoped_db("my_app")
```

---

## Basic Usage

### 1. Initialize the MongoDB Engine

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

# Create engine instance
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    manifests_dir=Path("manifests")
)

# Initialize (async)
await engine.initialize()
```

### 2. Get Scoped Database Access

```python
# Get app-scoped database
db = engine.get_scoped_db("my_app")

# Use MongoDB-style API
doc = await db.my_collection.find_one({"name": "test"})
docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
await db.my_collection.insert_one({"name": "New Doc"})
```

### 3. Register Apps

```python
# Load and validate manifest
manifest = await engine.load_manifest(Path("manifests/my_app/manifest.json"))

# Register app (automatically creates indexes)
await engine.register_app(manifest)

# Or reload all active apps from database
count = await engine.reload_apps()
```

### 4. Use Individual Components

```python
# Database scoping
from mdb_engine.database import ScopedMongoWrapper, AppDB

# Authentication & Authorization
from mdb_engine.auth import (
    setup_auth_from_manifest,
    get_current_user,
    get_authz_provider,
    require_admin
)

# Manifest validation
from mdb_engine.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

# Index management
from mdb_engine.indexes import AsyncAtlasIndexManager
```

## Context Manager Usage

```python
# Automatic cleanup
async with MongoDBEngine(mongo_uri, db_name) as engine:
    await engine.reload_apps()
    db = engine.get_scoped_db("my_app")
    # ... use engine
    # Automatic cleanup on exit
```

## FastAPI Integration

### Simplified Pattern with `create_app()`

The easiest way to integrate with FastAPI - handles all lifecycle management automatically:

```python
import os
from pathlib import Path
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "my_database"),
)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json"),
)

@app.get("/")
async def index():
    return {"app": "my_app", "status": "ok"}

@app.get("/items")
async def get_items():
    # Engine is already initialized, manifest loaded
    db = engine.get_scoped_db("my_app")
    items = await db.items.find({}).to_list(length=10)
    return {"items": items}
```

This pattern automatically:
- Initializes the engine on startup
- Loads and registers the manifest
- Auto-detects multi-site mode from manifest
- Auto-retrieves app tokens
- Shuts down the engine on app shutdown

### Custom Lifespan Pattern

For more control over FastAPI app creation:

```python
from fastapi import FastAPI
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(mongo_uri="...", db_name="...")

# Use engine's lifespan helper
app = FastAPI(
    title="My App",
    lifespan=engine.lifespan("my_app", Path("manifest.json"))
)

@app.get("/")
async def index():
    db = engine.get_scoped_db("my_app")
    return {"status": "ok"}
```

### With Optional Ray Support

Enable Ray for distributed processing:

```python
from mdb_engine import MongoDBEngine

# Enable Ray support (only activates if Ray is installed)
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    enable_ray=True,
    ray_namespace="my_namespace",
)

app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.get("/status")
async def status():
    return {
        "ray_enabled": engine.has_ray,
        "ray_namespace": engine.ray_namespace,
    }
```

## Authentication & Authorization

### Unified Auth Setup

Configure authentication and authorization in your `manifest.json`:

```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "link_users_roles": true,
        "default_roles": ["user", "admin"]
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users"
    }
  }
}
```

Then in your FastAPI app startup:

```python
from mdb_engine.auth import setup_auth_from_manifest

@app.on_event("startup")
async def startup():
    await engine.initialize()
    await engine.register_app(manifest)

    # Auto-creates Casbin provider from manifest
    await setup_auth_from_manifest(app, engine, "my_app")
```

### Using Authorization

```python
from mdb_engine.auth import get_authz_provider, get_current_user
from fastapi import Depends

@app.get("/protected")
async def protected_route(
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    # Check permission using auto-created provider
    has_access = await authz.check(
        subject=user.get("email"),
        resource="my_app",
        action="access"
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"user_id": user["user_id"]}
```

### Extensibility

**Custom Provider:**
```python
from mdb_engine.auth import AuthorizationProvider

class CustomProvider:
    async def check(self, subject, resource, action, user_object=None):
        # Your custom logic
        return True

app.state.authz_provider = CustomProvider()
```

**OSO Provider:**
```json
{
  "auth_policy": {
    "provider": "oso"
  }
}
```

**Custom Casbin Model:**
```json
{
  "auth_policy": {
    "provider": "casbin",
    "authorization": {
      "model": "/path/to/custom_model.conf"
    }
  }
}
```

### Auth Modes (Per-App vs Shared)

MDB_ENGINE supports two authentication modes configured in your manifest:

#### Per-App Auth (Default)
Each app has its own authentication - isolated users, isolated tokens.

```json
{
  "slug": "my_app",
  "auth": {
    "mode": "app",
    "token_required": true
  }
}
```

#### Shared Auth (SSO)
All apps share a central user pool. Login once, access multiple apps.

```json
{
  "slug": "my_app",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api/public"]
  }
}
```

When using shared auth:
- Users are stored in `_mdb_engine_shared_users` collection
- JWT tokens work across all apps (SSO)
- Each app defines its own role requirements
- `SharedAuthMiddleware` is auto-configured by `engine.create_app()`

```python
# Shared auth is automatic - just read from request.state
@app.get("/protected")
async def protected(request: Request):
    user = request.state.user  # Populated by middleware
    roles = request.state.user_roles
    return {"email": user["email"], "roles": roles}
```

For a complete example, see `examples/multi_app_shared/`.

## Observability

```python
# Health checks
health = await engine.get_health_status()
print(health["status"])  # "healthy", "degraded", "unhealthy"

# Metrics
metrics = engine.get_metrics()
print(metrics["summary"])

# Structured logging with correlation IDs
from mdb_engine.observability import get_logger, set_correlation_id

correlation_id = set_correlation_id()
logger = get_logger(__name__)
logger.info("Operation completed")  # Includes correlation_id automatically
```

## Testing

Run the test suite using the Makefile (recommended):

```bash
# Install test dependencies
make install-dev

# Run all tests
make test

# Run unit tests only (fast, no MongoDB required)
make test-unit

# Run with coverage report
make test-coverage-html
# Then open htmlcov/index.html in your browser
```

For more detailed testing information, see:
- [Testing Guide](guides/testing.md) - Comprehensive testing documentation
- [tests/README.md](../tests/README.md) - Test structure and examples
- [CONTRIBUTING.md](../CONTRIBUTING.md#testing) - Testing guidelines for contributors

## Package Structure

```
mdb_engine/
├── core/              # MongoDBEngine, Manifest validation
├── database/          # Scoped wrappers, AppDB, connection pooling
├── auth/              # Authentication, authorization
├── indexes/           # Index management
├── observability/     # Metrics, logging, health checks
├── utils/             # Utility functions
└── constants.py       # Shared constants
```

## Features

- ✅ **Automatic App Isolation** - All queries automatically scoped
- ✅ **Manifest Validation** - JSON schema validation with versioning
- ✅ **Index Management** - Automatic index creation and management
- ✅ **Observability** - Built-in metrics, logging, and health checks
- ✅ **Type Safety** - Comprehensive type hints
- ✅ **Test Infrastructure** - Full test suite

## Next Steps

- See main [README.md](../README.md) for detailed documentation
- Check [tests/README.md](../tests/README.md) for testing information
