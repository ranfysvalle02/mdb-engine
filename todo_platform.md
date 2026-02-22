# `create_platform` — Dynamic App Providers & Runtime Mounting

> Implementation roadmap for App-Store-style dynamic architectures in mdb-engine.

---

## Status

| Phase | Description | Effort | Status |
|-------|-------------|--------|--------|
| 0 | Refactor: Extract `_mount_single_app()` | ~5 days | Not started |
| 1 | `AppProvider` protocol + built-in providers | ~3 days | Not started |
| 2 | Runtime `mount_app()` | ~4 days | Not started |
| 3 | Runtime `unmount_app()` + service cleanup | ~5 days | Not started |
| 4 | Nested multi-app (fractal architecture) | ~4 days | Not started |
| 5 | DevEx: hot reload, CLI scaffolding | ~2 days | Not started |

---

## Phase 0 — Refactor the Lifespan Monolith

### Problem

`create_multi_app` in `mdb_engine/core/multi_app.py` runs all mounting logic inside
a single `@asynccontextmanager async def lifespan()` closure (~800 lines, lines 680–1500).
It captures ~15 local variables and is not callable from outside the lifespan. Every
subsequent phase depends on this logic being reusable.

### Tasks

- [ ] Extract per-app mounting into `async def _mount_single_app(self, parent_app, app_config, engine)`.
  - Inputs: parent `FastAPI` app, single app config dict, engine reference.
  - Responsibilities (currently inline in lifespan):
    1. Load manifest from path (`_read_json_file` via `asyncio.to_thread`).
    2. Validate manifest (optional, based on `validate`/`strict` flags).
    3. Create child `FastAPI` app via `engine.create_app(is_sub_app=True)`.
    4. Set child app state: `engine`, `app_slug`, `parent_app`.
    5. Import routes via `_import_app_routes()`.
    6. Share `user_pool` from parent if auth mode is `"shared"`.
    7. Pre-create shared LLM/embedding services via `_ensure_shared_services()`.
    8. Initialize graph service (must happen before memory).
    9. Initialize memory service.
    10. Initialize Perfect Brain if configured.
    11. Register WebSocket routes on parent app.
    12. Run `on_startup` callback.
    13. Store services on `child_app.state`.
    14. `parent_app.mount(path_prefix, child_app)`.
    15. Merge CORS config to parent.
    16. Update `mounted_apps` registry.
  - Returns: `child_app` instance or raises on failure.

- [ ] Extract SharedUserPool initialization into `async def _ensure_shared_user_pool(self, parent_app, apps)`.
  - Currently scattered across the lifespan. Consolidate the "scan manifests for shared auth → init pool" logic.

- [ ] Refactor `create_multi_app` lifespan to call `_mount_single_app` in a loop.
  - The lifespan becomes: `initialize engine → ensure shared pool → for app in apps: _mount_single_app(app) → yield → shutdown`.

- [ ] Add `_unmount_single_app()` stub (empty, implemented in Phase 3).

### Files touched

- `mdb_engine/core/multi_app.py` — primary refactor target.

### Testing

- All existing multi-app tests must pass unchanged.
- SSO multi-app example (`examples/advanced/sso-multi-app/`) must still boot and function.
- Add unit test: `_mount_single_app` called N times for N apps.

---

## Phase 1 — `AppProvider` Protocol & Built-in Providers

### Design

```python
# mdb_engine/core/providers.py

from typing import Protocol, Callable, Awaitable

class AppConfig(TypedDict):
    slug: str
    manifest: str | Path
    path_prefix: str
    on_startup: NotRequired[Callable | None]
    on_shutdown: NotRequired[Callable | None]

class AppProvider(Protocol):
    async def get_apps(self) -> list[AppConfig]: ...
    def on_app_added(self, callback: Callable[[AppConfig], Awaitable[None]]) -> None: ...
    def on_app_removed(self, callback: Callable[[str], Awaitable[None]]) -> None: ...
```

### Built-in Providers

#### `StaticAppProvider`

Wraps the existing `list[dict]` input. Zero behavior change — this is the default when
a user passes `apps=[...]`.

```python
class StaticAppProvider:
    def __init__(self, apps: list[AppConfig]):
        self._apps = apps

    async def get_apps(self) -> list[AppConfig]:
        return self._apps

    def on_app_added(self, callback): pass   # static, never fires
    def on_app_removed(self, callback): pass
```

#### `DatabaseAppProvider`

Reads app configs from a MongoDB collection. Optionally watches for changes via
Change Streams.

