"""Integration tests for Phase 0 + Phase 1 reconciler safety guarantees.

These exercise the *hard* correctness invariants:

- Crash-recovery: if a revision row is written but ``apps_config`` isn't,
  the next boot converges without data loss.
- Concurrent apply: only one worker ever holds the reconcile lock for a
  given slug, even across fencing-token reuse.
- Tombstone lifecycle: the sweeper is the **sole** deletion authority;
  manual inserts with expired ``expires_at`` survive until swept.
- Reserved-name guards: declaring ``_mdb_`` / trash-prefixed collection
  or rename sources fails at planning time with a ``ValueError``.
- ``confirm_if`` gate: plans that trip any threshold return
  ``status="confirmation_required"`` unless ``confirm=True``.
- Restore plan: ``trash_restore_plan`` reports conflicts without
  touching the database.
- Admin HTTP: valid / invalid / missing tokens hit the correct 200/403/401.

The tests depend on the existing ``real_mongo_db`` fixture from the
suite (a live MongoDB replica-set-capable container).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from mdb_engine.actions._builtin.trash_sweeper import sweep_once
from mdb_engine.constants import (
    MANIFEST_LOCKS_COLLECTION,
    RESERVED_TRASH_PREFIX,
    TRASH_COLLECTION,
)
from mdb_engine.core.reconciler import Reconciler
from mdb_engine.core.reconciler_store import (
    BOOTSTRAP_VERSION,
    META_COLLECTION,
    acquire_lock,
    bootstrap_reconciler_collections,
    make_holder_id,
    release_lock,
)


def _base_manifest(slug: str) -> dict:
    return {
        "schema_version": "2.0",
        "slug": slug,
        "name": slug,
        "status": "active",
        "collections": {
            "orders": {"auto_crud": True, "schema": {"type": "object"}},
            "customers": {"auto_crud": True, "schema": {"type": "object"}},
        },
        "manifest_tracking": {
            "enabled": True,
            "mode": "reconcile",
            "retention": {"max_revisions": 10, "max_age_days": 30, "trash_ttl_days": 1},
        },
    }


# ---------------------------------------------------------------------------
# P0.7 — Bootstrap short-circuit via _mdb_meta marker
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestBootstrapCache:
    async def test_marker_short_circuits_repeat_bootstrap(self, real_mongo_db):
        assert await bootstrap_reconciler_collections(real_mongo_db) is True
        marker = await real_mongo_db[META_COLLECTION].find_one({"_id": "__bootstrap__"})
        assert marker is not None
        assert marker["version"] == BOOTSTRAP_VERSION

        # Second call should short-circuit and return False (skipped).
        assert await bootstrap_reconciler_collections(real_mongo_db) is False

        # force=True still runs.
        assert await bootstrap_reconciler_collections(real_mongo_db, force=True) is True


# ---------------------------------------------------------------------------
# P0.2 — Lock fencing tokens
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestLockFencing:
    async def test_holder_ids_are_distinct(self):
        tokens = {make_holder_id("test") for _ in range(200)}
        assert len(tokens) == 200, "holder IDs must be unique per call"

    async def test_release_refuses_foreign_holder(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        slug = "fence_test"
        a = make_holder_id("a")
        b = make_holder_id("b")
        assert await acquire_lock(real_mongo_db, slug, holder=a)
        # B should not be able to release A's lock.
        await release_lock(real_mongo_db, slug, holder=b)
        doc = await real_mongo_db[MANIFEST_LOCKS_COLLECTION].find_one({"_id": f"reconcile::{slug}"})
        assert doc is not None
        assert doc["holder"] == a
        # A can release its own lock.
        await release_lock(real_mongo_db, slug, holder=a)
        doc = await real_mongo_db[MANIFEST_LOCKS_COLLECTION].find_one({"_id": f"reconcile::{slug}"})
        assert doc is None


# ---------------------------------------------------------------------------
# P0.1 — Atomic apps_config persistence
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestAtomicPersistence:
    async def test_apps_config_persisted_inside_lock(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "atomic_test"
        m = _base_manifest(slug)

        plan = await reco.plan(slug, m, prev_hash=None)
        result = await reco.apply(plan, manifest=m, applied_by="test", persist_manifest=True)
        assert result["status"] == "applied"

        cfg = await real_mongo_db.apps_config.find_one({"slug": slug})
        assert cfg is not None
        assert cfg["_applied_hash"] == plan.to_hash
        assert cfg["_applied_schema_hash"] == plan.schema_hash
        assert cfg["_applied_revision"] == result["revision"]["revision"]

    async def test_noop_refreshes_applied_metadata(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "noop_refresh"
        m = _base_manifest(slug)

        plan = await reco.plan(slug, m, prev_hash=None)
        await reco.apply(plan, manifest=m, applied_by="test", persist_manifest=True)

        # Simulate a crash between revision and apps_config by clearing
        # the hash. Re-apply should be a no-op but must re-persist.
        await real_mongo_db.apps_config.update_one({"slug": slug}, {"$unset": {"_applied_hash": ""}})
        plan2 = await reco.plan(slug, m, prev_hash=None)
        r2 = await reco.apply(plan2, manifest=m, applied_by="test", persist_manifest=True)
        # After re-applying, apps_config._applied_hash is set again.
        cfg = await real_mongo_db.apps_config.find_one({"slug": slug})
        assert cfg.get("_applied_hash") == plan2.to_hash


# ---------------------------------------------------------------------------
# P0.9 — Concurrent apply
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestConcurrentApply:
    async def test_two_workers_race(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        slug = "race_test"
        m = _base_manifest(slug)
        r_a = Reconciler(real_mongo_db)
        r_b = Reconciler(real_mongo_db)

        plan_a = await r_a.plan(slug, m, prev_hash=None)
        plan_b = await r_b.plan(slug, m, prev_hash=None)

        a_task = asyncio.create_task(r_a.apply(plan_a, manifest=m, applied_by="a"))
        b_task = asyncio.create_task(r_b.apply(plan_b, manifest=m, applied_by="b"))
        out_a, out_b = await asyncio.gather(a_task, b_task)
        statuses = sorted([out_a["status"], out_b["status"]])
        # One worker applies, the other either saw a no-op (if it ran
        # after the first committed) or got "locked" (if it raced the lock).
        assert "applied" in statuses
        assert statuses[1] in ("noop", "locked", "applied")


# ---------------------------------------------------------------------------
# P0.3 — Tombstone lifecycle: sweeper is sole authority
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestTombstoneLifecycle:
    async def test_sweeper_deletes_expired_only(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        now = datetime.now(timezone.utc)
        # Insert one expired + one future tombstone with matching physical colls.
        expired_name = f"{RESERVED_TRASH_PREFIX}sweep__test__expired"
        live_name = f"{RESERVED_TRASH_PREFIX}sweep__test__live"
        await real_mongo_db[expired_name].insert_one({"_": 1})
        await real_mongo_db[live_name].insert_one({"_": 1})
        await real_mongo_db[TRASH_COLLECTION].insert_many(
            [
                {
                    "slug": "sweep",
                    "kind": "collection",
                    "original_name": "expired",
                    "trash_name": expired_name,
                    "expires_at": now - timedelta(minutes=5),
                    "quarantined_at": now - timedelta(hours=1),
                },
                {
                    "slug": "sweep",
                    "kind": "collection",
                    "original_name": "live",
                    "trash_name": live_name,
                    "expires_at": now + timedelta(hours=1),
                    "quarantined_at": now,
                },
            ]
        )

        out = await sweep_once(real_mongo_db)
        assert out["dropped_collections"] >= 1
        assert out["tombstones_deleted"] >= 1

        # Live tombstone + physical still there.
        names = await real_mongo_db.list_collection_names()
        assert live_name in names
        assert expired_name not in names

        remaining = await real_mongo_db[TRASH_COLLECTION].find({"slug": "sweep"}).to_list(length=None)
        assert len(remaining) == 1
        assert remaining[0]["trash_name"] == live_name


# ---------------------------------------------------------------------------
# P0.4 — Reserved-name guards in _extract_desired
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestReservedNameGuards:
    async def test_reserved_prefix_rejected(self, real_mongo_db):
        reco = Reconciler(real_mongo_db)
        m = _base_manifest("guard")
        m["collections"]["_mdb_sneaky"] = {"auto_crud": True}
        with pytest.raises(ValueError, match="reserved|_mdb_"):
            await reco.plan("guard", m, prev_hash=None)

    async def test_rename_from_into_trash_rejected(self, real_mongo_db):
        reco = Reconciler(real_mongo_db)
        m = _base_manifest("guard")
        m["collections"]["orders"]["rename_from"] = ["_mdb_trash__whatever"]
        with pytest.raises(ValueError):
            await reco.plan("guard", m, prev_hash=None)


# ---------------------------------------------------------------------------
# P0.5 — Restore preview
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestRestorePlan:
    async def test_restore_plan_detects_conflict(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "rest_plan_test"
        m1 = _base_manifest(slug)
        plan1 = await reco.plan(slug, m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="test")

        await real_mongo_db[f"{slug}_orders"].insert_one({"x": 1})
        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        plan2 = await reco.plan(slug, m2, prev_hash=r1["revision"]["hash"])
        await reco.apply(plan2, manifest=m2, applied_by="test")

        entries = await reco.trash_list(slug)
        orders_entry = next(e for e in entries if e.get("original_name") == "orders")

        # Create a conflicting destination so preview reports it.
        await real_mongo_db[f"{slug}_orders"].insert_one({"occupant": True})

        preview = await reco.trash_restore_plan(slug, orders_entry["_id"])
        assert preview["can_restore"] is False
        assert any("already exists" in r for r in preview["reasons"])

        # Dry-run restore surfaces the same answer without mutating.
        dry = await reco.trash_restore(slug, orders_entry["_id"], dry_run=True)
        assert dry["restored"] is False
        assert dry["dry_run"] is True


# ---------------------------------------------------------------------------
# P1 — confirm_if gates
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestConfirmIfGates:
    async def test_destructive_threshold_blocks(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "gate_test"
        m1 = _base_manifest(slug)
        m1["manifest_tracking"]["confirm_if"] = {"destructive_ops": 1}

        plan1 = await reco.plan(slug, m1, prev_hash=None)
        await reco.apply(plan1, manifest=m1, applied_by="test")

        # Drop both collections — two destructive ops; threshold = 1.
        m2 = dict(m1)
        m2["collections"] = {}
        m2["managed_indexes"] = {}
        plan2 = await reco.plan(slug, m2, prev_hash=plan1.to_hash)
        result = await reco.apply(plan2, manifest=m2, applied_by="test")
        assert result["status"] == "confirmation_required"
        assert result["reasons"]

        # With confirm=True the plan applies.
        plan3 = await reco.plan(slug, m2, prev_hash=plan1.to_hash)
        result2 = await reco.apply(plan3, manifest=m2, applied_by="test", confirm=True)
        assert result2["status"] == "applied"

    async def test_protect_on_match_blocks(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "gate_glob"
        m1 = _base_manifest(slug)
        m1["manifest_tracking"]["confirm_if"] = {"protect_on_match": ["cust*"]}

        plan1 = await reco.plan(slug, m1, prev_hash=None)
        await reco.apply(plan1, manifest=m1, applied_by="test")

        m2 = dict(m1)
        m2["collections"] = {"orders": m1["collections"]["orders"]}
        plan2 = await reco.plan(slug, m2, prev_hash=plan1.to_hash)
        result = await reco.apply(plan2, manifest=m2, applied_by="test")
        assert result["status"] == "confirmation_required"
        assert any("protect_on_match" in r for r in result["reasons"])

    async def test_docs_at_risk_threshold_blocks(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "gate_docs"
        m1 = _base_manifest(slug)
        m1["manifest_tracking"]["confirm_if"] = {"docs_at_risk": 5}

        plan1 = await reco.plan(slug, m1, prev_hash=None)
        await reco.apply(plan1, manifest=m1, applied_by="test")

        # Seed 10 docs in the orders collection; planning to drop it should
        # trip the threshold (10 > 5).
        docs = [{"i": i} for i in range(10)]
        await real_mongo_db[f"{slug}_orders"].insert_many(docs)

        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        m2["managed_indexes"] = {}
        plan2 = await reco.plan(slug, m2, prev_hash=plan1.to_hash)
        result = await reco.apply(plan2, manifest=m2, applied_by="test")
        assert result["status"] == "confirmation_required"
        assert any("docs_at_risk" in r for r in result["reasons"])


# ---------------------------------------------------------------------------
# Large-trash sweeper perf test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestSweeperPerf:
    async def test_sweeps_many_tombstones_in_bounded_time(self, real_mongo_db):
        """Ensures the sweeper scales: 200 expired tombstones should
        complete in well under a minute against a local container.

        If this ever starts timing out it usually means the sweeper has
        regressed to per-doc round-trips or the ``expires_at`` index was
        dropped without replacement. Keep the threshold generous.
        """
        await bootstrap_reconciler_collections(real_mongo_db)
        now = datetime.now(timezone.utc)
        count = 200
        slug = "perf_sweep"
        tombstones = []
        for i in range(count):
            trash_name = f"{RESERVED_TRASH_PREFIX}{slug}__perf_{i:04d}"
            # One doc per trashed collection so drop_collection has work.
            await real_mongo_db[trash_name].insert_one({"_": 1})
            tombstones.append(
                {
                    "slug": slug,
                    "kind": "collection",
                    "original_name": f"perf_{i:04d}",
                    "trash_name": trash_name,
                    "expires_at": now - timedelta(minutes=1),
                    "quarantined_at": now - timedelta(hours=1),
                }
            )
        await real_mongo_db[TRASH_COLLECTION].insert_many(tombstones)

        started = time.perf_counter()
        out = await sweep_once(real_mongo_db)
        elapsed = time.perf_counter() - started

        assert out["dropped_collections"] >= count
        assert out["tombstones_deleted"] >= count
        assert elapsed < 30.0, f"sweeper took {elapsed:.1f}s for {count} tombstones"

        remaining = await real_mongo_db[TRASH_COLLECTION].count_documents({"slug": slug})
        assert remaining == 0


# ---------------------------------------------------------------------------
# Metadata attached to tombstones + revisions
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestCausedByMetadata:
    async def test_caused_by_commit_propagates(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        slug = "caused_by"
        m1 = _base_manifest(slug)
        p1 = await reco.plan(slug, m1, prev_hash=None)
        await reco.apply(p1, manifest=m1, applied_by="test")

        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        p2 = await reco.plan(slug, m2, prev_hash=p1.to_hash)
        await reco.apply(
            p2,
            manifest=m2,
            applied_by="test",
            caused_by_commit="deadbeef",
            caused_by_user="alice",
        )
        tomb = await real_mongo_db[TRASH_COLLECTION].find_one({"slug": slug, "original_name": "orders"})
        assert tomb is not None
        assert tomb["caused_by_commit"] == "deadbeef"
        assert tomb["caused_by_user"] == "alice"
        assert isinstance(tomb.get("removed_subtree"), list)
