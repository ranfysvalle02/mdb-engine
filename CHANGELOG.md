# Changelog

## 0.15.0

### Added — capability-aware grounding & future-proofing

The `0.14.0` grounding plumbing was correct, but production surfaced three
brittleness points: grounding is gated by the *model* (Gemini `2.5-*` ground,
the `-latest` aliases do **not**), the `google-genai` 2.x async transport
crashes on `aiohttp<3.14`, and `_extract_gemini_text` raised `TypeError` on
empty/blocked candidates. `0.15.0` makes the engine the single source of truth
so apps stop hardcoding model knowledge.

- **Model Capability Registry** (`mdb_engine.llm.capabilities`) — A curated,
  versioned map of what each model can actually do (`ModelCapabilities`:
  `thinking`, `web_search`, `vision`, `structured_output`, `max_input_tokens`,
  `default_reasoning`, …). New `LLMService` accessors:
  - `get_capabilities(model=None)` — resolve any model (canonical id, bare id,
    alias, or family heuristic; always returns a value).
  - `list_models(provider=, web_search=, thinking=, vision=)` — build app model
    selectors / toggles from the engine instead of hardcoded flags.
  - `supports(feature, model=None)` — quick boolean check.
  - Manifest overrides via `llm_config.model_overrides` self-heal the map
    without an engine release. Seeded with the verified truths: `gemini-2.5-*`
    → `web_search=True`, `gemini-flash-latest` / `gemini-pro-latest` →
    `web_search=False`.

- **`grounding_policy`** on `chat_completion` / `chat_completion_stream` /
  `stream` — `enable_web_search=True` is **never** a silent no-op again:
  - `"best_effort"` (default) — attach grounding if supported, else log + skip.
  - `"require"` — raise `GroundingUnsupportedError` if the model can't ground.
  - `"auto"` — transparently route the turn to a grounding-capable model for the
    same provider (e.g. `gemini-flash-latest` → `gemini-2.5-flash`), recorded in
    `GroundedCompletion.model_used`. Configurable via `llm_config.grounding_model`.

- **Typed streaming events** — New `LLMService.stream()` yields `TextDelta`,
  `ReasoningDelta`, `GroundingEvent`, and a terminal `DoneEvent` (with
  `grounded` / `model_used` / `citations`), removing the
  `startswith("__REASONING__")` / `startswith("__GROUNDING__")` string sniffing.
  The legacy `__REASONING__:` / `__GROUNDING__:` sentinels remain for back-compat.

- **Richer `GroundedCompletion`** — Now also carries `model_used` and
  `finish_reason` (`STOP` / `MAX_TOKENS` / `SAFETY` / `RECITATION`).

- **Normalized citations** — Each citation is now
  `{title, uri, domain, redirect_uri}`. Google returns `web.uri` as a
  `vertexaisearch.cloud.google.com/grounding-api-redirect/...` redirect with the
  real publisher in `web.title`; `domain` is the clean host for display.

- **OTel grounding attributes** — Spans now emit `gen_ai.grounding.enabled`,
  `gen_ai.grounding.citation_count`, and `gen_ai.grounding.model_used` so "is
  grounding firing in prod?" is a dashboard query.

### Changed / Fixed

- **`google-genai` x `aiohttp` streaming crash (self-healed)** — `google-genai`
  ≥ 2.4 calls `StreamReader.readline(max_line_length=...)`, a kwarg only
  accepted by `aiohttp` ≥ 3.14, so a clean install on `aiohttp` 3.11–3.13 would
  `TypeError` mid-stream. The engine now detects this at client build time and
  forces the httpx async transport, and logs a one-time SDK self-check warning.

