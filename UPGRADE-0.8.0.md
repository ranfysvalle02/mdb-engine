# Upgrading to mdb-engine 0.8.0

**Release focus:** Memory & graph robustness, manifest simplification, zero-boilerplate AI services, framework base template, index management hardening.

This release contains **breaking changes** by design. Backward compatibility has been removed for pre-1.0 patterns. Every breaking change eliminates code you no longer need to write.

---

## Quick checklist

1. `pip install --upgrade mdb-engine`
2. Replace verbose `memory_config` with shorthand or presets (see below)
3. Move `perfect_brain` inside `memory_config` (top-level no longer works)
4. Change `collection_name: "__kg"` to `"kg"` in all graph configs
5. Remove manual `EmbeddingService` / `LLMService` creation — use `Depends()` or `app.state`
6. Replace custom `get_graph_service_from_request()` helpers with `Depends(get_graph_service_optional)`
7. Replace manual `PerfectBrain` wiring with `Depends(get_perfect_brain)` (manifest-driven)
8. Remove `embedding_model_dims: 1536` from manifests (auto-detected)
9. Delete custom `/graph/stats` endpoints (auto-registered as `/_mdb/graph/stats`)
10. Delete manual graph/memory init from `on_startup` (engine handles all service init now)
11. Extend `mdb_base.html` in your app base templates (replaces hand-rolled `const BASE` boilerplate)
12. Run `make format && make lint-local` to verify

---

## Breaking changes

### `memory_config` accepts shorthand and presets

**Before (0.7.x):** Every app repeated 6-10 lines of defaults:

```json
"memory_config": {
  "enabled": true,
  "provider": "cognitive",
  "collection_name": "memories",
  "embedding_model": "text-embedding-3-small",
  "embedding_model_dims": 1536,
  "infer": true
}
```

**After (0.8.0):** All of the above is the default. Use shorthand:

```json
"memory_config": true
```

Or named presets for common configurations:

```json
"memory_config": "smart"
```

| Preset | What it enables |
|--------|----------------|
| `true` / `"basic"` | Infer with LLM extraction, no cognitive features |
| `"smart"` | Cognitive scoring, categories, salience gate, memory types |
| `"full"` | Everything: reflection, graph integration, emotion, conflict resolution, pruning |

Presets with overrides:

```json
"memory_config": {
  "preset": "full",
  "embedding_model": "text-embedding-3-large",
  "max_depth": 500
}
```

**Action:** Replace verbose memory configs with presets. Remove any keys that match defaults: `provider`, `embedding_model` (default: `text-embedding-3-small`), `embedding_model_dims` (auto-detected), `infer` (default: `true`), `enable_cognitive` (default: `true`), `async_mode` (default: `true`).

---

### `perfect_brain` moved inside `memory_config`

**Before (0.7.x):** Top-level manifest key:

```json
{
  "memory_config": {"enabled": true},
  "perfect_brain": {
    "enabled": true,
    "memory_veto": true
  }
}
```

**After (0.8.0):** Nested inside `memory_config`:

```json
{
  "memory_config": {
    "preset": "full",
    "perfect_brain": {
      "enabled": true,
      "memory_veto": true,
      "shared_memory": true,
      "consolidator": {"enabled": true}
    }
  }
}
```

The top-level `perfect_brain` key is no longer read by the engine.

**Action:** Move your `perfect_brain` config inside `memory_config`.

---

### `"__kg"` collection name removed

**Before (0.7.x):** Graph config defaulted to `"__kg"`, which was silently normalized to `"kg"` because `ScopedMongoWrapper` blocks double-underscore attribute access.

**After (0.8.0):** Default is `"kg"`. Any collection name starting with `__` is now auto-rewritten at runtime with a deprecation warning:

```
WARNING: graph_config.collection_name '__kg' uses a double-underscore prefix
which is incompatible with ScopedMongoWrapper (Python name mangling).
Automatically rewriting to 'kg'. Please update your manifest.
```

**Action:** Change `"collection_name": "__kg"` to `"collection_name": "kg"` in your manifests. Or remove it entirely (the default is `"kg"`).

Also update any Python code that references `"__kg"`:

```python
# Before
kg_col = scoped_db["__kg"]
db["__kg"].find(...)

# After
kg_col = scoped_db["kg"]
db["kg"].find(...)
```

---

### `embedding_model_dims` auto-detected

**Before (0.7.x):** You had to specify `"embedding_model_dims": 1536` or `3072` manually.

