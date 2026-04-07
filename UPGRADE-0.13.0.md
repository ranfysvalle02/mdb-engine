# Upgrading to mdb-engine 0.13.0

**Release focus:** Nine manifest-driven features that improve SEO, caching correctness, and developer experience for SSR sites — all without writing Python.

---

## Quick checklist

1. `pip install --upgrade mdb-engine`
2. Review your existing `cache` configs — `Cache-Control` now prepends `public` by default (CDN-friendly)
3. Add `seo.robots`, `ssr.lang`, `ssr.redirects`, `seo.breadcrumbs` to manifests as needed
4. No breaking changes — existing manifests work unchanged

---

## What changed

### Overview of new features

| Feature | Impact | Manifest key |
|---|---|---|
| Per-route meta robots | HIGH | `ssr.routes.*.seo.robots` |
| Configurable `<html lang>` | HIGH | `ssr.lang` |
| Preconnect / DNS-prefetch | HIGH | `ssr.preload[].rel` |
| Cache-Control scope (public/private) | HIGH | `ssr.routes.*.cache.scope` |
| Vary header | HIGH | `ssr.routes.*.cache.vary` |
| Manifest-driven redirects | HIGH | `ssr.redirects` |
| Trailing-slash normalization | MEDIUM | `ssr.trailing_slash` |
| ETag / conditional GET on SSR pages | MEDIUM | `ssr.routes.*.cache.etag` |
| Breadcrumb JSON-LD shorthand | MEDIUM | `ssr.routes.*.seo.breadcrumbs` |

---

## 1. Per-Route Meta Robots

**What it does:** Lets you tell search engines to skip indexing a page, follow/nofollow links, or any combination — directly from the manifest. Useful for search result pages, paginated archives, tag listings, or any route that shouldn't appear in search indexes.

**What existed before:** Nothing. There was no `<meta name="robots">` anywhere in `mdb_base.html` and no way to set it from the manifest.

**Usage:**

```json
{
  "ssr": {
    "routes": {
      "/search": {
        "template": "search.html",
        "seo": {
          "title": "Search Results",
          "robots": "noindex, follow"
        }
      },
      "/tags/:tag": {
        "template": "tag.html",
        "seo": {
          "title": "{{tag}} — Tags",
          "robots": "noindex, nofollow"
        }
      }
    }
  }
}
```

Renders `<meta name="robots" content="noindex, follow">` in `<head>`. Omit `robots` entirely to use search-engine defaults (`index, follow`).

**Common values:**

| Value | Meaning |
|---|---|
| `"noindex, follow"` | Don't index this page, but follow its links |
| `"noindex, nofollow"` | Don't index, don't follow links |
| `"index, nofollow"` | Index the page, but don't follow its outbound links |
| `"noarchive"` | Index but don't cache a copy |

---

## 2. Configurable `<html lang>`

**What it does:** Sets the `<html lang="...">` attribute for the entire site. Google uses this as a language signal and it's an accessibility requirement (WCAG).

**What existed before:** Hardcoded `<html lang="en">` in `mdb_base.html`.

**Usage:**

```json
{
  "ssr": {
    "lang": "es",
    "routes": { ... }
  }
}
```

Renders `<html lang="es">`. Default remains `"en"` when omitted, so existing sites are unaffected.

---

## 3. Preconnect / DNS-Prefetch Hints

**What it does:** If your site loads fonts from Google Fonts, images from a CDN, or analytics from a third-party domain, a `preconnect` hint saves 100–300ms per domain on mobile. The `preload` infrastructure already existed — it just didn't support `rel=preconnect` or `rel=dns-prefetch`.

**What existed before:** `ssr.preload` only supported `rel=preload` with `as` types (`style`, `script`, `font`, `image`, `fetch`, `document`).

**Usage:**

```json
{
  "ssr": {
    "preload": [
      { "href": "/public/style.css", "as": "style" },
      { "href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": true },
      { "href": "https://cdn.example.com", "rel": "dns-prefetch" }
    ]
  }
}
```

This emits both:
- **Link response header:** `<https://fonts.googleapis.com>; rel=preconnect; crossorigin`
- **HTML `<link>` tags in `<head>`:** `<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>`

**Schema change:** The `as` field is no longer required. It is only needed for `rel=preload` (the default) and is ignored for `preconnect` / `dns-prefetch`.

**Per-route preloads work too:**

