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
# Collection: conversations
# Query: {"app_id": "conversations"}

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

In your `main.py` (FastAPI example):

```python
from pathlib import Path
from fastapi import FastAPI
from mdb_engine import MongoDBEngine

app = FastAPI()
engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="my_database")

@app.on_event("startup")
async def startup():
    await engine.initialize()
    
    # Load and register app from manifest
    manifest_path = Path("manifest.json")
    manifest = await engine.load_manifest(manifest_path)
    await engine.register_app(manifest, create_indexes=True)

# 3. Use the Scoped Database
@app.post("/tasks")
async def create_task(task: dict):
    # This DB instance is physically and logically sandboxed to 'my_app'
    db = engine.get_scoped_db("my_app")
    
    # Auto-tagged with app_id; indexes auto-managed
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}
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
mongodump --query='{"app_id":"conversations"}' --out=./export

```


2. **Restore:** Load it into a fresh MongoDB cluster.
3. **Code:** Your code is already standard PyMongo/Motor code. Just remove the `engine.get_scoped_db` wrapper and replace it with a standard `AsyncIOMotorClient`.

---

## Project Structure

```text
.
├── main.py                  # Your FastAPI entry point
├── manifest.json            # The DNA of your app
└── mdb_engine/             # The Engine
    ├── core/                # Manifest validation & registration
    ├── database/            # ScopedMongoWrapper (The Proxy)
    ├── auth/                # JWT & RBAC logic
    ├── indexes/             # Auto-index management
    ├── embeddings/          # EmbeddingService for semantic text splitting
    ├── memory/              # Mem0MemoryService for intelligent memory
    ├── routing/             # WebSocket routing and management
    └── observability/       # Logging, metrics, and health checks
```

---

## Examples

Check out the examples in the repository to see MDB_ENGINE in action:

- **[`chit_chat`](examples/chit_chat/)**: AI chat application with persistent memory using Mem0 — demonstrates authentication, WebSockets, and memory management
- **[`interactive_rag`](examples/interactive_rag/)**: Full RAG application — semantic search with vector indexes and embedding service
- **[`vector_hacking`](examples/vector_hacking/)**: Advanced LLM usage — vector inversion attacks with real-time updates
- **[`parallax`](examples/parallax/)**: Schema generation and management — demonstrates dynamic schema handling

Each example includes a complete `manifest.json`, Docker setup, and working code you can run immediately.

---

**Stop building scaffolding. Start building features.**

