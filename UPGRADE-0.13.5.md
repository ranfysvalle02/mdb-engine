# Upgrading to mdb-engine 0.13.5

**Release focus:** The **admin plane** and the **manifest reconciler** — two
new, opt-in subsystems that together turn `manifest.json` into a real source
of truth for schema (with safe rollback) and give operators a first-class,
authenticated HTTP + CLI surface for day-2 operations.

This guide covers everything that landed across `0.13.3`, `0.13.4`, and
`0.13.5` in a single place — `0.13.3` shipped the epic, `0.13.4` fixed two
regressions found in integration, and `0.13.5` fixed a CI self-cancellation
bug that blocked publishing. If you're on `0.13.0 – 0.13.2`, upgrade
straight to `0.13.5`.

---

## Quick checklist

1. `pip install --upgrade "mdb-engine>=0.13.5"`
2. **Nothing breaks.** The admin plane and reconciler are **opt-in**; apps
   without `admin_api` or `manifest_tracking` blocks behave exactly as before.
3. If you want the admin plane: add an `admin_api` block to `manifest.json`
   and mint a token with `mdb-engine admin secrets bootstrap <slug>`.
4. If you want reconciler safety gates: add a `manifest_tracking` block
   (see §3 below).
5. If you're running mdb-engine in production with more than one worker/pod
   and plan to enable admin rate limits: set
   `admin_api.rate_limits.backend` to `"mongo"`.
6. Set `MDB_ENGINE_MASTER_KEY` to a 32-byte URL-safe secret. Without it,
   token fingerprints degrade to `null` (loudly) and rotation cannot encrypt
   previous-token hashes during overlap windows.

---

## What shipped

| Subsystem                  | Status    | Manifest key(s)                              |
| -------------------------- | --------- | -------------------------------------------- |
| Admin plane (HTTP + CLI)   | NEW       | `admin_api`                                  |
| Manifest reconciler        | NEW       | `manifest_tracking`                          |
| Graceful secret rotation   | NEW       | `admin_api.modules.secrets`                  |
| Pluggable rate-limit store | NEW       | `admin_api.rate_limits.backend`              |
| Stable principal hashing   | Hardening | — (automatic)                                |
| Token fingerprint hardening| Hardening | — (automatic, requires `MDB_ENGINE_MASTER_KEY`) |
| Trash / tombstones         | NEW       | built-in `trash_sweeper` action              |
| CI self-cancel fix         | Infra     | — (repo only)                                |

---

## 1. Admin plane (`admin_api`)

One authenticated HTTP surface mounted at `admin_api.path_prefix`
(default `/__mdb`) composed of small **modules**, each with its own router
and scope vocabulary, sharing auth, audit, rate-limit, and idempotency
middleware.

**Built-in modules:** `health`, `reconciler`, `trash`, `secrets`, `audit`.

### Enable it

```json
{
  "admin_api": {
    "enabled": true,
    "path_prefix": "/__mdb",
    "auth":   { "mode": "app_token", "header": "X-App-Token" },
    "audit":  { "enabled": true, "collection": "_mdb_admin_audit" },
    "rate_limits": {
      "backend": "memory",
      "read":  { "max": 120, "window_seconds": 60 },
      "write": { "max":  15, "window_seconds": 60 }
    },
    "modules": {
      "reconciler": { "enabled": true, "scopes": ["read", "apply"] },
      "trash":      { "enabled": true, "scopes": ["read", "restore", "purge"] },
      "secrets":    { "enabled": true, "scopes": ["read", "rotate"] },
      "audit":      { "enabled": true, "scopes": ["read"] },
      "health":     { "enabled": true, "public": false }
    }
  }
}
```

`enabled` defaults to `false` — nobody grows a new authenticated surface by
accident.

### Mint your first token

No token in the DB yet? The HTTP rotation path is itself auth-gated, so you
bootstrap from the CLI while holding DB credentials:

```bash
mdb-engine admin secrets bootstrap <slug> --scopes "*" --label "bootstrap"
# → prints the plaintext token ONCE. Stash it in your secret manager.
```

Rotate at any time (token is the revocation path):

```bash
mdb-engine admin secrets rotate <slug> \
    --label "ci-gha" \
    --scopes "reconciler:*,trash:read,secrets:read" \
    --overlap-seconds 300
```

### URL tree

