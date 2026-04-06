# Changelog

## 0.11.4

### Fixed -- Collection Auth Role Handling

- **Removed Casbin auto-creation for manifests without `auth.policy`** --
  Previously, `_initialize_auth_provider` auto-created a Casbin enforcer
  whenever any collection had an `auth` block (e.g. `write_roles`,
  `roles`), even when no `auth.policy` section existed in the manifest.
  The auto-created provider had role-to-collection policies but no
  user-to-role groupings, so every `enforce(email, collection, action)`
  call returned `False` -- resulting in 403 on every role-gated endpoint.
  Collections that use `auth.roles` / `auth.write_roles` without an
  explicit `auth.policy` now correctly fall back to the inline
  `require_role` path, which reads roles directly from the user object.

- **`public_read` collections no longer gated by `write_roles`** --
  The `public_read` code path in `auto_crud` incorrectly added an auth
  dependency to the read router when `write_roles` or `create_roles` was
  set (via the `_use_provider` flag). This caused anonymous users to
  receive 401 on collections that declared `"public_read": true`. The
  read router is now always unauthenticated when `public_read` is
  enabled, regardless of write/create role configuration.

## 0.11.3

### Improved -- Authorization Provider Testing & Hardening

- **Relaxed OsoAdapter import guard** -- `OsoAdapter.__init__` no longer
  requires `oso-cloud` or `oso` to be importable at construction time.
  The factory (`oso_factory.py`) already validates the package before
  building a client, so the redundant check was removed. A `None` client
  is still rejected (fail-closed). This makes mock-based testing possible
  without the real OSO package installed.

- **CI now installs `oso-cloud`** -- The integration test job installs
  `.[test,dev,ai,oso]` so OSO adapter tests run in CI instead of being
  skipped.

- **Expanded OSO adapter test coverage** -- Added tests for `save_policy`,
  `has_policy`, `has_role_for_user`, `remove_role_for_user`, `clear_cache`,
  cache TTL / hit behavior, uninitialized-deny, and `None`-client rejection
  (16 new tests).

- **Provider-parity contract tests** -- New `TestProviderParityContract`
  class runs identical authorization sequences (add policy, check, role
  inheritance, has_policy, clear_cache, save_policy) through both
  `CasbinAdapter` (real MongoDB enforcer) and `OsoAdapter` (thin in-memory
  fake), proving both honor the same `BaseAuthorizationProvider` contract.

## 0.11.2

### Added — SSR, SEO & Blog Features

- **Computed virtual fields (`x-computed`)** — Schema properties with
  `x-computed` are automatically derived on every create, replace, and patch.
  Built-in transforms: `plain_text`, `first_image`, `word_count`, `truncate`.

- **SEO fallback chains** — SEO fields now accept a `{"fallback": [...]}` array
  in addition to a plain string. The engine evaluates each expression in order
  and uses the first non-empty result. Placeholder expressions support pipe
  transforms: `{{post.body | plain_text | truncate(160)}}`.

- **RSS/Atom feed generation** — New `ssr.feeds` manifest key. The engine
  serves valid RSS 2.0 or Atom 1.0 feeds at configured paths with proper
  `Content-Type`, `ETag`, and `Cache-Control` headers.

- **Auto `<head>` meta injection** — `mdb_base.html` now auto-injects
  `<link rel="canonical">`, feed `<link rel="alternate">`, and pagination
  `<link rel="prev/next">` tags. No template changes needed.

- **Slug-based URLs (`x-slug`)** — Schema properties with `x-slug` get
  auto-generated URL-safe slugs on create/update with collision suffixing.
  SSR routes transparently resolve slugs when `id_param` is not a valid
  ObjectId.

- **Enhanced sitemaps** — `ssr.sitemap` now accepts an object with per-route
  `lastmod`, `changefreq`, `priority` metadata. Automatic sitemap index
  splitting at configurable `max_urls_per_file` (default 50,000).

- **`robots.txt` generation** — New `ssr.robots` manifest key with `allow`,
  `disallow`, and `sitemap` fields. Serves a valid `robots.txt` at `/robots.txt`.

- **Pagination SEO** — SSR routes with paginated data automatically inject
  `rel="prev"` / `rel="next"` link tags and `Link` HTTP headers.

