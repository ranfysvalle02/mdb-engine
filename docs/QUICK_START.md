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
from fastapi import Depends
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_embedding_service

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
async def get_items(db=Depends(get_scoped_db)):
    # db is automatically scoped to "my_app"
    items = await db.items.find({}).to_list(length=10)
    return {"items": items}

@app.post("/embed")
async def embed_text(
    text: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    # Both dependencies automatically bound to the current app
    result = await embedding_service.process_and_store(
        text_content=text,
        source_id="doc_1",
        collection=db.knowledge_base,
    )
    return {"chunks_created": result["chunks_created"]}
```

This pattern automatically:
- Initializes the engine on startup
- Loads and registers the manifest
- Auto-detects multi-site mode from manifest
- Auto-retrieves app tokens
- Shuts down the engine on app shutdown

### Request-Scoped Dependencies

Import dependencies from `mdb_engine.dependencies`:

| Dependency | Description |
|------------|-------------|
| `get_engine` | Get the MongoDBEngine instance |
| `get_app_slug` | Get the current app's slug |
| `get_app_config` | Get the app's manifest/config |
| `get_scoped_db` | Get scoped database for the current app (most common) |
| `get_embedding_service` | Get EmbeddingService for the current app |
| `get_memory_service` | Get Mem0 memory service (returns None if not configured) |
| `get_llm_client` | Get auto-configured OpenAI/AzureOpenAI client |
| `get_llm_model_name` | Get LLM deployment/model name |
| `get_authz_provider` | Get authorization provider (Casbin/OSO) |
| `get_current_user` | Get authenticated user from request.state |
| `get_user_roles` | Get current user's roles |
| `AppContext` | All-in-one context (see below) |

```python
from fastapi import Depends
from mdb_engine.dependencies import (
    get_scoped_db,
    get_embedding_service,
    get_memory_service,
    get_llm_client,
    get_llm_model_name,
)

@app.post("/chat")
async def chat(
    query: str,
    db=Depends(get_scoped_db),
    memory=Depends(get_memory_service),
    llm=Depends(get_llm_client),
):
    # Memory is optional - returns None if not configured
    context = []
    if memory:
        results = memory.search(query=query, user_id=user_id, limit=3)
        context = [r.get("memory") for r in results if r.get("memory")]
    
    # Use LLM
    response = llm.chat.completions.create(
        model=get_llm_model_name(),
        messages=[{"role": "user", "content": query}],
    )
    return {"response": response.choices[0].message.content}
```

### AppContext - All-in-One Magic ✨

For routes that need multiple services, use `AppContext` to get everything at once:

```python
from fastapi import Depends
from mdb_engine.dependencies import AppContext

@app.post("/process")
async def process(data: str, ctx: AppContext = Depends()):
    # Everything is available through ctx
    user = ctx.require_user()  # Raises 401 if not authenticated
    ctx.require_role("editor")  # Raises 403 if missing role
    
    # All services available
    docs = await ctx.db.documents.find({"user": user["email"]}).to_list(10)
    
    if ctx.embedding_service:
        embeddings = await ctx.embedding_service.embed_chunks([data])
    
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[{"role": "user", "content": data}],
        )
    
    return {"app": ctx.slug, "docs": len(docs)}

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

### Auto-Initialized from Manifest (Recommended)

When using `engine.create_app()`, authorization and demo users are **automatically initialized** from your manifest. No manual setup required!

**Casbin RBAC Example (manifest.json):**

```json
{
  "slug": "my_app",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "model": "rbac",
        "policies_collection": "casbin_policies",
        "initial_policies": [
          ["admin", "documents", "read"],
          ["admin", "documents", "write"],
          ["editor", "documents", "read"],
          ["editor", "documents", "write"],
          ["viewer", "documents", "read"]
        ],
        "initial_roles": [
          {"user": "alice@example.com", "role": "admin"},
          {"user": "bob@example.com", "role": "editor"}
        ]
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users",
      "demo_users": [
        {"email": "alice@example.com", "password": "password123", "role": "admin"},
        {"email": "bob@example.com", "password": "password123", "role": "editor"}
      ],
      "demo_user_seed_strategy": "auto"
    }
  }
}
```

**Application Code - That's it!**

```python
from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_scoped_db, get_authz_provider
from pathlib import Path

engine = MongoDBEngine(mongo_uri="...", db_name="...")

# Everything is auto-configured from manifest!
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

@app.get("/documents")
async def get_documents(
    request: Request,
    authz=Depends(get_authz_provider),
    db=Depends(get_scoped_db),
):
    user = await get_current_user(request)  # From session cookie
    
    # Check permission via Casbin
    if not await authz.check(user["email"], "documents", "read"):
        raise HTTPException(403, "Permission denied")
    
    return await db.documents.find({}).to_list(100)
```

**What gets auto-initialized:**
- ✅ Casbin enforcer with initial policies and role assignments
- ✅ Demo users with bcrypt-hashed passwords
- ✅ Session cookie authentication

### OSO Cloud Example

For OSO Cloud authorization:

```json
{
  "slug": "my_app",
  "auth": {
    "policy": {
      "provider": "oso",
      "authorization": {
        "initial_roles": [
          {"user": "alice@example.com", "role": "editor"},
          {"user": "bob@example.com", "role": "viewer"}
        ]
      }
    },
    "users": {
      "enabled": true,
      "demo_users": [
        {"email": "alice@example.com", "password": "password123", "role": "editor"},
        {"email": "bob@example.com", "password": "password123", "role": "viewer"}
      ]
    }
  }
}
```

**Environment Variables for OSO:**
```bash
export OSO_AUTH="your-oso-api-key"
export OSO_URL="http://oso-dev:8080"  # For OSO Dev Server
```

See `examples/basic/oso_hello_world/` for a complete OSO example with Docker Compose.

### Custom Authorization Provider

```python
from mdb_engine.auth import AuthorizationProvider

class CustomProvider:
    async def check(self, subject, resource, action, user_object=None):
        # Your custom logic
        return True

app.state.authz_provider = CustomProvider()
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
