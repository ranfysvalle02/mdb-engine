# MDB_RUNTIME

**The Missing Engine for Your Python and MongoDB Projects.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## The "Prototype Graveyard" Problem

If you are a builder, you know the feeling. You have a "digital garden" of scripts, tools, and prototypes. It's the data-entry tool for a friend, the internal dashboard, the AI chatbot.

Each one was a great idea. Each one lives in its own isolated folder. And each one, slowly, becomes a maintenance burden.

**Why? Because 70% of your time is spent on the "Scaffolding":**

* Writing the same MongoDB connection boilerplate.
* Manually creating indexes to prevent slow queries.
* Building another login page and JWT handler.
* Worrying about data leaks between your "dev" and "prod" logic.

**MDB_RUNTIME** is the engine that solves this. It is a "WordPress-like" platform for the modern Python/MongoDB stack, designed to minimize the friction between an idea and a live application.

---

## How It Works

**MDB_RUNTIME** acts as a hyper-intelligent proxy between your code and MongoDB. It handles the "boring" stuff so you can focus on the differentiation.

### 1. The Magic: Automatic Data Sandboxing 🛡️

The biggest pain point in multi-app (or even single-app) development is data isolation. MDB_RUNTIME solves this via a **two-layer scoping system** that requires zero effort from you.

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

Stop manually running `createIndex` in the Mongo shell. Define your indexes in your manifest, and MDB_RUNTIME ensures they exist on startup.

```json
"managed_indexes": {
  "tasks": [
    { "keys": {"status": 1, "created_at": -1}, "name": "status_sort" }
  ]
}

```

---

## Quick Start

```bash
pip install mdb-runtime

```

### 1. Define Your Manifest

Create `manifest.json` in your project root.

```json
{
  "slug": "conversations",
  "name": "My Chat App",
  "auth_required": true,
  "managed_indexes": {
    "tasks": [{ "keys": {"priority": -1}, "name": "priority_idx" }]
  }
}

```

### 2. Initialize the Engine

In your `main.py` (FastAPI example):

```python
from fastapi import FastAPI, Depends
from mdb_runtime import RuntimeEngine
from mdb_runtime.database import AppDB

app = FastAPI()
engine = RuntimeEngine(mongo_uri="mongodb://localhost:27017", db_name="cluster0")

@app.on_event("startup")
async def startup():
    await engine.initialize()
    # Auto-discovers manifest, creates indexes, sets up auth
    await engine.register_app("manifest.json")

# 3. Use the Scoped Database
@app.post("/tasks")
async def create_task(task: dict):
    # This DB instance is physically and logically sandboxed to 'conversations'
    db = engine.get_scoped_db("conversations")
    
    # Auto-tagged with app_id; indexes auto-managed
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

```

---

## Core Features Breakdown

### 🔐 Authentication & Authorization

Stop rewriting auth. The engine provides built-in support for multiple strategies (JWT, Session) and Role-Based Access Control (RBAC).

* **Manifest Config:** Set `"auth_required": true` and define roles in JSON.
* **Runtime Check:** `await get_app_sub_user(request, "app_slug", db)` handles the validation.

### 📡 Built-in WebSockets

Real-time features usually require a lot of setup. MDB_RUNTIME makes it configuration-based.

1. **Define:** Add `"websockets": {"realtime": {"path": "/ws"}}` to your manifest.
2. **Register:** `engine.register_websocket_routes(app, "my_app")`.
3. **Broadcast:** `await broadcast_to_app("my_app", {"type": "update", "data": ...})`.

### 📊 Observability (The "Black Box" Recorder)

You shouldn't have to add logging manually to every function.

* **Contextual Logs:** Every log entry is automatically tagged with the active `app_id`.
* **Metrics:** Record operation durations and success rates automatically.
* **Health Checks:** Built-in endpoints to monitor DB connectivity.

---

## No Lock-In: The Graduation Path 🎓

MDB_RUNTIME is an incubator, not a cage. Because all data is tagged with `app_id`, "graduating" an app to its own dedicated infrastructure is a simple database operation.

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
└── mdb_runtime/             # The Engine
    ├── core/                # Manifest validation & registration
    ├── database/            # ScopedMongoWrapper (The Proxy)
    ├── auth/                # JWT & RBAC logic
    └── indexes/             # Auto-index management

```

