# mdb-engine

**The MongoDB Engine for Python Apps** — Auto-sandboxing, index management, and AI services in one package.

[![PyPI](https://img.shields.io/pypi/v/mdb-engine)](https://pypi.org/project/mdb-engine/)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Coverage](https://img.shields.io/badge/coverage-90%25-green)]()
[![Tests](https://img.shields.io/badge/statements-5023%252F5585-green)]()

## Get Running in 60 Seconds

### Step 1: Start MongoDB

You need a running MongoDB instance. Pick one:

```bash
# Local (Docker) — full Atlas features including Vector Search
docker run -d --name mongodb -p 27017:27017 mongodb/mongodb-atlas-local:latest
```

Or use [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) (free tier):

```bash
export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
```

### Step 2: Install and run

```bash
pip install mdb-engine fastapi uvicorn
```

Create `web.py`:

```python
from mdb_engine import quickstart
from mdb_engine.dependencies import get_scoped_db
from fastapi import Depends

app = quickstart("my_app")

@app.get("/items")
async def list_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(10)
```

```bash
uvicorn web:app --reload
```

Open [http://localhost:8000/items](http://localhost:8000/items) -- you're live.

> **Using AI features?** You'll also need an API key:
> `export OPENAI_API_KEY=sk-...` (or `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).
> See [Environment Variables](#environment-variables) for the full list.

**What you get automatically:**

- **Data isolation** — every query is scoped by `app_id`; you cannot accidentally leak data across apps
- **Collection prefixing** — `db.items` transparently becomes `my_app_items`
- **Lifecycle management** — engine startup/shutdown handled for you
- **Dependency injection** — `get_scoped_db`, `get_memory_service`, etc. ready to use

---

## Installation

```bash
pip install mdb-engine
```

---

## Feature Layers

mdb-engine is designed for progressive adoption. Start with Layer 0 and add features as you need them.

| Layer | What it gives you | How to enable |
|-------|-------------------|---------------|
| **0: Scoped DB + Indexes** | Auto-sandboxed collections, declarative indexes | `quickstart("slug")` or minimal manifest |
| **1: Auth + GDPR** | JWT, RBAC (Casbin/OSO), SSO, data export/deletion | Add `auth` section to manifest |
| **2: LLM + Embeddings + Memory** | Persistent AI memory, semantic search, fact extraction | Add `llm_config` + `memory_config` to manifest |
| **3: GraphRAG + ChatEngine** | Knowledge graphs, conversation orchestration with STM + LTM | Add `graph_config`, use `ChatEngine` |

---

## Three Ways to Create an App

### 1. Zero-config (quickstart)

No manifest file, no explicit connection string. Best for getting started.

```python
from mdb_engine import quickstart

app = quickstart("my_app")
```

### 2. Inline manifest (dict)

Pass configuration directly in Python. Good for programmatic setups.

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine()
app = engine.create_app(
    slug="my_app",
    manifest={
        "schema_version": "2.0",
        "slug": "my_app",
        "name": "My App",
        "managed_indexes": {
            "tasks": [{"type": "regular", "keys": {"status": 1}, "name": "status_idx"}]
        },
    },
)
```

### 3. File-based manifest (recommended for production)

The full-featured approach. A single `manifest.json` defines your app's identity, indexes, auth, AI services, and more.

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb+srv://...",  # or set MONGODB_URI env var
    db_name="production"
)
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
```

**Minimal manifest.json (3 fields):**

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App"
}
```

**Learn more**: [Manifest Reference](docs/MANIFEST_REFERENCE.md) | [Quick Start Guide](docs/QUICK_START.md)

---

## Examples Ladder

Start simple, add complexity when you need it.

| Example | What it shows | Lines of code |
|---------|---------------|:---:|
| [hello_world](examples/basic/hello_world/) | Zero-config CRUD, no manifest | ~15 |
| [memory_quickstart](examples/basic/memory_quickstart/) | AI memory with semantic search | ~25 |
| [chit_chat](examples/basic/chit_chat/) | Full AI chat with ChatEngine, auth, WebSockets | ~2400 |
| [interactive_rag](examples/basic/interactive_rag/) | RAG with vector search | — |
| [simple_app](examples/advanced/simple_app/) | Task management with `create_app()` pattern | — |
| [sso-multi-app](examples/advanced/sso-multi-app/) | SSO with shared user pool across apps | — |

---

## CRUD Operations (Auto-Scoped)

All database operations are automatically scoped to your app:

```python
from mdb_engine.dependencies import get_scoped_db

@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({"status": "pending"}).to_list(length=10)
```

**Under the hood:**

```python
# You write:
await db.tasks.find({}).to_list(length=10)

# Engine executes:
# Collection: my_app_tasks
# Query: {"app_id": "my_app"}
```

---

## AI Memory (MemoryService)

Add persistent, searchable AI memory to any app.

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/remember")
async def remember(text: str, memory=Depends(get_memory_service)):
    result = await memory.add(messages=text, user_id="user1")
    return {"stored": result}

@app.get("/recall")
async def recall(q: str, memory=Depends(get_memory_service)):
    results = await memory.search(query=q, user_id="user1")
    return {"results": results}
```

> **Note**: All memory operations are async. Use `await` directly in your routes.

Enable in your manifest:

```json
{
  "llm_config": {"enabled": true, "default_model": "openai/gpt-4o-mini"},
  "embedding_config": {"enabled": true, "default_embedding_model": "text-embedding-3-small"},
  "memory_config": {"enabled": true, "provider": "cognitive", "infer": true}
}
```

Optional cognitive features (add to `memory_config`):

- **Importance scoring**: AI evaluates memory significance
- **Memory reinforcement**: Similar memories strengthen each other
- **Memory decay**: Less relevant memories fade over time
- **Memory merging**: Related memories combined intelligently

---

## ChatEngine (Conversation Orchestration)

`ChatEngine` (formerly `CognitiveEngine`) orchestrates full conversations with short-term memory (chat history) + long-term memory (semantic search):

```python
from mdb_engine.memory import ChatEngine

cognitive = ChatEngine(
    app_slug="my_app",
    memory_service=memory_service,
    chat_history_collection=db.chat_history,
    llm_provider=llm_provider,
)

result = await cognitive.chat(
    user_id="user123",
    session_id="session456",
    user_query="What did we discuss about the project?",
    system_prompt="You are a helpful assistant.",
    extract_facts=True,
)
```

---

## RequestContext (All Services in One Place)

```python
from mdb_engine import RequestContext, get_request_context

@app.post("/ai-chat")
async def chat(query: str, ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()       # 401 if not logged in
    ctx.require_role("user")        # 403 if missing role

    # ctx.db, ctx.memory, ctx.llm, ctx.embedding_service — all available
    if ctx.llm:
        response = ctx.llm.chat.completions.create(
            model=ctx.llm_model,
            messages=[{"role": "user", "content": query}]
        )
        return {"response": response.choices[0].message.content}
```

---

## Index Management

Define indexes in `manifest.json` — they're auto-created on startup:

```json
{
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}, "name": "status_sort"},
      {"type": "regular", "keys": {"email": 1}, "name": "email_unique", "unique": true}
    ]
  }
}
```

Supported index types: `regular`, `text`, `vector`, `ttl`, `compound`.

---

## Advanced Features

| Feature | Description | Learn More |
|---------|-------------|------------|
| **Authentication** | JWT + Casbin/OSO RBAC | [Bible: Auth](MDB_ENGINE_BIBLE.md#authentication-and-authorization) |
| **Vector Search** | Atlas Vector Search + embeddings | [RAG Example](examples/basic/interactive_rag) |
| **MemoryService** | Persistent AI memory with cognitive features | [Memory Docs](docs/MEMORY_SERVICE.md) |
| **GraphService** | Knowledge graph with `$graphLookup` traversal | [Graph Docs](docs/GRAPH_SERVICE.md) |
| **ChatEngine** | Full RAG pipeline with STM + LTM | [Chat Example](examples/basic/chit_chat) |
| **WebSockets** | Real-time updates from manifest config | [Bible: WebSockets](MDB_ENGINE_BIBLE.md#websocket-system) |
| **Multi-App** | Secure cross-app data access | [SSO Example](examples/advanced/sso-multi-app) |
| **SSO** | Shared auth across apps | [SSO Example](examples/advanced/sso-multi-app) |
| **GDPR** | Data discovery, export, deletion, rectification | [Bible: GDPR](MDB_ENGINE_BIBLE.md#gdpr-compliance) |

---

## MongoDB Connection Reference

mdb-engine connects to `mongodb://localhost:27017` by default. Override via env var or constructor:

| Setup | Connection String |
|-------|-------------------|
| Local / Docker | `mongodb://localhost:27017` (default, no config needed) |
| Atlas (free tier) | `mongodb+srv://user:password@cluster.mongodb.net/dbname` |

```bash
# Option A: environment variable
export MONGODB_URI="mongodb+srv://user:password@cluster.mongodb.net/"

# Option B: in code
engine = MongoDBEngine(mongo_uri="mongodb+srv://...")
```

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | No | MongoDB connection string (default: `localhost:27017`) |
| `MDB_DB_NAME` | No | Database name (default: `mdb_engine`) |
| `OPENAI_API_KEY` | For AI features | OpenAI API key for LLM/embeddings |
| `ANTHROPIC_API_KEY` | For AI features | Anthropic API key (alternative to OpenAI) |
| `GEMINI_API_KEY` | For AI features | Google Gemini API key (alternative to OpenAI) |
| `MDB_JWT_SECRET` | For auth | JWT signing secret for shared auth mode |

---

## Why mdb-engine?

- **Zero to running in 3 lines** — `quickstart("my_app")` and go
- **Data isolation built in** — multi-tenant ready with automatic app sandboxing
- **manifest.json is everything** — single source of truth for your app config
- **Incremental adoption** — start minimal, add features as needed
- **No lock-in** — standard Motor/PyMongo underneath; export with `mongodump --query='{"app_id":"my_app"}'`

---

## Links

- [GitHub Repository](https://github.com/ranfysvalle02/mdb-engine)
- [Documentation](docs/)
- [All Examples](examples/)
- [Quick Start Guide](docs/QUICK_START.md) — **Start here!**
- [Manifest Reference](docs/MANIFEST_REFERENCE.md)
- [Contributing](CONTRIBUTING.md)

---

**Stop building scaffolding. Start building features.**
