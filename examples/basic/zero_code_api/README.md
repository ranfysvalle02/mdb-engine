# Zero-Code Blog

A production-grade blog API from a single JSON file. No Python code required.

```
zero_code_api/
├── manifest.json        # the entire API — auth, CRUD, hooks, relations, TTL
├── public/
│   └── index.html       # blog explorer (auto-served at /)
├── docker-compose.yml   # one-command setup
└── README.md
```

## What the manifest defines

| Collection   | Features |
|--------------|----------|
| `posts`      | Schema validation, soft delete, defaults, scopes, pipelines, timestamps, **owner_field**, **immutable_fields**, **hooks** (audit trail), **computed** (comment count) |
| `comments`   | Authenticated create, admin-only moderation, **owner_field**, **immutable_fields**, **relations** (`?populate=post`), **hooks** |
| `categories` | Schema with **`x-unique`** constraint on name (409 on duplicates) |
| `audit_log`  | Read-only, no timestamps, **TTL** (90-day auto-expiry), auto-populated via hooks |

Auth is configured with `auth.users`:

- **Secure-by-default** — because `auth.users.enabled` is `true`, every collection endpoint requires authentication automatically unless `public_read` is set.
- **Public reads** — `posts` and `comments` have `"public_read": true` so anyone can browse without signing in. Writes still require auth.
- Admin seeded via `{{env.ADMIN_EMAIL}}` / `{{env.ADMIN_PASSWORD}}` (no plaintext secrets in the manifest)
- Self-registered users get role `reader` (`registration_role`)
- Post/category/comment mutation requires admin role (`write_roles`)

## Run with Docker

```bash
docker compose up
```

Override admin credentials with env vars:

```bash
ADMIN_EMAIL=me@corp.com ADMIN_PASSWORD=supersecret docker compose up
```

Open **http://localhost:8000** — the blog explorer is live.
Swagger docs at **http://localhost:8000/docs**.

## Run locally

```bash
pip install mdb-engine uvicorn
ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=admin123 mdb-engine serve manifest.json --reload
```

Or provision an admin without any secrets in files or env vars:

```bash
mdb-engine add-user manifest.json --email admin@example.com --role admin
# prompts for password interactively
```

> **How does the frontend work?** `mdb-engine serve` auto-detects the `public/`
> directory next to the manifest and serves `public/index.html` at `/`.
> No CORS, no extra config — same origin.

## What the frontend shows

- **Read mode** — clean public blog feed with computed comment counts
- **Auth UX** — register/login/logout in-app (guest + admin)
- **Comment gate** — guests must authenticate before commenting
- **Comment moderation** — admin approves pending comments
- **Admin authoring** — write/manage views only visible to admin
- **Soft delete** — admin trash + restore workflow for posts
- **Audit log** — admin panel shows all events auto-populated by hooks

---

## Power Features Showcase

Every feature below is configured entirely in `manifest.json`. Zero Python.

### Secure-by-default — auth enforced automatically

When `auth.users.enabled` is `true` in the manifest, **every collection endpoint
requires authentication** — reads, writes, deletes, everything. You don't need
to add `"auth": {"required": true}` to each collection. The engine enforces it.

Per-collection `auth` config can only *tighten* access (e.g. `write_roles: ["admin"]`),
never loosen it. There is no way to make a collection publicly accessible when
app-level auth is on. This prevents accidental data exposure in zero-code apps.

Additional hardening (all automatic when `auth.users.enabled` is true):

- **Users collection blocked** -- the auth users collection (default `"users"`) is
  never exposed via auto-CRUD. Users are managed exclusively through `/auth/*` endpoints.
- **Protected fields** -- `role`, `roles`, `password`, `password_hash`, and `is_admin`
  are automatically immutable on every collection. No client can escalate privileges
  via PATCH/PUT.
- **`writable_fields` allowlist** -- explicitly declare which fields clients can write.
  Everything else is silently stripped. Allowlist > denylist.
- **`public_read`** -- per-collection opt-in to anonymous read access. Writes still
  require auth. Perfect for blogs, docs, catalogs.
- **Login rate limiting** -- `max_login_attempts` (default 5) and `login_lockout_seconds`
  (default 15 minutes) protect against brute-force attacks. Returns 429 with `Retry-After`.
- **Restore policy enforced** -- soft-delete `_restore` endpoints respect ownership
  policies. Non-owners cannot restore documents they don't own.
- **Bulk insert hooks** -- `after_create` hooks fire for every document in a bulk insert,
  ensuring audit trails have no blind spots.

### Computed fields — `?computed=comment_count`

Posts can include a live comment count computed via aggregation at read time:

```bash
curl -s "http://localhost:8000/api/posts?scope=published&computed=comment_count"
```

