# MDB_ENGINE Examples

This directory contains example applications demonstrating how to use MDB_ENGINE. **All examples follow best practices and use mdb-engine abstractions consistently.**

---

## When to Use What

### Choose Your Pattern

| If you need... | Use this | Example |
|----------------|----------|---------|
| **Simple single app** | `engine.create_app()` | [simple_app](./simple_app/) |
| **Custom FastAPI setup** | Manual `initialize()`/`shutdown()` | [hello_world](./hello_world/) |
| **Multiple apps with SSO** | Shared auth mode | [multi_app_shared](./multi_app_shared/) |
| **Multiple apps (isolated auth)** | Multi-app with `read_scopes` | [multi_app](./multi_app/) |
| **Authorization/permissions** | OSO or Casbin from manifest | [oso_hello_world](./oso_hello_world/) |
| **Vector search/RAG** | EmbeddingService + Atlas Search | [interactive_rag](./interactive_rag/) |
| **Distributed processing** | `enable_ray=True` | [simple_app](./simple_app/) |

### Feature Decision Tree

```
Do you need a FastAPI web app?
├── YES → Use engine.create_app() ⭐ RECOMMENDED
│         └── Need custom startup logic? → Use on_startup callback
│
└── NO  → Use engine directly:
          await engine.initialize()
          db = engine.get_scoped_db("my_app")
```

### Database Access: Depends() vs Direct Engine

Two valid patterns exist. Use the right one for your context:

| Context | Pattern | Example |
|---------|---------|---------|
| Route handlers | `db=Depends(get_scoped_db)` | Clean, testable |
| Startup callback | `engine.get_scoped_db(APP_SLUG)` | In on_startup |
| Decorators/middleware | `engine.get_scoped_db(APP_SLUG)` | Runs before route DI |
| WebSocket setup | `engine.get_scoped_db(APP_SLUG)` | Before connection |

```python
# In route handlers - use Depends()
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(100)

# In on_startup callback - use engine directly
async def my_startup(app, engine, manifest):
    db = engine.get_scoped_db(APP_SLUG)
    await db.items.create_index("name")

app = engine.create_app(slug=APP_SLUG, manifest=..., on_startup=my_startup)
```

**⚠️ IMPORTANT:** Do NOT use `@app.on_event("startup")` with `engine.create_app()`.
FastAPI ignores `on_event` decorators when a `lifespan` context manager is provided.
Always use the `on_startup` callback parameter instead.

See [Best Practices](../docs/BEST_PRACTICES.md) for full guidance.

### When to Enable Features

| Feature | Enable When... | How |
|---------|---------------|-----|
| **Ray** | Heavy computation, distributed processing, isolated actors | `enable_ray=True` |
| **Multi-site** | Multiple apps need to share data securely | `read_scopes` in manifest |
| **Shared Auth (SSO)** | Multi-app with single sign-on | `"auth": {"mode": "shared"}` in manifest |
| **Per-App Auth** | Isolated auth per app (default) | `"auth": {"mode": "app"}` in manifest |
| **App Secrets** | Production deployment, need encrypted tokens | Set `MDB_ENGINE_MASTER_KEY` |
| **OSO Auth** | Fine-grained permission rules | `"provider": "oso"` in manifest |
| **Casbin Auth** | RBAC with simple roles | `"provider": "casbin"` in manifest |
| **Memory Service** | AI chat with persistent memory | `memory_config` in manifest |
| **Embeddings** | Semantic search, RAG applications | `embedding_config` in manifest |

---

## Available Examples

### [Simple App](./simple_app/) ⭐ Start Here

