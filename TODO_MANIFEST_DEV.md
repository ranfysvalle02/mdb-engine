# Manifest-Driven Development — Roadmap & Limitations

## Current Limitations

1. **No `before_*` hooks** — can't block or transform writes. Only `after_create`, `after_update`, `after_delete` exist. No way to reject a write based on custom business logic (e.g. "max 5 active projects per user").

2. **Template resolver caps at depth 3, only 4 namespaces** — `{{user.*}}`, `{{doc.*}}`, `{{prev.*}}`, `{{env.*}}`, `$$NOW`. Can't reference hook results, computed values, counters, other collections, or request query params inside hooks/policies.

3. **Hook conditions are flat AND only** — `_evaluate_condition` supports `$ne`, `$in`, `$gt`, etc. but no `$or`, `$and`, or nested boolean logic.

4. **Hooks are fire-and-forget** — errors are logged and swallowed. Can't make hooks synchronous/blocking. `TransactionalHookExecutor` wraps DB ops in a transaction but can't roll back HTTP webhooks.

5. **No cross-collection or cross-document validation** — `x-references` and `x-values-from` check FK existence and value sets, but can't express aggregate constraints like "sum of budgets must not exceed limit."

6. **SSR data sources are independent** — no chained queries (fetch A, use A's result to query B), no data transformation between fetch and render, no external API sources, no POST/session input.

7. **JSON Schema only** — no conditional cross-field rules (`if`/`then`/`else`), no "if type is recurring then frequency is required" without Python.

8. **No manifest-level rate limiting** — middleware infra exists (`_add_rate_limit_middleware`) but isn't exposed declaratively per-collection.

9. **Crude cron parsing** — `parse_cron_to_seconds` supports shortcuts and simple intervals but not real cron expressions. Can't express "3:15 AM on weekdays."

10. **Manifest is static** — no hot-reload, no runtime reconfiguration without restart.

---

## Graduation Path (Progressive Disclosure)

The goal: each step only requires **adding a file or a key**, never rewriting what already works.

| Level | What you write | When to use |
|-------|---------------|-------------|
| 1. Pure manifest | `manifest.json` only | CRUD, auth, policies, scopes, hooks, SSR, memory |
| 2. Manifest + hook handlers | `manifest.json` + `hooks.py` | Business logic that can't be expressed declaratively |
| 3. Manifest + custom routes | `manifest.json` + `web.py`/`routes.py` | Entirely custom endpoints alongside auto-CRUD |
| 4. Manifest + plugins | `manifest.json` + plugin modules | Full custom middleware, workers, integrations |
| 5. Programmatic + manifest assist | Own FastAPI app via `engine.lifespan()` | Full control, cherry-pick auto-CRUD per collection |

---

## Tier 1: Low-Effort, High-Leverage

### 1.1 `before_*` hooks with veto power

Add `before_create`, `before_update`, `before_delete` events. A `before_*` hook can reject the operation.

```json
"hooks": {
  "before_create": [
    {
      "action": "validate",
      "collection": "projects",
      "filter": { "owner_id": "{{user._id}}", "status": "active" },
      "max_count": 5,
      "reject": { "status": 403, "detail": "Maximum 5 active projects" }
    }
  ]
}
```

Framework does `count_documents` against the filter, rejects if it exceeds `max_count`. Declarative guard types: `max_count`, `must_exist`, `must_not_exist`.

### 1.2 `$or` / `$and` in hook conditions

Extend `_evaluate_condition` (~20 lines) to support nested boolean logic:

```json
"if": {
  "$or": [
    { "doc.status": "published", "doc.priority": { "$gt": 3 } },
    { "doc.role": "admin" }
  ]
}
```

### 1.3 Manifest-level rate limiting

Wire existing middleware infra to a declarative config:

```json
"collections": {
  "messages": {
    "rate_limit": { "writes": "100/min", "reads": "1000/min" }
  }
}
```

### 1.4 Built-in transform functions in hook templates

Small set of built-in transforms for common needs:

```json
"document": {
  "slug": { "$slugify": "{{doc.title}}" },
  "word_count": { "$length": { "$split": ["{{doc.body}}", " "] } },
  "summary": { "$truncate": ["{{doc.body}}", 200] }
}
```

Target set: `$slugify`, `$lowercase`, `$uppercase`, `$truncate`, `$concat`, `$coalesce`, `$split`, `$length`.

---

## Tier 2: The Graduation Bridge

### 2.1 Python hook handlers referenced from manifest (HIGHEST ROI)

Let users point to Python functions directly from the manifest:

```json
"hooks": {
  "before_create": [
    { "action": "call", "handler": "hooks.validate_budget_limit" }
  ],
  "after_create": [
    { "action": "call", "handler": "hooks.send_welcome_email" }
  ]
}
```

Resolved as a dotted import path relative to the app directory (like `web.py`/`routes.py` auto-discovery). Fixed handler signature:

```python
async def validate_budget_limit(ctx: HookContext) -> HookResult:
    total = await ctx.db["projects"].count_documents(
        {"owner_id": ctx.user["_id"], "status": "active"}
    )
    if total >= 5:
        return HookResult.reject(403, "Max 5 active projects")
    return HookResult.ok()
```

User stays in the manifest for routing, auth, CRUD, schema. Drops to Python *only* for the hook that needs it.

### 2.2 Chained SSR data sources with `depends_on`

Allow data sources to reference each other via `{{data.<source>.<field>}}`:

```json
"data": {
  "article": { "collection": "articles", "id_param": "id" },
  "related": {
    "collection": "articles",
    "filter": { "category_id": "{{data.article.category_id}}" },
    "depends_on": "article",
    "limit": 5
  }
}
```

`depends_on` declares execution order. `{{data.*}}` is a new template namespace.

### 2.3 Manifest-declared custom validators (`x-validate`)

Cross-field and cross-document validation without Python:

```json
"properties": {
  "end_date": {
    "type": "string",
    "x-validate": { "$gt": "{{doc.start_date}}" }
  },
  "budget": {
    "type": "number",
    "x-validate": {
      "$aggregate": {
        "collection": "projects",
        "filter": { "owner_id": "{{user._id}}" },
        "sum": "budget",
        "max": 100000
      }
    }
  }
}
```

---

## Tier 3: Platform-Level

### 3.1 Hot-reload manifests

Admin endpoint (`POST /_admin/reload`) or file watcher. Diffs new manifest against old, applies changes without restart. Critical for multi-tenant platforms where each tenant has their own manifest.

### 3.2 Proper cron via `croniter`

Replace `parse_cron_to_seconds` with real cron evaluation. Compute next run time and sleep until then instead of fixed intervals.

### 3.3 `"plugins"` manifest key

For when someone truly outgrows hooks:

```json
"plugins": [
  { "module": "plugins.analytics", "config": { "track_reads": true } },
  { "module": "plugins.stripe_billing", "config": { "webhook_secret": "{{env.STRIPE_SECRET}}" } }
]
```

Each plugin receives the FastAPI app, engine, and manifest at startup. Can register routes, middleware, hooks. User still benefits from the manifest for everything else.

---

## Priority Order

| # | Item | Effort | Impact | Notes |
|---|------|--------|--------|-------|
| 1 | Python hook handlers (2.1) | Medium | **Critical** | The bridge from manifest to code. Highest ROI. |
| 2 | `before_*` hooks (1.1) | Medium | High | Most common reason people need to escape the manifest. |
| 3 | `$or`/`$and` conditions (1.2) | Small | Medium | ~20 lines, big expressiveness gain. |
| 4 | Rate limiting (1.3) | Small | Medium | Wiring only, middleware exists. |
| 5 | Chained SSR sources (2.2) | Medium | Medium | Unlocks real SSR apps. |
| 6 | `x-validate` (2.3) | Medium | Medium | Cross-field/cross-doc validation. |
| 7 | Transform functions (1.4) | Small | Medium | Common data munging without Python. |
| 8 | Proper cron (3.2) | Small | Low | Replace with `croniter`. |
| 9 | Hot-reload (3.1) | Large | High | Multi-tenant requirement. |
| 10 | Plugins (3.3) | Large | High | Full extensibility story. |
