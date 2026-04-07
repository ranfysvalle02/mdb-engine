# Upgrading to mdb-engine 0.12.0

**Release focus:** Performance middleware suite. Six new engine-level features that improve PageSpeed scores for sites built on mdb-engine — without any changes to app repositories.

---

## Quick checklist

1. `pip install --upgrade mdb-engine` (GZip compression + Cache-Control work out of the box)
2. Optionally: `pip install mdb-engine[perf]` for Brotli compression + CSS/JS minification
3. Optionally: `pip install mdb-engine[markdown]` for server-side Markdown rendering
4. Add `static_cache`, `compression`, or `ssr.preload` keys to your manifest if you want to customize defaults
5. Replace client-side Markdown rendering with `{{ post.body | markdown | safe }}` in your templates
6. Use `{{ asset_url("style.css") }}` in templates for cache-busted static asset URLs

---

## What changed

### Overview of new features

| Feature | Impact | Extra deps needed? | Manifest key |
|---|---|---|---|
| GZip compression | HIGH | No (built into Starlette) | `compression` (opt-out) |
| Brotli compression | HIGH | `pip install mdb-engine[perf]` | Same — auto-detected |
| Cache-Control for static assets | HIGH | No | `static_cache` |
| Server-side Markdown filter | HIGH | `pip install mdb-engine[markdown]` | None (auto-registered) |
| Asset fingerprinting | MEDIUM | No | None (automatic) |
| Link preload headers | MEDIUM | No | `ssr.preload` |
| CSS/JS minification | LOW-MEDIUM | `pip install mdb-engine[perf]` | `static_cache.minify` |

---

## 1. GZip / Brotli Compression (automatic)

**What it does:** Every response over 500 bytes is now compressed automatically. This typically reduces transfer sizes by 70-80% for HTML, CSS, JS, and JSON responses.

**Zero-config:** Compression is enabled by default with no manifest changes needed. The engine uses Starlette's built-in `GZipMiddleware`. When `brotli-asgi` is installed (via `pip install mdb-engine[perf]`), Brotli is preferred (better compression ratios than GZip).

**Customizing or disabling:**

```json
{
  "compression": {
    "enabled": true,
    "minimum_size": 500
  }
}
```

Set `"enabled": false` to disable compression entirely (e.g., if your reverse proxy already handles it).

Increase `minimum_size` to skip compression for small responses where the CPU cost outweighs the bandwidth saving.

---

## 2. Cache-Control Headers for Static Assets (automatic)

**What it does:** All files served from `public/` now get appropriate `Cache-Control` headers based on file type. Previously, static files had no cache headers at all.

**Zero-config defaults:**

| File type | Cache-Control | Reasoning |
|---|---|---|
| Fonts (`.woff2`, `.woff`, `.ttf`, `.otf`) | `max-age=31536000, immutable` | Fonts never change per URL |
| CSS (`.css`) | `max-age=86400, stale-while-revalidate=3600` | 1 day + 1h grace |
| JS (`.js`, `.mjs`) | `max-age=86400, stale-while-revalidate=3600` | 1 day + 1h grace |
| Images (`.png`, `.jpg`, `.svg`, `.webp`, etc.) | `max-age=604800` | 7 days |
| Everything else | `max-age=3600` | 1 hour |

**Customizing:**

Override any category in your manifest:

```json
{
  "static_cache": {
    "fonts": "max-age=31536000, immutable",
    "styles": "max-age=604800, stale-while-revalidate=86400",
    "scripts": "max-age=604800, stale-while-revalidate=86400",
    "images": "max-age=2592000",
    "default": "max-age=86400"
  }
}
```

Combined with asset fingerprinting (see below), you can safely set long cache lifetimes.

---

## 3. Server-Side Markdown Rendering

**What it does:** Adds a `| markdown` Jinja filter that converts Markdown to sanitized HTML on the server. This eliminates the need to ship client-side Markdown libraries (marked.js, DOMPurify) — saving ~60 KB of JavaScript and eliminating layout shift (CLS).

**Install:**

```bash
pip install mdb-engine[markdown]
```

**Usage in templates:**