A minimal task management app demonstrating the **unified MongoDBEngine pattern** with `create_app()`:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(mongo_uri="...", db_name="...")
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
```

- Automatic lifecycle management (no startup/shutdown boilerplate)
- Scoped database access with `get_scoped_db()`
- Manifest-driven indexes
- Optional Ray support

**Perfect for:** Getting started with the recommended pattern

**Run it:**
```bash
cd simple_app
docker-compose up --build
```

### [Hello World](./hello_world/)

A simple, beginner-friendly example that demonstrates:
- Initializing the MongoDB Engine
- Creating and registering an app manifest
- Basic CRUD operations with automatic app scoping
- Using `engine.get_scoped_db()` for all database access (raw database access removed for security)
- Using `engine.get_scoped_db()` for app-scoped operations
- Authentication with JWT
- WebSocket support with real-time updates
- Health checks and metrics

**Perfect for:** Getting started with MDB_ENGINE

**Run it:**
```bash
cd hello_world
./run_with_docker.sh
```

### [OSO Hello World](./oso_hello_world/)

A simple example demonstrating:
- OSO Cloud authorization integration
- Automatic OSO provider setup from manifest.json
- Permission-based access control (read/write permissions)
- OSO Dev Server for local development
- Zero boilerplate OSO initialization

**Perfect for:** Learning OSO Cloud integration with mdb-engine

**Run it:**
```bash
cd oso_hello_world
docker-compose up
```

### [Interactive RAG](./interactive_rag/)

An advanced example demonstrating:
- Embedding service (`EmbeddingService`)
- Vector search with MongoDB Atlas Vector Search
- Knowledge base management with sessions
- Document processing and chunking
- Semantic search and retrieval
- Using OpenAI SDK directly for LLM operations

**Perfect for:** Building RAG applications with vector search

### [Vector Hacking](./vector_hacking/)

A demonstration of:
- Vector inversion attacks using LLM service abstraction
- Real-time attack visualization
- LLM service configuration via manifest.json
- Using `EmbeddingService` for embeddings and OpenAI SDK for chat

**Perfect for:** Understanding vector embeddings and LLM abstractions

### [Multi-App](./multi_app/)

An advanced example demonstrating:
- **Unified MongoDBEngine pattern** with `create_app()`
- Secure cross-app data access with app-level authentication
- Envelope encryption for app secrets
- Manifest-level authorization via `read_scopes`
- Automatic app token retrieval
- OSO authorization for fine-grained access control
- Docker Compose orchestration of multiple apps

**Perfect for:** Understanding multi-tenant security and cross-app access patterns

**Run it:**
```bash
cd multi_app
./scripts/setup.sh
docker-compose up
```

### [Multi-App Shared Auth](./multi_app_shared/) 🆕

SSO (Single Sign-On) example with shared user pool across multiple apps:

```python
# In manifest.json
{
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer"
  }
}
```

Demonstrates:
- **Shared auth mode** (`auth.mode="shared"`)
- Single Sign-On across apps
- Per-app role requirements
- Auto-configured `SharedAuthMiddleware`
- Cross-app data access with SSO

**Perfect for:** Building platforms with SSO, multi-app user management

**Run it:**
```bash
cd multi_app_shared
docker-compose up --build
# Visit:
#   Click Tracker: http://localhost:8000 (viewer role)
#   Dashboard: http://localhost:8001 (editor role required)
```

## Docker Compose Setup

Each example includes a `docker-compose.yml` file that provides:

### Standard Services

- **MongoDB** - Database server with authentication
- **MongoDB Express** - Web UI for browsing data (optional)

### Quick Start with Docker

```bash
# Navigate to an example
cd hello_world

# Start all services
docker-compose up -d

# Run the example
python main.py

# Stop services
docker-compose down
```

### Service URLs

When Docker Compose is running:

- **MongoDB:** `mongodb://admin:password@localhost:27017/?authSource=admin`
- **MongoDB Express UI:** http://localhost:8081 (admin/admin, optional with `--profile ui`)

## Running Examples

### Prerequisites

**Just Docker and Docker Compose!** That's it.

- Docker Desktop: https://www.docker.com/products/docker-desktop
- Or install separately: https://docs.docker.com/compose/install/

No need to install Python, MongoDB, or MDB_ENGINE - everything runs in containers.

### Quick Start

```bash
cd hello_world
docker-compose up
```

The example will:
1. Build the application (installs MDB_ENGINE automatically)
2. Start MongoDB
3. Start MongoDB Express (Web UI)
4. Run the example automatically
5. Show you all the output

### Example Structure

Each example includes:
- `README.md` - Explanation of what the example does
- `manifest.json` - App configuration manifest
- `main.py` - Main example code
- `Dockerfile` - Builds and runs the example
- `docker-compose.yml` - Orchestrates all services

### Environment Variables

Environment variables are set in `docker-compose.yml`. Common variables:
- `MONGO_URI` - MongoDB connection string (uses Docker service name)
- `MONGO_DB_NAME` - Database name
- `APP_SLUG` - App identifier
- `LOG_LEVEL` - Logging level

### How the Dockerfile Works

The Dockerfile:
1. Copies `mdb_engine` source code from the project root
2. Installs it with `pip install -e` (editable mode)
3. Installs all dependencies from `pyproject.toml`
4. Copies the example files
5. Runs the example automatically