```json
{
  "data": [{
    "_id": "...",
    "title": "Hello World",
    "comment_count": 3,
    ...
  }]
}
```

The manifest declares the pipeline that computes this:

```json
"computed": {
  "comment_count": {
    "pipeline": [
      { "$lookup": { "from": "comments", "let": { "pid": { "$toString": "$_id" } },
        "pipeline": [{ "$match": { "$expr": { "$eq": ["$post_id", "$$pid"] }}}], "as": "_comments" }},
      { "$addFields": { "comment_count": { "$size": "$_comments" } } },
      { "$project": { "_comments": 0 } }
    ]
  }
}
```

### Relations — `?populate=post`

Comments can include the full post they belong to:

```bash
curl -s "http://localhost:8000/api/comments?scope=approved&populate=post"
```

```json
{
  "data": [{
    "_id": "...",
    "body": "Great post!",
    "post": {
      "_id": "...",
      "title": "Hello World",
      "body": "..."
    }
  }]
}
```

Declared in the manifest as:

```json
"relations": {
  "post": { "from": "posts", "local_field": "post_id", "foreign_field": "_id", "single": true }
}
```

### Hooks — automatic audit trail

Every create, update, and delete on posts and comments fires a hook that inserts
a document into `audit_log`. No application code, no event bus — just config:

```json
"hooks": {
  "after_create": [{
    "action": "insert", "collection": "audit_log",
    "document": {
      "event": "post_created",
      "entity_id": "{{doc._id}}",
      "actor": "{{user.email}}",
      "timestamp": "$$NOW"
    }
  }]
}
```

```bash
# View the audit trail (admin only in the UI, but API is public read)
curl -s "http://localhost:8000/api/audit_log?sort=-timestamp&limit=5"
```

### Unique constraints — `x-unique`

Category names are unique. The engine auto-creates a unique index at startup:

```bash
curl -s -X POST http://localhost:8000/api/categories \
  -H "Content-Type: application/json" -d '{"name":"Tech"}'
# -> 201 Created

curl -s -X POST http://localhost:8000/api/categories \
  -H "Content-Type: application/json" -d '{"name":"Tech"}'
# -> 409 {"detail": "Duplicate value for unique field(s): name"}
```

Declared in the schema with a single extension keyword:

```json
"name": { "type": "string", "x-unique": true }
```

### Owner field — automatic ownership

Posts have `"owner_field": "author_id"`. The engine auto-injects the creator's
user ID as a default and generates write/delete policies:

```bash
# Login as admin and create a post (use your ADMIN_EMAIL / ADMIN_PASSWORD)
curl -s -c cookies -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}'

curl -s -b cookies -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{"title":"My Post","status":"published"}'
# -> author_id is auto-set to the admin's user ID
```

Admin bypasses ownership checks. Non-admin users can only modify documents they own.

### Immutable fields — tamper-proof

`"immutable_fields": ["author_id"]` on posts means even if a client sends
`author_id` in a PATCH body, the engine silently strips it:

```bash
curl -s -b cookies -X PATCH http://localhost:8000/api/posts/{id} \
  -H "Content-Type: application/json" \
  -d '{"author_id":"hacked","title":"New Title"}'
# -> title updates, author_id unchanged
```

### TTL — auto-expiring documents

The `audit_log` collection has `"ttl": {"field": "timestamp", "expire_after": "90d"}`.
MongoDB automatically deletes documents older than 90 days. No cron jobs, no cleanup code.

### Env-var admin seeding — `{{env.*}}` in demo_users

No more plaintext passwords in `manifest.json`. Admin credentials are resolved
from environment variables at startup:

```json
"demo_users": [
  {
    "email": "{{env.ADMIN_EMAIL}}",
    "password": "{{env.ADMIN_PASSWORD}}",
    "role": "admin"
  }
]
```

Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in your environment (or `.env` file for
Docker Compose). The engine resolves `{{env.*}}` placeholders before seeding.

### Registration role control — `registration_role`

Self-registered users default to a configurable role instead of hardcoded `"guest"`:

```json
"users": {
  "allow_registration": true,
  "registration_role": "reader"
}
```

### Invite-only registration — `allow_registration: "invite_only"`

Restrict registration to holders of a valid invite code:

```json
"users": {
  "allow_registration": "invite_only",
  "invite_codes": ["{{env.INVITE_CODE}}", "beta-tester-2025"]
}
```

Clients include `"invite_code"` in the registration body. Codes support `{{env.*}}`
placeholders so they can be rotated without changing the manifest.

### CLI admin provisioning — `mdb-engine add-user`

Create users directly from the command line, no secrets in files:

```bash
mdb-engine add-user manifest.json --email admin@corp.com --role admin
# Password: ********
# Confirm: ********
# User admin@corp.com created with role 'admin'.
```

