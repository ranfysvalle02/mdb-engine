# Multi-Tenant Blog

A **zero-code** multi-tenant blog platform powered by `mdb-engine serve-multi`.
Two fully independent blogs — **Tech Blog** and **Cooking Blog** — share a single
server process, MongoDB instance, and stylesheet, but maintain completely isolated
data, auth, and API routes.

## What This Demonstrates

- **`serve-multi --apps-dir`** — auto-discovers `manifest.json` files in subdirectories
- **Tenant isolation** — each blog has its own scoped database, users, and CRUD APIs
- **SSR** — server-side rendered HTML with Jinja2 templates (no frontend build step)
- **Declarative everything** — posts, comments, hooks, cascade deletes, indexes — all in JSON
- **Shared static assets** — a single `public/style.css` served to all tenants

## Directory Structure

```
blogs/
  tech/
    manifest.json           # slug: tech-blog
    templates/
      index.html            # list posts
      article.html          # single post + comments
  cooking/
    manifest.json           # slug: cooking-blog
    templates/
      index.html
      article.html
  public/
    style.css               # shared stylesheet
```

## Quick Start

### With the CLI (recommended)

```bash
pip install mdb-engine uvicorn jinja2

mdb-engine serve-multi --apps-dir ./blogs/ --port 8000
```

### With Docker Compose

```bash
docker compose up
```

### Access

| URL | Description |
|-----|-------------|
| `http://localhost:8000/tech-blog/` | Tech Blog (SSR home) |
| `http://localhost:8000/cooking-blog/` | Cooking Blog (SSR home) |
| `http://localhost:8000/docs` | OpenAPI / Swagger docs |
| `http://localhost:8000/tech-blog/api/posts` | Tech Blog posts API |
| `http://localhost:8000/cooking-blog/api/posts` | Cooking Blog posts API |

### Demo Accounts

Each blog has its own user pool:

| Blog | Email | Password | Role |
|------|-------|----------|------|
| Tech | `admin@tech.example.com` | `admin123` | admin |
| Tech | `writer@tech.example.com` | `writer123` | editor |
| Cooking | `admin@cooking.example.com` | `admin123` | admin |
| Cooking | `chef@cooking.example.com` | `chef123` | editor |

## How It Works

`mdb-engine serve-multi --apps-dir ./blogs/` does the following:

1. Scans `./blogs/` for subdirectories containing `manifest.json`
2. Reads each manifest's `slug` to determine the path prefix (`/tech-blog/`, `/cooking-blog/`)
3. Calls `engine.create_multi_app(apps_dir=...)` to mount each app as a child FastAPI application
4. Starts uvicorn — all blogs share a single process and MongoDB connection pool

Data isolation is automatic: each app gets its own `ScopedDB` keyed by slug, so
`tech-blog.posts` and `cooking-blog.posts` are separate MongoDB collections.

## Authorization — Unified AuthZ with OSO

This example demonstrates mdb-engine's **unified authorization architecture**.
Authorization flows through a single pluggable `AuthorizationProvider` —
here powered by [OSO](https://www.osohq.com/).

### How the pieces fit together

1. **Manifest DSL** — each blog's `manifest.json` declares roles and
   collection-level auth (`write_roles`, `public_read`, `role_hierarchy`)
   in plain JSON.

2. **Policy Compiler** — at startup the engine reads those declarations and
   compiles them into OSO facts (e.g. `has_role(User:"admin@tech", "admin",
   Post:"posts")`).  This happens automatically — no manual wiring.

3. **Polar Policy** (`blogs/main.polar`) — defines *what roles mean*.  The
   `Post` resource declares `"write" if "editor"`, `"editor" if "admin"`,
   etc.  Combined with the compiled facts, OSO can answer any
   `authorize(actor, action, resource)` question.

4. **auto-CRUD** — every generated API endpoint delegates its permission
   gate to `authz_provider.check()`.  MQL data-scoping (`owner_field`,
   `policy`, `scopes`) still runs after the boolean gate, giving you both
   coarse-grained *access control* and fine-grained *data filtering* in a
   single request.

### Swapping providers

To switch from OSO to Casbin, change `"provider": "oso"` to
`"provider": "casbin"` (or remove the `policy` block entirely — Casbin is
the default).  The manifest DSL, the auto-CRUD routes, and the data-scoping
layer all stay the same.

## Adding a New Blog

1. Create a new directory under `blogs/` (e.g., `blogs/travel/`)
2. Add a `manifest.json` with a unique `slug`
3. Add `templates/` with `index.html` and `article.html`
4. Restart the server — the new blog is auto-discovered

No code changes required.
