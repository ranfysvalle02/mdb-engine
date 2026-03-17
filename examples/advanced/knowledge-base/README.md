# Zero-Code Knowledge Base

**A production-grade, server-rendered knowledge base from a single JSON file
and a handful of HTML templates. No Python. No JavaScript frameworks.
No build step.**

This example showcases every major feature introduced in mdb-engine 0.8.7.
It is the most complete demonstration of manifest-driven development in the
project.

```
knowledge-base/
├── manifest.json          # the ENTIRE application
├── templates/
│   ├── index.html         # server-rendered article listing with pagination
│   ├── article.html       # server-rendered article page with comments
│   ├── dashboard.html     # auth-gated personal dashboard
│   └── 404.html           # custom error page
├── public/
│   └── style.css          # static assets
├── docker-compose.yml     # one-command setup
└── README.md
```

## The Story

Imagine you're building a knowledge base for your team. You need:

- **Articles** with categories, tags, and cover images
- **Comments** that require moderation before they appear
- **Roles**: admins manage everything, editors write content, moderators
  approve comments, readers browse and comment
- **SEO**: every article needs to be crawlable with proper meta tags,
  Open Graph, and structured data (JSON-LD)
- **Pagination**: the homepage lists 12 articles per page
- **Activity feed**: track who created, published, or deleted articles
- **Notifications**: auto-generated when articles are published (conditional
  hooks that detect status transitions)
- **Activity feed**: a server-rendered page showing every action taken
- **Cascade deletes**: when an article is deleted, its comments and
  bookmarks go with it
- **Denormalized counters**: show "12 comments" on article cards without
  an extra query per card
- **Automatic archival**: drafts untouched for 90 days are auto-archived
- **Sitemap**: auto-generated for search engines
- **Referential integrity**: comments can't reference non-existent articles
- **Tag validation**: only tags from the `tags` collection are accepted
- **Custom 404 page**: styled, branded, helpful

In a traditional stack, this is weeks of work. Models, migrations,
serializers, views, templates, tests, deployment config.

**With mdb-engine 0.8.7, it's one JSON file.**

## Run It

```bash
docker compose up
```

Open:
- **http://localhost:8000** -- server-rendered knowledge base
- **http://localhost:8000/docs** -- auto-generated Swagger API docs
- **http://localhost:8000/sitemap.xml** -- auto-generated sitemap

## Demo Accounts

| Email | Password | Role | Can do |
|-------|----------|------|--------|
| admin@example.com | admin123 | admin | Everything (inherits editor + moderator + reader) |
| editor@example.com | editor123 | editor | Create/edit articles, manage tags (inherits reader) |
| reader@example.com | reader123 | reader | Browse articles, post comments, bookmark |

Self-registration is enabled with role `reader`.

## What the Manifest Defines

### 7 Collections

| Collection | Features |
|------------|----------|
| `articles` | Schema, soft delete, owner_field, **per-role writable_fields**, scopes, pipelines, relations, **computed** (comment_count), **conditional hooks**, **cascade delete**, **cache directives**, **x-references**, **x-values-from** |
| `comments` | Schema, soft delete, owner_field, moderation (approved/pending scopes), **x-references** to articles, relations |
| `categories` | Schema with **x-unique** on name + slug, public read, admin-only write |
| `tags` | Schema with **x-unique**, editor-writable, used for **x-values-from** validation |
| `bookmarks` | Auth-required, owner_field, **x-references** to articles, relations |
| `notifications` | Auto-populated via **conditional hooks** on publish, **TTL** (7-day auto-expiry), unread scope |
| `activity_feed` | Read-only, **TTL** (30-day auto-expiry), populated via hooks |

### Role Hierarchy

```json
"role_hierarchy": {
  "admin": ["editor", "moderator", "reader"],
  "editor": ["reader"],
  "moderator": ["reader"]
}
```

An admin inherits every permission. An editor can do everything a reader
can plus write articles. A moderator can do everything a reader can plus
approve comments.

### Per-Role Writable Fields

```json
"writable_fields": {
  "editor": ["title", "slug", "body", "excerpt", "category_id", "tags", "status", "cover_image"],
  "moderator": ["status", "tags"]
}
```

Editors write full articles. Moderators can only change status (publish/
archive) and tags. Admins bypass the allowlist entirely.

### Conditional Hooks (Publish Detection)

A notification is created **only** when an article transitions to published:

```json
{
  "action": "insert",
  "collection": "notifications",
  "document": {
    "type": "article_published",
    "message": "{{doc.title}}",
    "article_id": "{{doc._id}}",
    "actor": "{{user.email}}",
    "read": false,
    "timestamp": "$$NOW"
  },
  "if": {
    "doc.status": "published",
    "prev.status": { "$ne": "published" }
  }
}
```

