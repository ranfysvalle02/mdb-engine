---
name: mdb-engine-guide
description: Comprehensive development guide for the mdb-engine framework (FastAPI + Motor + MongoDB). Use when writing routes, models, database operations, authentication, memory services, manifests, or any code inside an mdb-engine project. Enforces framework abstractions and prevents raw boilerplate.
---

# MDB-Engine Development Guide

## 1. Project Overview

**mdb-engine** is a batteries-included MongoDB runtime for Python. It wraps Motor (async MongoDB driver) with automatic data isolation, manifest-driven configuration, and optional AI services.

**Core dependencies:** FastAPI, Motor, Pydantic V2, PyJWT, bcrypt, cryptography.
**Python:** >=3.10 | **Version:** 0.8.4 | **License:** MIT

**Golden rule:** Use the framework's abstractions. Do NOT write raw Motor/pymongo boilerplate, manual JWT parsing, or unscoped database access.

---

## 2. Creating an App (Three Tiers)

### Tier 1 — Zero-config (`quickstart`)

```python
from mdb_engine import quickstart
from mdb_engine.dependencies import get_scoped_db
from fastapi import Depends

app = quickstart("my_app")

@app.get("/items")
async def list_items(db=Depends(get_scoped_db)):
    return await db.items.find({}).to_list(10)
```

Reads `MONGODB_URI` / `MDB_MONGO_URI` env var (falls back to `localhost:27017`).

### Tier 2 — Manifest-based (`create_app`)

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="mydb")
app = engine.create_app(slug="my_app", manifest=Path("manifest.json"))
```

### Tier 3 — Multi-app (`create_multi_app`)

```python
app = engine.create_multi_app(
    apps=[
        {"slug": "app1", "manifest": Path("app1/manifest.json"), "path_prefix": "/app1"},
        {"slug": "app2", "manifest": Path("app2/manifest.json"), "path_prefix": "/app2"},
    ],
    title="My Platform",
)
```

---

## 3. Database Access — Two API Styles

### Style A: Scoped Wrapper (Motor-like API)

Use `get_scoped_db` for familiar MongoDB operations. All queries are automatically filtered by `app_id`.

```python
from mdb_engine.dependencies import get_scoped_db

@app.post("/tasks")
async def create_task(task: dict, db=Depends(get_scoped_db)):
    result = await db.tasks.insert_one(task)
    return {"id": str(result.inserted_id)}

@app.get("/tasks")
async def list_tasks(db=Depends(get_scoped_db)):
    return await db.tasks.find({"status": "active"}).to_list(50)

@app.put("/tasks/{task_id}")
async def update_task(task_id: str, data: dict, db=Depends(get_scoped_db)):
    await db.tasks.update_one({"_id": task_id}, {"$set": data})
    return {"ok": True}

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, db=Depends(get_scoped_db)):
    await db.tasks.delete_one({"_id": task_id})
    return {"ok": True}
```

**Available methods** on `db.<collection>`: `find_one`, `find`, `insert_one`, `insert_many`, `update_one`, `update_many`, `delete_one`, `delete_many`, `count_documents`, `aggregate`.

### Style B: Repository Pattern (Entity-based)

Use `Entity` + `UnitOfWork` for type-safe, domain-driven data access.

**Step 1 — Define your entity:**

```python
from dataclasses import dataclass
from mdb_engine.repositories import Entity

@dataclass
class Task(Entity):
    title: str = ""
    status: str = "pending"
    owner_id: str = ""
```

`Entity` provides `id`, `created_at`, `updated_at` automatically. The `to_dict()` / `from_dict()` methods handle `_id` <-> `id` mapping.

**Step 2 — Use via UnitOfWork:**

```python
from mdb_engine.dependencies import get_unit_of_work

@app.get("/tasks/{task_id}")
async def get_task(task_id: str, uow=Depends(get_unit_of_work)):
    task = await uow.tasks.get(task_id)         # Returns Task | None
    if not task:
        raise HTTPException(404)
    return task

@app.get("/tasks")
async def list_tasks(uow=Depends(get_unit_of_work)):
    return await uow.tasks.find({"status": "pending"}, limit=50)

@app.post("/tasks")
async def create_task(uow=Depends(get_unit_of_work)):
    task = Task(title="New task", owner_id="user123")
    task_id = await uow.tasks.add(task)          # Returns str (ID)
    return {"id": task_id}