| Module     | Endpoint                                     | Scope         | Mutates |
| ---------- | -------------------------------------------- | ------------- | ------- |
| (surface)  | `GET  /__mdb/health/live`                    | *public*      | no      |
| health     | `GET  /__mdb/health`                         | `read`        | no      |
| health     | `GET  /__mdb/health/modules`                 | `read`        | no      |
| reconciler | `GET  /__mdb/reconciler/plan`                | `read`        | no      |
| reconciler | `POST /__mdb/reconciler/apply?dry_run=&yes=` | `apply`       | **yes** |
| reconciler | `GET  /__mdb/reconciler/manifest/history`    | `read`        | no      |
| reconciler | `GET  /__mdb/reconciler/manifest/diff`       | `read`        | no      |
| trash      | `GET  /__mdb/trash`                          | `read`        | no      |
| trash      | `GET  /__mdb/trash/summary`                  | `read`        | no      |
| trash      | `POST /__mdb/trash/{id}/restore?dry_run=`    | `restore`     | **yes** |
| trash      | `POST /__mdb/trash/{id}/purge`               | `purge`       | **yes** |
| secrets    | `GET  /__mdb/secrets/current`                | `read`        | no      |
| secrets    | `POST /__mdb/secrets/rotate`                 | `rotate`      | **yes** |
| audit      | `GET  /__mdb/audit`                          | `read`        | no      |
| audit      | `GET  /__mdb/audit/recent`                   | `read`        | no      |
| audit      | `GET  /__mdb/audit/stats`                    | `read`        | no      |

### Scopes (per-endpoint, not per-module)

| Form              | Example               | Meaning                                  |
| ----------------- | --------------------- | ---------------------------------------- |
| `*`               | `*`                   | Unrestricted.                            |
| `<module>:*`      | `reconciler:*`        | Every endpoint in that module.           |
| `<module>:<verb>` | `reconciler:read`     | One verb within one module.              |
| Bare `<verb>`     | `read`                | Legacy — any endpoint with that scope.   |

A token with `reconciler:read` can call `GET /reconciler/plan` but is
**rejected 403** on `POST /reconciler/apply`. Unknown scope values are
caught at **boot** and logged as warnings, so a typo in your manifest
surfaces before you mint tokens against it.

### Idempotency for destructive POSTs

Send `Idempotency-Key: <opaque>` on any mutating request. A replay within
24h returns the original response with `X-Idempotent-Replay: true` in the
headers. Safe to retry from a flaky script, a CI rerun, or a double-click.

### Unauthenticated liveness probe

`GET /__mdb/health/live` bypasses auth **and** rate limits. Point your k8s
liveness probe or load balancer at it; everything else stays gated.

### CLI parity

Everything reachable over HTTP is also reachable from `mdb-engine admin …`:

```bash
mdb-engine admin --base-url http://localhost:8000 --token "$MDB_ADMIN_TOKEN" \
  reconciler plan --slug demo

mdb-engine admin reconciler apply --slug demo --dry-run
mdb-engine admin reconciler apply --slug demo --yes          # bypass prompt
MDB_CONFIRM=1 mdb-engine admin trash purge --slug demo <id>  # env bypass
```

Destructive commands prompt kubectl-style by default; `--yes` or
`MDB_CONFIRM=1` skips.

---

## 2. Graceful secret rotation

`POST /__mdb/secrets/rotate` now accepts an optional `overlap_seconds`
window during which the **previous** token stays valid. Rolling a token
across a fleet used to mean a burst of 401s while callers picked up the new
value; now you get a bounded grace window (capped at 1h).

```bash
curl -X POST https://api.example.com/__mdb/secrets/rotate \
  -H "X-App-Token: $OLD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "label": "prod-ci",
        "scopes": ["reconciler:read", "reconciler:apply"],
        "overlap_seconds": 300
      }'
```

Response (only place in the whole API that ever returns a plaintext token):

```json
{
  "token": "tok_xxx...",
  "token_id": "a1b2c3d4e5f60718",
  "label": "prod-ci",
  "scopes": ["reconciler:read", "reconciler:apply"],
  "rotation_count": 4,
  "overlap_seconds": 300,
  "previous_expires_at": "2026-04-22T07:35:00Z"
}
```

- `overlap_seconds: 0` (the default) preserves the **legacy immediate
  revocation** behavior.
- Response carries `Cache-Control: no-store` + `Pragma: no-cache`. No proxy
  can cache a plaintext token.
- Previous tokens are fingerprinted with HMAC(engine_dek, token)[:16] — the
  DB never sees a plaintext previous-token value.