```html
<!-- Before: client-side rendering with marked.js + DOMPurify -->
<div id="content"></div>
<script src="/public/marked.min.js"></script>
<script src="/public/purify.min.js"></script>
<script>
  document.getElementById('content').innerHTML =
    DOMPurify.sanitize(marked.parse(rawMarkdown));
</script>

<!-- After: server-side, zero JS needed -->
<div class="prose">
  {{ post.body | markdown | safe }}
</div>
```

The `| safe` is required because Jinja's `autoescape` is on. The filter already sanitizes HTML via `nh3` (a Rust-backed sanitizer), so `| safe` does not introduce XSS.

**Allowed HTML tags:** `h1`-`h6`, `p`, `br`, `hr`, `ul`, `ol`, `li`, `a`, `strong`, `em`, `code`, `pre`, `blockquote`, `img`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `del`, `sup`, `sub`, `details`, `summary`, `div`, `span`.

**Allowed attributes:** `href`, `title` on links; `src`, `alt`, `title`, `width`, `height`, `loading` on images; `class` on code/pre/div/span; `align` on table cells. Links automatically get `rel="noopener noreferrer"` via `nh3`'s default `link_rel` — no need to allow `rel` manually.

**Graceful degradation:** If `mistune` and `nh3` are not installed, the filter is simply not registered. Templates that reference `| markdown` will get a Jinja `UndefinedError` — install the deps to fix.

**Works in both SSR routes and `web.py` apps:** The filter is registered on the SSR Jinja environment and on any `Jinja2Templates` object found on `web.py` route modules in multi-app setups.

---

## 4. Asset Fingerprinting

**What it does:** At startup, the engine hashes every file in `public/` and exposes an `asset_url()` Jinja global that appends `?v=<hash>` to URLs. This lets you set aggressive cache lifetimes safely — when a file changes, its hash changes, and browsers fetch the new version.

**Usage in templates:**

```html
<!-- Before: no cache busting -->
<link rel="stylesheet" href="/public/style.css">
<script src="/public/app.js"></script>

<!-- After: automatic cache busting -->
<link rel="stylesheet" href="{{ asset_url('style.css') }}">
<script src="{{ asset_url('app.js') }}"></script>

<!-- Renders as: -->
<link rel="stylesheet" href="/public/style.css?v=a1b2c3d4">
<script src="/public/app.js?v=e5f6g7h8"></script>
```

**Multi-app:** In multi-app deployments, `asset_url()` automatically includes the app's path prefix (e.g., `/my-app/public/style.css?v=a1b2c3d4`).

**No manifest config needed.** The registry is built automatically whenever a `public/` directory exists.

---

## 5. Link Preload Headers (Early Hints)

**What it does:** SSR responses can now include `Link` preload headers that tell the browser (or CDN) to start fetching critical CSS, fonts, and scripts before the HTML is parsed. When behind a CDN that supports 103 Early Hints (Cloudflare, etc.), the assets start downloading during the server's database query latency — effectively making that latency "free."

**Site-wide preloads (all SSR routes):**

```json
{
  "ssr": {
    "enabled": true,
    "preload": [
      { "href": "/public/style.css", "as": "style" },
      { "href": "/public/fonts/inter.woff2", "as": "font", "crossorigin": true }
    ],
    "routes": { ... }
  }
}
```

**Per-route preloads (merged with site-wide):**

```json
{
  "ssr": {
    "enabled": true,
    "preload": [
      { "href": "/public/style.css", "as": "style" }
    ],
    "routes": {
      "/posts/:slug": {
        "template": "post.html",
        "preload": [
          { "href": "/public/prism.js", "as": "script" }
        ]
      }
    }
  }
}
```

The `/posts/:slug` route will emit:
```
Link: </public/style.css>; rel=preload; as=style, </public/prism.js>; rel=preload; as=script
```

**Allowed `as` values:** `style`, `script`, `font`, `image`, `fetch`, `document`.

**Tip:** Always set `"crossorigin": true` for fonts — browsers require CORS for font preloading.

---

## 6. CSS / JS Minification

**What it does:** Optionally minifies `.css` and `.js` files in `public/` at startup (in-memory, original files are not modified). This provides the benefits of a build step without actually needing one.

**Install:**

```bash
pip install mdb-engine[perf]
```

**Enable in manifest:**

```json
{
  "static_cache": {
    "minify": true
  }
}
```