No manual installation needed!

## Troubleshooting

### App Container Issues

1. **View app logs:**
   ```bash
   docker-compose logs app
   ```

2. **Rebuild after code changes:**
   ```bash
   docker-compose up --build
   ```

3. **Check if MongoDB is ready:**
   ```bash
   docker-compose ps mongodb
   docker-compose logs mongodb
   ```

### Port Conflicts

Modify ports in `docker-compose.yml`:

```yaml
services:
  mongodb:
    ports:
      - "27018:27017"  # Change host port
```

### Services Not Starting

1. **Check Docker is running:**
   ```bash
   docker ps
   ```

2. **View all logs:**
   ```bash
   docker-compose logs
   ```

3. **Check service health:**
   ```bash
   docker-compose ps
   ```

## Best Practices: Using MDB_ENGINE Abstractions

**All examples in this directory follow these best practices.** When building your own applications, always use mdb-engine abstractions:

### ✅ DO: Use MongoDB Engine Abstractions

```python
from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)
await engine.initialize()

# For app-scoped data (all operations should use scoped databases)
db = engine.get_scoped_db("my_app")
await db.my_collection.insert_one({"name": "Test"})
# All collections are automatically app-scoped for data isolation

# For LLM operations (use OpenAI SDK directly)
from openai import AzureOpenAI
client = AzureOpenAI(...)
response = client.chat.completions.create(...)
```

### ❌ DON'T: Create Direct MongoDB Clients

```python
# ❌ BAD: Creates new connection, bypasses pooling and observability
from motor.motor_asyncio import AsyncIOMotorClient
client = AsyncIOMotorClient(mongo_uri)
db = client[db_name]
await db.collection.find_one({})
client.close()  # Manual cleanup needed

# ✅ GOOD: Uses engine's scoped database
db = engine.get_scoped_db("my_app")
await db.collection.find_one({})
# No cleanup needed - engine manages connections and scoping
```

### Key Benefits of Using Abstractions

1. **Connection Pooling**: Reuses managed connection pools automatically
2. **Observability**: All operations tracked by engine metrics
3. **Resource Management**: Automatic cleanup, no manual client management
4. **App Scoping**: Automatic data isolation with `get_scoped_db()`
5. **Index Management**: Automatic index creation from manifest.json
6. **Health Checks**: Built-in health monitoring via `engine.get_health_status()`
7. **Embedding Service**: Provider-agnostic embeddings via EmbeddingService

### Common Patterns

#### Pattern 0: Unified create_app() (Recommended)
```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(mongo_uri="...", db_name="...")

# Optional: Custom startup/shutdown callbacks
async def my_startup(app, engine, manifest):
    """Runs after engine is fully initialized."""
    db = engine.get_scoped_db("my_app")
    await db.config.insert_one({"initialized": True})

async def my_shutdown(app, engine, manifest):
    """Runs before engine shuts down."""
    print("Cleaning up...")

# Automatic lifecycle management with optional callbacks
app = engine.create_app(
    slug="my_app",
    manifest=Path("manifest.json"),
    on_startup=my_startup,    # Optional
    on_shutdown=my_shutdown,  # Optional
)

@app.get("/items")
async def get_items():
    db = engine.get_scoped_db("my_app")
    return await db.items.find({}).to_list(10)
```

**Note:** The `on_startup` and `on_shutdown` callbacks run within the engine's lifespan context,
so the engine is fully initialized when `on_startup` runs. Do NOT use `@app.on_event("startup")`
with `create_app()` - those decorators are ignored when a lifespan is provided.

#### Pattern 1: App-Scoped Data (Most Common)
```python
# All operations automatically scoped to "my_app"
db = engine.get_scoped_db("my_app")
await db.products.insert_one({"name": "Widget"})
products = await db.products.find({}).to_list(length=10)
```

#### Pattern 2: Cross-App Data Access
```python
# For accessing data from multiple apps, use read_scopes
# This allows reading from multiple apps while writing to one
db = engine.get_scoped_db(
    app_slug="my_app",
    read_scopes=["my_app", "shared_app"],  # Can read from multiple apps
    write_scope="my_app"  # Writes go to this app
)
user = await db.users.find_one({"email": "user@example.com"})
```

