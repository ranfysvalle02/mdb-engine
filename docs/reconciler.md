# Manifest Reconciler

> **TL;DR** — mdb-engine treats your `manifest.json` as the source of truth
> for every app's schema. On every boot (and on every `mdb-engine reconcile`
> call) it plans a minimal set of MongoDB mutations to make the database
> match the manifest, quarantines anything destructive, writes a revision
> to an audit log, and commits the new applied hash atomically. This page
> documents the full lifecycle.

---

## 1. Mental model

```
manifest.json ──▶  canonicalize ──▶  hash ──▶  plan ──▶  apply ──▶  ledger
                                  │                    │
                                  │                    └── revisions + tombstones
                                  │
                                  └── short-circuit if hash unchanged
```

Every apply runs inside a **per-slug advisory lock** (`_mdb_manifest_locks`)
with a fencing token (`uuid+hostname+pid+boot_nonce`) so a second worker
will either no-op (if the first committed) or see `status="locked"` and
can retry safely.

### The collections the engine owns

| Collection | Purpose |
|---|---|
| `apps_config` | Current manifest + `_applied_hash` / `_applied_revision` |
| `_mdb_manifest_revisions` | Append-only audit log (one doc per apply) |
| `_mdb_manifest_locks` | Advisory locks, keyed `reconcile::<slug>` |
| `_mdb_owned_artifacts` | Ledger of collections/indexes owned per slug |
| `_mdb_trash` | Tombstones for quarantined collections/indexes |
| `_mdb_meta` | Bootstrap marker; short-circuits repeat boots |
| `_mdb_trash__*` | Quarantined physical collections (ephemeral) |

All of the `_mdb_*` names are **reserved**. The planner refuses to plan a
manifest that declares one as a user collection or `rename_from` source.

---

## 2. Hashing & change detection

Two hashes are computed on a **canonicalized** copy of the manifest:

- `_applied_hash` — full canonical hash (drives "is this a no-op?")
- `_applied_schema_hash` — subset of schema-affecting keys only (drives
  whether schema validators need to be re-applied)

Both are formatted `sha256:v{N}:{64 hex chars}`; `N` is bumped whenever
the canonicalization rules change. The engine refuses to short-circuit on
a stale-version hash.

Stripped before hashing (runtime-only):

- `_applied_*` fields (written *by* the reconciler)
- `ssr.routes.*.cache` (TTL-only)
- `observability.*.sampling`
- `initial_data` (seed data — never schema-affecting)

Property test (`tests/unit/test_manifest_hash.py::test_canonicalization_is_permutation_invariant`)
guarantees this is stable across Python dict iteration order.

---

## 3. The plan

`Reconciler.plan(slug, manifest, prev_hash)` returns a `ReconcilePlan`:

```python
@dataclass
class ReconcilePlan:
    slug: str
    from_hash: str | None
    to_hash: str
    schema_hash: str
    ops: list[ReconcileOp]
    patch: list[JsonPatchOp]    # RFC-6902 manifest diff
    gate_result: GateResult | None
    is_noop: bool
```

Each `ReconcileOp` is one of:

| `op` | Effect |
|---|---|
| `add_collection` | Create a collection + validator |
| `update_validator` | Change JSON Schema validator (non-destructive) |
| `rename_collection` | Atomic rename (supports `rename_from`) |
| `drop_collection` | **Quarantine** into `_mdb_trash__*` |
| `add_index` | Ensure an index exists |
| `drop_index` | Drop an index not in the manifest |
| `enable_service` / `disable_service` | Service-owned artifacts |

Ops carry a `.patch` slice (the JSON Pointer subtree that caused them) so
tombstones can record *why* they were created.

---

## 4. Safety gates (`confirm_if`)

Declaratively declare "I want to be asked first before big destructive
changes land":

```json
"manifest_tracking": {
  "confirm_if": {
    "destructive_ops": 3,
    "docs_at_risk": 10000,
    "protect_on_match": ["*_audit", "ledger_*"]
  }
}
```

- `destructive_ops` — count of drops/renames exceeds this number
- `docs_at_risk` — summed `count_documents` across targets exceeds this
- `protect_on_match` — any destructive op name matches any glob

When a gate trips:

```python
res = await engine.reconcile(slug, confirm=False)
# res["status"] == "confirmation_required"
# res["reasons"] == ["destructive_ops=4 exceeds threshold 3", ...]
```

Pass `confirm=True` (or `--yes` on the CLI) to bypass. The CLI will also
prompt on a TTY: `Proceed? [y/N/diff]`.

---

## 5. Quarantine & restore

### Dropping → tombstone + rename

When `drop_collection` runs:

1. Rename `<slug>_<name>` → `_mdb_trash__<slug>__<base64(uuid)>`.
2. Insert a row in `_mdb_trash`:
   ```json
   {
     "slug": "demo",
     "kind": "collection",
     "original_name": "orders",
     "trash_name": "_mdb_trash__demo__a1b2c3",
     "quarantined_at": "2026-04-20T00:00:00Z",
     "expires_at": "2026-04-27T00:00:00Z",
     "caused_by_commit": "deadbeef",
     "caused_by_user": "alice",
     "removed_subtree": [ { "op": "remove", "path": "/collections/orders", ... } ]
   }
   ```
3. `_mdb_owned_artifacts` row is moved to `status="quarantined"`.

### Sweeper

The built-in `trash_sweeper` action (cron-style, default hourly) is the
**sole deletion authority**. It:

1. Finds tombstones where `expires_at <= now`.
2. Drops the physical trash collection.
3. Deletes the tombstone row.
4. Emits `mdb.reconcile.trash_swept`.

There is deliberately **no MongoDB TTL index** on `expires_at`. Hard
drops must be observable and traceable to a deploy.

### Restore

```python
preview = await engine.trash_restore_plan(slug, trash_id)
# { "can_restore": false, "reasons": ["collection ... already exists"] }

if preview["can_restore"]:
    await engine.trash_restore(slug, trash_id)
```

CLI: `mdb-engine trash restore <slug> <id> [--dry-run]`.

---

## 6. Revisions & watch

Every apply writes to `_mdb_manifest_revisions`:

```json
{
  "slug": "demo",
  "revision": 17,
  "hash": "sha256:v2:...",
  "prev_hash": "sha256:v2:...",
  "patch": [ { "op": "add", "path": "/collections/leads", ... } ],
  "ops": [ ... ],
  "applied_by": "alice",
  "applied_at": "2026-04-20T00:00:00Z"
}
```

This is a deploy event bus. Tail it over change streams:

```python
async for rev in engine.watch_revisions("demo"):
    await send_slack(f"Deploy {rev['revision']} applied: {rev['hash'][:16]}")
```

### Adopting an existing database

If you're onboarding a live database into mdb-engine, run once:

```bash
mdb-engine manifest adopt <slug>
```

The reconciler will claim existing collections into `_mdb_owned_artifacts`
and emit a `revision=1` "baseline" without dropping anything.

---

## 7. GitOps / CI

The CLI speaks JSON and uses stable exit codes:

| Code | Meaning |
|---|---|
| 0 | OK / no-op |
| 1 | Error |
| 2 | Drift (manifest differs from `--expected-head`) |
| 3 | Lock contention |
| 4 | Confirmation required |

Pure file-only diff (no Mongo connection needed):

```bash
mdb-engine reconcile demo --manifest-only --against=HEAD~1 --output-format=markdown
```

Use this as the body of a PR comment for every change that touches a
manifest. Combine with `--expected-head=<hash>` in deploy jobs to fail
fast when the DB isn't at the commit you think it is.

---

## 8. Admin HTTP API

The reconciler's HTTP surface is the `reconciler` module of the
first-class **admin plane**. Enable it with a top-level `admin_api`
block in the manifest; see [docs/admin_plane.md](./admin_plane.md) for
the full URL tree, scope model, audit shape, and instructions for
shipping your own module. The reference
[`business_spa`](../examples/basic/business_spa/) example's Ops tab is
a working copy-paste starting point.

---

## 9. Observability

Structured events are emitted via `mdb_engine.core.reconciler_events`:

- `mdb.reconcile.plan_built`
- `mdb.reconcile.apply_started` / `apply_succeeded` / `apply_failed`
- `mdb.reconcile.op_applied` / `op_skipped`
- `mdb.reconcile.gate_tripped`
- `mdb.reconcile.trash_swept`

If OpenTelemetry is installed, the same events are recorded as spans
under a `mdb.reconcile.apply` parent span, tagged with
`mdb.slug`, `mdb.revision`, `mdb.from_hash`, and `mdb.to_hash`.

---

## 10. Breaking changes & migration

**Hash bump — `v1` → `v2`.** On upgrade, every app will re-apply once to
refresh its `_applied_hash`. The apply will be a no-op in terms of
MongoDB mutations (ops list is empty) but writes a fresh revision. No
action required.

**`_mdb_trash` TTL index dropped.** The reconciler will drop the
deprecated `expires_at_ttl` index on first boot. The sweeper is now the
only thing that deletes tombstones.

**Lock holder format.** Existing `_mdb_manifest_locks` rows written by
older versions will be released by their TTL or by the first release
call from the owning process. Subsequent acquisitions use the new
`uuid+hostname+pid+nonce` format.

**Admin API promoted to a top-level manifest block.**
`manifest_tracking.admin_api` is gone; the new top-level `admin_api`
block is richer (per-module enable/scopes, audit, introspection) and
defaults to `enabled: false`. The URL tree also changed:
`/__mdb/reconcile/*` → `/__mdb/reconciler/*`,
`/__mdb/manifest/*` → `/__mdb/reconciler/manifest/*`. See
[docs/admin_plane.md](./admin_plane.md).
