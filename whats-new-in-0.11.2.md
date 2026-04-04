# What's New in mdb-engine 0.11.2

**Release focus:** SSR, SEO, and blog-oriented features. Ten new capabilities
that eliminate boilerplate, prevent SEO bugs, and give manifest-driven apps
feature parity with purpose-built blog platforms.

## Upgrading

```bash
pip install --upgrade mdb-engine

# For OG image generation (Feature 10):
pip install --upgrade "mdb-engine[og-image]"
```

No breaking changes. All new features are opt-in via manifest config or schema
extensions. Existing manifests work without modification.

---

## 1. Computed Virtual Fields (`x-computed`)

Automatically derive fields on every write. No backfill scripts, no
client-side duplication.

### Manifest

Add `x-computed` to any schema property:

```json
{
  "collections": {
    "posts": {
      "schema": {
        "properties": {
          "body": { "type": "string" },
          "excerpt": {
            "type": "string",
            "x-computed": {
              "from": "body",
              "transform": "plain_text",
              "max_length": 160
            }
          },
          "cover_image": {
            "type": "string",
            "x-computed": {
              "from": "body",
              "transform": "first_image"
            }
          },
          "reading_time": {
            "type": "integer",
            "x-computed": {
              "from": "body",
              "transform": "word_count",
              "divide_by": 200
            }
          }
        }
      }
    }
  }
}
```

### Available Transforms

| Transform | Output | Parameters |
|-----------|--------|------------|
| `plain_text` | Stripped markdown/HTML as plain text | — |
| `first_image` | First `![](url)` or `<img src>` URL | — |
| `word_count` | Word count (or reading time with `divide_by`) | `divide_by` |
| `truncate` | Truncated plain text with ellipsis | `max_length` (default 160) |

### Behavior

- **POST** (create): all computed fields are derived from the source field.
- **PUT** (replace): all computed fields are re-derived.
- **PATCH** (partial update): only re-derives computed fields whose source
  field is in the patch body.

Computed values are persisted to MongoDB and are queryable/indexable.

---

## 2. SEO Fallback Chains

Prevent empty `<meta>` tags with ordered fallback expressions.

### Manifest

String form (with pipe transforms):

```json
{
  "seo": {
    "description": "{{post.body | plain_text | truncate(160)}}"
  }
}
```

Array form (explicit fallback chain):

```json
{
  "seo": {
    "description": {
      "fallback": [
        "{{post.excerpt}}",
        "{{post.body | plain_text | truncate(160)}}",
        "{{site_description}}"
      ]
    }
  }
}
```

The engine evaluates each expression in order and uses the first non-empty
result.

### Available Pipe Transforms

- `plain_text` — strip markdown/HTML
- `truncate(N)` — truncate to N characters with ellipsis

---

## 3. RSS/Atom Feed Generation

Serve valid RSS 2.0 or Atom 1.0 feeds from collection data.

### Manifest

```json
{
  "ssr": {
    "feeds": {
      "/feed.xml": {
        "format": "rss",
        "collection": "posts",
        "scope": "published",
        "sort": { "created_at": -1 },
        "limit": 20,
        "title": "{{site_name}}",
        "description": "{{site_description}}",
        "item": {
          "title": "{{doc.title}}",
          "link": "{{base_url}}/s/posts/{{doc._id}}",
          "description": "{{doc.excerpt}}",
          "pubDate": "{{doc.created_at}}",
          "author": "{{doc.author}}"
        }
      }
    }
  }
}
```

### Details

- Supported formats: `"rss"` (RSS 2.0) and `"atom"` (Atom 1.0).
- Responses include `Content-Type`, `ETag`, and `Cache-Control` headers.
- Respects collection scopes, soft delete, and policy filters.
- Feed links are auto-discovered via `<link rel="alternate">` in `mdb_base.html`.

---

## 4. Auto `<head>` Meta Injection

`mdb_base.html` now renders a complete, standards-compliant SEO `<head>` by
default. No template changes needed.

### What's auto-injected

- `<link rel="canonical">` — request URL with query params stripped
- `<link rel="alternate">` — for each configured RSS/Atom feed
- `<link rel="prev">` / `<link rel="next">` — for paginated routes
- Full Open Graph and Twitter Card sets (already existed)
- JSON-LD (already existed)

Templates that override `{% block seo_meta %}` continue to work as before.

---

## 5. Slug-based URLs (`x-slug`)

Human-readable, SEO-friendly URLs generated from any text field.

### Manifest

```json
{
  "collections": {
    "posts": {
      "schema": {
        "properties": {
          "title": { "type": "string" },
          "slug": {
            "type": "string",
            "x-slug": {
              "from": "title",
              "unique": true
            }
          }
        }
      }
    }
  }
}
```

### Behavior

- **Create:** `"The Shape of Memory"` becomes `"the-shape-of-memory"`.
- **Collision:** appends `-2`, `-3`, etc. (`the-shape-of-memory-2`).
- **Update:** slug is regenerated when the source field changes.
- **SSR routes:** `_fetch_single_doc` automatically tries slug lookup when
  `id_param` is not a valid ObjectId. No SSR config changes needed — routes
  like `/s/posts/{id}` work with both ObjectIds and slugs.

### SSR Route Example

```json
{
  "ssr": {
    "routes": {
      "/s/posts/:id": {
        "template": "post.html",
        "data": {
          "post": {
            "collection": "posts",
            "id_param": "id"
          }
        }
      }
    }
  }
}
```