```

**Repository methods:** `get(id)`, `find(filter, skip, limit, sort)`, `find_one(filter)`, `add(entity)`, `add_many(entities)`, `update(id, entity)`, `update_fields(id, fields)`, `delete(id)`, `count(filter)`, `exists(id)`, `aggregate(pipeline)`.

**Register entity classes** for type-safe repos:

```python
uow = UnitOfWork(db)
uow.register_entity("tasks", Task)
```

Or create a typed UoW:

```python
from mdb_engine.repositories import UnitOfWork, Repository

class AppUnitOfWork(UnitOfWork):
    @property
    def tasks(self) -> Repository[Task]:
        return self.repository("tasks", Task)
```

---

## 4. RequestContext — All-in-One Dependency

`RequestContext` provides lazy-loaded access to every service. Prefer this over importing individual dependencies.

```python
from mdb_engine.dependencies import RequestContext, get_request_context

@app.post("/documents")
async def create_doc(data: dict, ctx: RequestContext = Depends(get_request_context)):
    # Database (must await first access)
    uow = await ctx.get_uow()
    doc_id = await uow.documents.add(Document(title=data["title"]))

    # Or use scoped db directly
    db = await ctx.get_db()
    await db.audit_log.insert_one({"action": "create", "doc_id": doc_id})

    # Auth
    user = ctx.require_user()          # Raises 401 if not authenticated
    ctx.require_role("editor")         # Raises 403 if missing role

    # AI services (None if not configured)
    if ctx.memory:
        await ctx.memory.add(messages=data["content"], user_id=user["_id"])

    return {"id": doc_id}
```

**Properties:** `engine`, `slug`, `config`, `user`, `user_roles`, `authz`, `memory`, `profile`, `embedding_service`, `llm_service`, `llm` (legacy), `llm_model`.
**Async getters:** `get_db()`, `get_uow()`.
**Auth helpers:** `require_user()`, `require_role(*roles)`, `check_permission(resource, action)`.

---

## 5. Authentication & Authorization

### Reading the Current User

Auth is handled via middleware that populates `request.state.user`. Use dependencies — never parse headers manually.

```python
from mdb_engine.dependencies import get_current_user, require_user, require_role

# Optional auth (returns None if not logged in)
@app.get("/public")
async def public_route(user=Depends(get_current_user)):
    return {"user": user.get("email") if user else "anonymous"}

# Required auth (raises 401)
@app.get("/dashboard")
async def dashboard(user=Depends(require_user())):
    return {"email": user["email"]}

# Role-gated (raises 403)
@app.get("/admin")
async def admin_panel(user=Depends(require_role("admin"))):
    return {"msg": f"Welcome admin {user['email']}"}
```

### Authorization Providers

Pluggable via manifest (`auth.policy.provider`): Casbin or OSO Cloud.

```python
from mdb_engine.dependencies import get_authz_provider

@app.delete("/items/{item_id}")
async def delete_item(item_id: str, authz=Depends(get_authz_provider), user=Depends(require_user())):
    allowed = await authz.check(user["email"], "items", "delete")
    if not allowed:
        raise HTTPException(403, "Not authorized to delete items")
    ...
```

### SSO Multi-App (SharedUserPool)

For multi-app deployments with SSO, use `SharedUserPool`. Users exist in a single shared collection with per-app roles.

```python
from mdb_engine.auth import SharedUserPool

pool = SharedUserPool(db=engine._db, jwt_secret="secret")
token = await pool.authenticate(email, password, app_slug="my_app")
user = await pool.validate_token(token)
```

---

## 6. Manifest Configuration

The manifest is the declarative contract for each app. Minimal manifest:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application"
}
```

