# Upgrading to mdb-engine 0.7.10

**Release focus:** Developer experience — eliminate boilerplate, harden defaults, ship tooling.

This release contains **breaking changes** by design. Backward compatibility is not a goal on the path to 1.0. Every breaking change removes code you no longer need to write.

---

## Quick checklist

1. `pip install --upgrade mdb-engine`
2. Delete your `/auth/callback` and `/logout` route handlers (the engine registers them automatically now)
3. Remove `/auth/callback` from `public_routes` and `csrf_protection.exempt_routes` in your manifests
4. Replace `from shared_security import ...` with `from mdb_engine.auth import ...`
5. Delete your `shared_security.py` file
6. Remove `logging.basicConfig(...)` blocks from your `web.py` files
7. Rename env vars to canonical `MDB_` names (old names still work with a deprecation warning)
8. Run `mdb-engine doctor` to verify your environment

---

## Breaking changes

### Auth callback and logout are now auto-registered

**Before (0.7.9):** Every SSO app had to implement ~30 lines of `/auth/callback` and ~20 lines of `/logout`:

```python
# DELETE ALL OF THIS
@app.get("/auth/callback")
async def auth_callback(request: Request, token: str = None):
    from urllib.parse import unquote_plus
    from mdb_engine.auth.shared_users import SharedUserPool
    # ... 25 more lines of token validation, cookie setting, redirect logic
```

**After (0.7.10):** The engine auto-registers both routes on every app with `auth.mode: "shared"`. Zero lines of auth code in your app.

To customize redirect targets, add to your manifest:

```json
"auth": {
  "mode": "shared",
  "on_login_redirect": "/dashboard",
  "on_logout_redirect": "/auth-hub/login",
  "cookie_max_age": 86400
}
```

**Action:** Delete your `auth_callback` and `logout` functions from `web.py`. Remove `/auth/callback` from `public_routes` and `csrf_protection.exempt_routes` in your manifest — the engine adds them automatically.

---

### `shared_security.py` is gone

**Before:** Every app had a try/except import dance:

```python
# DELETE ALL OF THIS
try:
    from shared_security import get_cookie_settings, validate_jwt_token_format
except ImportError:
    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}
    def validate_jwt_token_format(token: str) -> bool:
        return bool(token and len(token) > 10)
```

**After:** Both functions are first-class exports from the engine:

```python
from mdb_engine.auth import get_cookie_settings, validate_jwt_token_format
```

**Action:** Replace all `from shared_security import ...` with `from mdb_engine.auth import ...`. Delete `shared_security.py`.

---

### Logging is auto-configured

**Before:** Every `web.py` started with:

```python
# DELETE ALL OF THIS
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)
```

**After:** The multi-app lifespan calls `configure_logging()` automatically. In production (`ENVIRONMENT=production`), output is JSON. In development, it's human-readable with timestamps.

```python
# All you need:
logger = logging.getLogger(__name__)
```

Or use the new dependency:

```python
from mdb_engine.dependencies import get_app_logger

@app.get("/api/data")
async def get_data(logger=Depends(get_app_logger)):
    logger.info("This log includes the app slug automatically")
```

**Action:** Remove `logging.basicConfig(...)` from your `web.py` files. Keep `logger = logging.getLogger(__name__)`.

---

## New features

### `public_routes` now supports globs and inversion

You no longer need to list every route individually:

```json
// Glob patterns — match entire subtrees
"public_routes": ["/api/**", "/health"]

// Or invert: everything is public except admin routes
"default_policy": "public",
"protected_routes": ["/api/admin/**", "/api/settings/**"]
```

Pattern syntax:
- `/exact` — exact match
- `/prefix/*` — single-segment wildcard
- `/prefix/**` — any depth below prefix
- Standard fnmatch globs (`?`, `[seq]`)

---

### Canonical environment variables

All env vars now have a canonical `MDB_` name. Old names still work but emit a `DeprecationWarning` at startup.

| Canonical | Replaces |
|-----------|----------|
| `MDB_MONGO_URI` | `MONGODB_URI`, `MONGO_URI` |
| `MDB_DB_NAME` | `MONGODB_DB`, `MONGO_DB_NAME`, `DB_NAME` |
| `MDB_JWT_SECRET` | `MDB_ENGINE_JWT_SECRET`, `SECRET_KEY`, `FLASK_SECRET_KEY` |

**Action:** Update your `.env` / `docker-compose.yml` to use the canonical names. No rush — old names work for now.