Both `/s/posts/69b9ee777ca264becdfe7004` and `/s/posts/the-shape-of-memory`
resolve to the same document.

---

## 6. Enhanced Sitemaps

Sitemaps now support `<lastmod>`, `<changefreq>`, `<priority>`, and automatic
index splitting for large sites.

### Manifest

```json
{
  "ssr": {
    "sitemap": {
      "routes": {
        "/s/posts/:id": {
          "lastmod": "{{doc.updated_at}}",
          "changefreq": "weekly",
          "priority": 0.8
        },
        "/s": {
          "changefreq": "daily",
          "priority": 1.0
        }
      },
      "max_urls_per_file": 50000
    }
  }
}
```

### Details

- Backward compatible: `"sitemap": true` retains the existing flat-list
  behavior.
- `lastmod` supports `{{doc.field}}` placeholders resolved per-document.
- When URL count exceeds `max_urls_per_file`, the engine serves a sitemap
  index at `/sitemap.xml` pointing to `/sitemap-1.xml`, `/sitemap-2.xml`, etc.

---

## 7. `robots.txt` Generation

Prevent crawlers from indexing API endpoints, SPA shells, and other non-content
paths.

### Manifest

```json
{
  "ssr": {
    "robots": {
      "allow": ["/s", "/s/posts/*"],
      "disallow": ["/api/*", "/#*"],
      "sitemap": "{{base_url}}/sitemap.xml"
    }
  }
}
```

### Output

```
User-agent: *
Allow: /s
Allow: /s/posts/*
Disallow: /api/*
Disallow: /#*
Sitemap: https://myblog.com/sitemap.xml
```

Served at `GET /robots.txt` with `text/plain` content type.

---

## 8. Pagination SEO

Automatic `rel="prev"` / `rel="next"` for paginated SSR routes. No config
needed — the engine detects pagination in the data context and injects the
tags.

### What's injected

**HTML** (in `mdb_base.html`):

```html
<link rel="prev" href="/s?page=1">
<link rel="next" href="/s?page=3">
```

**HTTP headers:**

```
Link: </s?page=1>; rel="prev", </s?page=3>; rel="next"
```

---

## 9. Cache Status Headers

Every SSR response now includes diagnostic headers for CDN debugging.

### Response Headers

| Header | Value |
|--------|-------|
| `X-Cache-Status` | `MISS` (always, since there is no server-side cache) |
| `X-Cache-Age` | `0` (when `Cache-Control` is configured) |

### Template Context

Templates receive a `cache` object:

```html
{% if cache.is_stale %}
<meta name="x-cache-status" content="stale">
{% endif %}
```

Currently `cache.is_stale` is always `False` and `cache.cached_at` is `None`,
providing a stable contract for future server-side caching.

---

## 10. OG Image Generation

Auto-generate 1200x630 social preview images for documents without a cover
image.

### Setup

```bash
pip install "mdb-engine[og-image]"
```

### Manifest

```json
{
  "ssr": {
    "og_image_fallback": {
      "enabled": true,
      "background": "#1a1a2e",
      "text_color": "#ffffff",
      "font": "Inter",
      "logo": "/public/zero-logo.png",
      "title_field": "title",
      "author_field": "author",
      "cover_field": "cover_image"
    }
  }
}
```

### Behavior

- Registers `GET /og/{collection}/{doc_id}.png`.
- If the document has a `cover_image`, redirects (302) to it.
- Otherwise, renders a title card PNG with the configured branding.
- Images are LRU-cached in memory (256 entries).
- Response includes `Cache-Control: public, max-age=86400`.

### Combining with SEO fallback

```json
{
  "seo": {
    "og_image": {
      "fallback": [
        "{{post.cover_image}}",
        "/og/posts/{{post._id}}.png"
      ]
    }
  }
}
```

---

## Quick Reference: New Manifest Keys

| Location | Key | Type | Purpose |
|----------|-----|------|---------|
| `schema.properties.*.x-computed` | object | `{from, transform, ...}` | Write-time computed field |
| `schema.properties.*.x-slug` | object | `{from, unique}` | Auto-generated URL slug |
| `ssr.seo.*.fallback` | array | `[expr, expr, ...]` | SEO fallback chain |
| `ssr.feeds` | object | `{path: {format, collection, ...}}` | RSS/Atom feeds |
| `ssr.robots` | object | `{allow, disallow, sitemap}` | robots.txt rules |
| `ssr.sitemap` | object | `{routes, max_urls_per_file}` | Enhanced sitemap |
| `ssr.og_image_fallback` | object | `{enabled, background, ...}` | OG image generation |

## New Files

| File | Purpose |
|------|---------|
| `mdb_engine/routing/_computed.py` | Transform registry and write-time application |
| `mdb_engine/routing/_og_image.py` | Pillow-based OG image renderer |

## Changed Files

| File | Changes |
|------|---------|
| `mdb_engine/routing/_ssr.py` | Fallback chains, pipe transforms, feeds, robots.txt, enhanced sitemap, pagination SEO, cache headers, OG image wiring |
| `mdb_engine/routing/auto_crud.py` | Slug generation and computed field hooks in create/replace/patch |
| `mdb_engine/templates/mdb_base.html` | Canonical link, feed auto-discovery, pagination prev/next |
| `mdb_engine/core/manifest.py` | Schema definitions for all new config keys |
| `pyproject.toml` | Version bump, `og-image` optional dependency group |
