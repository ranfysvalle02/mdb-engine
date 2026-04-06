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

**Allowed attributes:** `href`, `title`, `rel` on links; `src`, `alt`, `loading` on images; `class` on code/pre/div/span; `align` on table cells.

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

**After (0.12.0):**

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
