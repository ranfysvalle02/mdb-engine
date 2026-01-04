# MDB_ENGINE

**The Missing Engine for Your Python and MongoDB Projects.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

![](mdb_engine.png)

## The "Prototype Graveyard" Problem

If you are a builder, you know the feeling. You have a "digital garden" of scripts, tools, and prototypes. It's the data-entry tool for a friend, the internal dashboard, the AI chatbot.

Each one was a great idea. Each one lives in its own isolated folder. And each one, slowly, becomes a maintenance burden.

**Why? Because 70% of your time is spent on the "Scaffolding":**

* Writing the same MongoDB connection boilerplate.
* Manually creating indexes to prevent slow queries.
* Building another login page and JWT handler.
* Worrying about data leaks between your "dev" and "prod" logic.

**MDB_ENGINE** is the engine that solves this. It is a "WordPress-like" platform for the modern Python/MongoDB stack, designed to minimize the friction between an idea and a live application.

---

## How It Works

**MDB_ENGINE** acts as a hyper-intelligent proxy between your code and MongoDB. It handles the "boring" stuff so you can focus on the differentiation.

### 1. The Magic: Automatic Data Sandboxing 🛡️

The biggest pain point in multi-app (or even single-app) development is data isolation. MDB_ENGINE solves this via a **two-layer scoping system** that requires zero effort from you.

* **Layer 1 (Physical Scoping):** All collection access is prefixed. When your app writes to `db.users`, the engine actually writes to `db.my_app_users`.
* **Layer 2 (Logical Scoping):** All writes are automatically tagged with `{"app_id": "my_app"}`. All reads are automatically filtered by this ID.

```python
# YOU WRITE THIS (Clean, Naive Code):
await db.tasks.find({}).to_list(length=10)

# THE ENGINE EXECUTES THIS (Secure, Scoped Query):
# Collection: my_app_tasks
# Query: {"app_id": "my_app"}
```

### 2. Manifest-Driven "DNA" 🧬

Your application's configuration lives in a simple `manifest.json`. This is the "genome" of your project. It defines your indexes, authentication rules, and WebSocket endpoints declaratively.

### 3. Automatic Index Management ⚙️

Stop manually running `createIndex` in the Mongo shell. Define your indexes in your manifest, and MDB_ENGINE ensures they exist on startup.

```json
"managed_indexes": {
  "tasks": [
    {
      "type": "regular",
      "keys": {"status": 1, "created_at": -1},
      "name": "status_sort"
    }
  ]
}
```

---

## Quick Start

```bash
pip install mdb-engine

# Set required environment variable for JWT security
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
```

### 1. Define Your Manifest

Create `manifest.json` in your project root.

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My First App",
  "status": "active",
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": false,
      "allow_anonymous": true,
      "authorization": {
        "model": "rbac",
        "link_users_roles": true
      }
    }
  },
  "managed_indexes": {
    "tasks": [
      {
        "type": "regular",
        "keys": {"priority": -1},
        "name": "priority_idx"
      }
    ]
  }
}
```

### 2. Initialize the Engine

**Simplified Pattern (Recommended):**

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="my_database")

# One line - automatic initialization, manifest loading, and cleanup
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))

# 3. Use the Scoped Database
@app.post("/tasks")
async def create_task(task: dict):
    db = engine.get_scoped_db("my_app")
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

**Manual Pattern (More Control):**

```python
from pathlib import Path
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

app = FastAPI()
engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="my_database")

@app.on_event("startup")
async def startup():
    await engine.initialize()
    manifest_path = Path("manifest.json")
    manifest = await engine.load_manifest(manifest_path)
    await engine.register_app(manifest, create_indexes=True)

@app.on_event("shutdown")
async def shutdown():
    await engine.shutdown()