- **Packaging** — The `ai` / `all` extras now pin `google-genai>=2.8.0,<3.0.0`
  and `aiohttp>=3.14.0` (overriding `google-genai`'s too-loose floor).

- **Robust response parsing** — `_extract_gemini_text` no longer raises
  `TypeError` when a thinking model returns a candidate with
  `content.parts is None` (truncated/blocked); it returns `""` cleanly, and the
  finish reason is surfaced on `GroundedCompletion` and the OTel span.

## 0.14.0

### Added — Gemini web-search grounding

- **`enable_web_search` flag** — A new provider-agnostic keyword on
  `LLMService.chat_completion` and `LLMService.chat_completion_stream` (and the
  underlying `_LLMProvider` methods) enables **Google Search grounding** for
  Gemini models. It attaches `Tool(google_search=GoogleSearch())` to the
  Gemini request so answers are grounded in live web results — without callers
  ever touching the `google-genai` SDK directly.

- **Grounding citations** — Sources are returned as `[{"title", "uri"}]`:
  - Non-streaming: pass `return_metadata=True` to receive a new
    `GroundedCompletion` dataclass (`text` / `citations` / `grounded`) instead
    of a plain `str`. `return_metadata` defaults to `False`, so the default
    return type is unchanged.
  - Streaming: a single trailing `__GROUNDING__:{json}` event is emitted before
    the stream ends (mirroring the existing `__REASONING__:` convention).

- **Raw `tools` forwarding for Gemini** — `_call_gemini` now reads `tools` and
  the Gemini branches of both public methods forward `**kwargs`, so configured
  tools and the legacy `tools=[{"googleSearch": {}}]` dict (previously a silent
  no-op for Gemini) are translated into real grounding tools.

- **`GroundedCompletion`** — Exported from `mdb_engine.llm` for typed access to
  grounded results.

### Changed

- **Structured-output guard** — Gemini does not allow tools/grounding together
  with a JSON `response_format`; when both are requested, grounding is dropped
  with a warning so the structured call still succeeds.

- **Graceful degradation** — A model that rejects the tools/grounding config is
  retried once without tools (ungrounded) instead of failing the request,
  matching the existing thinking-config fallback behavior.

- **Non-Gemini providers** — `enable_web_search=True` is ignored with a clear
  warning for OpenAI/Azure (no built-in equivalent; no crash).

## 0.12.4

### Fixed

- **File serving crash** — `retrieve()` and `delete()` in the upload service
  used dict subscripting (`["_id"]`, `.get("metadata")`) on `GridOut` objects
  returned by `AsyncIOMotorGridFSBucket.find()`. Real `GridOut` objects expose
  data via attributes (`.metadata`, `._id`), not dict access, causing
  `TypeError: 'GridOut' object is not subscriptable` on every file serve
  request. Switched to attribute access throughout.

- **Misleading master key warning** — The startup warning when
  `MDB_ENGINE_MASTER_KEY` is not set previously stated "App-level
  authentication will not be available", which implied cookie/session auth was
  broken. The message now clarifies that only envelope encryption for app
  secrets is disabled and cookie/session authentication is not affected.

### Improved

- **Test fakes hardened** — Unit test mocks now use a `FakeGridOut` wrapper
  that mimics real Motor `GridOut` attribute-only access (no `__getitem__`),
  preventing dict-vs-attribute bugs from passing tests while failing at
  runtime.

## 0.12.3

### Added — Upload Service

- **GridFS-backed file upload endpoint** — New `uploads` manifest section enables
  a content-addressed file upload service scoped per app. Files are stored in
  GridFS with SHA-256 deduplication. Supports multipart form uploads and base64
  JSON bodies. Configure via `uploads.enabled`, `uploads.max_size`,
  `uploads.allowed_types`, and `uploads.path_prefix` in the manifest.

- **Upload auth configuration** — Upload endpoint respects `uploads.auth.required`
  and `uploads.auth.roles` for fine-grained access control. Inherits app-level
  auth when no upload-specific config is provided.

- **Multi-app upload isolation** — Each mounted app gets its own GridFS bucket
  (`{slug}_uploads`) and routes are scoped under the app's `path_prefix`,
  preventing cross-app collisions.

- **New dependencies** — `get_upload_service` and `get_upload_service_optional`
  FastAPI dependencies for injecting the upload service into custom routes.

### Security — Upload Service Hardening

- **Hash validation on serve route** — The `GET {path_prefix}/{hash}.{ext}` route
  now validates that `file_hash` matches `^[0-9a-f]{64}$` before querying GridFS,
  preventing NoSQL injection via crafted path parameters.

- **Zero-byte upload rejection** — `UploadService.store()` now raises `ValueError`
  for empty payloads, preventing storage of zero-byte files.

- **SVG XSS mitigation** — `image/svg+xml` removed from `DEFAULT_ALLOWED_TYPES`
  (now opt-in via manifest `allowed_types`). When SVGs are served, the response
  includes `Content-Disposition: attachment` to force download instead of inline
  rendering.

- **`If-None-Match` / 304 support** — The serve route now checks the
  `If-None-Match` request header against the file hash and returns `304 Not
  Modified` when they match, saving a full GridFS round-trip.

### Added — Manifest Schema

- **`uploads` schema key** — New top-level manifest property with `enabled`,
  `max_size`, `allowed_types`, `path_prefix`, and `auth` sub-properties.

### Added — New dependency

- `python-multipart>=0.0.7` added to core dependencies for multipart file upload
  support.

## 0.12.2

### Fixed

- **`| markdown` filter strips `data:` URI images** — `nh3.clean()` defaults
  `url_schemes` to `{"http", "https", "mailto"}`, which silently removes
  `src` attributes containing `data:` URIs (e.g. base64-encoded images).
  The `nh3.clean()` call now passes `url_schemes={"http", "https", "mailto",
  "data"}` so inline images survive sanitisation.  A post-process step
  neutralizes `data:` URIs in `<a href>` attributes (replacing them with
  `href="#"`) to prevent XSS via `data:text/html` link navigation.

- **`og:image` meta tag accepts `data:` URIs** — When a post's cover image
  resolved to a base64 `data:` URI, the full multi-KB string landed in the
  `og:image` meta tag. Social platforms cannot use these.  The SEO fallback
  resolver now skips values starting with `data:` for image-related SEO keys,
  and the OG image route's cover-field redirect ignores `data:` URIs (falling
  through to the auto-generated PNG).

