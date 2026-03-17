# What's New in mdb-engine 0.8.7

**Release date:** March 2026

This is the largest feature release in mdb-engine's history. Version 0.8.7
pushes manifest-driven development to the next level with **14 new manifest
features**, a complete **server-side rendering (SSR) engine**, and **3 new
CLI commands** -- all without requiring a single line of Python.

---

## Table of Contents

1. [Server-Side Rendering (SSR)](#1-server-side-rendering-ssr)
2. [Atomic Update Operators in Hooks](#2-atomic-update-operators-in-hooks)
3. [Conditional Hooks](#3-conditional-hooks)
4. [Hook Delete and HTTP Actions](#4-hook-delete-and-http-actions)
5. [Transactional Hook Mode](#5-transactional-hook-mode)
6. [Background Hooks with Retry](#6-background-hooks-with-retry)
7. [Per-Role Writable Fields](#7-per-role-writable-fields)
8. [Multi-Role Users and Role Hierarchies](#8-multi-role-users-and-role-hierarchies)
9. [Referential Integrity Constraints](#9-referential-integrity-constraints)
10. [Schema Validation Extensions](#10-schema-validation-extensions)
11. [Cascade Delete Policies](#11-cascade-delete-policies)
12. [Cache Directives](#12-cache-directives)
13. [Scheduled Jobs](#13-scheduled-jobs)
14. [CLI: Manifest Diff](#14-cli-manifest-diff)
15. [CLI: Manifest Dry-Run](#15-cli-manifest-dry-run)
16. [CLI: Contract Generation (TypeScript)](#16-cli-contract-generation-typescript)
17. [Template Resolver: `{{prev.*}}` Placeholders](#17-template-resolver-prev-placeholders)
18. [Upgrading from 0.8.6](#18-upgrading-from-086)

---

## 1. Server-Side Rendering (SSR)

The headline feature. Define routes in your manifest, write Jinja2 templates,
and get a fully server-rendered web app with SEO, pagination, sitemaps,
JSON-LD, caching, relations, and error pages. No Python. No JavaScript
frameworks. No build step.

### Quick Start

Create a `templates/` directory next to your manifest and add `ssr` config:

```
my_blog/
├── manifest.json
├── templates/
│   ├── index.html
│   ├── post.html
│   ├── 404.html       # optional custom error page
│   └── 500.html       # optional custom error page
├── public/
│   └── style.css      # static assets still work
```

```json
{
  "ssr": {
    "enabled": true,
    "site_name": "My Blog",
    "site_description": "A blog built with mdb-engine.",
    "base_url": "https://myblog.com",
    "routes": {
      "/": {
        "template": "index.html",
        "data": {
          "posts": {
            "collection": "posts",
            "scope": "published",
            "sort": { "created_at": -1 },
            "limit": 10,
            "populate": ["author"],
            "computed": ["comment_count"]
          }
        },
        "seo": { "title": "My Blog" },
        "cache": { "ttl": "5m" }
      },
      "/posts/{id}": {
        "template": "post.html",
        "data": {
          "post": {
            "collection": "posts",
            "id_param": "id",
            "populate": ["author"]
          },
          "comments": {
            "collection": "comments",
            "filter": { "post_id": "{{params.id}}" },
            "sort": { "created_at": 1 }
          }
        },
        "seo": {
          "title": "{{post.title}} — My Blog",
          "description": "{{post.excerpt}}",
          "og_type": "article",
          "json_ld": {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": "{{post.title}}",
            "datePublished": "{{post.created_at}}",
            "author": {
              "@type": "Person",
              "name": "{{post.author.name}}"
            }
          }
        },
        "cache": { "ttl": "1h", "stale_while_revalidate": "5m" }
      }
    }
  }
}
```

Run it:

```bash
mdb-engine serve manifest.json
```

### SSR Features

**Pagination** -- List routes automatically support `?page=N`. Templates
receive a `{name}_pagination` context:

```html
{% for p in posts %}
  <article><h2>{{ p.title }}</h2></article>
{% endfor %}

<nav>
  Page {{ posts_pagination.page }} of {{ posts_pagination.total_pages }}
  ({{ posts_pagination.total }} total)
</nav>
```

**Relations ($lookup)** -- Use `"populate": ["author"]` to join related
collections. Requires `relations` defined in the collection config.

**Computed fields** -- Use `"computed": ["comment_count"]` to include
aggregation-computed fields in templates.

**Cache-Control headers** -- Per-route caching:

```json
"cache": { "ttl": "5m", "stale_while_revalidate": "30s" }
```

**JSON-LD** -- Define structured data in the manifest:

```json
"seo": {
  "json_ld": {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    "headline": "{{post.title}}"
  }
}
```

Use in templates with `{{ seo.json_ld | safe }}`.

**Sitemap.xml** -- Auto-generated at `/sitemap.xml` from all public SSR
routes. Dynamic routes expand to include every document. Auth-gated routes
are excluded. Disable with `"sitemap": false`.

**Custom error pages** -- Place `404.html` and/or `500.html` in your
`templates/` directory for styled error pages.

**Policy enforcement** -- Collection-level `policy.read` filters, soft-delete
exclusion, and `default_projection` field hiding are all enforced in SSR
data fetching.

**Auth-gated routes** -- Set `"auth": true` on any route to require login:

```json
"/dashboard": {
  "template": "dashboard.html",
  "auth": true,
  "data": { "tasks": { "collection": "tasks" } }
}
```

**Framework templates** -- SSR templates can `{% extends "mdb_base.html" %}`
for consistent nav, CSRF, and JavaScript globals.

---

## 2. Atomic Update Operators in Hooks

Hook `update` actions now support MongoDB update operators (`$inc`, `$push`,
`$pull`, `$set`, `$unset`, `$addToSet`, `$min`, `$max`, etc.) directly.
Previously all updates were wrapped in `$set`.

### Before (0.8.6)

```json
"hooks": {
  "after_create": [{
    "action": "update",
    "collection": "posts",
    "filter": { "_id": "{{doc.post_id}}" },
    "update": { "comment_count": 1 }
  }]
}
```

This would run `$set: { comment_count: 1 }` -- always setting to 1.

### After (0.8.7)

```json
"hooks": {
  "after_create": [{
    "action": "update",
    "collection": "posts",
    "filter": { "_id": "{{doc.post_id}}" },
    "update": { "$inc": { "comment_count": 1 } }
  }]
}
```

This runs an atomic `$inc` -- properly incrementing the counter. If your
update object has no `$`-prefixed keys, it is still auto-wrapped in `$set`
for backward compatibility.

**Supported operators:** `$set`, `$unset`, `$inc`, `$push`, `$pull`,
`$addToSet`, `$pop`, `$min`, `$max`, `$mul`, `$rename`, `$currentDate`,
`$setOnInsert`, `$bit`.

---

## 3. Conditional Hooks

Hooks can now include an `if` condition. The hook only fires when the
condition matches the current document (and optionally the previous state).

```json
"hooks": {
  "after_update": [{
    "action": "insert",
    "collection": "notifications",
    "document": {
      "type": "post_published",
      "post_id": "{{doc._id}}",
      "timestamp": "$$NOW"
    },
    "if": {
      "doc.status": "published",
      "prev.status": { "$ne": "published" }
    }
  }]
}
```

This hook fires **only** when a post transitions from non-published to
published. The `prev.*` context contains the document state before the
update.

**Supported condition operators:** equality, `$ne`, `$in`, `$nin`,
`$exists`, `$gt`, `$lt`, `$gte`, `$lte`.

---

## 4. Hook Delete and HTTP Actions

Two new hook action types: `delete` and `http`.

### Delete action

Cascade-delete related documents:

```json
"after_delete": [{
  "action": "delete",
  "collection": "comments",
  "filter": { "post_id": "{{doc._id}}" }
}]
```

### HTTP action (webhooks)

Send HTTP requests to external services:

```json
"after_create": [{
  "action": "http",
  "url": "https://hooks.slack.com/services/...",
  "method": "POST",
  "body": {
    "text": "New post created",
    "post_id": "{{doc._id}}"
  },
  "headers": { "X-Api-Key": "{{env.SLACK_TOKEN}}" },
  "timeout": 10
}]
```

Webhook failures are caught and logged -- fire-and-forget behavior is
preserved. Requires `httpx` (`pip install httpx`).

---

## 5. Transactional Hook Mode

Opt into MongoDB multi-document transactions for hooks. If any hook fails,
the entire operation (primary write + all hooks) rolls back.

```json
"hooks": {
  "transactional": true,
  "after_create": [
    { "action": "insert", "collection": "audit_log", "document": { ... } },
    { "action": "update", "collection": "stats", "filter": { ... }, "update": { "$inc": { "total": 1 } } }
  ]
}
```

Requires a MongoDB replica set. Trades latency for correctness -- use for
audit-critical or financial collections.

---

## 6. Background Hooks with Retry

Offload hooks to background tasks with configurable retry:

```json
"after_create": [{
  "action": "http",
  "url": "https://api.sendgrid.com/v3/mail/send",
  "body": { ... },
  "background": true,
  "retry": {
    "attempts": 3,
    "backoff": "exponential"
  }
}]
```

Failed hooks are logged to a `_hook_failures` system collection.
Backoff options: `"exponential"`, `"linear"`, `"fixed"`.

---

## 7. Per-Role Writable Fields

`writable_fields` now accepts a role map in addition to a flat list:

### Before (flat list -- all users)

```json
"writable_fields": ["title", "body", "tags"]
```

### After (per-role map)

```json
"writable_fields": {
  "editor": ["title", "body", "tags", "status"],
  "reader": ["body"]
}
```

Editors can modify content fields. Readers can only edit comment body.
Admin users bypass the allowlist entirely. The flat list form still works
for backward compatibility.

---

## 8. Multi-Role Users and Role Hierarchies

Users can now have multiple roles and manifests can define role inheritance.

### Multi-role users

Users with a `roles` array field are now fully supported alongside the
single `role` string:

```json
{ "_id": "u1", "roles": ["editor", "moderator"] }
```

Both `role` (string) and `roles` (array) are checked.

### Role hierarchy

Define inheritance in the manifest:

```json
"auth": {
  "users": {
    "role_hierarchy": {
      "admin": ["editor", "moderator", "reader"],
      "editor": ["reader"],
      "moderator": ["reader"]
    }
  }
}
```

A user with `"role": "admin"` automatically inherits `editor`, `moderator`,
and `reader` permissions. Collection-level `write_roles: ["editor"]` now
means "editor or any role that includes editor."

**Programmatic access:**

```python
from mdb_engine.dependencies import get_effective_roles

roles = get_effective_roles(user, hierarchy)
# {"admin", "editor", "moderator", "reader"}
```

---

## 9. Referential Integrity Constraints

Validate foreign key references at write time with `x-references`:

```json
"schema": {
  "properties": {
    "post_id": {
      "type": "string",
      "x-references": { "collection": "posts", "field": "_id" }
    }
  }
}
```

On `POST /api/comments`, the engine verifies that `post_id` points to an
existing post. Returns `422` if the reference is invalid.

Works with `_id` fields (auto-converts to ObjectId) and any other field.

---

## 10. Schema Validation Extensions

### x-values-from

Validate that field values exist in a lookup collection:

```json
"tags": {
  "type": "array",
  "items": { "type": "string" },
  "x-values-from": { "collection": "categories", "field": "name" }
}
```

On write, every tag is checked against the `categories` collection. Invalid
values return `422` with a descriptive error.

---

## 11. Cascade Delete Policies

Declarative cascade behavior on delete:

```json
"posts": {
  "cascade": {
    "on_delete": [
      { "collection": "comments", "match_field": "post_id", "action": "delete" },
      { "collection": "reactions", "match_field": "post_id", "action": "delete" }
    ],
    "on_soft_delete": [
      { "collection": "comments", "match_field": "post_id", "action": "soft_delete" }
    ]
  }
}
```

When a post is deleted, all matching comments and reactions are
automatically cascade-deleted. `on_soft_delete` rules fire when the parent
is soft-deleted and set `deleted_at` on children.

---

## 12. Cache Directives

Declarative caching per collection scope:

```json
"posts": {
  "cache": {
    "scope:published": { "ttl": "5m", "stale_while_revalidate": "30s" },
    "default": { "ttl": "0" }
  }
}
```

The engine sets `Cache-Control` headers on API responses. The `published`
scope gets 5-minute caching with 30-second stale-while-revalidate; all
other queries bypass the cache.

---

## 13. Scheduled Jobs

Declarative cron-like jobs in the manifest:

```json
"jobs": {
  "archive_old_drafts": {
    "schedule": "0 3 * * *",
    "action": "update",
    "collection": "posts",
    "filter": {
      "status": "draft",
      "updated_at": { "$lt": "$$NOW_MINUS_90D" }
    },
    "update": { "$set": { "status": "archived" } }
  },
  "cleanup_expired_sessions": {
    "schedule": "1h",
    "action": "delete",
    "collection": "sessions",
    "filter": {
      "expires_at": { "$lt": "$$NOW" }
    }
  }
}
```

**Schedule formats:** cron expressions (`0 3 * * *`), shortcuts (`@hourly`,
`@daily`, `@weekly`), or interval strings (`30m`, `6h`, `1d`).

**Time variables:** `$$NOW`, `$$NOW_MINUS_90D`, `$$NOW_MINUS_24H`,
`$$NOW_MINUS_30M`.

---

## 14. CLI: Manifest Diff

Compare two manifest files and see what changed:

```bash
mdb-engine diff manifest.v1.json manifest.v2.json
```

Output:

```
+ collection "reactions" added (auto_crud)
~ posts.writable_fields: changed
~ posts.scopes.published: changed
- comments.hooks.after_delete: removed
⚠ BREAKING: posts.schema.required now includes "body" (existing docs may fail validation)
```

Breaking changes (removed collections, new required fields) are flagged
with warnings and cause a non-zero exit code -- ideal for CI pipelines.

---

## 15. CLI: Manifest Dry-Run

Print everything a manifest would generate without connecting to MongoDB:

```bash
mdb-engine dry-run manifest.json
```

Output:

```
=== Auth ===
  mode: app
  users_enabled: True
  registration: True

=== Routes (11) ===
  GET    /api/posts       [authenticated]
  GET    /api/posts/_count [authenticated]
  POST   /api/posts       [roles:editor]
  ...

=== Scopes ===
  posts: published, featured, mine

=== Indexes (3) ===
  posts.auto_unique_slug [unique] keys=slug
  posts.auto_ttl_expires_at [ttl] keys=expires_at

=== Hooks (2) ===
  posts.after_create -> insert audit_log
  posts.after_update -> insert notifications (conditional)
```

Also available as JSON: `mdb-engine dry-run manifest.json --json`.

---

## 16. CLI: Contract Generation (TypeScript)

Auto-generate a typed API client from your manifest:

```bash
mdb-engine codegen manifest.json --target typescript --out api-client.ts
```

Generates:

```typescript
export interface Posts {
  _id: string;
  created_at?: string;
  updated_at?: string;
  title: string;
  body?: string;
  status?: "draft" | "published" | "archived";
  tags?: string[];
}

export type PostsCreate = Omit<Posts, '_id' | 'created_at' | 'updated_at'>;

export async function listPostss(params?: {
  scope?: "published" | "featured" | "mine";
  limit?: number;
  skip?: number;
  sort?: string;
}): Promise<Posts[]> { ... }

export async function getPosts(id: string): Promise<Posts> { ... }
export async function createPosts(data: PostsCreate): Promise<{ data: { _id: string } }> { ... }
export async function updatePosts(id: string, data: Partial<PostsCreate>): Promise<...> { ... }
export async function deletePosts(id: string): Promise<...> { ... }
```

Eliminates hand-maintained `API_CONFIG` objects. Catches manifest/frontend
drift at build time.

Options: `--base-url /app1` for mounted apps.

---

## 17. Template Resolver: `{{prev.*}}` Placeholders

The template resolver now supports `{{prev.*}}` placeholders for accessing
the previous document state in `after_update` hooks:

```json
"after_update": [{
  "action": "insert",
  "collection": "change_log",
  "document": {
    "entity_id": "{{doc._id}}",
    "old_status": "{{prev.status}}",
    "new_status": "{{doc.status}}",
    "changed_at": "$$NOW"
  }
}]
```

Works in any hook context where the previous document is available (update
and replace operations).

---

## 18. Upgrading from 0.8.6

### Breaking Changes

**None.** All changes are backward compatible. Existing manifests work
without modification.

### Behavioral Changes

- **Hook error handling:** The hook executor now catches all `Exception`
  subclasses (previously only caught `PyMongoError`, `HTTPException`,
  `KeyError`, `ValueError`, `TypeError`). This means hooks are more
  reliably fire-and-forget.

- **`sanitize_body` call order:** User extraction (`_get_user_from_request`)
  now happens **before** `sanitize_body()` in create/update routes. This
  enables the per-role writable fields feature. The behavior is identical
  for flat-list `writable_fields`.

- **`require_role` hierarchy awareness:** The `require_role` dependency now
  checks `role_hierarchy` from `app.state` (if present) when evaluating
  roles. Without a hierarchy defined, behavior is identical to 0.8.6.

### New Dependencies

- **`httpx`** (optional) -- Required only for `http` hook actions. Install
  with `pip install httpx`.

- **`jinja2`** (optional) -- Required only for SSR. Already a transitive
  dependency of FastAPI if using `Jinja2Templates`.

### New CLI Commands

```bash
mdb-engine diff <old.json> <new.json>    # Compare manifests
mdb-engine dry-run <manifest.json>       # Print routes/indexes/hooks
mdb-engine codegen <manifest.json>       # Generate TypeScript client
```

### New Manifest Keys

| Key | Section | Description |
|-----|---------|-------------|
| `ssr` | top-level | Server-side rendering configuration |
| `ssr.enabled` | ssr | Enable SSR (requires `templates/` directory) |
| `ssr.site_name` | ssr | Site name for meta tags |
| `ssr.site_description` | ssr | Default meta description |
| `ssr.base_url` | ssr | Base URL for sitemap generation |
| `ssr.sitemap` | ssr | Auto-generate `/sitemap.xml` (default: true) |
| `ssr.routes` | ssr | Route definitions with template/data/seo/cache/auth |
| `jobs` | top-level | Scheduled job definitions |
| `hooks.transactional` | collection.hooks | Wrap writes + hooks in a transaction |
| `hooks[].if` | hook action | Conditional execution |
| `hooks[].background` | hook action | Offload to background task |
| `hooks[].retry` | hook action | Retry config for background hooks |
| `hooks[].url` | hook action | URL for http action |
| `hooks[].method` | hook action | HTTP method for http action |
| `hooks[].body` | hook action | Request body for http action |
| `hooks[].headers` | hook action | Request headers for http action |
| `hooks[].timeout` | hook action | Timeout for http action |
| `writable_fields` (object form) | collection | Per-role writable field map |
| `auth.users.role_hierarchy` | auth | Role inheritance map |
| `auth.users.roles_field` | auth | Field name for roles array |
| `cascade` | collection | Cascade delete/soft-delete policies |
| `cache` | collection | Cache-Control directives per scope |
| `x-references` | schema property | Foreign key reference validation |
| `x-values-from` | schema property | Lookup value validation |

### New Hook Actions

| Action | Description |
|--------|-------------|
| `delete` | Delete documents from another collection |
| `http` | Send HTTP request to external URL (webhook) |

### New Python API

```python
from mdb_engine.dependencies import get_effective_roles

roles = get_effective_roles(user, hierarchy)
```

```python
from mdb_engine.routing._ssr import mount_ssr_routes

mount_ssr_routes(app, templates_dir, ssr_config, collections_config)
```

```python
from mdb_engine.routing._hooks import (
    HookExecutor,
    TransactionalHookExecutor,
    BackgroundHookExecutor,
)
```

```python
from mdb_engine.jobs import ManifestJobScheduler

scheduler = ManifestJobScheduler(jobs_config, db)
scheduler.start()
```

```python
from mdb_engine.cli.commands.diff import compute_diff
from mdb_engine.cli.commands.dry_run import analyze_manifest
from mdb_engine.cli.commands.codegen import generate_typescript
```

---

## Test Coverage

This release adds **172 new unit tests** covering every feature:

| Feature | Test File | Tests |
|---------|-----------|-------|
| SSR (all features) | `tests/unit/test_ssr.py` | 39 |
| Hooks (atomic/conditional/delete/http) | `tests/unit/test_hooks.py` | 33 |
| Manifest diff | `tests/unit/test_cli_diff.py` | 12 |
| Manifest dry-run | `tests/unit/test_cli_dry_run.py` | 13 |
| TypeScript codegen | `tests/unit/test_cli_codegen.py` | 10 |
| Schema extensions | `tests/unit/test_schema_extensions.py` | 11 |
| Multi-role / hierarchy | `tests/unit/test_multi_role.py` | 13 |
| Cascade policies | `tests/unit/test_cascade.py` | 5 |
| Cache directives | `tests/unit/test_cache_directives.py` | 13 |
| Background hooks | `tests/unit/test_background_hooks.py` | 4 |
| Scheduled jobs | `tests/unit/test_scheduled_jobs.py` | 17 |
| Transactional hooks | `tests/unit/test_transactional_hooks.py` | 3 |
| Per-role writable fields | `tests/unit/test_auth_hardening.py` | +5 |

Full suite: **2861 passed, 0 failed.**