#### Pattern 3: LLM Operations
```python
# Use OpenAI SDK directly for chat completions
from openai import AzureOpenAI
client = AzureOpenAI(...)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)

# Use EmbeddingService for embeddings
from mdb_engine.embeddings import EmbeddingService
embedding_service = EmbeddingService(...)
embeddings = await embedding_service.embed_chunks(["text to embed"])
```

#### Pattern 4: FastAPI Dependencies (Recommended)
```python
from fastapi import Depends
from mdb_engine.dependencies import get_scoped_db, get_embedding_service

# Use request-scoped dependencies from mdb_engine.dependencies
@app.get("/items")
async def get_items(db=Depends(get_scoped_db)):
    items = await db.items.find({}).to_list(length=10)
    return items

@app.post("/embed")
async def embed_text(
    text: str,
    db=Depends(get_scoped_db),
    embedding_service=Depends(get_embedding_service),
):
    # Both dependencies are automatically bound to the current app
    result = await embedding_service.process_and_store(
        text_content=text,
        source_id="doc_1",
        collection=db.knowledge_base,
    )
    return {"chunks_created": result["chunks_created"]}
```

Available dependencies from `mdb_engine.dependencies`:

| Dependency | Description |
|------------|-------------|
| `get_engine` | Get MongoDBEngine instance |
| `get_app_slug` | Get current app slug |
| `get_app_config` | Get app manifest/config |
| `get_scoped_db` | Get scoped database (most common) |
| `get_embedding_service` | Get EmbeddingService for the current app |
| `get_memory_service` | Get Mem0 memory service (None if not configured) |
| `get_llm_client` | Get auto-configured OpenAI/AzureOpenAI client |
| `get_llm_model_name` | Get LLM deployment/model name |
| `get_authz_provider` | Get authorization provider (Casbin/OSO) |
| `get_current_user` | Get authenticated user from request.state |
| `get_user_roles` | Get current user's roles |
| `AppContext` | All-in-one context with everything above |

#### Pattern 5: AppContext - All-in-One Magic ✨
```python
from fastapi import Depends
from mdb_engine.dependencies import AppContext

# AppContext gives you everything in one place
@app.post("/chat")
async def chat(query: str, ctx: AppContext = Depends()):
    # Access everything through ctx
    user = ctx.require_user()  # Raises 401 if not authenticated
    
    # Get memories if memory service is configured
    context = []
    if ctx.memory:
        results = ctx.memory.search(query=query, user_id=user["email"], limit=3)
        context = [r.get("memory") for r in results]
    
    # Generate embeddings
    if ctx.embedding_service:
        embeddings = await ctx.embedding_service.embed_chunks([query])
    
    # Use LLM if configured
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[{"role": "user", "content": query}],
        )
        return {"response": response.choices[0].message.content}
    
    return {"query": query, "context_count": len(context)}

@app.get("/admin")
async def admin_endpoint(ctx: AppContext = Depends()):
    # Require admin role - raises 403 if missing
    user = ctx.require_role("admin")
    
    # Check fine-grained permissions
    if not await ctx.check_permission("settings", "write"):
        raise HTTPException(403, "Cannot modify settings")
    
    return {"admin": user["email"], "app": ctx.slug}
```

#### Pattern 6: CSRF Token Handling in Frontend

All examples with authentication use CSRF protection. Include the `X-CSRF-Token` header in state-changing requests:

```javascript
// Helper to read CSRF token from cookie
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Include in all POST/PUT/DELETE requests
async function submitData(data) {
    const response = await fetch('/api/data', {
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

// Logout must be POST (not GET) with CSRF token
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

For full CSRF documentation, see [Auth README](../mdb_engine/auth/README.md#csrf-protection) and [Security Guide](../docs/SECURITY.md#csrf-protection).

## Contributing Examples

If you've built something cool with MDB_ENGINE, consider contributing an example! Examples should:

- **Use mdb-engine abstractions exclusively** - Never create direct MongoDB clients
- Always use `engine.get_scoped_db()` for all database operations (raw database access has been removed for security)
- Use OpenAI SDK directly for LLM operations
- Be self-contained and runnable
- Include a README explaining what they demonstrate
- Include a `docker-compose.yml` with all required services
- Use clear, well-commented code
- Focus on specific features or use cases
- Include a manifest.json file
- Include environment variable examples
- Demonstrate best practices that others can follow

## Need Help?

- Check the [main README](../../README.md) for general documentation
- See the [Quick Start Guide](../../docs/QUICK_START.md) for detailed setup instructions
- Open an issue if you encounter problems