```python
class DatabaseAppProvider:
    def __init__(self, collection: str = "installed_apps", watch: bool = False):
        ...

    async def get_apps(self) -> list[AppConfig]:
        # Read all documents from self._collection
        ...

    def on_app_added(self, callback):
        self._on_added_callbacks.append(callback)

    def on_app_removed(self, callback):
        self._on_removed_callbacks.append(callback)

    async def _watch_loop(self):
        # MongoDB Change Stream: watch for insert/delete on collection
        # On insert → call on_app_added callbacks
        # On delete → call on_app_removed callbacks
        ...
```

**Collection document schema:**

```json
{
  "_id": "ai-chat",
  "slug": "ai-chat",
  "manifest_path": "./apps/ai-chat/manifest.json",
  "path_prefix": "/ai-chat",
  "enabled": true,
  "installed_at": "2026-02-21T00:00:00Z"
}
```

#### `FilesystemAppProvider`

Watches a directory for subdirectories containing `manifest.json`. Useful for
development and plugin-style deployments.

```python
class FilesystemAppProvider:
    def __init__(self, apps_dir: Path, watch: bool = False):
        ...

    async def get_apps(self) -> list[AppConfig]:
        # Scan apps_dir for */manifest.json
        # Derive slug from directory name, path_prefix from /{slug}
        ...

    async def _watch_loop(self):
        # asyncio file watcher (watchfiles or polling)
        # On new directory with manifest.json → on_app_added
        # On directory removal → on_app_removed
        ...
```

### Integration with `create_multi_app` / `create_platform`

```python
def create_multi_app(
    self,
    apps: list[dict] | AppProvider | None = None,  # ← accept provider
    ...
) -> FastAPI:
    # Normalize input
    if isinstance(apps, list):
        provider = StaticAppProvider(apps)
    elif isinstance(apps, AppProvider):
        provider = apps
    ...
```

Or introduce `create_platform` as a new entry point:

```python
def create_platform(
    self,
    apps: AppProvider,
    title: str = "Platform",
    **kwargs,
) -> FastAPI:
    """Dynamic platform with runtime app management."""
    ...
```

### Decision: `create_platform` vs evolving `create_multi_app`

**Option A — New method `create_platform`:**
- Clean separation. Static multi-app stays stable. Dynamic features live separately.
- Downside: code duplication unless both delegate to shared internals (which Phase 0 enables).

**Option B — Evolve `create_multi_app` to accept `AppProvider`:**
- Single API surface. `StaticAppProvider` wraps the existing list behavior.
- Downside: the method signature grows. May confuse users who only need static apps.

**Recommendation:** Option A with shared internals. `create_multi_app` stays frozen.
`create_platform` is the new forward-looking API that uses the same `_mount_single_app`
under the hood.

### Files touched

- New: `mdb_engine/core/providers.py`
- Modified: `mdb_engine/core/multi_app.py` (or new `platform.py`)
- Modified: `mdb_engine/__init__.py` (public exports)

### Testing

- Unit tests for each provider in isolation.
- `DatabaseAppProvider`: mock Motor collection, test `get_apps()` and change stream callbacks.
- `FilesystemAppProvider`: use `tmp_path` fixture, test discovery and watch events.

---

## Phase 2 — Runtime `mount_app()`

### Design

Expose `mount_app` on the engine instance. Callable at any time after the platform
is running.

```python
async def mount_app(
    self,
    slug: str,
    manifest_path: str | Path,
    path_prefix: str,
    on_startup: Callable | None = None,
    on_shutdown: Callable | None = None,
) -> None:
```

### Implementation

This method delegates to `_mount_single_app()` from Phase 0, then handles
post-mount bookkeeping:

```python
async def mount_app(self, slug, manifest_path, path_prefix, **kwargs):
    parent_app = self._multi_app_instance  # stored during create_platform

    # Validate prefix doesn't conflict with existing mounts
    existing_prefixes = [m["path_prefix"] for m in parent_app.state.mounted_apps]
    if path_prefix in existing_prefixes:
        raise ValueError(f"Path prefix '{path_prefix}' already in use")

    app_config = {
        "slug": slug,
        "manifest": Path(manifest_path),
        "path_prefix": path_prefix,
        "on_startup": kwargs.get("on_startup"),
        "on_shutdown": kwargs.get("on_shutdown"),
    }

    await self._mount_single_app(parent_app, app_config, self)

    # Invalidate OpenAPI schema cache so /docs reflects new routes
    parent_app.openapi_schema = None

    logger.info(f"Runtime mount: '{slug}' at '{path_prefix}'")
```