```json
{
  "ssr": {
    "preload": [
      { "href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": true }
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

---

## 4. Cache-Control Scope (public / private)

**What it does:** `Cache-Control` now includes a scope directive (`public` or `private`) before `max-age`. This tells CDNs whether they're allowed to cache the response.

**What existed before:** `_build_cache_header` only emitted `max-age=X, stale-while-revalidate=Y` with no scope. CDN behavior was undefined.

**Usage:**

```json
{
  "ssr": {
    "routes": {
      "/": {
        "template": "index.html",
        "cache": { "ttl": "1h" }
      },
      "/dashboard": {
        "template": "dashboard.html",
        "auth": true,
        "cache": {
          "ttl": "5m",
          "scope": "private",
          "stale_while_revalidate": "30s"
        }
      }
    }
  }
}
```

| Route | Cache-Control header |
|---|---|
| `/` | `public, max-age=3600` |
| `/dashboard` | `private, max-age=300, stale-while-revalidate=30` |

**Migration note:** Existing `cache` configs that previously produced `max-age=300` will now produce `public, max-age=300`. This is correct behavior for CDN caching and should not cause issues. If you're behind a CDN that was caching pages it shouldn't have been, add `"scope": "private"` to those routes.

---

## 5. Vary Header on SSR Routes

**What it does:** Sets the `Vary` response header, which tells CDNs to store separate cached copies based on the listed request headers. Essential when an SSR page looks different for logged-in vs. anonymous users.

**What existed before:** No `Vary` header was set on SSR responses.

**Usage:**

```json
{
  "cache": {
    "ttl": "5m",
    "vary": ["Cookie", "Accept-Language"]
  }
}
```

**Automatic behavior:** When `auth: true` is set on a route and no explicit `vary` is configured, `Vary: Cookie` is added automatically. This prevents CDNs from serving personalized content to anonymous users.

---

## 6. Manifest-Driven 301/302 Redirects

**What it does:** When you change URL structures (`/blog/my-post` → `/posts/my-post`), you need redirects for SEO equity. Previously there was no way to do this without writing Python.

**What existed before:** No redirect DSL in the manifest.

**Usage:**

```json
{
  "ssr": {
    "redirects": {
      "/old-path": { "to": "/new-path", "status": 301 },
      "/blog/:slug": { "to": "/posts/:slug", "status": 301 },
      "/temp": { "to": "/", "status": 302 }
    },
    "routes": { ... }
  }
}
```

- Supports both Express-style `:param` and FastAPI-style `{param}` patterns
- Path params from the source URL are interpolated into the target
- Redirects are registered **before** SSR routes so they take precedence
- Default status is `301` (permanent) if omitted

---

## 7. Trailing-Slash Normalization

**What it does:** `/posts/hello` and `/posts/hello/` are different URLs to Google. If both return 200, you get duplicate content. Most SEO tools flag this. This feature enforces a canonical policy with 301 redirects.

**What existed before:** Nothing systematic — `shared_middleware` preserved trailing slashes for route matching but didn't enforce a canonical policy.

**Usage:**

```json
{
  "ssr": {
    "trailing_slash": "strip",
    "routes": { ... }
  }
}
```

| Value | Behavior |
|---|---|
| `"strip"` | `/about/` → 301 → `/about` (root `/` is exempt) |
| `"ensure"` | `/about` → 301 → `/about/` |
| `"ignore"` | No redirects (default) |

---

## 8. ETag / Conditional GET on SSR Pages

**What it does:** The same ETag pattern that was added for feeds in 0.12.5 is now available for SSR pages. After rendering HTML, the engine computes an MD5 hash, sets an `ETag` header, and returns `304 Not Modified` when a browser sends back a matching `If-None-Match`. This saves bandwidth for SSR pages that don't change often (blog posts, about pages) and helps with CDN origin-pull and browser back-button performance.

**What existed before:** SSR handler set `X-Cache-Status: MISS` but no `ETag` or `If-None-Match` check.

**Usage:**

```json
{
  "cache": {
    "ttl": "1h",
    "etag": true
  }
}
```

This is **opt-in** because hashing large pages on every request has CPU cost. Best suited for mostly-static pages.

---

## 9. Breadcrumb JSON-LD Shorthand

**What it does:** Breadcrumbs are the single most common structured data type after Article. The generic `json_ld` already exists but requires verbose config. This shorthand generates a `BreadcrumbList` JSON-LD object from a simple array.

**What existed before:** `seo.json_ld` accepted arbitrary objects with placeholder resolution, but you had to write the full BreadcrumbList schema by hand.

**Usage:**

```json
{
  "seo": {
    "breadcrumbs": [
      { "name": "Home", "url": "{{base_url}}/" },
      { "name": "Blog", "url": "{{base_url}}/blog" },
      { "name": "{{post.title}}", "url": "{{base_url}}/posts/{{post.slug}}" }
    ]
  }
}
```

Auto-generates:

```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Home", "item": "https://myblog.com/" },
    { "@type": "ListItem", "position": 2, "name": "Blog", "item": "https://myblog.com/blog" },
    { "@type": "ListItem", "position": 3, "name": "My Post", "item": "https://myblog.com/posts/my-post" }
  ]
}
```

When combined with an explicit `json_ld` config, both are merged into a `@graph` array automatically.

---

## Full example manifest

All new keys are inside the existing `ssr` block:

```json
{
  "schema_version": "2.0",
  "slug": "my_blog",
  "name": "My Blog",

  "ssr": {
    "enabled": true,
    "lang": "en",
    "site_name": "My Blog",
    "site_description": "A blog about things",
    "base_url": "https://myblog.com",
    "trailing_slash": "strip",

    "redirects": {
      "/blog/:slug": { "to": "/posts/:slug", "status": 301 },
      "/old-about": { "to": "/about" }
    },

    "preload": [
      { "href": "/public/style.css", "as": "style" },
      { "href": "/public/fonts/inter.woff2", "as": "font", "crossorigin": true },
      { "href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": true }
    ],

    "routes": {
      "/": {
        "template": "index.html",
        "data": { "posts": { "collection": "posts", "limit": 10 } },
        "seo": { "title": "My Blog — Home", "description": "Latest posts" },
        "cache": { "ttl": "5m", "etag": true }
      },
      "/posts/:slug": {
        "template": "post.html",
        "data": { "post": { "collection": "posts", "id_param": "slug" } },
        "seo": {
          "title": "{{post.title}} — My Blog",
          "description": "{{post.excerpt | plain_text | truncate(160)}}",
          "breadcrumbs": [
            { "name": "Home", "url": "{{base_url}}/" },
            { "name": "{{post.title}}", "url": "{{base_url}}/posts/{{post.slug}}" }
          ]
        },
        "cache": { "ttl": "1h", "etag": true }
      },
      "/search": {
        "template": "search.html",
        "seo": {
          "title": "Search",
          "robots": "noindex, follow"
        }
      },
      "/dashboard": {
        "template": "dashboard.html",
        "auth": true,
        "seo": { "title": "Dashboard", "robots": "noindex, nofollow" },
        "cache": { "ttl": "5m", "scope": "private" }
      }
    }
  }
}
```

---

## What did NOT change

- **All existing manifest keys** — no schema changes to existing fields
- **`create_app()` and `create_multi_app()` API** — same signatures, same behavior
- **`mount_ssr_routes()` API** — same signature, backward-compatible
- **`mdb-engine serve` / `mdb-engine serve-multi`** — same CLI interface, new features automatic
- **Authentication, authorization, CRUD, WebSockets** — unaffected
- **Memory, graph, embedding, LLM services** — unaffected
- **Feeds, sitemap, robots.txt, OG image generation** — unaffected

---

## Manifest schema additions

### SSR top-level (new keys)

| Key | Type | Default | Description |
|---|---|---|---|
| `ssr.lang` | string | `"en"` | `<html lang>` attribute |
| `ssr.trailing_slash` | `"strip"` \| `"ensure"` \| `"ignore"` | `"ignore"` | Trailing-slash redirect policy |
| `ssr.redirects` | object | `{}` | URL redirect definitions |
| `ssr.preload[].rel` | `"preload"` \| `"preconnect"` \| `"dns-prefetch"` | `"preload"` | Link relation type |

### Per-route SEO (new keys)

| Key | Type | Default | Description |
|---|---|---|---|
| `seo.robots` | string | — | `<meta name="robots">` value |
| `seo.breadcrumbs` | array | — | Breadcrumb items for JSON-LD |

### Per-route cache (new keys)

| Key | Type | Default | Description |
|---|---|---|---|
| `cache.scope` | `"public"` \| `"private"` | `"public"` | Cache-Control scope directive |
| `cache.vary` | array of strings | — | Vary response header values |
| `cache.etag` | boolean | `false` | Enable ETag / 304 support |

---

## Modified files

| File | What changed |
|---|---|
| `mdb_engine/core/manifest.py` | Added `lang`, `trailing_slash`, `redirects` to SSR schema; `robots`, `breadcrumbs` to SEO schema; `scope`, `vary`, `etag` to cache schema; `rel` to preload schema |
| `mdb_engine/routing/_ssr.py` | Redirect routes, trailing-slash middleware, ETag support, Vary header, breadcrumb JSON-LD builder, preconnect Link headers, preload `<link>` tags in template context |
| `mdb_engine/templates/mdb_base.html` | Configurable `lang` attribute, `<meta name="robots">`, `<link>` tags for preload/preconnect items |

---

## Action required

`pip install --upgrade mdb-engine` to `>=0.13.0`. No manifest changes needed for existing apps — all new features are opt-in. Add the new manifest keys as needed for your SEO requirements.