Minification is only applied when the `rjsmin` and `csscompressor` packages are installed. If they're not installed, the flag is silently ignored.

---

## New optional dependency extras

| Extra | Command | What it includes |
|---|---|---|
| `perf` | `pip install mdb-engine[perf]` | `brotli-asgi`, `rjsmin`, `csscompressor` |
| `markdown` | `pip install mdb-engine[markdown]` | `mistune`, `nh3` |

Both are also included in `pip install mdb-engine[all]`.

---

## Manifest schema additions

Three new top-level keys are available in schema version 2.0:

```json
{
  "schema_version": "2.0",
  "slug": "my_app",
  "name": "My App",

  "compression": {
    "enabled": true,
    "minimum_size": 500
  },

  "static_cache": {
    "fonts": "max-age=31536000, immutable",
    "styles": "max-age=86400, stale-while-revalidate=3600",
    "scripts": "max-age=86400, stale-while-revalidate=3600",
    "images": "max-age=604800",
    "default": "max-age=3600",
    "minify": false
  },

  "ssr": {
    "enabled": true,
    "preload": [
      { "href": "/public/style.css", "as": "style" }
    ],
    "routes": {
      "/": {
        "template": "index.html",
        "preload": [
          { "href": "/public/hero.webp", "as": "image" }
        ]
      }
    }
  }
}
```

All new keys are optional. Existing manifests work unchanged — compression and cache headers activate automatically with sensible defaults.

---

## What did NOT change

- **All existing manifest keys** — no schema changes to existing fields
- **`create_app()` and `create_multi_app()` API** — same signatures, same behavior
- **`mount_ssr_routes()` API** — new optional `asset_registry` parameter (backward-compatible)
- **`mdb-engine serve` / `mdb-engine serve-multi`** — same CLI interface, new features automatic
- **Authentication, authorization, CRUD, WebSockets** — unaffected
- **Memory, graph, embedding, LLM services** — unaffected

---

## Migration recipes

### Recipe: Blog with client-side Markdown

**Before (0.11.x):**

```html
{% block content %}
<article>
  <h1>{{ post.title }}</h1>
  <div id="post-body"></div>
</article>
{% endblock %}

{% block extra_js %}
<script src="{{ base_path }}/public/marked.min.js"></script>
<script src="{{ base_path }}/public/purify.min.js"></script>
<script>
const raw = {{ post.body | tojson }};
document.getElementById('post-body').innerHTML =
  DOMPurify.sanitize(marked.parse(raw));
</script>
{% endblock %}
```

**After (0.12.x):**

```html
{% block content %}
<article>
  <h1>{{ post.title }}</h1>
  <div class="prose">{{ post.body | markdown | safe }}</div>
</article>
{% endblock %}
```

Delete `marked.min.js` and `purify.min.js` from `public/`. Save ~60 KB of client JS, eliminate CLS, and render instantly on first paint.

### Recipe: Maximum PageSpeed score

Add this to your `manifest.json`:

```json
{
  "compression": { "enabled": true, "minimum_size": 300 },
  "static_cache": {
    "fonts": "max-age=31536000, immutable",
    "styles": "max-age=604800, stale-while-revalidate=86400",
    "scripts": "max-age=604800, stale-while-revalidate=86400",
    "images": "max-age=2592000",
    "minify": true
  },
  "ssr": {
    "preload": [
      { "href": "/public/style.css", "as": "style" },
      { "href": "/public/fonts/inter-var.woff2", "as": "font", "crossorigin": true }
    ]
  }
}
```

Update templates to use `{{ asset_url('...') }}` for all static references:

```html
<link rel="stylesheet" href="{{ asset_url('style.css') }}">
<link rel="preload" href="{{ asset_url('fonts/inter-var.woff2') }}" as="font" crossorigin>
```

Install the perf extras:

```bash
pip install mdb-engine[perf,markdown]
```

### Recipe: Reverse proxy handles compression

If nginx or Cloudflare already compresses responses, disable engine-level compression to avoid double-encoding:

```json
{
  "compression": { "enabled": false }
}
```

Cache-Control headers and asset fingerprinting still work independently.

---

## New files in the engine