Full manifest with common options:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My Application",
  "status": "active",
  "data_access": {
    "read_scopes": ["my_app"],
    "write_scope": "my_app"
  },
  "managed_indexes": {
    "tasks": [
      {"type": "regular", "keys": {"status": 1, "created_at": -1}, "name": "status_sort"}
    ]
  },
  "auth": {
    "mode": "app",
    "policy": {"provider": "casbin", "required": true},
    "users": {"enabled": true, "allow_registration": true}
  },
  "memory_config": true,
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"]
  },
  "websockets": {
    "chat": {"path": "/ws", "auth": {"required": true}}
  }
}
```

---

## 6.5. Zero-Code Collections (MQL-as-DSL)

Define collections in the manifest with `auto_crud: true` to generate a full REST API with no Python code. Run with `mdb-engine serve manifest.json`.

**Golden rule:** MQL is the DSL. Every `policy`, `scopes`, `pipelines`, and `defaults` value is a native MongoDB Query Language expression. The manifest speaks the same language as the database.

### Collection Config Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `auto_crud` | boolean | `true` | Generate REST endpoints |
| `schema` | object | — | JSON Schema for document validation |
| `read_only` | boolean | `false` | GET endpoints only |
| `timestamps` | boolean | `true` | Auto-inject `created_at` / `updated_at` |
| `soft_delete` | boolean | `false` | Soft delete with trash/restore |
| `bulk_insert` | boolean | `true` | Enable `POST /_bulk` |
| `auth` | object | — | Per-collection auth (`required`, `roles`) |
| `realtime` | boolean | `false` | Change Stream WebSocket events |
| `policy` | object | — | Document-level access policies (MQL filters) |
| `scopes` | object | — | Named MQL filters activated via `?scope=` |
| `pipelines` | object | — | Named aggregation endpoints |
| `defaults` | object | — | Default field values on create |
| `default_projection` | object | — | Default MongoDB projection for reads |

### Template Placeholders

Policy, scopes, pipelines, and defaults support `{{user.*}}` placeholders resolved at runtime from the authenticated user:

- `"{{user._id}}"` — the user's `_id`
- `"{{user.team_id}}"` — any top-level user field
- `"{{user.profile.org}}"` — nested paths (max 3 levels)
- `"$$NOW"` — current UTC datetime

### Policy — Document-Level Access Control

```json
{
  "policy": {
    "read":   { "team_id": "{{user.team_id}}" },
    "write":  { "owner_id": "{{user._id}}" },
    "delete": { "owner_id": "{{user._id}}" }
  }
}
```

- `read` — merged into every list/get/count query
- `write` — merged into update/replace lookups
- `delete` — merged into delete lookups (falls back to `write` if omitted)

If a `{{user.*}}` placeholder is used and no user is authenticated, the endpoint returns 401.

### Scopes — Named Query Shortcuts

```json
{
  "scopes": {
    "active":  { "status": { "$ne": "archived" } },
    "overdue": { "due_date": { "$lt": "$$NOW" }, "status": { "$ne": "done" } },
    "mine":    { "owner_id": "{{user._id}}" }
  }
}
```

Clients activate scopes via the `?scope=` query parameter:

```
GET /api/tasks?scope=active
GET /api/tasks?scope=active,mine
GET /api/tasks?scope=active&assignee=alice
GET /api/tasks/_count?scope=active
```

Multiple scopes are `$and`-merged. Unknown scope names return 400.

### Pipelines — Aggregation Endpoints

```json
{
  "pipelines": {
    "by_status": [
      { "$group": { "_id": "$status", "count": { "$sum": 1 } } },
      { "$sort": { "count": -1 } }
    ],
    "with_assignee": [
      { "$lookup": { "from": "users", "localField": "assignee_id", "foreignField": "_id", "as": "assignee" } },
      { "$unwind": { "path": "$assignee", "preserveNullAndEmptyArrays": true } }
    ]
  }
}
```

Each pipeline becomes `GET /api/{collection}/_agg/{name}`. The `app_id` `$match` stage is prepended automatically by `ScopedCollectionWrapper`.

### Defaults — Auto-Populated Fields

```json
{
  "defaults": {
    "status": "pending",
    "priority": 3,
    "owner_id": "{{user._id}}"
  }
}
```

Applied to new documents via `setdefault` — caller-provided values always take precedence.

### Default Projection — Hide Internal Fields

```json
{
  "default_projection": { "internal_notes": 0, "audit_trail": 0 }
}
```

Applied to list and get queries when the client does not specify `?fields=`. The `?fields=` parameter overrides the default projection.

### Complete Example

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",
  "collections": {
    "tasks": {
      "auto_crud": true,
      "soft_delete": true,
      "schema": {
        "type": "object",
        "properties": {
          "title": { "type": "string" },
          "status": { "type": "string", "enum": ["pending", "in_progress", "done"] }
        },
        "required": ["title"]
      },
      "auth": { "required": true },
      "policy": {
        "read":  { "team_id": "{{user.team_id}}" },
        "write": { "owner_id": "{{user._id}}" }
      },
      "defaults": {
        "status": "pending",
        "owner_id": "{{user._id}}",
        "team_id": "{{user.team_id}}"
      },
      "scopes": {
        "active": { "status": { "$ne": "done" } },
        "mine":   { "owner_id": "{{user._id}}" }
      },
      "pipelines": {
        "by_status": [
          { "$group": { "_id": "$status", "count": { "$sum": 1 } } }
        ]
      },
      "default_projection": { "internal_notes": 0 },
      "realtime": true
    }
  }
}
```