@app.post("/tasks")
async def create_task(task: dict):
    db = engine.get_scoped_db("my_app")
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
```

---

## When to Use What

### Choosing Your Integration Pattern

| Scenario | Recommended | Example |
|----------|-------------|---------|
| **New FastAPI app** | `engine.create_app()` | Automatic lifecycle, zero boilerplate |
| **Existing FastAPI app** | Manual `initialize()`/`shutdown()` | Keep your existing setup |
| **Script or CLI** | `async with engine` | Clean resource management |
| **Multiple isolated apps** | Multi-app with `read_scopes` | Secure cross-app data access |

### Feature Guide

| Feature | When to Use | Enable With |
|---------|------------|-------------|
| **Ray Support** | Distributed processing, heavy computation | `enable_ray=True` |
| **Multi-site Mode** | Multiple apps sharing data securely | `read_scopes` in manifest |
| **App Secrets** | Production deployment | Set `MDB_ENGINE_MASTER_KEY` |
| **Casbin Auth** | Simple RBAC (admin, user, guest roles) | `"provider": "casbin"` |
| **OSO Auth** | Complex policies (ownership, attributes) | `"provider": "oso"` |
| **Memory Service** | AI chatbots with persistent memory | `memory_config` in manifest |
| **Embeddings** | Vector search, RAG applications | `EmbeddingService` |
| **WebSockets** | Real-time updates | `websockets` in manifest |

### Quick Decision

```
Building a new FastAPI app?
├── YES → Use engine.create_app() - it handles everything
└── NO  → Use engine directly with async context manager
```

---

## Core Features Breakdown

### 🔐 Authentication & Authorization

Stop rewriting auth. The engine provides a unified authentication and authorization system with automatic Casbin provider setup.

**Unified Auth Stack:**
* **Auto-created Provider:** Casbin authorization provider is automatically created from manifest (default)
* **MongoDB-backed Policies:** Policies stored in MongoDB with zero configuration
* **App-Level User Management:** App-level users automatically get Casbin roles assigned
* **Zero Boilerplate:** Just configure in manifest, everything works automatically

**Manifest Configuration:**
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

**Usage:**
```python
from mdb_engine.auth import setup_auth_from_manifest, get_authz_provider, get_current_user

# Auto-setup (in startup)
await setup_auth_from_manifest(app, engine, "my_app")