**After (0.8.0):** Dimensions are resolved automatically from the model name:

| Model | Auto-detected dims |
|-------|-------------------|
| `text-embedding-3-small` | 1536 |
| `text-embedding-3-large` | 3072 |
| `text-embedding-ada-002` | 1536 |
| `embed-english-v3.0` | 1024 |
| `voyage-3` | 1024 |

**Action:** Remove `embedding_model_dims` from manifests unless you're using a model not in the lookup table.

---

### `MemoryConsolidator` constructor signature changed

**Before (0.7.x):**

```python
consolidator = MemoryConsolidator(
    db_client=db,
    llm_service=None,         # Defaulted to None but raised at runtime
    embedding_service=None,   # Same
)
```

**After (0.8.0):** `llm_service` and `embedding_service` are keyword-only required parameters:

```python
consolidator = MemoryConsolidator(
    db_client=db,
    llm_service=llm_service,         # Required
    embedding_service=embedding_service,  # Required
)
```

**Action:** If you create `MemoryConsolidator` directly, pass both services. Or use `PerfectBrain` which handles this automatically.

---

### `get_embedding_service` no longer creates services on-the-fly

**Before (0.7.x):** The `Depends(get_embedding_service)` dependency and `engine.get_embedding_service(slug)` would create a new `EmbeddingService` from `embedding_config` if no cached service existed.

**After (0.8.0):** It returns the shared service created during initialization, or raises `HTTPException(503)`. Services are created once by `_ensure_shared_services()` before graph and memory init.

**Action:** Ensure your manifest has either `embedding_config`, `memory_config`, or `llm_config` so the engine creates the shared services during startup. If you were relying on lazy on-the-fly creation, add `"embedding_config": {"enabled": true}` to your manifest.

---

### Memory builder no longer probes environment for LLM credentials

**Before (0.7.x):** If no `LLMService` was injected, the builder checked for `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `GEMINI_API_KEY` etc. and used LiteLLM directly.

**After (0.8.0):** The builder expects an injected `LLMService`. The engine provides this automatically via `_ensure_shared_services()` when `llm_config` is present in the manifest.

**Action:** Add `llm_config` to your manifest if you use `infer: true`:

```json
{
  "llm_config": {
    "providers": {
      "chat": "openai/gpt-4o"
    }
  },
  "memory_config": true
}
```

---

## New features

### Framework base template (`mdb_base.html`)

mdb-engine now ships a framework-provided Jinja2 base template that eliminates the most common class of frontend bugs: scripts running before `BASE` is defined.

**The problem it solves:** In 0.7.x, every app hand-rolled `const BASE = '{{ base_path }}'` in their base template and hoped child pages put their scripts in the right block. If a child template placed `<script>` inside `{% block content %}` instead of `{% block extra_js %}`, the script executed before `BASE` was defined, causing `ReferenceError: BASE is not defined`.

**How it works:** `mdb_base.html` defines the correct block ordering and provides a frozen `MDB` JavaScript context object that's always available to scripts in `{% block base_js %}` and `{% block extra_js %}`:

```
Render order:
  1. {% block body %}     — visual structure (header, nav, content)
  2. <script>MDB = ...</script>  — framework JS context (always defined)
  3. {% block base_js %}  — app-level base scripts (logout, etc.)
  4. {% block extra_js %} — page-level scripts
```

**JavaScript globals available in `base_js` / `extra_js`:**

| Global | Description |
|--------|-------------|
| `MDB.BASE` | App mount path (e.g. `"/ai-chat"`) |
| `MDB.AUTH_HUB` | Auth hub URL (e.g. `"/auth-hub"`) |
| `MDB.APP_SLUG` | App slug (e.g. `"ai-chat"`) |
| `MDB.csrfToken()` | Current CSRF token from cookie |
| `getCookie(name)` | Read any cookie by name |
| `BASE` | Alias for `MDB.BASE` (backwards-compatible) |

**Usage — app base template:**

```html
{% extends "mdb_base.html" %}

{% block head %}
<style>/* your app styles */</style>
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

**Usage — page template:**

```html
{% extends "base.html" %}

{% block content %}
<div>My page content</div>
{% endblock %}

{% block extra_js %}
<script>
// MDB.BASE and getCookie() are always available here
const response = await fetch(BASE + '/api/data');
</script>
{% endblock %}
```

The engine automatically registers the framework templates directory in child app Jinja2 loaders, so `{% extends "mdb_base.html" %}` works without any configuration.

