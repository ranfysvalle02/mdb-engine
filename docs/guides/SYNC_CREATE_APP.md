# Why `create_app` and `create_multi_app` Are Synchronous

A technical explanation of why both app creation methods are `def` (sync) in an async-first framework, and the hard lessons learned getting there.

---

## The Constraint

Uvicorn imports your module from inside its own async event loop:

```
uvicorn.run("my_module:app")
  -> uvicorn.server.serve()           # async, event loop running
    -> config.load()
      -> import_from_string("my_module:app")
        -> importlib.import_module("my_module")   # YOUR CODE RUNS HERE
          -> app = engine.create_multi_app(...)    # must be sync
```

By the time Python executes your module-level code, an event loop is already running. This means:

- `asyncio.run()` crashes: `RuntimeError: asyncio.run() cannot be called from a running event loop`
- `await` is illegal at module level (SyntaxError outside `async def`)
- Any async work in app creation must be **deferred**, not executed immediately

## The Pattern

Both `create_app` and `create_multi_app` follow the same pattern:

```
SYNC (module import time)          ASYNC (server startup time)
─────────────────────────          ──────────────────────────
Read manifests (json.load)         engine.initialize() - MongoDB connect
Validate path prefixes             SharedUserPool setup
Build FastAPI object               Memory service init
Register middleware                Mount child apps
Define lifespan ctx manager        Route auto-import
Return FastAPI app                 WebSocket registration
                                   Index creation
                                   Demo user seeding
```

**Left column**: Pure sync operations -- file reads, config parsing, object construction. These happen when uvicorn imports the module.

**Right column**: Async operations requiring MongoDB, network, or I/O. These happen inside the `lifespan` async context manager, which FastAPI/uvicorn calls after the event loop is ready.

```python
def create_multi_app(self, apps, ...):
    # SYNC: read configs, validate, build objects
    manifest_data = _read_json_file(manifest_path)   # json.load()
    self._validate_path_prefixes(apps)                # pure logic
    parent_app = FastAPI(lifespan=lifespan, ...)      # object creation

    @asynccontextmanager
    async def lifespan(app):
        # ASYNC: all heavy work deferred to here
        await engine.initialize()                      # MongoDB connection
        await self._initialize_shared_user_pool(...)   # auth setup
        # ... mount apps, init services, etc.
        yield
        await engine.shutdown()

    return parent_app  # uvicorn gets a real FastAPI instance
```

## What Went Wrong (The History)

### Attempt 1: `async def create_multi_app`

The original implementation was `async def` because it used `await asyncio.to_thread()` to read manifest files off the main thread. Technically "correct" async practice, but it meant every caller needed `await`:

```python
# Tests (worked fine -- already in async context)
app = await engine.create_multi_app(apps=[...])

# Module-level entry point (broken -- no way to await at module level)
app = engine.create_multi_app(apps=[...])  # returns coroutine, not FastAPI
```

Every example and doc showed the broken pattern. The `app` variable was a coroutine object, not a FastAPI app. Route decorators like `@app.get("/")` crashed with `AttributeError`.

### Attempt 2: `asyncio.run()` wrapper

```python
app = asyncio.run(engine.create_multi_app(apps=[...]))
```

This works when running `python my_module.py` directly (no event loop yet). But uvicorn imports modules from inside its event loop:

```
RuntimeError: asyncio.run() cannot be called from a running event loop
```

Dead on arrival for the primary use case (`uvicorn my_module:app`).

### Attempt 3: Make it sync (final answer)

The `asyncio.to_thread()` calls were wrapping already-synchronous operations:

```python
# Before (unnecessarily async)
manifest_data = await asyncio.to_thread(_read_json_file, path)  # json.load in a thread

# After (just call it)
manifest_data = _read_json_file(path)  # json.load directly
```

Every "async" operation in `create_multi_app` before the lifespan was just `json.load()`, directory scanning, or config validation -- all CPU-bound, sub-millisecond, synchronous work. Wrapping them in `asyncio.to_thread()` added complexity for zero benefit.

The fix: `def create_multi_app` (sync), matching `create_app`. All callers just work:

```python
# Works everywhere -- module level, scripts, tests, uvicorn
app = engine.create_multi_app(apps=[...])
```

## The Rule

**App creation is sync. Everything else is async.**

| Method | Type | Why |
|--------|------|-----|
| `MongoDBEngine()` | sync | Object construction |
| `create_app()` | sync | Config + FastAPI object creation |
| `create_multi_app()` | sync | Config + FastAPI object creation |
| `engine.initialize()` | async | MongoDB connection (network I/O) |
| `engine.shutdown()` | async | Connection cleanup |
| `engine.get_scoped_db()` | async | Database access |
| Memory/Graph/Embedding ops | async | All service operations |

The sync/async boundary is clean: **building the app** is sync (happens at import time), **running the app** is async (happens in the event loop).

## For Contributors

If you add new functionality to `create_app` or `create_multi_app`:

1. If it's config parsing, validation, or object construction -- keep it sync
2. If it needs MongoDB, network, or any I/O -- put it in the `lifespan`
3. Never use `asyncio.run()`, `asyncio.to_thread()`, or `await` in these methods
4. Test that `uvicorn my_module:app` works (not just `python my_module.py`)

The lifespan is your async escape hatch. Use it.