# Use in routes
@app.get("/protected")
async def protected_route(
    user: dict = Depends(get_current_user),
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    has_access = await authz.check(
        subject=user.get("email"),
        resource="my_app",
        action="access"
    )
    return {"user_id": user["user_id"]}
```

**Extensibility:**
* **Custom Providers:** Implement `AuthorizationProvider` protocol for custom auth logic
* **OSO Support:** Use `"provider": "oso"` for OSO/Polar-based authorization
* **Custom Models:** Provide custom Casbin model files or use built-in RBAC/ACL
* **Manual Setup:** Override auto-creation by setting `app.state.authz_provider` manually

### 🛡️ Security Helpers (Optional)

MDB_ENGINE provides optional security helpers and decorators that you can opt-in to use. These are **not enforced** by default - the engine provides the building blocks, you choose which security features to enable.

**Optional Decorators:**

* **`@rate_limit_auth`** - Rate limiting for authentication endpoints
  ```python
  from mdb_engine.auth import rate_limit_auth

  @app.post("/login")
  @rate_limit_auth(max_attempts=5, window_seconds=300)
  async def login(credentials: dict):
      return await authenticate(credentials)
  ```

* **`@token_security`** - CSRF protection and HTTPS enforcement
  ```python
  from mdb_engine.auth import token_security

  @app.post("/sensitive")
  @token_security(enforce_https=True, check_csrf=True)
  async def sensitive_operation(user: dict = Depends(get_current_user)):
      return {"data": "sensitive"}
  ```

* **`@require_auth`** - Authentication requirement decorator
  ```python
  from mdb_engine.auth import require_auth

  @app.get("/dashboard")
  @require_auth(redirect_to="/login")
  async def dashboard(request: Request):
      user = request.state.user
      return {"user": user}
  ```

**Optional Middleware:**

* **`SecurityMiddleware`** - Security headers and CSRF token management
  ```python
  from mdb_engine.auth import SecurityMiddleware

  app.add_middleware(
      SecurityMiddleware,
      require_https=True,
      csrf_protection=True,
      security_headers=True
  )
  ```

These helpers are **opt-in** - use them when you need additional security layers beyond the core engine functionality.

### 📡 Built-in WebSockets

Real-time features usually require a lot of setup. MDB_ENGINE makes it configuration-based.

1. **Define:** Add `"websockets": {"realtime": {"path": "/ws"}}` to your manifest.
2. **Register:** WebSocket routes are automatically registered when you register your app with the engine.
3. **Broadcast:** `await broadcast_to_app("my_app", {"type": "update", "data": ...})`.

### 📊 Observability (The "Black Box" Recorder)

You shouldn't have to add logging manually to every function.

* **Contextual Logs:** Every log entry is automatically tagged with the active `app_id`.
* **Metrics:** Record operation durations and success rates automatically.
* **Health Checks:** Built-in endpoints to monitor DB connectivity.

---

## No Lock-In: The Graduation Path 🎓

MDB_ENGINE is an incubator, not a cage. Because all data is tagged with `app_id`, "graduating" an app to its own dedicated infrastructure is a simple database operation.

**To export your app:**

1. **Dump:** Use `mongodump` with a query filter:
```bash
mongodump --query='{"app_id":"my_app"}' --out=./export
```


2. **Restore:** Load it into a fresh MongoDB cluster.
3. **Code:** Your code is already standard PyMongo/Motor code. Just remove the `engine.get_scoped_db` wrapper and replace it with a standard `AsyncIOMotorClient`.

---

## Project Structure

```text
.
├── mdb_engine/              # Core engine package
│   ├── core/                # Manifest validation & registration
│   ├── database/            # ScopedMongoWrapper (The Proxy)
│   ├── auth/                # JWT & RBAC logic
│   ├── indexes/             # Auto-index management
│   ├── embeddings/          # EmbeddingService for semantic text splitting
│   ├── memory/              # Mem0MemoryService for intelligent memory
│   ├── routing/             # WebSocket routing and management
│   └── observability/       # Logging, metrics, and health checks
├── examples/                # Example applications
├── tests/                   # Test suite
├── docs/                    # Documentation
└── scripts/                 # Utility scripts
```

The project structure is shown above. Each module is documented in its respective README.md file.

---

## Examples

Check out the examples in the repository to see MDB_ENGINE in action:

- **[`chit_chat`](examples/chit_chat/)**: AI chat application with persistent memory using Mem0 — demonstrates authentication, WebSockets, and memory management
- **[`interactive_rag`](examples/interactive_rag/)**: Full RAG application — semantic search with vector indexes and embedding service
- **[`vector_hacking`](examples/vector_hacking/)**: Advanced LLM usage — vector inversion attacks with real-time updates
- **[`parallax`](examples/parallax/)**: Schema generation and management — demonstrates dynamic schema handling

Each example includes a complete `manifest.json`, Docker setup, and working code you can run immediately.

---

## Contributing

We welcome contributions! Please read our [Contributing Guide](CONTRIBUTING.md) for:

- Exception handling best practices (Staff Engineer level)
- Code style guidelines
- Makefile commands for development
- Code quality checks (`make lint`, `make format`)
- Testing requirements
- Pull request process

**Key Requirements:**
- All exception handling must follow our [best practices](CONTRIBUTING.md#exception-handling-best-practices)
- Code quality checks must pass (`make lint`) before submitting PRs
- Tests must be included for new features

**Quick Start for Contributors:**
```bash
# Install development dependencies
make install-dev

# Run quick quality check (lint + unit tests)
make check

# View all available commands
make help
```

**Quick Links:**
- [Contributing Guide](CONTRIBUTING.md)
- [Development Setup](SETUP.md)
- [Testing Guide](docs/guides/testing.md)
- [Documentation](docs/README.md)
- [Quick Start Guide](docs/QUICK_START.md)

---

**Stop building scaffolding. Start building features.**