### Wiring to AppProvider events

During `create_platform`, wire the provider's event callbacks:

```python
async def _on_provider_app_added(app_config: AppConfig):
    await engine.mount_app(**app_config)

async def _on_provider_app_removed(slug: str):
    await engine.unmount_app(slug)  # Phase 3

provider.on_app_added(_on_provider_app_added)
provider.on_app_removed(_on_provider_app_removed)
```

### Admin API (optional, user-facing)

Provide a built-in admin router that users can include:

```python
# mdb_engine/routing/admin_routes.py

from fastapi import APIRouter, Depends

router = APIRouter(prefix="/_platform", tags=["Platform Admin"])

@router.post("/apps/{slug}/mount")
async def mount_app_route(slug: str, manifest_path: str, path_prefix: str, engine=Depends(get_engine)):
    await engine.mount_app(slug=slug, manifest_path=manifest_path, path_prefix=path_prefix)
    return {"status": "mounted", "slug": slug, "path_prefix": path_prefix}

@router.get("/apps")
async def list_apps(request: Request):
    return request.app.state.mounted_apps
```

### Edge cases

- **Duplicate slug:** Reject with clear error.
- **Conflicting prefix:** Reject. Prefix validation reuses `_validate_path_prefixes`.
- **Manifest not found:** Raise `FileNotFoundError` with path context.
- **Service init failure:** Roll back partial mount (remove child app from routes if services fail).
- **Concurrent mounts:** Use `asyncio.Lock` to serialize mount operations.

### Files touched

- `mdb_engine/core/multi_app.py` (or `platform.py`) — `mount_app` method.
- New: `mdb_engine/routing/admin_routes.py` (optional admin API).

### Testing

- Test: mount app at runtime → verify routes accessible via TestClient.
- Test: mount with conflicting prefix → expect ValueError.
- Test: mount with bad manifest → expect FileNotFoundError.
- Test: concurrent mount calls → verify serialization.
- Integration test: `DatabaseAppProvider` inserts document → app auto-mounts.

---

## Phase 3 — Runtime `unmount_app()` + Service Cleanup

### The Hard Part

FastAPI/Starlette has no `app.unmount()`. We must manipulate `app.routes` directly.

```python
async def unmount_app(self, slug: str) -> None:
    parent_app = self._multi_app_instance
    mounted = _find_mounted_app_entry(slug)
    if not mounted:
        raise ValueError(f"App '{slug}' is not mounted")

    path_prefix = mounted["path_prefix"]

    # 1. Run on_shutdown hook
    child_app = self._get_child_app(slug)
    shutdown_fn = getattr(child_app.state, "on_shutdown", None)
    if shutdown_fn:
        await shutdown_fn(child_app, self, mounted.get("manifest"))

    # 2. Remove Mount from parent routes
    parent_app.routes = [
        r for r in parent_app.routes
        if not (isinstance(r, Mount) and r.path == path_prefix)
    ]

    # 3. Remove WebSocket routes registered on parent
    ws_prefix = f"{path_prefix}/ws"
    parent_app.routes = [
        r for r in parent_app.routes
        if not (hasattr(r, "path") and str(getattr(r, "path", "")).startswith(ws_prefix))
    ]

    # 4. Shutdown and remove services
    await self._shutdown_app_services(slug)

    # 5. Remove from AppRegistrationManager
    self._app_registration_manager.remove_app(slug)

    # 6. Clean up sys.modules
    self._cleanup_imported_modules(slug)

    # 7. Recalculate merged CORS config
    await self._recalculate_cors(parent_app)

    # 8. Update mounted_apps registry
    parent_app.state.mounted_apps = [
        m for m in parent_app.state.mounted_apps if m["slug"] != slug
    ]

    # 9. Invalidate OpenAPI cache
    parent_app.openapi_schema = None
```

### Service shutdown

Add a `_shutdown_app_services` method to `ServiceInitializer`:

```python
async def _shutdown_app_services(self, slug: str) -> None:
    # Graph service
    graph_svc = self._service_initializer._graph_services.pop(slug, None)
    if graph_svc and hasattr(graph_svc, "close"):
        await graph_svc.close()

    # Memory service
    mem_svc = self._service_initializer._memory_services.pop(slug, None)
    if mem_svc and hasattr(mem_svc, "close"):
        await mem_svc.close()

    # Profile service
    self._service_initializer._profile_services.pop(slug, None)

    # Embedding / LLM (shared instances — only remove mapping, don't destroy)
    self._service_initializer._embedding_services.pop(slug, None)
    self._service_initializer._llm_services.pop(slug, None)

    # Perfect Brain
    pb = self._service_initializer._perfect_brain_services.pop(slug, None)
    if pb and hasattr(pb, "close"):
        await pb.close()

    # WebSocket configs
    self._service_initializer._websocket_configs.pop(slug, None)
```