This means:
- Saving a draft? No notification.
- Updating a published article's body? No notification.
- Publishing for the first time? Notification created.
- Re-publishing after archiving? Notification created.

The `prev.*` context gives the hook access to the document's state
**before** the update, enabling precise transition detection.

You can verify this yourself:

1. Log in as editor, create an article (status: draft)
2. Visit `/activity` -- you'll see "article_created" but no notification
3. Edit the article, change status to "published"
4. Visit `/activity` -- now you'll see the notification appeared

> **Want webhooks too?** Add an `http` hook action alongside the insert.
> See the `WHATS_NEW_087.md` docs for the Slack/Discord/email pattern.

### Cascade Deletes

```json
"cascade": {
  "on_delete": [
    { "collection": "comments", "match_field": "article_id", "action": "delete" },
    { "collection": "bookmarks", "match_field": "article_id", "action": "delete" }
  ],
  "on_soft_delete": [
    { "collection": "comments", "match_field": "article_id", "action": "soft_delete" }
  ]
}
```

Hard-delete an article and its comments + bookmarks are gone. Soft-delete
it and the comments are soft-deleted too (restorable).

### Referential Integrity

```json
"category_id": {
  "type": "string",
  "x-references": { "collection": "categories", "field": "_id" }
}
```

Try to create an article with a non-existent category_id? 422.

### Tag Validation

```json
"tags": {
  "type": "array",
  "items": { "type": "string" },
  "x-values-from": { "collection": "tags", "field": "name" }
}
```

Every tag must exist in the `tags` collection. Create your allowed tags
first, then articles can only use those tags.

### Cache Directives

```json
"cache": {
  "scope:published": { "ttl": "5m", "stale_while_revalidate": "30s" },
  "default": { "ttl": "0" }
}
```

The published articles API endpoint gets `Cache-Control: max-age=300,
stale-while-revalidate=30`. Draft/review queries bypass the cache.

### Scheduled Jobs

```json
"jobs": {
  "archive_stale_drafts": {
    "schedule": "@daily",
    "action": "update",
    "collection": "articles",
    "filter": { "status": "draft", "updated_at": { "$lt": "$$NOW_MINUS_90D" } },
    "update": { "$set": { "status": "archived" } }
  }
}
```

Every day at midnight, drafts untouched for 90 days are auto-archived.
No cron, no external scheduler.

## Server-Side Rendering

### How SSR Routes Work

The `ssr` section defines 4 routes:

| Route | Template | Auth | Cache | Data |
|-------|----------|------|-------|------|
| `/` | `index.html` | Public | 5m | 12 published articles (with category + comment_count), all categories |
| `/articles/{id}` | `article.html` | Public | 10m | Article (with category + author), approved comments |
| `/dashboard` | `dashboard.html` | Required | None | User's articles + bookmarks |
| `/activity` | `activity.html` | Required | None | Activity feed + unread notifications |

### SEO

Every article page includes:

- `<title>` with the article title
- `<meta name="description">` with the excerpt
- Open Graph tags (`og:title`, `og:description`, `og:type`, `og:image`)
- JSON-LD structured data (Article schema)

This is resolved from the manifest SEO config:

```json
"seo": {
  "title": "{{article.title}} — Knowledge Base",
  "description": "{{article.excerpt}}",
  "og_type": "article",
  "og_image": "{{article.cover_image}}",
  "json_ld": {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "{{article.title}}",
    "datePublished": "{{article.created_at}}",
    "author": { "@type": "Person", "name": "{{article.author_name}}" }
  }
}
```

### Pagination

The homepage shows 12 articles per page. Navigate with `?page=2`.
Templates receive pagination context:

```html
Page {{ articles_pagination.page }} of {{ articles_pagination.total_pages }}
```

### Custom 404

When an article ID doesn't exist, the styled `404.html` template renders
instead of a raw JSON error.

### Sitemap

Auto-generated at `/sitemap.xml`. Includes the homepage and every
published article URL. The `/dashboard` route is excluded because it's
auth-gated.

## CLI Tools

### Preview what the manifest generates

```bash
mdb-engine dry-run manifest.json
```

### Compare manifest versions

```bash
mdb-engine diff manifest.v1.json manifest.json
```

### Generate a TypeScript API client

```bash
mdb-engine codegen manifest.json --target typescript --out api-client.ts
```

## Feature Coverage

This single example demonstrates **every** feature added in 0.8.7:

| Feature | Where in manifest |
|---------|------------------|
| SSR with Jinja2 templates | `ssr.routes` |
| SSR pagination | `ssr.routes["/"].data.articles.limit` + `?page=` |
| SSR relations ($lookup) | `ssr.routes.data.*.populate` |
| SSR computed fields | `ssr.routes.data.*.computed` |
| SSR JSON-LD | `ssr.routes["/articles/{id}"].seo.json_ld` |
| SSR Cache-Control | `ssr.routes["/"].cache` |
| SSR custom 404 | `templates/404.html` |
| SSR sitemap.xml | `ssr.sitemap: true` |
| SSR auth-gated routes | `ssr.routes["/dashboard"].auth` |
| Atomic hook operators | `comments.hooks.after_create` (`$inc`) |
| Conditional hooks | `articles.hooks.after_update[0].if` and `[1].if` |
| Conditional insert (notifications) | `articles.hooks.after_update[1]` |
| Per-role writable fields | `articles.writable_fields` (role map) |
| Multi-role / role hierarchy | `auth.users.role_hierarchy` |
| Referential integrity | `articles.schema.category_id.x-references` |
| Schema validation (x-values-from) | `articles.schema.tags.x-values-from` |
| Cascade delete policies | `articles.cascade` |
| Cache directives | `articles.cache` |
| Scheduled jobs | `jobs.archive_stale_drafts` |
| Template `{{prev.*}}` | `articles.hooks.after_update[0].if` |

## Try It Yourself (Verification Walkthrough)

After `docker compose up`, walk through these steps to see every feature
in action:

### 1. Browse as anonymous visitor

- Open **http://localhost:8000** -- server-rendered homepage (view page
  source to confirm: real HTML, not an empty `<div>`)
- Open **http://localhost:8000/sitemap.xml** -- auto-generated sitemap
- Try **http://localhost:8000/articles/000000000000000000000000** -- custom
  404 page

### 2. Create content as editor

```bash
# Log in as editor
curl -s -c cookies.txt http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"editor@example.com","password":"editor123"}'

# Create a tag (required by x-values-from)
curl -s -b cookies.txt http://localhost:8000/api/tags \
  -H 'Content-Type: application/json' \
  -d '{"name":"tutorial"}'

# Create a category
curl -s -b cookies.txt -c cookies.txt http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"admin123"}'

curl -s -b cookies.txt http://localhost:8000/api/categories \
  -H 'Content-Type: application/json' \
  -d '{"name":"Guides","slug":"guides","description":"How-to guides"}'

# Log back in as editor and create a draft article
curl -s -b cookies.txt -c cookies.txt http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"editor@example.com","password":"editor123"}'

curl -s -b cookies.txt http://localhost:8000/api/articles \
  -H 'Content-Type: application/json' \
  -d '{"title":"Getting Started","body":"<p>Welcome to the knowledge base.</p>","excerpt":"A quick start guide.","tags":["tutorial"]}'
```

### 3. Verify the activity feed

- Open **http://localhost:8000/activity** (log in as editor first)
- You'll see an "article_created" event but **no notification** (it's still
  a draft)

### 4. Publish and watch conditional hooks fire

```bash
# Get the article ID from the create response, then publish it:
curl -s -b cookies.txt http://localhost:8000/api/articles/ARTICLE_ID \
  -X PATCH -H 'Content-Type: application/json' \
  -d '{"status":"published"}'
```

- Refresh **http://localhost:8000** -- the article appears (server-rendered!)
- Refresh **http://localhost:8000/activity** -- now you see BOTH an
  "article_published" event AND an unread notification

### 5. Verify per-role writable fields

```bash
# Log in as reader
curl -s -b cookies.txt -c cookies.txt http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"reader@example.com","password":"reader123"}'

# Try to create an article (should fail - readers can't write articles)
curl -s -b cookies.txt http://localhost:8000/api/articles \
  -X POST -H 'Content-Type: application/json' \
  -d '{"title":"Nope"}'
# Returns 403
```

### 6. Check SEO (view page source)

Open any article page and view the page source. You'll see:
- `<title>` with the article title
- `<meta name="description">` with the excerpt
- `<meta property="og:title">` for social sharing
- `<script type="application/ld+json">` with Article schema

This is what Google and social media crawlers see -- fully rendered HTML,
not a blank JavaScript shell.

---

## Zero Code Required

This entire application -- server-rendered pages, SEO optimization,
role-based access control, cascade deletes, webhooks, pagination, activity
feeds, scheduled jobs, and a type-safe API -- is defined in **one JSON file
and four HTML templates**.

No `web.py`. No `routes.py`. No `models.py`. No `requirements.txt`.

Just:

```bash
mdb-engine serve manifest.json
```