- **Cache status headers** — Every SSR response now includes
  `X-Cache-Status: MISS` (and `X-Cache-Age: 0` when `Cache-Control` is set).
  Templates receive a `cache` context object for future server-side caching.

- **OG image generation** — New `ssr.og_image_fallback` config auto-generates
  1200×630 social preview PNGs for documents without a cover image. Requires
  optional `Pillow` dependency (`pip install mdb-engine[og-image]`).

### Added — New Optional Dependency Group

- `mdb-engine[og-image]` — Installs `Pillow>=10.0.0` for OG image generation.

### New Files

- `mdb_engine/routing/_computed.py` — Write-time computed field transform registry.
- `mdb_engine/routing/_og_image.py` — OG social preview image generator.

## 0.11.0

### Added — Gateway Features

- **`BackgroundHookExecutor` wired into auto-CRUD** — After-event hooks
  (`after_create`, `after_update`, `after_delete`) now run in the background
  with configurable retry logic and dead-letter logging to `_hook_failures`.
  Previously only the base fire-and-forget `HookExecutor` was wired in.

- **`before_create` / `before_update` hooks** — New synchronous hook events
  that run *before* the write is persisted. Errors propagate and abort the
  request, letting hooks validate, enrich, or veto documents. Declare them
  in the manifest alongside the existing `after_*` hooks:

  ```json
  "hooks": {
    "before_create": [{
      "action": "http",
      "url": "https://validation.example.com/check",
      "method": "POST"
    }]
  }
  ```

- **Per-collection rate limiting** — Declare `rate_limits` on any collection
  in the manifest. Separate limits for reads (GET) and writes
  (POST/PUT/PATCH/DELETE), keyed by authenticated user or client IP:

  ```json
  "rate_limits": {
    "reads":  { "max_attempts": 100, "window_seconds": 60 },
    "writes": { "max_attempts": 20,  "window_seconds": 60 },
    "per": "user"
  }
  ```

  Returns `429` with a `Retry-After` header when exceeded.

### Added — DX Polish

- **`GET /health` for single-app** — Standalone apps (`quickstart()`,
  `mdb-engine serve`) now expose a `/health` endpoint that calls
  `engine.get_health_status()`. Sub-apps skip it — the multi-app parent
  owns the health route.

- **`py.typed` marker (PEP 561)** — Type checkers in strict mode now
  recognize `mdb-engine` as a typed package.

- **Stable error codes** — Every `MongoDBEngineError` subclass now carries a
  `code` class attribute (e.g. `MDB_QUERY_INVALID`, `MDB_LLM_AUTH_FAILED`).
  The JSON error response includes a machine-readable `"code"` field:

  ```json
  { "error": "QueryValidationError", "code": "MDB_QUERY_INVALID", "message": "..." }
  ```

  LLM exceptions are now mapped to proper HTTP status codes instead of
  falling through to 500:

  | Exception              | Code                   | HTTP |
  | ---------------------- | ---------------------- | ---- |
  | `LLMAPIError`          | `MDB_LLM_ERROR`        | 502  |
  | `LLMAuthenticationError` | `MDB_LLM_AUTH_FAILED` | 401  |
  | `LLMNotFoundError`     | `MDB_LLM_NOT_FOUND`    | 404  |
  | `LLMRateLimitError`    | `MDB_LLM_RATE_LIMITED` | 429  |

- **`--debug` flag on `mdb-engine serve`** — Pass `--debug` to get
  debug-level uvicorn logging during development.

## 0.8.7

### Fixed

- **Soft-delete restore query** — `POST /{id}/_restore` now correctly finds
  soft-deleted documents. Previously, `write_filter()` injected
  `"deleted_at": None` (to exclude deleted docs from normal writes), which
  contradicted the restore handler's `"deleted_at": {"$ne": None}` requirement
  when merged via `$and`. The restore endpoint now builds its filter directly,
  bypassing `write_filter` while still honoring write policies.

### Improved

- **Exception handling hardened across the engine** — Replaced all broad
  `except Exception` catches with specific exception types (`PyMongoError`,
  `HTTPException`, `KeyError`, `ValueError`, etc.) in:
  - App-user session middleware (`app_auth_routes.py`)
  - Declarative lifecycle hooks (`_hooks.py`)
  - Index auto-creation at startup (`fastapi_app.py`)
  - CLI `add-user` command (`cli/commands/add_user.py`)