| File | Purpose |
|---|---|
| `mdb_engine/routing/static.py` | `CachedStaticFiles` (Cache-Control) + `AssetRegistry` (fingerprinting) + minification |

## Modified files

| File | What changed |
|---|---|
| `mdb_engine/core/fastapi_app.py` | Added `_add_compression_middleware` to middleware stack |
| `mdb_engine/core/manifest.py` | Added `compression`, `static_cache`, and `ssr.preload` to schema V2 |
| `mdb_engine/core/multi_app.py` | Registers `| markdown` filter on `web.py` Jinja environments |
| `mdb_engine/routing/_ssr.py` | Added `| markdown` filter, `asset_url()` global, preload Link headers |
| `mdb_engine/cli/_serve_app.py` | Uses `CachedStaticFiles` + `AssetRegistry` instead of bare `StaticFiles` |
| `mdb_engine/cli/_serve_multi_app.py` | Same as above for multi-app |
| `pyproject.toml` | Version bump + new `perf` and `markdown` extras |

---

## 0.12.1 Patch

**Bug fix:** The `| markdown` filter crashed with a `ValueError` on any Markdown containing links (e.g. `[text](https://example.com)`).

**Root cause:** `nh3.clean()` was called with `rel` in the allowed-attributes dict for `<a>` tags, but `nh3`'s default `link_rel="noopener noreferrer"` conflicts with an explicit `rel` allow — `nh3` raises `ValueError` to prevent ambiguity.

**Fix:** Removed `rel` from the `<a>` allow-list. `nh3`'s default `link_rel` now manages `rel` automatically, which is the correct security behavior (all user-generated links get `rel="noopener noreferrer"`).

**Action required:** `pip install --upgrade mdb-engine` to `>=0.12.1`. No manifest or template changes needed.

---

## 0.12.2 Patch

**Bug fix 1:** The `| markdown` filter stripped `src` from `<img>` tags when the URL used a `data:` scheme (base64-encoded images). `nh3.clean()` defaults `url_schemes` to `{"http", "https", "mailto"}` — `data:` was not included.

**Fix:** Added `url_schemes={"http", "https", "mailto", "data"}` to the `nh3.clean()` call. Inline base64 images now render correctly. A post-process step also neutralizes `data:` URIs in `<a href>` attributes (replacing with `href="#"`) to prevent XSS via `data:text/html` link navigation — so `data:` is safe for images but cannot be weaponized in links.

**Bug fix 2:** When a post's cover image was a `data:` URI, the `og:image` meta tag contained the full multi-KB base64 string. Social platforms (X, LinkedIn, Facebook) cannot render these.

**Fix:** The SEO fallback resolver now skips values starting with `data:` for image-related SEO keys (`og_image`, `og:image`, `twitter_image`, `twitter:image`, `image`), falling through to the next candidate in the chain. The OG image route's cover-field redirect also ignores `data:` URIs, falling through to the auto-generated PNG.

**Action required:** `pip install --upgrade mdb-engine` to `>=0.12.2`. No manifest or template changes needed.

---

## 0.12.3 — Upload Service & Security Hardening

**New feature: GridFS-backed file uploads.** Add `"uploads": {"enabled": true}` to your manifest and the engine exposes `POST /api/_uploads` (multipart or base64 JSON) and `GET /uploads/{hash}.{ext}` (content-addressed serving). Files are stored in per-app GridFS buckets with SHA-256 deduplication.

**Manifest configuration:**

```json
{
  "uploads": {
    "enabled": true,
    "max_size": "5MB",
    "allowed_types": ["image/jpeg", "image/png", "image/gif", "image/webp"],
    "path_prefix": "/uploads",
    "auth": {
      "required": true,
      "roles": ["editor", "admin"]
    }
  }
}
```

**Security hardening included in this release:**
- Serve route validates `file_hash` as hex-only (`^[0-9a-f]{64}$`) to prevent NoSQL injection.
- Zero-byte uploads are rejected with 400.
- `image/svg+xml` removed from default allowed types (opt-in only). SVGs are served with `Content-Disposition: attachment` to prevent XSS.
- `If-None-Match` / 304 support on the serve route saves GridFS round-trips on repeat requests.

**New dependencies for custom routes:**
- `get_upload_service(request)` — raises 503 if uploads not enabled.
- `get_upload_service_optional(request)` — returns `None` if not enabled.