### New methods needed on existing classes

- `AppRegistrationManager.remove_app(slug)` — remove from `self._apps` dict.
- `ServiceInitializer.shutdown_services(slug)` — remove from all service dicts.
- Services (`GraphService`, `CognitiveMemoryService`) — add `async def close()` if not present.

### CORS recalculation

```python
async def _recalculate_cors(self, parent_app):
    """Rebuild merged CORS from all remaining mounted apps."""
    merged = {"allow_origins": [], "allow_methods": ["GET", "POST"], ...}
    for mounted in parent_app.state.mounted_apps:
        manifest = mounted.get("manifest", {})
        cors = manifest.get("cors", {})
        # merge origins, methods, headers
        ...
    # Re-apply to parent app middleware
    ...
```

### Edge cases

- **Active requests:** In-flight requests to the app being unmounted may fail. Document
  this as expected behavior. Consider a "draining" mode in a future iteration.
- **Shared services:** Embedding/LLM services may be shared across apps. Only remove the
  slug mapping, don't destroy the underlying service instance.
- **SharedUserPool:** Never destroy on unmount. It's shared and other apps depend on it.
  Only destroy when the entire platform shuts down.

### Files touched

- `mdb_engine/core/multi_app.py` (or `platform.py`) — `unmount_app` method.
- `mdb_engine/core/app_registration.py` — `remove_app()`.
- `mdb_engine/core/service_initialization.py` — `shutdown_services()`.
- `mdb_engine/graph/search.py` — `close()` method if missing.
- `mdb_engine/memory/cognitive.py` — `close()` method if missing.

### Testing

- Test: mount → unmount → verify routes return 404.
- Test: unmount → services removed from registries.
- Test: unmount non-existent slug → ValueError.
- Test: unmount → remount same slug → works.
- Test: unmount one app, other apps unaffected.

---

## Phase 4 — Nested Multi-App (Fractal Architecture)

### Problem

`create_multi_app` / `create_platform` always creates a new `SharedUserPool`.
Nested platforms become disconnected from the parent session.

### Design

```python
def create_platform(
    self,
    apps: AppProvider,
    inherit_auth: bool = False,   # Use parent's SharedUserPool
    inherit_db: bool = False,     # Use parent's ConnectionManager
    parent_engine: MongoDBEngine | None = None,  # Explicit parent reference
    **kwargs,
) -> FastAPI:
```

When `inherit_auth=True`:
- Skip `_ensure_shared_user_pool()`.
- Copy `self._shared_user_pool` from `parent_engine` (or from `self` if already set).
- Middleware must NOT re-validate JWT — trust the parent's middleware.

When `inherit_db=True`:
- Skip creating a new `ConnectionManager`.
- Reuse `parent_engine._connection_manager`.
- `ScopedMongoWrapper` still provides per-app scoping on the shared connection pool.

### Middleware stacking concern

With nesting, a request to `/engineering/docs/api/page` traverses:

```
Root middleware → /engineering Mount → Engineering middleware → /docs Mount → Docs middleware
```

Risk: double auth, double CORS headers.

**Solution:** Add an `_mdb_auth_resolved` flag to `request.state`. Auth middleware checks
this flag and skips if already resolved by a parent. Similarly for CORS — only the
outermost app should set CORS headers.

```python
class SharedAuthMiddleware:
    async def __call__(self, request, call_next):
        if getattr(request.state, "_mdb_auth_resolved", False):
            return await call_next(request)  # parent already authenticated
        # ... normal auth logic ...
        request.state._mdb_auth_resolved = True
        return await call_next(request)
```

### Use case example

```python
engine = MongoDBEngine(mongo_uri=..., db_name=...)

# Root platform
root = engine.create_platform(
    apps=StaticAppProvider([
        {"slug": "hr", "manifest": "apps/hr/manifest.json", "path_prefix": "/hr"},
    ]),
    title="Company Intranet",
)

# Inside engineering app's on_startup:
async def eng_startup(app, engine, manifest):
    sub_platform = engine.create_platform(
        apps=FilesystemAppProvider(Path("./eng-apps")),
        inherit_auth=True,
        inherit_db=True,
    )
    app.mount("/tools", sub_platform)
```