---

## 3. Manifest reconciler (`manifest_tracking`)

Reconciles your DB to match `manifest.json`, with audit, safety gates, and
soft-delete for everything it owns. Opt in by adding a `manifest_tracking`
block:

```json
{
  "manifest_tracking": {
    "enabled": true,
    "confirm_if": {
      "destructive_ops": 3,
      "docs_at_risk": 10000,
      "protect_on_match": ["*_audit", "ledger_*"]
    }
  }
}
```

### What it does on boot / apply

1. **Canonicalize** the manifest and hash it. If the hash equals
   `_applied_hash` on `apps_config`, the apply is a no-op.
2. **Plan** a minimal set of MongoDB mutations.
3. **Gate** destructive ops through `confirm_if` (see below).
4. **Quarantine** collections/indexes that would be dropped into
   `_mdb_trash__*` + a tombstone row (soft-delete with a configurable TTL).
5. **Apply** the safe ops under a per-slug advisory lock with a fencing
   token (`uuid+hostname+pid+boot_nonce`), so a second worker either no-ops
   or retries safely — never double-applies.
6. **Write a revision** to `_mdb_manifest_revisions` (append-only audit
   log, one doc per apply) and commit `_applied_hash` atomically.

### Owned collections

| Collection                 | Purpose |
| -------------------------- | ------- |
| `apps_config`              | Current manifest + `_applied_hash` / `_applied_revision` |
| `_mdb_manifest_revisions`  | Append-only audit log — **your deploy bus** |
| `_mdb_manifest_locks`      | Advisory locks, keyed `reconcile::<slug>` |
| `_mdb_owned_artifacts`     | Ledger of collections/indexes owned per slug |
| `_mdb_trash`               | Tombstones for quarantined collections/indexes |
| `_mdb_trash__*`            | Quarantined physical collections (ephemeral) |
| `_mdb_meta`                | Bootstrap marker; short-circuits repeat boots |

All `_mdb_*` names are **reserved**. The planner refuses to plan a manifest
that declares one as a user collection or `rename_from` source.

### Safety gates (`confirm_if`)

Declaratively decide "ask me first":

| Key                | Type              | Trips when…                                               |
| ------------------ | ----------------- | --------------------------------------------------------- |
| `destructive_ops`  | int               | count of drops/renames exceeds this number                |
| `docs_at_risk`     | int               | summed `count_documents` across destructive targets exceeds |
| `protect_on_match` | array of globs    | any destructive op name matches any glob                  |

When a gate trips:

```python
res = await engine.reconcile(slug, confirm=False)
# res["status"] == "confirmation_required"
# res["reasons"] == ["destructive_ops=4 exceeds threshold 3", ...]
```

Pass `confirm=True` (or `--yes` on the CLI) to bypass. The CLI prompts on
a TTY: `Proceed? [y/N/diff]`.

### Trash & restore

```python
preview = await engine.trash_restore_plan(slug, trash_id)
if preview["can_restore"]:
    await engine.trash_restore(slug, trash_id)
```

Or from the CLI:

```bash
mdb-engine trash ls <slug>
mdb-engine trash restore <slug> <id> --dry-run
mdb-engine trash restore <slug> <id>
```

The built-in `trash_sweeper` action is the **sole deletion authority** —
it runs on a schedule, finds tombstones where `expires_at <= now`, drops
the physical collection, and emits `mdb.reconcile.trash_swept`. No MongoDB
TTL index on `expires_at`, intentionally: hard drops must be observable
and traceable to a deploy.

### Watching revisions

Every apply writes a revision — this is a deploy event bus:

```python
async for rev in engine.watch_revisions("demo"):
    await send_slack(f"Deploy {rev['revision']} applied: {rev['hash'][:16]}")
```

### GitOps exit codes

The `mdb-engine reconcile` CLI speaks JSON and uses stable exit codes for
CI pipelines:

| Code | Meaning |
| ---- | ------- |
| 0    | OK / no-op |
| 1    | Error |
| 2    | Drift (manifest differs from `--expected-head`) |
| 3    | Lock contention |
| 4    | Confirmation required |

Pure file-only diff (no Mongo connection needed, perfect for PR comments):

```bash
mdb-engine reconcile demo \
    --manifest-only \
    --against=HEAD~1 \
    --output-format=markdown
```

### Adopting an existing database

If you're onboarding a live database into mdb-engine, run once:

```bash
mdb-engine manifest adopt <slug>
```