**Action:** Update your app's `base.html` to extend `mdb_base.html` instead of writing a standalone HTML document. Remove your hand-rolled `const BASE`, `getCookie`, and CSRF boilerplate — the framework provides all of it.

---

### `mdb-engine new-app` scaffolds templates

The `mdb-engine new-app` CLI now generates a `templates/` directory with:

- `base.html` — extends `mdb_base.html` with minimal app styling
- `index.html` — starter page that extends `base.html`
- `web.py` — uses `Jinja2Templates` instead of inline `HTMLResponse`

```bash
mdb-engine new-app my-app --services memory
# Creates:
#   my-app/manifest.json
#   my-app/web.py
#   my-app/templates/base.html   (extends mdb_base.html)
#   my-app/templates/index.html
```

---

### Shared LLM and embedding services

Services are created once during app initialization and shared across memory, graph, and Perfect Brain. No more duplicate instances.

```python
# In routes — use dependencies
from mdb_engine.dependencies import get_embedding_service, get_llm_service

@app.post("/embed")
async def embed(text: str, svc=Depends(get_embedding_service)):
    return await svc.embed([text])

@app.post("/generate")
async def generate(prompt: str, llm=Depends(get_llm_service)):
    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        provider_name="chat",
    )
```

Or via `app.state`:

```python
request.app.state.llm_service        # LLMService | None
request.app.state.embedding_service  # EmbeddingService | None
request.app.state.memory_service     # MemoryService | None
request.app.state.graph_service      # GraphService | None
```

Or via `RequestContext`:

```python
ctx.llm_service        # LLMService (preferred over ctx.llm)
ctx.embedding_service  # EmbeddingService
ctx.memory             # MemoryService
```

---

### `get_graph_service_optional` dependency

New dependency that returns `None` instead of raising `HTTPException(503)`:

```python
from mdb_engine.dependencies import get_graph_service_optional

@app.get("/search")
async def search(q: str, graph=Depends(get_graph_service_optional)):
    if graph:
        return await graph.hybrid_search(query=q, user_id="user1")
    return {"results": [], "method": "vector_only"}
```

**Action:** Replace custom `get_graph_service_from_request()` helpers with `Depends(get_graph_service_optional)`.

---

### Graph service now initializes in multi-app (`create_multi_app`)

**Before (0.7.x):** Graph service was only initialized through the `app_registration.py`
callback path (used by `create_app`). Apps mounted via `create_multi_app` never got
their graph service initialized — `engine.get_graph_service(slug)` always returned
`None`, `auto_extract` never fired, and the knowledge graph stayed empty.

**After (0.8.0):** The multi-app mount path now calls `initialize_graph_service` for
each app, in the correct order (shared services first, then graph, then memory). Graph
is fully functional in both `create_app` and `create_multi_app`.

No action needed — this is automatic.

---

### Lazy graph retry with memory re-injection

If graph service initialization fails at startup (e.g., transient network issue), the engine now retries lazily on the first `get_graph_service()` call. If the retry succeeds, the graph service is automatically re-injected into the memory service so `auto_extract` starts working.

No action needed — this is automatic.

---

### Auto-registered `/_mdb/graph/stats` endpoint

When `graph_config.enabled` is true (the default), the engine auto-registers a `/_mdb/graph/stats` endpoint. Delete your custom `/graph/stats` or `/api/graph/stats` routes.

---

### `PerfectBrain` container and `get_perfect_brain` dependency

`PerfectBrain` is a new class that auto-initializes all enabled advanced memory
components from the manifest. Access via dependency injection:

```python
from mdb_engine.dependencies import get_perfect_brain

@app.get("/vetoes")
async def vetoes(user_id: str, brain=Depends(get_perfect_brain)):
    return await brain.memory_veto.get_user_vetoes(user_id=user_id)
```

Available components: `shared_memory`, `memory_veto`, `prospective_memory`,
`cognitive_memory`, `timeline_service`, `memory_versioning`, `consolidator`,
`reflection_service`.

Or outside routes: `engine.get_perfect_brain(slug)`.

---

### `get_embedding_service` accepts `memory_config` shortcut

```python
from mdb_engine.embeddings.service import get_embedding_service

# Before — extract fields manually
svc = get_embedding_service(config={"default_embedding_model": manifest["memory_config"]["embedding_model"]})

# After — pass memory_config directly
svc = get_embedding_service(memory_config=manifest["memory_config"])
```

---

### `CognitiveMemoryService` exposes its services