Result:
- `/hr` → HR app
- `/engineering/tools/jira` → Jira app (inherits auth from root)
- `/engineering/tools/docs` → Docs app (inherits auth from root)

### Files touched

- `mdb_engine/core/multi_app.py` (or `platform.py`) — `inherit_auth`/`inherit_db` params.
- `mdb_engine/core/fastapi_app.py` — auth middleware skip logic.
- Middleware classes — `_mdb_auth_resolved` guard.

### Testing

- Test: nested platform inherits user pool (same instance).
- Test: request authenticated at root level, grandchild sees `request.state.user`.
- Test: CORS headers not duplicated across nesting levels.

---

## Phase 5 — DevEx: Hot Reload & CLI

### Hot Reload (Dev Mode)

When `MDB_ENV=development`, mount a file watcher that triggers unmount/remount on
file changes within an app's directory.

```python
# In create_platform lifespan, after initial mount:
if os.getenv("MDB_ENV") == "development":
    async def _on_file_change(slug: str):
        logger.info(f"Hot-reloading app '{slug}'...")
        await engine.unmount_app(slug)
        await engine.mount_app(slug, manifest_path=..., path_prefix=...)

    asyncio.create_task(_watch_app_directories(apps, _on_file_change))
```

### CLI: `mdb-cli create-app`

Extend `mdb_engine/cli/commands/new_app.py` to generate a directory compatible with
dynamic loading:

```
my-new-app/
├── manifest.json    # pre-filled with slug, app_name, version
├── web.py           # starter routes with @app.get("/")
└── README.md
```

Command:
```bash
mdb-cli create-app my-new-app --template basic
mdb-cli create-app my-new-app --template with-memory
mdb-cli create-app my-new-app --template with-graph
```

### Files touched

- `mdb_engine/cli/commands/new_app.py` — extend scaffolding.
- New: `mdb_engine/cli/templates/` — app templates.

---

## Appendix A — FastAPI Route Removal

FastAPI stores routes in `app.routes` as a plain `list[BaseRoute]`. Mounted sub-apps
appear as `starlette.routing.Mount` objects. There is no official `unmount()` API.

**Our approach:**

```python
from starlette.routing import Mount

def _remove_mount(app: FastAPI, path: str) -> bool:
    before = len(app.routes)
    app.routes = [
        r for r in app.routes
        if not (isinstance(r, Mount) and r.path == path)
    ]
    removed = len(app.routes) < before
    if removed:
        app.openapi_schema = None  # force regeneration
    return removed
```

This is tested to work on Starlette 0.27+ and FastAPI 0.100+. It's undocumented but
stable — `app.routes` has been a public mutable list since Starlette's inception.

---

## Appendix B — MongoDB Change Streams for `DatabaseAppProvider`

```python
async def _watch_loop(self):
    collection = self._db[self._collection_name]
    async with collection.watch(
        [{"$match": {"operationType": {"$in": ["insert", "delete", "update"]}}}],
        full_document="updateLookup",
    ) as stream:
        async for change in stream:
            op = change["operationType"]
            if op == "insert":
                doc = change["fullDocument"]
                config = AppConfig(
                    slug=doc["slug"],
                    manifest=doc["manifest_path"],
                    path_prefix=doc["path_prefix"],
                )
                for cb in self._on_added_callbacks:
                    await cb(config)
            elif op == "delete":
                slug = change["documentKey"]["_id"]
                for cb in self._on_removed_callbacks:
                    await cb(slug)
            elif op == "update":
                doc = change["fullDocument"]
                if not doc.get("enabled", True):
                    for cb in self._on_removed_callbacks:
                        await cb(doc["slug"])
                else:
                    config = AppConfig(
                        slug=doc["slug"],
                        manifest=doc["manifest_path"],
                        path_prefix=doc["path_prefix"],
                    )
                    for cb in self._on_added_callbacks:
                        await cb(config)
```

Requires a MongoDB replica set (Change Streams are not available on standalone instances).

---

## Appendix C — Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| FastAPI removes `app.routes` mutability | Low | Critical | Pin Starlette version; upstream PR to add `unmount()` |
| In-flight requests fail during unmount | Medium | Medium | Document as expected; add drain mode later |
| Change Stream disconnects | Medium | Low | Reconnect with exponential backoff in `_watch_loop` |
| Memory leak from `sys.modules` on repeated mount/unmount cycles | Medium | Medium | Track and remove imported modules per slug |
| Middleware double-execution in nested platforms | High | Medium | `_mdb_auth_resolved` flag (Phase 4) |
| Service init failure leaves partial mount | Medium | High | Rollback logic in `_mount_single_app` |