---

### Use the environment variable helpers

**Do not** read MongoDB or JWT env vars with `os.getenv()` directly. The `mdb_engine.env` module checks canonical names first, then all deprecated aliases, and emits a deprecation warning when an old name is used. Direct `os.getenv()` bypasses this and will silently fail if the env var name doesn't match exactly.

```python
# WRONG — will miss MONGODB_URI, MONGO_URI, etc.
mongo_uri = os.getenv("MDB_MONGO_URI", "mongodb://localhost:27017")
db_name = os.getenv("MDB_DB_NAME", "my_db")

# RIGHT — checks canonical + all deprecated aliases automatically
from mdb_engine.env import get_mongo_uri, get_db_name, get_jwt_secret

mongo_uri = get_mongo_uri()                     # fallback: mongodb://localhost:27017
db_name = get_db_name(fallback="my_db")          # fallback: mdb_engine
jwt_secret = get_jwt_secret()                    # no fallback — returns None if unset
```

`MongoDBEngine.__init__` uses these helpers internally, so if you pass `mongo_uri` or `db_name` explicitly they take priority:

```python
engine = MongoDBEngine(
    mongo_uri=get_mongo_uri(),
    db_name=get_db_name(fallback="my_db"),
)
```

**Action:** Replace any `os.getenv("MDB_MONGO_URI", ...)` or `os.getenv("MDB_DB_NAME", ...)` calls with the helpers from `mdb_engine.env`.

---

### Cookie settings now require a `request` argument

`get_cookie_settings()` (aliased from `get_secure_cookie_settings()`) now takes a `Request` parameter so it can detect HTTPS vs HTTP and set `secure` accordingly. Calling it without `request` raises `TypeError`.

```python
# WRONG — raises TypeError at runtime
cookie_settings = get_cookie_settings()

# RIGHT — pass the FastAPI Request object
cookie_settings = get_cookie_settings(request)
```

**SSO child apps no longer need cookie code at all.** The engine auto-registers `/auth/callback` and `/logout` routes (see above) which handle cookie management internally. If you still need manual cookie control in an auth hub, always pass `request`.

**Action:** Update any remaining `get_cookie_settings()` calls to `get_cookie_settings(request)`. Delete manual cookie code from SSO child apps.

---

### Docker Compose env var alignment

A common deployment pitfall: your `docker-compose.yml` passes one env var name, but the app reads a different one. Inside a Docker container, `localhost` refers to the container itself — not the MongoDB container. If the env var lookup fails, the app falls back to `mongodb://localhost:27017`, which causes a connection timeout.

Always use the canonical names in `docker-compose.yml`:

```yaml
environment:
  - MDB_MONGO_URI=mongodb://admin:password@mongodb:27017/?authSource=admin
  - MDB_DB_NAME=my_db
  - MDB_JWT_SECRET=${MDB_JWT_SECRET:-change-me-in-production}
```

If your app uses the `mdb_engine.env` helpers, both old and new names work. But if you mix `os.getenv("MDB_MONGO_URI")` in Python with `MONGODB_URI` in docker-compose, the lookup silently returns `None` and falls back to localhost — breaking the container.

**Action:** Audit your `docker-compose.yml` files and ensure all MongoDB/JWT env vars use the `MDB_` canonical names.

---

### CLI: `mdb-engine` command

After `pip install mdb-engine`, you get a CLI:

```bash
# Scaffold a new app
mdb-engine new-app my-app --mode shared --services memory,graph

# Validate manifests
mdb-engine validate manifest.json

# Migrate manifests to latest schema
mdb-engine migrate manifest.json --in-place

# Diagnose your environment
mdb-engine doctor
mdb-engine doctor --apps-dir apps/
```

`mdb-engine doctor` checks:
- MongoDB connectivity and version
- Required env vars present and strong JWT secret
- OpenAI / Azure API keys
- Manifest schema validity

---

### Platform info dependency

Child apps can now discover sibling apps for navigation and cross-app links:

```python
from mdb_engine.dependencies import get_platform_info

@app.get("/api/navigation")
async def nav(platform=Depends(get_platform_info)):
    return {
        "current_app": platform.current_slug,
        "apps": [
            {"slug": a["slug"], "path": a["path_prefix"]}
            for a in platform.apps
        ],
    }
```

---

### Graph service dependency

`get_graph_service` joins `get_memory_service` and `get_profile_service` as a first-class FastAPI dependency:

```python
from mdb_engine.dependencies import get_graph_service

@app.get("/api/graph/nodes")
async def list_nodes(graph=Depends(get_graph_service)):
    return await graph.get_nodes(limit=50)
```

---

### Testing utilities

New `mdb_engine.testing` module — test your apps without a running MongoDB:

```python
from mdb_engine.testing import create_test_client, mock_scoped_db, mock_user

@pytest.fixture
async def client():
    async with create_test_client("my-app") as c:
        yield c

async def test_list_items(client):
    resp = await client.get("/items")
    assert resp.status_code == 200

# Or override individual dependencies:
app.dependency_overrides[get_scoped_db] = mock_scoped_db({"tasks": [{"title": "A"}]})
app.dependency_overrides[require_user()] = mock_user({"email": "test@test.com"})
```

---

### Background tasks

Managed recurring tasks with exponential backoff on failure:

```python
from mdb_engine.tasks import recurring_task

@recurring_task(interval_seconds=3600, name="cleanup-sessions")
async def cleanup():
    # This runs every hour, restarts on failure, reports to /health
    ...
```

Start them in your `on_startup`:

```python
from mdb_engine.tasks import start_all_tasks, stop_all_tasks

async def on_startup(app_instance, engine_ref, manifest):
    start_all_tasks()

# Tasks are automatically cancelled on shutdown
```

---

### WebSocket auth and rooms

New `authenticated_websocket` decorator validates cookies before the handler runs:

```python
from mdb_engine.routing.websockets import authenticated_websocket, RoomManager

rooms = RoomManager()

@app.websocket("/ws/chat")
@authenticated_websocket
async def chat(ws, user):
    room = f"user:{user['_id']}"
    await rooms.connect(ws, room)
    try:
        async for msg in ws.iter_json():
            await rooms.broadcast(room, msg)
    finally:
        rooms.disconnect(ws, room)
```

---

### Manifest validation at startup

Manifests are now validated during the multi-app lifespan. Invalid manifests log actionable errors:

```
Manifest validation failed for 'member': auth.mode must be "app" or "shared", got "sso"
  Paths: auth.mode
```

A JSON Schema file is published at `mdb_engine/schemas/manifest.schema.json` for editor autocomplete. Add this to your manifest files:

```json
{
  "$schema": "../path/to/mdb_engine/schemas/manifest.schema.json",
  "schema_version": "2.0",
  "slug": "my-app"
}
```

---

### CORS dev defaults

In non-production environments, `localhost:8000` through `localhost:8009` are automatically added to CORS allowed origins. No more maintaining a list of dev ports in every manifest.

---

## Migration script

For a typical SSO multi-app project, the migration looks like this:

```bash
# 1. Update the package
pip install --upgrade mdb-engine

# 2. Delete shared_security.py
rm apps/shared_security.py

# 3. In each app's web.py, find and replace:
#    - Delete: try/from shared_security ... except ... block
#    - Delete: @app.get("/auth/callback") ... entire function
#    - Delete: @app.post("/logout") ... entire function
#    - Delete: logging.basicConfig(...) block
#    - Delete: unused imports of get_cookie_settings / validate_jwt_token_format
#      (SSO child apps no longer need these — handled by auto-registered routes)

# 4. Use env helpers instead of os.getenv():
#    - Replace: os.getenv("MDB_MONGO_URI", "mongodb://localhost:27017")
#    - With:    from mdb_engine.env import get_mongo_uri; get_mongo_uri()
#    - Same for get_db_name() and get_jwt_secret()

# 5. Fix cookie settings calls (if you have a custom auth hub):
#    - Replace: get_cookie_settings()
#    - With:    get_cookie_settings(request)

# 6. In each app's manifest.json:
#    - Remove "/auth/callback" from public_routes
#    - Remove "/auth/callback" from csrf_protection.exempt_routes

# 7. Rename env vars in .env / docker-compose.yml:
#    MONGODB_URI          -> MDB_MONGO_URI
#    MONGODB_DB           -> MDB_DB_NAME
#    MONGO_URI            -> MDB_MONGO_URI
#    MONGO_DB_NAME        -> MDB_DB_NAME
#    SECRET_KEY           -> MDB_JWT_SECRET
#    MDB_ENGINE_JWT_SECRET -> MDB_JWT_SECRET

# 8. Verify
mdb-engine doctor --apps-dir apps/
```