- **`_register_write_routes` complexity reduced** — Extracted
  `_register_bulk_insert_route()` and `_register_delete_routes()` from the
  monolithic write-route factory, bringing cyclomatic complexity from 35 to
  well under the 25 threshold. No behavioral changes — same route registration
  order and semantics.

## 0.8.6

### Added — Manifest Power Features

Eight new declarative collection primitives that make manifest-driven backends
production-grade. Zero Python code required for any of these.

- **`owner_field`** — Automatic ownership enforcement. Declare `"owner_field": "author_id"`
  and the engine auto-injects `{{user._id}}` as the default value, generates write/delete
  policies so users can only modify their own documents, and grants admin role a full
  bypass. Pure syntactic sugar — composable with explicit `policy` if both are set.

- **`immutable_fields`** — Declare fields that cannot be changed after creation.
  `"immutable_fields": ["author_id", "post_id"]` silently strips those keys from
  PATCH and PUT request bodies. No error, no leak — the fields simply cannot move.

- **`hooks`** — Declarative lifecycle hooks (`after_create`, `after_update`,
  `after_delete`). Fire-and-forget side effects that never fail the originating
  request. Supports `insert` (write a document to another collection) and `update`
  (run `$set` on matching documents) actions. All template placeholders work:
  `{{doc.*}}`, `{{user.*}}`, `$$NOW`.

  ```json
  "hooks": {
    "after_create": [{
      "action": "insert", "collection": "audit_log",
      "document": { "event": "created", "entity_id": "{{doc._id}}", "actor": "{{user.email}}", "timestamp": "$$NOW" }
    }]
  }
  ```

- **`relations` / `?populate=`** — Declarative cross-collection joins. Define
  relations in the manifest, then request them at read time with `?populate=post`.
  The engine injects `$lookup` stages into the aggregation pipeline. Handles
  ObjectId-to-string type coercion automatically when `foreign_field` is `_id`.
  Supports `"single": true` for `$unwind` to a single object.

- **`computed` / `?computed=`** — Virtual fields computed at read time via
  aggregation. `?computed=comment_count` injects the configured pipeline stages
  into the query. Two forms: expression (injected as `$addFields`) or pipeline
  (stages injected inline). Combinable with `?populate=` in the same request.

- **`x-unique`** — Schema-driven unique constraints. Add `"x-unique": true` to
  any property in your JSON Schema and the engine auto-creates a unique index at
  startup. Duplicate inserts/updates return `409 Conflict` with a clear message
  identifying the violating field(s).

- **`ttl`** — Collection-level TTL shorthand. `"ttl": {"field": "timestamp", "expire_after": "90d"}`
  auto-creates a MongoDB TTL index at startup. Supports `s` (seconds), `m` (minutes),
  `h` (hours), `d` (days) duration units.

- **Extended template resolver** — `resolve_template()` now supports three
  placeholder families:
  - `{{user.*}}` — authenticated user context (existing)
  - `{{doc.*}}` — the document being created/updated (new — used by hooks)
  - `{{env.*}}` — environment variables, restricted to `^[A-Z_][A-Z0-9_]*$` (new)
  - `$$NOW` — current UTC datetime (existing)

### Changed

- **`DuplicateKeyError` passthrough** — `ScopedCollectionWrapper.insert_one()`
  now lets `pymongo.errors.DuplicateKeyError` propagate instead of wrapping it
  in `MongoDBEngineError`. This enables auto-CRUD's 409 handling and any
  user-level duplicate-key logic.

- **Query parser** — `populate` and `computed` are now reserved query parameters.
  `ParsedQuery` includes `populate: list[str] | None` and `computed: list[str] | None`.

### Examples

- **Zero-Code Blog (updated)** — The `examples/basic/zero_code_api` manifest now
  showcases every new feature:
  - `posts`: `owner_field`, `immutable_fields`, `hooks` (audit trail), `computed` (comment count)
  - `comments`: `owner_field`, `immutable_fields`, `relations` (`?populate=post`), `hooks`
  - `categories`: `x-unique` on name
  - `audit_log`: `read_only`, `ttl` (90-day auto-expiry), auto-populated via hooks
  - Frontend updated with `?computed=comment_count` on feed, audit log panel in admin view