The reconciler claims existing collections into `_mdb_owned_artifacts` and
emits a `revision=1` baseline without dropping anything.

---

## 4. Pluggable rate-limit store

The admin plane ships two rate-limit backends. Switch with one manifest
key:

```json
{
  "admin_api": {
    "rate_limits": { "backend": "memory" }
  }
}
```

| Backend    | Correctness              | When to use                                |
| ---------- | ------------------------ | ------------------------------------------ |
| `memory`   | Single-process correct   | Dev, single-worker deployments (default)   |
| `mongo`    | Multi-worker / multi-pod | Production with `uvicorn --workers >1`, k8s replicas, Gunicorn |

- `memory` — sliding window, bounded LRU so a flood of principals can't
  OOM the process.
- `mongo` — fixed window, atomic `findOneAndUpdate` with a TTL index, so
  every worker and every pod sees the same counter. No Redis dependency.

**Principal hashing.** Rate-limit buckets are keyed by
`sha256(token_fingerprint | ip)`. Previously this used the builtin
`hash()`, which seeds per-process and made buckets leak across workers.
Now stable across workers, pods, restarts.

---

## 5. Token fingerprint hardening (`MDB_ENGINE_MASTER_KEY`)

Token fingerprints (`token_id` — what you see in audit logs and in
`GET /secrets/current`) are now HMAC-SHA256 derived from the engine DEK.
If `MDB_ENGINE_MASTER_KEY` is missing at boot:

- `token_id` returns `null` (the engine refuses to fall back to a public
  constant — a silently-predictable fingerprint is worse than none).
- A single `ERROR`-level log line fires explaining the degrade.
- The engine keeps serving traffic; only fingerprinting is affected.

**Action required for production:** set `MDB_ENGINE_MASTER_KEY` to a
32-byte URL-safe secret. Same variable as before; no new env to plumb.

---

## 6. Bug fixes landed in 0.13.4

Only relevant if you were running `0.13.3` for a brief window:

- `trace_span()` / `emit_event()` kwarg collisions in the reconciler — a
  call like `trace_span("mdb.reconcile.op", name=op.name, ...)` passed
  `name=` twice (positional + keyword) and raised `TypeError`. Fixed by
  renaming to `op_name=`. Affected the reconciler apply path and the
  `trash_sweeper` action.
- `MANIFEST_SCHEMA_V2.manifest_tracking.confirm_if` was missing from the
  inline JSON schema (`mdb_engine/core/manifest.py`). Manifests that used
  `confirm_if` validated in the file-based schema but failed at runtime.
  Schema now matches the feature.

If you only ever used `0.13.2` or earlier, nothing was published broken to
you; `0.13.3` was immediately superseded by `0.13.4`. `0.13.5` is
drop-in compatible with `0.13.4`.

---

## 7. CI self-cancellation fix (`0.13.5`, repo-only)

Repo-side only; zero effect on anyone consuming the published package.
Kept here so you can cherry-pick the pattern into your own repos:

- `.github/workflows/ci.yml` concurrency group now includes
  `github.event_name`, so a `push:tag` and a `release:published` on the
  same ref land in **different** groups and don't cancel each other.
- The `publish` job no longer runs on `release:published` — it auto-creates
  the GitHub Release via `softprops/action-gh-release@v2`, so responding
  to its own event would collide with PyPI (`version already exists`).
  Publish fires only on tag push or explicit `workflow_dispatch`.
- Blocking jobs (`code-quality`, `test-unit`, `test-integration`) now
  include `github.event_name == 'release'` in their `if:` gates so a
  release-triggered run still runs the full pipeline as a post-publish
  sanity check.

---

## What did NOT change

- **Public Python API** — `MongoDBEngine`, `create_app()`, `quickstart()`,
  `mount_ssr_routes()`, `get_scoped_db`, `get_memory_service`,
  `get_request_context` — same signatures, same behavior.
- **`mdb-engine serve` / `mdb-engine serve-multi`** — same CLI interface.
- **Manifest `schema_version: "2.0"`** — still the current version. All
  new keys (`admin_api`, `manifest_tracking`) are **additive** and
  **optional**.
- **Authentication, authorization (Casbin/Oso), CRUD, WebSockets, SSE,
  SSR, sitemap, robots.txt, feeds, OG image generation** — unaffected.
- **Memory, graph, embedding, LLM services** — unaffected.

---

## Manifest schema additions

### Top-level (new blocks, both optional)