### Generated Endpoints

For a collection named `tasks`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks` | List (filter, sort, paginate, scope, project) |
| GET | `/api/tasks/_count` | Count with filter/scope support |
| GET | `/api/tasks/_trash` | List soft-deleted (if `soft_delete`) |
| GET | `/api/tasks/_agg/{name}` | Run named pipeline |
| GET | `/api/tasks/{id}` | Get by ID |
| POST | `/api/tasks` | Create (with defaults + validation) |
| POST | `/api/tasks/_bulk` | Bulk create (if `bulk_insert`) |
| PUT | `/api/tasks/{id}` | Full replace |
| PATCH | `/api/tasks/{id}` | Partial update |
| DELETE | `/api/tasks/{id}` | Delete or soft-delete |
| POST | `/api/tasks/{id}/_restore` | Restore (if `soft_delete`) |

---

## 7. Memory Service (AI)

Requires `pip install mdb-engine[ai]`. Enable in manifest via `memory_config.enabled: true`.

```python
from mdb_engine.dependencies import get_memory_service

@app.post("/remember")
async def remember(text: str, memory=Depends(get_memory_service)):
    # Add with LLM-powered fact extraction
    results = await memory.add(messages=text, user_id="user1")
    return results

@app.post("/inject")
async def inject_fact(fact: str, memory=Depends(get_memory_service)):
    # Inject directly without LLM extraction
    result = await memory.inject(memory=fact, user_id="user1")
    return result

@app.get("/recall")
async def recall(q: str, memory=Depends(get_memory_service)):
    # Semantic search via Atlas Vector Search
    return await memory.search(query=q, user_id="user1", limit=5)

@app.get("/memories")
async def all_memories(memory=Depends(get_memory_service)):
    return await memory.get_all(user_id="user1")
```

**MemoryService methods:** `add()`, `inject()`, `search()`, `get_all()`, `get()`, `update()`, `delete()`.

### ChatEngine (Orchestrator)

Combines short-term chat history + long-term memory + LLM:

```python
from mdb_engine.memory import ChatEngine
```

### Pluggable Strategies

Override scoring, decay, extraction, importance, persona, or reflection by passing strategy instances to `get_memory_service()`.

### Accessing Embedding and LLM Services

The engine caches the `EmbeddingService` and `LLMService` that were created during memory service initialization. Use these instead of creating your own:

```python
from mdb_engine.dependencies import get_embedding_service, get_llm_service

@app.post("/embed")
async def embed(text: str, embedding_svc=Depends(get_embedding_service)):
    return await embedding_svc.embed([text])

@app.post("/generate")
async def generate(prompt: str, llm=Depends(get_llm_service)):
    return await llm.chat_completion(messages=[{"role": "user", "content": prompt}])
```

Or outside of routes: `engine.get_embedding_service(slug)`, `engine.get_llm_service(slug)`.

### Perfect Brain (Advanced Components)

Enable inside `memory_config` for a unified container of SharedMemory, MemoryVeto, Consolidator, etc.:

```json
{
  "memory_config": {
    "preset": "full",
    "perfect_brain": {
      "enabled": true,
      "memory_veto": true,
      "shared_memory": true,
      "timeline_service": true,
      "consolidator": { "enabled": true, "interval_hours": 6 }
    }
  }
}
```

```python
from mdb_engine.dependencies import get_perfect_brain

@app.get("/vetoes")
async def vetoes(user_id: str, brain=Depends(get_perfect_brain)):
    return await brain.memory_veto.get_user_vetoes(user_id=user_id)
