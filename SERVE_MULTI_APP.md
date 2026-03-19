# `serve-multi` — Zero-Code Multi-App Deployments

**Added in mdb-engine 0.8.8**

Deploy multiple mdb-engine apps from a single CLI command.  No Python.  No
orchestration.  Just manifests, templates, and `mdb-engine serve-multi`.

```bash
mdb-engine serve-multi --apps-dir ./blogs/ --port 8000
```

```
mdb-engine serve-multi
  Apps dir : /home/deploy/blogs
  Apps     : 2 discovered
    /tech-blog  (tech/)
    /cooking-blog  (cooking/)
  Server   : http://0.0.0.0:8000
  Docs     : http://0.0.0.0:8000/docs
```

---

## Table of Contents

1. [Why This Exists](#why-this-exists)
2. [Quick Start](#quick-start)
3. [Two Input Modes](#two-input-modes)
4. [Architecture](#architecture)
5. [CLI Reference](#cli-reference)
6. [App Factory (`_serve_multi_app.py`)](#app-factory)
7. [SSR and Static Files](#ssr-and-static-files)
8. [Data Isolation](#data-isolation)
9. [Directory Layout Conventions](#directory-layout-conventions)
10. [Environment Variables](#environment-variables)
11. [Docker Deployment](#docker-deployment)
12. [Programmatic Alternative](#programmatic-alternative)
13. [How `serve` and `serve-multi` Relate](#how-serve-and-serve-multi-relate)
14. [Troubleshooting](#troubleshooting)

---

## Why This Exists

mdb-engine already had `mdb-engine serve` for single apps and a full
programmatic `create_multi_app()` API for multi-app deployments.  But
there was a gap: **if you wanted multiple apps without writing Python,
there was no zero-code path.**

`serve-multi` closes that gap.  It's the multi-app counterpart to `serve`:

| Command | Input | Output |
|---------|-------|--------|
| `mdb-engine serve manifest.json` | One manifest | One app |
| `mdb-engine serve-multi --apps-dir ./` | Directory of manifests | N apps, one server |
| `mdb-engine serve-multi --manifest multi.json` | Multi-app manifest | N apps, one server |

---

## Quick Start

### 1. Create app directories

```
my-platform/
  app-a/
    manifest.json
  app-b/
    manifest.json
```

Each manifest must have a unique `"slug"`.  The slug becomes the URL
path prefix: slug `app-a` is mounted at `/app-a`.

### 2. Run

```bash
pip install mdb-engine uvicorn

mdb-engine serve-multi --apps-dir ./my-platform/ --port 8000
```

### 3. Access

- `http://localhost:8000/app-a/api/...` — App A's CRUD endpoints
- `http://localhost:8000/app-b/api/...` — App B's CRUD endpoints
- `http://localhost:8000/docs` — Combined OpenAPI docs

That's it.  No `web.py`, no imports, no Docker compose (unless you want it).

---

## Two Input Modes

### Mode 1: Apps Directory (`--apps-dir`)

Point to a directory where each subdirectory contains a `manifest.json`.
The CLI scans for `*/manifest.json`, reads the slug, and auto-discovers apps.

```bash
mdb-engine serve-multi --apps-dir ./apps/
```

```
apps/
  storefront/
    manifest.json        # slug: storefront
    templates/
    public/
  admin-panel/
    manifest.json        # slug: admin-panel
  public/                # shared static files (optional)
    style.css
```

**Discovery rules:**
- Only immediate subdirectories are scanned (not recursive)
- Directories without `manifest.json` are silently skipped
- Directories with invalid JSON are silently skipped
- If a manifest has no `slug` field, the directory name is used as fallback
- Apps are sorted alphabetically by directory name

### Mode 2: Multi-App Manifest (`--manifest`)

Point to a JSON file that explicitly lists apps with their manifests and
path prefixes.  This is the same format used by
[`create_multi_app(multi_app_manifest=...)`](docs/MULTI_APP.md).

```bash
mdb-engine serve-multi --manifest multi_app_manifest.json
```

```json
{
  "schema_version": "2.0",
  "multi_app": {
    "enabled": true,
    "apps": [
      {
        "slug": "auth-hub",
        "manifest": "./apps/auth-hub/manifest.json",
        "path_prefix": "/auth-hub"
      },
      {
        "slug": "storefront",
        "manifest": "./apps/storefront/manifest.json",
        "path_prefix": "/store"
      }
    ],
    "shared_middleware": {
      "cors": true,
      "rate_limiting": true,
      "health_checks": true
    }
  }
}
```

**When to use which:**

| Criteria | `--apps-dir` | `--manifest` |
|----------|-------------|-------------|
| Simplest setup | Yes | No |
| Custom path prefixes | No (derived from slug) | Yes |
| Shared middleware config | No | Yes |
| SSO / shared auth | No | Yes |
| Dynamic app addition | Just add a directory | Edit the manifest |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI Layer                             │
│                                                         │
│  mdb-engine serve-multi                                 │
│    │                                                    │
│    ├─ Validates inputs (--apps-dir or --manifest)       │
│    ├─ Discovers apps and prints startup banner          │
│    ├─ Sets env vars (_MDB_SERVE_MULTI_MODE, etc.)       │
│    └─ Starts uvicorn → _serve_multi_app:app             │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                    App Factory                           │
│                                                         │
│  _serve_multi_app.py                                    │
│    │                                                    │
│    ├─ Reads env vars                                    │
│    ├─ engine = MongoDBEngine(...)                        │
│    ├─ app = engine.create_multi_app(...)                 │
│    │    │                                               │
│    │    ├─ Creates parent FastAPI app                    │
│    │    ├─ Registers /health, /docs, middleware          │
│    │    └─ Mounts child apps at /{slug}                  │
│    │         ├─ Auto-CRUD routes                        │
│    │         ├─ Auth routes                             │
│    │         └─ WebSocket routes                        │
│    │                                                    │
│    └─ Post-process: mount SSR + static files            │
│         ├─ mount_ssr_routes(child, ..., base_path=...)   │
│         ├─ Mount child public/ directories               │
│         └─ Mount shared public/ directory                │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                    Engine Layer                           │
│                                                         │
│  MongoDBEngine.create_multi_app()                        │
│    Already handles:                                      │
│    ✓ Manifest validation                                 │
│    ✓ Path prefix validation (conflicts, reserved paths)  │
│    ✓ Child app creation via create_app(is_sub_app=True)  │
│    ✓ CRUD route registration                             │
│    ✓ Auth (app-level or shared/SSO)                      │
│    ✓ Middleware injection (AppContext, CORS, CSRF)        │
│    ✓ WebSocket route registration                        │
│    ✓ Memory/graph/embedding service initialization       │
│    ✓ Redirect URL rewriting                              │
│                                                         │
│  The CLI adds on top:                                    │
│    ✓ SSR template route registration                     │
│    ✓ Static file serving (public/ directories)           │
│    ✓ Shared static files (parent-level public/)          │
└─────────────────────────────────────────────────────────┘
```

### Why SSR is in the factory, not the engine

`create_multi_app()` is a general-purpose engine method used by both
programmatic deployments and the CLI.  SSR (Jinja2 templates) and
static file serving are **conventions of the zero-code CLI layer** — they
assume a filesystem layout (`templates/`, `public/`) that programmatic
apps may not follow.

The same split exists in single-app mode: `create_app()` returns a
FastAPI app with CRUD/auth/middleware, and `_serve_app.py` adds SSR
and static files on top.

---

## CLI Reference

```
Usage: mdb-engine serve-multi [OPTIONS]

  Start a multi-app server from an apps directory or manifest.

  Provide exactly one of --apps-dir or --manifest.

Options:
  -d, --apps-dir DIRECTORY  Directory containing app subdirectories,
                            each with a manifest.json
  -m, --manifest FILE       Path to a multi_app_manifest.json file
  --host TEXT               Bind host  [default: 0.0.0.0]
  -p, --port INTEGER        Bind port  [default: 8000]
  --reload                  Enable auto-reload (development)
  --mongo-uri TEXT          MongoDB connection URI
  --db-name TEXT            Database name
  --title TEXT              Parent app title  [default: Multi-App API]
  --help                    Show this message and exit.
```

### Examples

```bash
# Auto-discover from directory
mdb-engine serve-multi --apps-dir ./apps/

# From a multi-app manifest
mdb-engine serve-multi --manifest multi_app_manifest.json

# Full options
mdb-engine serve-multi \
  --apps-dir ./apps/ \
  --host 127.0.0.1 \
  --port 3000 \
  --reload \
  --mongo-uri "mongodb+srv://user:pass@cluster.mongodb.net" \
  --db-name my_platform \
  --title "My Platform"
```

---

## App Factory

The app factory is the module uvicorn imports to get the ASGI `app` object.
It lives at `mdb_engine.cli._serve_multi_app` and follows the same pattern
as the single-app factory `mdb_engine.cli._serve_app`.

### Lifecycle

```
1. Module import (uvicorn loads _serve_multi_app.py)
   ├─ Read _MDB_SERVE_MULTI_MODE and _MDB_SERVE_MULTI_PATH from env
   ├─ Create MongoDBEngine instance
   ├─ Call engine.create_multi_app(apps_dir=... or multi_app_manifest=...)
   │    └─ Returns parent FastAPI app with all children mounted
   └─ _mount_ssr_and_static()
        ├─ For each mounted child app:
        │    ├─ Find manifest directory on disk
        │    ├─ If templates/ exists + ssr.enabled → mount_ssr_routes()
        │    └─ If public/ exists → mount StaticFiles
        └─ If shared public/ exists → mount on parent app

2. Uvicorn lifespan startup (async)
   ├─ engine.initialize() — connects to MongoDB
   ├─ Creates child apps, imports route modules
   ├─ Initializes auth, memory, graph services
   └─ Calls on_startup callbacks

3. Request serving
   ├─ Parent app routes requests to child apps by path prefix
   ├─ AppContextMiddleware sets request.state on each request
   └─ SSR routes render Jinja2 templates with data from MongoDB
```

### Using the factory directly (Docker)

You can bypass the CLI and point uvicorn at the factory directly.
This is how the Docker Compose examples work:

```yaml
environment:
  - _MDB_SERVE_MULTI_MODE=apps_dir
  - _MDB_SERVE_MULTI_PATH=/app/blogs
  - _MDB_SERVE_MULTI_TITLE=My Platform
  - MONGODB_URI=mongodb://mongo:27017
command: >
  uvicorn mdb_engine.cli._serve_multi_app:app
    --host 0.0.0.0 --port 8000 --reload
```

---

## SSR and Static Files

### How SSR works in multi-app mode

When a child app's manifest has `"ssr": { "enabled": true, ... }` and a
`templates/` directory exists next to the manifest, the factory
automatically registers SSR routes on that child app.

Each child app gets its own Jinja2 Environment with a `base_path` global
set to the child's path prefix (e.g., `/tech-blog`).  Templates use this
to generate correct links:

```html
<!-- Works in both single-app (base_path="") and multi-app (base_path="/tech-blog") -->
<a href="{{ base_path }}/">Home</a>
<a href="{{ base_path }}/posts/{{ post._id }}">{{ post.title }}</a>
<link rel="stylesheet" href="{{ base_path }}/public/style.css">
```

### Static files

Three levels of static file serving:

| Level | Path | Source |
|-------|------|--------|
| Per-app | `/{slug}/public/*` | `{app_dir}/public/` |
| Shared | `/public/*` | `{apps_dir}/public/` |
| Index fallback | `/{slug}/` | `{app_dir}/public/index.html` (if no SSR `/` route) |

### Template variables available in SSR

| Variable | Type | Description |
|----------|------|-------------|
| `base_path` | `str` | Path prefix for this app (e.g., `/tech-blog` or `""`) |
| `seo` | `dict` | Resolved SEO metadata from manifest |
| `user` | `dict\|None` | Current authenticated user |
| `request` | `Request` | FastAPI/Starlette request object |
| *data keys* | varies | Data sources defined in the route's `data` config |

---

## Data Isolation

Each app gets its own `ScopedDB` keyed by slug.  MongoDB collection names
are automatically prefixed with the slug:

| App slug | Collection in manifest | Actual MongoDB collection |
|----------|----------------------|--------------------------|
| `tech-blog` | `posts` | `tech-blog.posts` |
| `tech-blog` | `comments` | `tech-blog.comments` |
| `cooking-blog` | `posts` | `cooking-blog.posts` |
| `cooking-blog` | `comments` | `cooking-blog.comments` |

This means:
- Apps can use the same collection names in their manifests
- Data never leaks between apps
- All apps share one MongoDB database (configurable via `--db-name`)
- Each app has its own user pool (unless using shared auth / SSO)

---

## Directory Layout Conventions

### Apps directory mode

```
my-platform/                    # --apps-dir points here
  blog/
    manifest.json               # Required (must have "slug")
    templates/                  # Optional (SSR templates)
      index.html
      article.html
      404.html                  # Optional custom error page
    public/                     # Optional (static files)
      style.css
      favicon.ico
  admin/
    manifest.json
    templates/
      dashboard.html
  public/                       # Optional (shared static files)
    shared.css                  # Served at /public/shared.css
```

### Multi-app manifest mode

```
deploy/
  multi_app_manifest.json       # --manifest points here
  apps/
    auth-hub/
      manifest.json
      web.py                    # Optional (custom routes)
      templates/
    storefront/
      manifest.json
      templates/
```

---

## Environment Variables

### Set by the CLI (internal)

| Variable | Values | Description |
|----------|--------|-------------|
| `_MDB_SERVE_MULTI_MODE` | `apps_dir` or `manifest` | Which input mode |
| `_MDB_SERVE_MULTI_PATH` | Absolute path | Resolved path to apps dir or manifest file |
| `_MDB_SERVE_MULTI_TITLE` | String | Title for the parent FastAPI app |

### Passed through to the engine

| Variable | CLI flag | Description |
|----------|----------|-------------|
| `MONGODB_URI` | `--mongo-uri` | MongoDB connection string |
| `MDB_DB_NAME` | `--db-name` | Database name |
| `MDB_JWT_SECRET` | *(env only)* | JWT signing secret |
| `MDB_ENGINE_MASTER_KEY` | *(env only)* | Master API key |

---

## Docker Deployment

### Using the CLI

```yaml
services:
  app:
    image: python:3.11-slim
    volumes:
      - ./apps:/app/apps
    command: >
      bash -c "pip install mdb-engine uvicorn jinja2 -q &&
               mdb-engine serve-multi --apps-dir /app/apps --port 8000"
    environment:
      - MONGODB_URI=mongodb://mongo:27017
      - MDB_DB_NAME=my_platform
    ports:
      - "8000:8000"
    depends_on:
      mongo:
        condition: service_healthy

  mongo:
    image: mongodb/mongodb-atlas-local:8.0
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### Using the factory directly

```yaml
services:
  app:
    image: python:3.11-slim
    volumes:
      - ./apps:/app/apps
    command: >
      bash -c "pip install mdb-engine uvicorn jinja2 -q &&
               uvicorn mdb_engine.cli._serve_multi_app:app
                 --host 0.0.0.0 --port 8000 --reload"
    environment:
      - _MDB_SERVE_MULTI_MODE=apps_dir
      - _MDB_SERVE_MULTI_PATH=/app/apps
      - _MDB_SERVE_MULTI_TITLE=My Platform
      - MONGODB_URI=mongodb://mongo:27017
      - MDB_DB_NAME=my_platform
    ports:
      - "8000:8000"
```

---

## Programmatic Alternative

`serve-multi` is the zero-code path.  If you need custom startup logic,
middleware, or route modules, use `create_multi_app()` directly:

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_platform",
)

# From an apps directory (same as --apps-dir)
app = engine.create_multi_app(apps_dir=Path("./apps"))

# From a multi-app manifest (same as --manifest)
app = engine.create_multi_app(multi_app_manifest=Path("multi_app_manifest.json"))

# Fully programmatic
app = engine.create_multi_app(
    apps=[
        {"slug": "blog", "manifest": Path("./blog/manifest.json")},
        {"slug": "admin", "manifest": Path("./admin/manifest.json"), "path_prefix": "/admin-panel"},
    ],
    title="My Platform",
)
```

See [docs/MULTI_APP.md](docs/MULTI_APP.md) for the full programmatic API,
SSO configuration, shared user pools, and child app route patterns.

---

## How `serve` and `serve-multi` Relate

```
mdb-engine serve manifest.json
  │
  ├─ Sets _MDB_SERVE_MANIFEST env var
  ├─ Starts uvicorn → _serve_app.py
  │    ├─ engine.create_app(slug, manifest)
  │    ├─ mount_ssr_routes(app, templates_dir, ...)
  │    └─ Mount public/ static files
  └─ One app, one slug, one process

mdb-engine serve-multi --apps-dir ./apps/
  │
  ├─ Sets _MDB_SERVE_MULTI_MODE + _MDB_SERVE_MULTI_PATH env vars
  ├─ Starts uvicorn → _serve_multi_app.py
  │    ├─ engine.create_multi_app(apps_dir=...)
  │    │    └─ For each manifest: engine.create_app(is_sub_app=True)
  │    └─ For each child: mount SSR + static files
  └─ N apps, N slugs, one process
```

Key differences:

| | `serve` | `serve-multi` |
|---|---------|--------------|
| Input | One `manifest.json` | Directory or multi-app manifest |
| Apps | 1 | N |
| URL structure | `/api/...` | `/{slug}/api/...` |
| SSR base_path | `""` (empty) | `"/{slug}"` |
| Shared static files | No | Yes (`{apps_dir}/public/`) |
| Auth | Per-app only | Per-app or shared (SSO) |

---

## Troubleshooting

### "No apps found"

The directory exists but no subdirectory contains a `manifest.json`.
Check that your manifests are at `{apps_dir}/{name}/manifest.json`,
not nested deeper.

### "uvicorn is required"

Install uvicorn: `pip install uvicorn`.  It's not a core dependency of
mdb-engine because the engine can be used without a web server.

### SSR templates not rendering

- Ensure `jinja2` is installed: `pip install jinja2`
- Ensure the manifest has `"ssr": { "enabled": true, "routes": { ... } }`
- Ensure a `templates/` directory exists next to the manifest
- Check the template file names match the `"template"` values in routes

### Links broken in multi-app SSR

Use `{{ base_path }}` in all internal links:

```html
<!-- Correct -->
<a href="{{ base_path }}/posts/{{ post._id }}">Read more</a>
<link rel="stylesheet" href="{{ base_path }}/public/style.css">

<!-- Wrong (will point to root, not child app) -->
<a href="/posts/{{ post._id }}">Read more</a>
```

### Static files not loading

- Per-app static files: place in `{app_dir}/public/` → served at `/{slug}/public/*`
- Shared static files: place in `{apps_dir}/public/` → served at `/public/*`

### Apps mounted at wrong paths

In `--apps-dir` mode, the path prefix is `/{slug}` where slug comes from
the manifest's `"slug"` field.  To control the path prefix explicitly,
use `--manifest` mode with `"path_prefix"` in each app entry.

---

## Source Files

| File | Purpose |
|------|---------|
| [`mdb_engine/cli/commands/serve_multi.py`](mdb_engine/cli/commands/serve_multi.py) | CLI command (Click) |
| [`mdb_engine/cli/_serve_multi_app.py`](mdb_engine/cli/_serve_multi_app.py) | App factory (loaded by uvicorn) |
| [`mdb_engine/core/multi_app.py`](mdb_engine/core/multi_app.py) | `create_multi_app()` engine method |
| [`mdb_engine/routing/_ssr.py`](mdb_engine/routing/_ssr.py) | SSR route registration |
| [`tests/unit/test_cli_serve_multi.py`](tests/unit/test_cli_serve_multi.py) | CLI tests (13 cases) |
| [`examples/advanced/multi-tenant-blog/`](examples/advanced/multi-tenant-blog/) | Working example |

---

## Example: Multi-Tenant Blog

See [`examples/advanced/multi-tenant-blog/`](examples/advanced/multi-tenant-blog/)
for a complete working example — two independent blogs (Tech + Cooking)
with SSR, comments, hooks, cascade deletes, and shared styling.

```bash
cd examples/advanced/multi-tenant-blog
mdb-engine serve-multi --apps-dir ./blogs/ --port 8000
```

| URL | What |
|-----|------|
| `http://localhost:8000/tech-blog/` | Tech Blog (server-rendered) |
| `http://localhost:8000/cooking-blog/` | Cooking Blog (server-rendered) |
| `http://localhost:8000/tech-blog/api/posts` | Tech Blog REST API |
| `http://localhost:8000/cooking-blog/api/posts` | Cooking Blog REST API |
| `http://localhost:8000/docs` | OpenAPI docs (all apps) |