| Key                         | Type   | Default | Description |
| --------------------------- | ------ | ------- | ----------- |
| `admin_api`                 | object | —       | Enables the admin plane. |
| `manifest_tracking`         | object | —       | Enables reconciler features (gates, trash TTL). |

### `admin_api`

| Key                              | Type                         | Default        | Description |
| -------------------------------- | ---------------------------- | -------------- | ----------- |
| `admin_api.enabled`              | bool                         | `false`        | Master switch. |
| `admin_api.path_prefix`          | string                       | `"/__mdb"`     | Mount prefix. |
| `admin_api.auth.mode`            | `"app_token"`                | `"app_token"`  | Only mode today. |
| `admin_api.auth.header`          | string                       | `"X-App-Token"`| Token header name. |
| `admin_api.audit.enabled`        | bool                         | `true`         | Write audit trail. |
| `admin_api.audit.collection`     | string                       | `"_mdb_admin_audit"` | Collection name. |
| `admin_api.rate_limits.backend`  | `"memory"` \| `"mongo"`      | `"memory"`     | Backend selector. |
| `admin_api.rate_limits.read`     | `{max, window_seconds}`      | `120 / 60`     | Read-endpoint limit. |
| `admin_api.rate_limits.write`    | `{max, window_seconds}`      | `15 / 60`      | Write-endpoint limit. |
| `admin_api.modules.<name>.enabled` | bool                       | `true`         | Per-module switch. |
| `admin_api.modules.<name>.scopes`  | array of strings           | —              | Declared vocabulary (validated at boot). |
| `admin_api.modules.health.public`  | bool                       | `false`        | If `true`, `GET /__mdb/health` is also unauthenticated (like `/health/live`). |

### `manifest_tracking`

| Key                                   | Type             | Default | Description |
| ------------------------------------- | ---------------- | ------- | ----------- |
| `manifest_tracking.enabled`           | bool             | `false` | Master switch. |
| `manifest_tracking.confirm_if.destructive_ops`  | int    | —       | Gate on destructive op count. |
| `manifest_tracking.confirm_if.docs_at_risk`     | int    | —       | Gate on total documents at risk. |
| `manifest_tracking.confirm_if.protect_on_match` | array  | —       | Gate on glob match of op name. |

---

## Full example manifest (admin + reconciler enabled)

```json
{
  "schema_version": "2.0",
  "slug": "orders",
  "name": "Orders",

  "manifest_tracking": {
    "enabled": true,
    "confirm_if": {
      "destructive_ops": 2,
      "docs_at_risk": 5000,
      "protect_on_match": ["*_audit", "*_ledger"]
    }
  },

  "admin_api": {
    "enabled": true,
    "path_prefix": "/__mdb",
    "auth":  { "mode": "app_token", "header": "X-App-Token" },
    "audit": { "enabled": true, "collection": "_mdb_admin_audit" },
    "rate_limits": {
      "backend": "mongo",
      "read":  { "max": 240, "window_seconds": 60 },
      "write": { "max":  30, "window_seconds": 60 }
    },
    "modules": {
      "reconciler": { "enabled": true, "scopes": ["read", "apply"] },
      "trash":      { "enabled": true, "scopes": ["read", "restore", "purge"] },
      "secrets":    { "enabled": true, "scopes": ["read", "rotate"] },
      "audit":      { "enabled": true, "scopes": ["read"] },
      "health":     { "enabled": true, "public": false }
    }
  },

  "managed_indexes": {
    "orders": [
      { "type": "regular", "keys": {"status": 1, "created_at": -1}, "name": "status_sort" }
    ]
  }
}
```

---

## Further reading

- `docs/admin_plane.md` — full module-by-module reference, audit shape,
  idempotency semantics, and the built-in scope vocabulary.
- `docs/reconciler.md` — hashing, planning, safety gates, quarantine &
  restore, revisions, GitOps exit codes, adoption flow.
- `examples/basic/business_spa/` — end-to-end example app that exercises
  the admin plane, reconciler, outbox pattern, and built-in actions.

---

## Action required

`pip install --upgrade "mdb-engine>=0.13.5"`.

If you want zero new behavior: you're done. The admin plane, reconciler,
and new CLI surfaces are **all opt-in**. Add `admin_api` and/or
`manifest_tracking` blocks to your manifest when you want them.

If you run in production with more than one worker or pod: set
`admin_api.rate_limits.backend: "mongo"` and export a 32-byte
`MDB_ENGINE_MASTER_KEY`.