```

### Important: `app_id` Auto-Injection in Vector Search Indexes

When the engine creates vector search indexes (via `managed_indexes` or `_ensure_memory_vector_index`), it **automatically adds `app_id` as a filter field**. Do NOT add `app_id` manually to your index definitions — doing so creates duplicates and can cause Atlas errors.

---

## 8. Dependency Injection Container

```python
from mdb_engine.di import Container, Scope
from mdb_engine.dependencies import inject

# Register
container = Container()
container.register(MyService, scope=Scope.SINGLETON)
container.register_factory(Database, lambda c: Database(c.resolve(Config).url))
container.register_instance(Config, my_config)

# Resolve in routes
@app.get("/")
async def index(svc: MyService = Depends(inject(MyService))):
    return svc.do_work()
```

**Scopes:** `SINGLETON` (one per app), `REQUEST` (one per HTTP request), `TRANSIENT` (new each time).

---

## 9. Framework Base Template (`mdb_base.html`)

mdb-engine ships a Jinja2 base template that all app templates should extend. It guarantees correct script ordering so `BASE`, `MDB`, and `getCookie` are always defined before child scripts run.

### Block hierarchy (render order)

| Block | Purpose | MDB available? |
|-------|---------|---------------|
| `title` | Page `<title>` | N/A (server-side) |
| `head` | `<head>` content (CSS, meta, fonts) | N/A |
| `body` → `content` | Visual structure + page content | NO (scripts here must not use MDB) |
| `base_js` | App-level base scripts (logout, nav) | YES |
| `extra_js` | Page-level scripts | YES |

### JavaScript globals (in `base_js` / `extra_js`)

| Global | Description |
|--------|-------------|
| `MDB.BASE` | App mount path (e.g. `"/ai-chat"`) |
| `MDB.AUTH_HUB` | Auth hub URL (e.g. `"/auth-hub"`) |
| `MDB.APP_SLUG` | App slug |
| `MDB.csrfToken()` | Current CSRF token |
| `getCookie(name)` | Read any cookie |
| `BASE` | Alias for `MDB.BASE` (backwards-compatible) |

### App base template pattern

```html
{% extends "mdb_base.html" %}

{% block head %}
<style>/* app styles */</style>
{% block extra_css %}{% endblock %}
{% endblock %}

{% block body %}
<header>...</header>
<main>{% block content %}{% endblock %}</main>
{% endblock %}

{% block base_js %}
<script>
async function logout() {
    await fetch(MDB.BASE + '/logout', {
        method: 'POST',
        headers: { 'X-CSRF-Token': MDB.csrfToken() }
    });
    window.location.href = MDB.AUTH_HUB + '/login';
}
</script>
{% endblock %}
```

### Page template pattern

```html
{% extends "base.html" %}
{% block content %}<div>Page HTML</div>{% endblock %}
{% block extra_js %}
<script>
// MDB.BASE, getCookie, BASE all available here
const res = await fetch(BASE + '/api/data');
</script>
{% endblock %}
```

### Rules

- **NEVER** put `<script>` tags that use `BASE`/`MDB` inside `{% block content %}`. They run before `MDB` is defined.
- **ALWAYS** put scripts in `{% block extra_js %}` (page-level) or `{% block base_js %}` (app-level).
- The engine auto-registers the framework templates directory in child Jinja2 loaders, so `{% extends "mdb_base.html" %}` works with no configuration.

---

## 10. File & Module Conventions

```
my_project/
├── manifest.json              # App configuration
├── web.py                     # FastAPI app (entry point)
├── templates/                 # Jinja2 templates
│   ├── base.html              # App base (extends mdb_base.html)
│   └── index.html             # Pages (extend base.html)
├── models/                    # Entity dataclasses
│   └── task.py
├── routes/                    # APIRouter modules
│   ├── tasks.py
│   └── users.py
└── services/                  # Business logic
    └── task_service.py
```

- **Templates:** App `base.html` extends `mdb_base.html`. Pages extend app `base.html`. Scripts go in `{% block extra_js %}`.
- **Routes:** Use `APIRouter` and include in the app.
- **Models:** Use `@dataclass` with `Entity` base for repositories, or plain Pydantic `BaseModel` for request/response schemas.
- **Services:** Business logic classes registered in DI or created per-request.

---

## 11. Creating a New Feature (Golden Path)

When asked to create a CRUD feature, follow this exact pattern.

### 1. Define the Entity (`models/invoice.py`)

```python
from dataclasses import dataclass
from mdb_engine.repositories import Entity