## 0.12.1

### Fixed

- **`| markdown` filter crash on links** — `nh3.clean()` raises a `ValueError`
  when `rel` is in the allowed-attributes dict while `link_rel` is set (the
  default).  Any Markdown containing links (e.g. `[text](url)`) caused a 500.
  Removed `rel` from the `<a>` allow-list so `nh3`'s default
  `link_rel="noopener noreferrer"` handles it automatically — which is the
  correct security behavior for user-generated links.

## 0.12.0

### Added — Performance Middleware Suite

- **GZip / Brotli response compression** — All responses over 500 bytes are
  now compressed automatically via Starlette's `GZipMiddleware`.  When
  `brotli-asgi` is installed (`pip install mdb-engine[perf]`), Brotli is
  preferred.  Opt out via `compression.enabled: false` in the manifest.

- **Cache-Control headers for static assets** — Files served from `public/`
  now receive `Cache-Control` headers based on file type (fonts get 1-year
  immutable, CSS/JS get 1-day with `stale-while-revalidate`, images get
  7-day).  Configurable via the new `static_cache` manifest key.

- **Server-side Markdown Jinja filter** — A `| markdown` filter is
  registered on all SSR Jinja environments.  Uses `mistune` + `nh3` for
  fast rendering with server-side sanitization.  Install with
  `pip install mdb-engine[markdown]`.  Eliminates client-side JS for
  Markdown rendering (~60 KB savings, no CLS).

- **Asset fingerprinting** — An `AssetRegistry` computes content hashes
  for all `public/` files at startup.  Templates can use
  `{{ asset_url('style.css') }}` which renders as
  `/public/style.css?v=a1b2c3d4` for cache busting on deploy.

- **Link preload headers** — SSR responses can include `Link: <...>;
  rel=preload` headers for critical CSS, fonts, and scripts.  Configured
  via `ssr.preload` (global) and per-route `preload` arrays in the
  manifest.  Works with CDN Early Hints (Cloudflare, etc.).

- **CSS / JS minification** — Optional in-memory minification of `.css`
  and `.js` files at startup.  Enable with `static_cache.minify: true` and
  `pip install mdb-engine[perf]`.

### Added — New optional extras

- `pip install mdb-engine[perf]` — `brotli-asgi`, `rjsmin`, `csscompressor`
- `pip install mdb-engine[markdown]` — `mistune`, `nh3`

### Added — Manifest schema keys

- `compression` — GZip/Brotli opt-out and `minimum_size` tuning
- `static_cache` — Per-category `Cache-Control` overrides and `minify` flag
- `ssr.preload` — Global and per-route resource preload hints

## 0.11.5

### Fixed -- Scoped Auth Role Hierarchy

- **Scope `auth.roles` now respects `role_hierarchy`** -- The
  `resolve_scopes` method in `auto_crud` previously checked only the
  user's raw `role` / `roles` fields when evaluating scope-level auth
  gates (e.g. `"pending": {"filter": ..., "auth": {"roles": ["moderator"]}}`).
  Role hierarchy was ignored, so an admin whose hierarchy includes
  moderator would receive 403 on moderator-gated scopes. The scope auth
  path now calls `get_effective_roles(user, role_hierarchy)`, matching
  the behavior of `require_role()` and `require_collection_permission()`.

### Fixed -- OSO Provider Parity Test

- **`_FakeOsoClient` now handles `oso_cloud.Value` objects** -- When
  `oso-cloud` is installed (CI), `OsoAdapter.add_role_for_user` wraps
  arguments in `Value` objects. The test fake's `insert()` method used
  `str()` on these, producing a repr string that didn't match the `.id`
  extraction in `authorize()`. Added a `_id()` helper that consistently
  extracts `.id` from Value objects, fixing the `test_role_inheritance`
  CI failure.

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