```python
memory_service = engine.get_memory_service(slug)
memory_service.embedding_service  # EmbeddingService it uses
memory_service.llm_service        # LLMService it uses
```

---

### Lazy community detection

Community detection (`graphrag_config.community_detection`) now runs as a background task on first search access instead of blocking startup. The `CommunityService` is created lazily when the first search query needs it, and `maybe_rebuild()` is scheduled as a fire-and-forget background task.

---

### Index management hardened

Eight improvements to index management reliability and performance:

**Correctness:**

- **Error continuation in index loops:** When creating multiple indexes for a collection, a failure on one index no longer halts the rest. All indexes are attempted; errors are collected and the first is re-raised after all attempts complete.
- **FAILED index auto-recovery:** Search indexes in the `FAILED` terminal state are now automatically dropped and recreated instead of just logging an error. If recovery fails, falls back to the previous "manual intervention required" message.

**Performance:**

- **Removed redundant sleeps:** Eliminated `asyncio.sleep(0.5)` and `asyncio.sleep(1.0)` calls that added unnecessary startup latency. The `_handle_regular_index` verification poll loop (10s) was also removed — `create_index(wait_for_ready=True)` already handles readiness internally.
- **`AutoIndexManager` list_indexes cache:** Added a 5-second TTL cache for `list_indexes()` results to avoid redundant round trips when multiple auto-indexes trigger concurrently at startup.
- **Bounded `_query_counts`:** The `AutoIndexManager._query_counts` dict (which tracked query patterns to decide when to auto-create indexes) previously grew unboundedly. Now capped at 500 entries with lowest-count eviction, and patterns are removed after successful index creation.

**Cleanup:**

- **Removed deprecated `background=True`:** The `background` index option was deprecated in MongoDB 4.2. Removed from `AutoIndexManager._create_index_safely`.
- **Removed insert/delete collection fallback:** The `run_index_creation_for_collection` function previously fell back to inserting and deleting a dummy document to ensure a collection exists. Replaced with proper `OperationFailure` code-48 (`NamespaceExists`) handling.
- **Fixed `normalize_json_def` import:** `service_initialization.py` imported `normalize_json_def` from `..indexes.helpers` (which doesn't export it). Fixed to import from `..indexes.manager`.

---

### `cursor_to_list` handles sync and async cursors

`_async_compat.cursor_to_list` now works with both Motor async cursors and PyMongo sync cursors, eliminating `TypeError: object list can't be used in 'await' expression`.

---

### Startup summary log

Memory service initialization now logs a one-liner with all resolved config:

```
Memory service for 'my_app': model=openai/gpt-4o, embedding=text-embedding-3-small (1536d), cognitive=on, preset=smart
```

---

## Migration script

```bash
# 1. Update
pip install --upgrade mdb-engine

# 2. Simplify manifests
# Replace verbose memory_config with presets:
#   {"enabled": true, "provider": "cognitive", ...}  ->  true  or  "smart"
# Move perfect_brain inside memory_config
# Change "__kg" to "kg" in graph_config
# Remove embedding_model_dims (auto-detected)

# 3. Update Python code
# Replace:  scoped_db["__kg"]              -> scoped_db["kg"]
# Replace:  db["__kg"]                     -> db["kg"]
# Replace:  get_graph_service_from_request -> Depends(get_graph_service_optional)
# Replace:  manual EmbeddingService()      -> Depends(get_embedding_service)
# Replace:  manual LLMService()            -> Depends(get_llm_service)
# Replace:  manual PerfectBrain wiring     -> Depends(get_perfect_brain)
# Delete:   custom /graph/stats endpoints  (auto-registered as /_mdb/graph/stats)
# Delete:   manual graph/memory init in on_startup (engine handles it now)
# Delete:   hand-rolled const BASE / getCookie (extend mdb_base.html instead)
# Add:      llm_config to manifests if using infer=true

# 4. Update templates
# In your app's base.html:
#   Replace:  <!DOCTYPE html>...  with  {% extends "mdb_base.html" %}
#   Replace:  const BASE = '{{ base_path }}'  ->  (provided by framework)
#   Replace:  function getCookie(...)  ->  (provided by framework)
#   Put app-level JS (logout, etc.) in {% block base_js %}
#   Put page-level JS in {% block extra_js %}
#   Use MDB.BASE, MDB.AUTH_HUB, MDB.csrfToken() in JS

# 5. Verify
make format && make lint-local
python -m pytest tests/unit/ -x -q
```