## 0.8.5

### Added

- **`public/` directory convention** -- `mdb-engine serve` now automatically serves static files from a `public/` directory if it exists next to the manifest. `public/index.html` is served at `/`. This enables zero-config single-origin frontend hosting for manifest-based apps.
- **CORS support for `create_app`** -- The `cors` manifest configuration is now respected by `create_app()` (single-app mode), not just `create_multi_app()`. This fixes CORS issues for standalone apps and the CLI serve command.

### Examples

- **Zero-Code Blog** -- Completely redesigned `examples/basic/zero_code_api` as a blog platform.
  - Zero Python code (manifest + HTML only).
  - Uses the new `public/` convention to serve a single-file React-less frontend explorer.
  - Showcases all zero-code features: schema validation, soft delete, scopes (`published`, `drafts`), pipelines (`by_author`), bulk insert, and read-only collections.

## 0.8.4

### Added

- **Zero-Code MQL-as-DSL** -- Five new manifest primitives for `collections`:
  `policy`, `scopes`, `pipelines`, `defaults`, `default_projection`. MongoDB
  Query Language expressions embedded directly in `manifest.json` are resolved
  at runtime with `{{user.*}}` template variables and merged into the auto-CRUD
  filter chain. No Python code required.

  - **`policy`** -- Document-level access policies. MQL filters for `read`,
    `write`, and `delete` operations, merged into every query via `$and`.
    `delete` falls back to `write` when omitted.

  - **`scopes`** -- Named MQL filters activated by clients via `?scope=name`.
    Multiple scopes can be combined: `?scope=active,mine`. Works on list,
    count, and trash endpoints.

  - **`pipelines`** -- Named aggregation pipelines exposed as read-only
    `GET /api/{collection}/_agg/{name}` endpoints. `app_id` scoping is applied
    automatically.

  - **`defaults`** -- Default field values applied to new documents on create
    and bulk create. Uses `setdefault` semantics (caller values take precedence).
    Supports `{{user.*}}` templates.

  - **`default_projection`** -- MongoDB projection applied to list/get queries
    when the client does not specify `?fields=`. Useful for hiding internal
    fields from API responses.

- **Template resolver** -- New `mdb_engine.routing.template_resolver` module
  with `resolve_template()` (recursive `{{user.*}}` / `$$NOW` resolution) and
  `merge_filters()` (generic N-way `$and` merge that generalises the existing
  soft-delete pattern).

- **Query parser `?scope=` support** -- `scope` is now a reserved query
  parameter. `parse_query_params()` returns a `scope: list[str] | None` field.

## 0.8.3

### Bug Fixes

- **CognitiveEngine: timeline-aware chat** -- `CognitiveEngine.chat()` now accepts a
  `timeline_id` parameter and forwards it to `MemoryService.search()` (via `_fetch_ltm`)
  and includes it in storage metadata (via `_extract_and_store_facts`). When `timeline_id`
  is `None`, it auto-resolves from `TimelineService.get_active_timeline()` if available,
  falling back to `"root"`.

- **inject(): missing `metadata.confidence`** -- `inject()` now stores `confidence` in
  `metadata.confidence` (in addition to the root-level field), matching what
  `_process_new_facts` already does. Previously, injected memories were invisible to
  `$vectorSearch` because the pre-filter on `metadata.confidence` excluded them.

- **inject(): importance/emotion from metadata dict** -- `inject()` now reads
  `importance`, `emotion`, and `emotion_type` from the `metadata` dict as a fallback
  when they are not provided as top-level kwargs. Priority order: kwargs > metadata > default.

- **search(): parameter override bug** -- `search()` no longer unconditionally overwrites
  its explicit `timeline_id` and `min_confidence` parameters with kwargs defaults. The
  explicit parameter value is now preserved unless explicitly overridden via `**kwargs`.

- **_ensure_cognitive_fields(): backfill `metadata.confidence`** -- The migration helper
  now backfills `metadata.confidence` from root-level `confidence` for existing documents
  (or defaults to `0.8`), ensuring pre-existing memories are visible to `$vectorSearch`
  after upgrading.