---

## Try with curl

```bash
# Login as admin first (all endpoints require auth)
# (use your ADMIN_EMAIL / ADMIN_PASSWORD values)
curl -s -c cookies -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}'

# Now all authenticated endpoints work:

# List published posts (with computed comment count)
curl -s -b cookies "http://localhost:8000/api/posts?scope=published&computed=comment_count"

# Get a single post
curl -s -b cookies "http://localhost:8000/api/posts/{id}"

# Pipeline: posts by author
curl -s -b cookies "http://localhost:8000/api/posts/_agg/by_author"

# List approved comments (with populated post)
curl -s -b cookies "http://localhost:8000/api/comments?scope=approved&populate=post"

# Create a post (admin only)
curl -s -b cookies -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{"title":"Hello World","body":"# Markdown works","status":"published"}'

# Register a reader (registration_role: "reader")
curl -s -c guest -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"guest@example.com","password":"guest123"}'

# Post a comment as guest (auto-defaults: approved=false, author={{user.email}})
curl -s -b guest -X POST http://localhost:8000/api/comments \
  -H "Content-Type: application/json" \
  -d '{"post_id":"{id}","body":"Great post!"}'

# Approve comment as admin
curl -s -b cookies -X PATCH http://localhost:8000/api/comments/{comment_id} \
  -H "Content-Type: application/json" -d '{"approved":true}'

# View audit log (requires auth — use admin cookies)
curl -s -b cookies "http://localhost:8000/api/audit_log?sort=-timestamp"

# Read-only collection — POST blocked
curl -s -b cookies -X POST http://localhost:8000/api/audit_log \
  -H "Content-Type: application/json" -d '{"event":"nope"}'
# -> 405 Method Not Allowed

# Unauthenticated access is blocked (secure-by-default)
curl -s "http://localhost:8000/api/posts"
# -> 401 {"detail": "Authentication required"}
```

## What you get automatically

| Feature | How |
|---------|-----|
| Secure-by-default | All endpoints require auth when `auth.users.enabled` is on |
| Users collection blocked | Auth users collection never exposed via auto-CRUD |
| Protected fields | `role`, `password_hash`, etc. auto-immutable on all collections |
| Writable fields allowlist | `writable_fields` restricts which fields clients can write |
| Public read | `public_read: true` allows anonymous reads, writes still require auth |
| Login rate limiting | `max_login_attempts` blocks brute-force with 429 + Retry-After |
| Auto-CRUD | GET, POST, PUT, PATCH, DELETE from a manifest |
| JSON Schema validation | Rejects bad documents before they hit the database |
| Named scopes | `?scope=published,drafts` activates predefined MQL filters |
| Aggregation pipelines | `GET /api/{collection}/_agg/{name}` |
| Document defaults | Auto-populate fields on create with `{{user.*}}` templates |
| Owner field | Auto-inject creator ID, enforce write/delete ownership, admin bypass |
| Immutable fields | Silently strip protected fields from updates |
| Lifecycle hooks | Fire-and-forget side effects with `{{doc.*}}`, `{{user.*}}`, `$$NOW` |
| Relations / populate | `?populate=name` injects `$lookup` joins at read time |
| Computed fields | `?computed=name` injects aggregation pipelines at read time |
| Unique constraints | `x-unique` in schema auto-creates indexes, returns 409 on duplicates |
| TTL | `"ttl": {"field":"ts","expire_after":"90d"}` auto-creates TTL indexes |
| Soft delete | Delete, trash, restore lifecycle |
| Bulk insert | Batch up to 1000 documents in one request |
| Read-only mode | GET-only collections for logs, audit trails |
| Timestamps | `created_at` and `updated_at` injected automatically |
| Filtering | `?field=value`, `?field=gt:18`, `?field=in:a,b,c` |
| Sorting | `?sort=-created_at,title` |
| Pagination | `?limit=10&skip=20` |
| Field selection | `?fields=title,status` |
| Data isolation | All queries scoped by `app_id` automatically |
| Env-var seeding | `{{env.*}}` in demo_users — no plaintext secrets in manifest |
| Registration role | `registration_role` controls what role self-registered users get |
| Invite-only mode | `allow_registration: "invite_only"` with `invite_codes` |
| CLI admin provisioning | `mdb-engine add-user` creates users with interactive password prompt |
| OpenAPI docs | Swagger UI at `/docs` |

## The design principle

> **MQL is the DSL.** Every `scopes`, `pipelines`, `defaults`, `hooks`, `relations`,
> and `computed` value is a native MongoDB Query Language expression. The manifest
> speaks the same language as the database — no translation layer, no custom syntax.
> Declare what you want. The engine handles the rest.
