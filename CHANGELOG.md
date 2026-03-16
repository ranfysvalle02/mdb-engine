# Changelog

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