**Action required:** `pip install --upgrade mdb-engine` to `>=0.12.3`. Add `"uploads": {"enabled": true}` to your manifest to activate. No changes needed if you don't use uploads.

---

## 0.12.4 Patch

**Bug fix:** The upload service's file serving route (`GET /uploads/{hash}.{ext}`) crashed with `TypeError: 'GridOut' object is not subscriptable` on every request. `retrieve()` and `delete()` used dict subscripting (`["_id"]`, `.get("metadata")`) on `GridOut` objects returned by Motor's `AsyncIOMotorGridFSBucket.find()`, but `GridOut` exposes data via attributes (`.metadata`, `._id`), not dict access.

**Fix:** Switched `retrieve()` and `delete()` to use attribute access (`grid_out._id`, `getattr(grid_out, "metadata", None)`). Updated the `_find_by_hash` return type annotation accordingly. Unit test fakes now use a `FakeGridOut` wrapper that blocks dict subscripting, so this class of bug will be caught in tests going forward.

**Improved warning:** The startup warning when `MDB_ENGINE_MASTER_KEY` is not set previously read "App-level authentication will not be available", implying cookie/session auth was broken. It now reads: "Envelope encryption for app secrets is disabled. Cookie/session authentication is not affected."

**Action required:** `pip install --upgrade mdb-engine` to `>=0.12.4`. No manifest or template changes needed.

---

## 0.12.5 — RSS/Atom Feed Polish

**RSS/Atom feeds are now spec-compliant out of the box** and include several DX improvements for manifest authors.

### RSS 2.0 compliance

RSS feeds now emit three elements required/recommended by the RSS 2.0 spec and RSS Board best practices:

- `<atom:link rel="self">` — self-referencing link inside `<channel>` (required when the Atom namespace is declared)
- `<lastBuildDate>` — RFC 822 timestamp of when the feed was generated
- `<generator>mdb-engine</generator>`

### Atom 1.0 compliance

Atom feeds now emit:

- Feed-level `<updated>` — RFC 3339 timestamp (required by RFC 4287)
- `<generator>mdb-engine</generator>`

### New pipe transforms: `rfc822` and `rfc3339`

Date fields from MongoDB (ISO strings or Python `datetime` values) can now be formatted inline using pipe transforms in feed item templates:

```json
{
  "ssr": {
    "feeds": {
      "/feed.xml": {
        "collection": "posts",
        "scope": "published",
        "item": {
          "title": "{{doc.title}}",
          "link": "{{base_url}}/posts/{{doc.slug}}",
          "description": "{{doc.excerpt | plain_text | truncate(200)}}",
          "pubDate": "{{doc.created_at | rfc822}}"
        }
      }
    }
  }
}
```

- `rfc822` — formats as `Mon, 06 Apr 2026 12:00:00 GMT` (RSS `pubDate` format)
- `rfc3339` — formats as `2026-04-06T12:00:00+00:00` (Atom `updated` format)

Both transforms handle ISO 8601 strings, Python `str(datetime)` format, and `Z` suffix. Invalid values pass through unchanged.

### Bug fix: `<link rel="alternate">` title placeholders

The `{{site_name}}` placeholder in feed titles was not resolved in the `<link rel="alternate">` tag injected into SSR page `<head>` tags. The title now resolves correctly.

### Bug fix: multi-app feed link paths

In multi-app deployments, the `<link rel="alternate" href="...">` tag in `<head>` now correctly includes the app's `base_path` prefix (e.g. `/blog/feed.xml` instead of `/feed.xml`).

### Conditional GET (304 Not Modified)

Feed routes now honor the `If-None-Match` request header. When a feed aggregator sends back the `ETag` from a previous response, the engine returns `304 Not Modified` with no body — saving bandwidth on repeated polls.

### Action required

`pip install --upgrade mdb-engine` to `>=0.12.5`. No manifest changes needed — existing `ssr.feeds` configurations automatically benefit from all improvements. To use the new date transforms, update your feed item templates (e.g. `"pubDate": "{{doc.created_at | rfc822}}"`).

---

See [UPGRADE-0.13.0.md](UPGRADE-0.13.0.md) for the next release.