@dataclass
class Invoice(Entity):
    customer_id: str = ""
    amount: float = 0.0
    status: str = "draft"
    line_items: list = None

    def __post_init__(self):
        if self.line_items is None:
            self.line_items = []
```

### 2. Define Request/Response Schemas (optional, for validation)

```python
from pydantic import BaseModel

class InvoiceCreate(BaseModel):
    customer_id: str
    amount: float
    line_items: list[dict] = []

class InvoiceUpdate(BaseModel):
    amount: float | None = None
    status: str | None = None
```

### 3. Create the Routes (`routes/invoices.py`)

```python
from fastapi import APIRouter, Depends, HTTPException
from mdb_engine.dependencies import RequestContext, get_request_context
from models.invoice import Invoice
from schemas import InvoiceCreate, InvoiceUpdate

router = APIRouter(prefix="/invoices", tags=["invoices"])

@router.get("/")
async def list_invoices(ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()
    uow = await ctx.get_uow()
    uow.register_entity("invoices", Invoice)
    return await uow.invoices.find({"customer_id": user["_id"]}, limit=50)

@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, ctx: RequestContext = Depends(get_request_context)):
    ctx.require_user()
    uow = await ctx.get_uow()
    uow.register_entity("invoices", Invoice)
    invoice = await uow.invoices.get(invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return invoice

@router.post("/", status_code=201)
async def create_invoice(data: InvoiceCreate, ctx: RequestContext = Depends(get_request_context)):
    user = ctx.require_user()
    uow = await ctx.get_uow()
    uow.register_entity("invoices", Invoice)
    invoice = Invoice(
        customer_id=user["_id"],
        amount=data.amount,
        line_items=data.line_items,
    )
    invoice_id = await uow.invoices.add(invoice)
    return {"id": invoice_id}

@router.patch("/{invoice_id}")
async def update_invoice(invoice_id: str, data: InvoiceUpdate, ctx: RequestContext = Depends(get_request_context)):
    ctx.require_user()
    uow = await ctx.get_uow()
    uow.register_entity("invoices", Invoice)
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    updated = await uow.invoices.update_fields(invoice_id, fields)
    if not updated:
        raise HTTPException(404, "Invoice not found")
    return {"ok": True}

@router.delete("/{invoice_id}")
async def delete_invoice(invoice_id: str, ctx: RequestContext = Depends(get_request_context)):
    ctx.require_user()
    uow = await ctx.get_uow()
    uow.register_entity("invoices", Invoice)
    deleted = await uow.invoices.delete(invoice_id)
    if not deleted:
        raise HTTPException(404, "Invoice not found")
    return {"ok": True}
```

### 4. Register the Router

```python
from routes.invoices import router as invoices_router
app.include_router(invoices_router)
```

---

## 12. Rules & Anti-Patterns

### DO NOT

1. **Use synchronous DB calls.** Never use `pymongo` directly. Always `await` Motor operations.
2. **Bypass scoping.** Never access `engine._db` or raw Motor collections directly in route handlers. Always go through `get_scoped_db` or `UnitOfWork`.
3. **Parse JWT manually.** Use `get_current_user`, `require_user()`, or `ctx.user`.
4. **Convert ObjectId manually.** The framework handles `_id` <-> `id` mapping in `Entity.to_dict()` / `from_dict()`. Use `str()` only when needed for API responses.
5. **Write complex aggregations inline.** Move pipelines to a method on the Entity or a service class.
6. **Use `dict()` on Pydantic models.** Use `model_dump()` (Pydantic V2).
7. **Create raw `MongoRepository` in routes.** Use `UnitOfWork` which creates and caches repositories.
8. **Hardcode `app_id` filters.** `ScopedCollectionWrapper` injects these automatically.

### DO

1. **Type-hint** all function arguments and return values.
2. **Use `RequestContext`** for routes needing multiple services (db + auth + memory).
3. **Use individual dependencies** (`get_scoped_db`, `get_current_user`) for simple routes.
4. **Use `Entity` dataclasses** for domain models, `BaseModel` for request/response schemas.
5. **Keep routes thin.** Business logic belongs in service classes.
6. **Use `Depends()`** for all service injection.
7. **Handle errors** with `HTTPException` in routes, `MongoDBEngineError` subclasses in services.

---

## 13. Environment Variables

Canonical names use the `MDB_` prefix. Legacy names still work but emit a deprecation warning.

| Canonical | Deprecated aliases | Purpose | Default |
|-----------|-------------------|---------|---------|
| `MDB_MONGO_URI` | `MONGODB_URI`, `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MDB_DB_NAME` | `MONGODB_DB`, `MONGO_DB_NAME`, `DB_NAME` | Database name | `mdb_engine` |
| `MDB_JWT_SECRET` | `MDB_ENGINE_JWT_SECRET`, `SECRET_KEY`, `FLASK_SECRET_KEY` | JWT signing secret | — |
| `OPENAI_API_KEY` | — | OpenAI API key (for memory/LLM) | — |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI key | — |
| `AZURE_OPENAI_ENDPOINT` | — | Azure OpenAI endpoint | — |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | — | Azure deployment name | `gpt-4o` |
| `OPENAI_MODEL` | — | OpenAI model name | `gpt-4o` |

---

## 14. Key Imports Cheat Sheet

```python
# App creation
from mdb_engine import quickstart, MongoDBEngine

# Dependencies (use in Depends())
from mdb_engine.dependencies import (
    get_scoped_db,          # ScopedMongoWrapper
    get_unit_of_work,       # UnitOfWork
    get_request_context,    # RequestContext (all-in-one)
    get_current_user,       # dict | None
    get_memory_service,     # BaseMemoryService
    get_graph_service,          # GraphService (raises 503 if missing)
    get_graph_service_optional, # GraphService | None (no 503)
    get_embedding_service,  # EmbeddingService (shared singleton)
    get_llm_service,        # LLMService (shared singleton)
    get_perfect_brain,      # PerfectBrain container
    get_profile_service,    # ProfileService
    get_llm_client,         # OpenAI | AzureOpenAI (legacy)
    get_authz_provider,     # AuthorizationProvider | None
    get_platform_info,      # PlatformInfo (multi-app nav)
    get_app_logger,         # Logger scoped to app slug
    require_user,           # Factory -> raises 401
    require_role,           # Factory -> raises 403
    inject,                 # DI container resolution
)
from mdb_engine.dependencies import RequestContext, PlatformInfo

# Repository pattern
from mdb_engine.repositories import Entity, Repository, MongoRepository, UnitOfWork

# Database layer
from mdb_engine.database import ScopedMongoWrapper, AppDB

# Memory & AI
from mdb_engine.memory import MemoryService, ChatEngine

# DI
from mdb_engine.di import Container, Scope

# Auth
from mdb_engine.auth import SharedUserPool, AuthorizationProvider
from mdb_engine.auth import validate_jwt_token_format, get_cookie_settings

# Testing
from mdb_engine.testing import create_test_client, mock_scoped_db, mock_user

# Actions
from mdb_engine.actions import ActionContext, ActionResponse
from mdb_engine.actions.discovery import mount_actions, discover_actions

# Background tasks
from mdb_engine.tasks import recurring_task

# WebSocket utilities
from mdb_engine.routing.websockets import authenticated_websocket, RoomManager

# Errors
from mdb_engine.exceptions import (
    MongoDBEngineError,
    InitializationError,
    ManifestValidationError,
    ConfigurationError,
    QueryValidationError,
)

# Utilities
from mdb_engine.utils import clean_mongo_doc, clean_mongo_docs
```

---

## 15. Actions (Manifest-Driven Handlers)

Actions are single-file Python handlers in an `actions/` directory next to the manifest. Each file exports an async `handler(ctx)` function. Three trigger types are supported: **HTTP**, **schedule**, and **event**.

### Directory Convention

```
my_app/
├── manifest.json
├── web.py                     # Custom routes (optional)
├── actions/
│   ├── send-email.py          # HTTP trigger (POST /actions/v1/send-email)
│   ├── cleanup-sessions.py    # Scheduled trigger
│   └── on-user-signup.py      # Event trigger (after_create on users)
```

Files prefixed with `_` are skipped. Each `.py` file must export an `async def handler(ctx: ActionContext)`.

### HTTP Action Example

```python
# actions/send-email.py
from mdb_engine.actions import ActionContext

async def handler(ctx: ActionContext):
    user = ctx.require_user()
    body = await ctx.json()
    db = await ctx.get_db()
    await db.email_queue.insert_one({"to": body["email"], "user_id": user["_id"]})
    return ctx.json_response({"queued": True})
```

Mounted at `POST /actions/v1/send-email` automatically.

### Scheduled Action Example

```python
# actions/cleanup-sessions.py
from mdb_engine.actions import ActionContext

__trigger__ = "schedule"
__interval_seconds__ = 3600  # every hour

async def handler(ctx: ActionContext):
    db = await ctx.get_db()
    from datetime import datetime, timezone
    await db.sessions.delete_many({"expires_at": {"$lt": datetime.now(timezone.utc)}})
```

### Event Action Example

```python
# actions/on-user-signup.py
from mdb_engine.actions import ActionContext

__trigger__ = "event"
__event__ = "after_create"
__collection__ = "users"

async def handler(ctx: ActionContext):
    doc = ctx.event_doc  # the created document
    db = await ctx.get_db()
    await db.welcome_emails.insert_one({"user_id": doc["_id"]})
```

Event actions are injected as hooks into the target collection's auto-CRUD pipeline.

### Module-Level Metadata

Actions can declare metadata via module-level constants (manifest config overrides these):

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `__trigger__` | str | `"http"` | `"http"`, `"schedule"`, or `"event"` |
| `__method__` | str | `"POST"` | HTTP method (http triggers) |
| `__timeout__` | int | `10` | Max execution seconds (1–300) |
| `__schedule__` | str | `""` | Cron expression (schedule triggers, requires `croniter`) |
| `__interval_seconds__` | float | `0` | Simple interval (schedule triggers) |
| `__event__` | str | `""` | `"after_create"`, `"after_update"`, `"after_delete"` |
| `__collection__` | str | `""` | Target collection (event triggers) |
| `__auth__` | dict | `{}` | `{"required": True, "roles": ["admin"]}` |

### Manifest Configuration

```json
{
  "actions": {
    "send-email": {
      "trigger": "http",
      "method": "POST",
      "auth": { "required": true, "roles": ["editor"] },
      "timeout": 30
    },
    "cleanup-sessions": {
      "trigger": "schedule",
      "interval_seconds": 3600
    },
    "on-user-signup": {
      "trigger": "event",
      "event": "after_create",
      "collection": "users"
    }
  }
}
```

Manifest config always overrides module-level metadata.

### ActionContext API

`ActionContext` provides access to all engine services:

**Request helpers** (HTTP triggers only):
- `await ctx.json()` — parse request body as JSON
- `await ctx.text()` — read request body as text
- `ctx.method` — HTTP method string
- `ctx.headers` — request headers dict
- `ctx.query_params` — query parameters dict

**Event helpers** (event triggers only):
- `ctx.event_doc` — the document that triggered the event
- `ctx.event_prev` — previous document state (updates only)
- `ctx.event_name` — event name (`"after_create"`, etc.)

**Auth:**
- `ctx.user` — current user dict or `None`
- `ctx.require_user()` — raises 401 if not authenticated
- `ctx.require_role("admin")` — raises 403 if missing role

**Database:**
- `await ctx.get_db()` — scoped database wrapper
- `await ctx.get_uow()` — Unit of Work

**AI services** (None when not configured):
- `ctx.memory` — MemoryService
- `ctx.llm` — LLMService
- `ctx.embedding` — EmbeddingService

**Response helpers:**
- `ctx.json_response(data, status=200)` — JSONResponse
- `ctx.text_response(text, status=200)` — plain text Response
- `ctx.error(status, detail)` — HTTPException (raise the return value)

### CLI Commands

```bash
# Scaffold a new action
mdb-engine actions new send-email
mdb-engine actions new cleanup --trigger schedule --interval 3600
mdb-engine actions new on-signup --trigger event --event after_create --collection users

# List discovered actions
mdb-engine actions list manifest.json
```

### Programmatic Usage

```python
from mdb_engine.actions.discovery import mount_actions

# Mount actions manually (for inline-dict manifests without a file path)
mount_actions(
    app,
    actions_dir=Path("./actions"),
    actions_config=manifest.get("actions", {}),
    engine=engine,
    slug="my_app",
    app_auth_enabled=True,
    collections_config=manifest.get("collections", {}),
)
```

---
