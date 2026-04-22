"""Integration tests for the manifest reconciler + quarantine-to-trash.

Uses a real MongoDB container via the ``real_mongo_db`` fixture.
"""

from __future__ import annotations

import pytest

from mdb_engine.constants import (
    MANIFEST_REVISIONS_COLLECTION,
    OWNED_ARTIFACTS_COLLECTION,
    RESERVED_TRASH_PREFIX,
    TRASH_COLLECTION,
)
from mdb_engine.core.reconciler import Reconciler
from mdb_engine.core.reconciler_store import bootstrap_reconciler_collections


def _manifest_v1() -> dict:
    return {
        "schema_version": "2.0",
        "slug": "reco_test",
        "name": "Reconciler Test",
        "status": "active",
        "collections": {
            "orders": {"auto_crud": True, "schema": {"type": "object"}},
            "customers": {"auto_crud": True, "schema": {"type": "object"}},
        },
        "managed_indexes": {
            "orders": [{"type": "regular", "keys": {"created_at": -1}, "name": "idx_orders_when"}],
        },
        "manifest_tracking": {
            "enabled": True,
            "mode": "reconcile",
            "retention": {"max_revisions": 10, "max_age_days": 30, "trash_ttl_days": 7},
        },
    }


@pytest.mark.integration
@pytest.mark.asyncio
class TestReconcilerLifecycle:
    async def test_initial_apply_creates_ledger(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m = _manifest_v1()

        plan = await reco.plan("reco_test", m, prev_hash=None)
        assert not plan.is_noop
        assert plan.from_hash is None
        # We expect two add_collection ops + one add_index op.
        kinds = {op.op for op in plan.ops}
        assert "add_collection" in kinds
        assert "add_index" in kinds

        result = await reco.apply(plan, manifest=m, applied_by="test")
        assert result["status"] == "applied"
        assert result["revision"]["revision"] == 1

        # Ledger should now contain our two collections.
        owned = await real_mongo_db[OWNED_ARTIFACTS_COLLECTION].find({"slug": "reco_test"}).to_list(length=None)
        coll_entries = [d for d in owned if d["artifact_type"] == "collection"]
        assert {d["name"] for d in coll_entries} >= {"orders", "customers"}

    async def test_unchanged_manifest_is_noop(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m = _manifest_v1()

        plan1 = await reco.plan("noop_test", m, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m, applied_by="test")
        applied_hash = r1["revision"]["hash"]

        plan2 = await reco.plan("noop_test", m, prev_hash=applied_hash)
        assert plan2.is_noop
        r2 = await reco.apply(plan2, manifest=m, applied_by="test")
        assert r2["status"] == "noop"

    async def test_removed_collection_is_quarantined(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m1 = _manifest_v1()

        plan1 = await reco.plan("quar_test", m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="test")
        hash1 = r1["revision"]["hash"]

        # Seed data so we can verify the quarantined collection preserves it.
        await real_mongo_db["quar_test_orders"].insert_one({"x": 1})

        # New manifest drops the "orders" collection.
        m2 = _manifest_v1()
        m2["slug"] = "quar_test"
        m2["collections"].pop("orders")
        m2["managed_indexes"].pop("orders", None)

        plan2 = await reco.plan("quar_test", m2, prev_hash=hash1)
        op_kinds = {op.op for op in plan2.ops if not op.skipped}
        assert "drop_collection" in op_kinds

        r2 = await reco.apply(plan2, manifest=m2, applied_by="test")
        assert r2["status"] == "applied"

        # The physical collection must have been renamed into the trash namespace.
        names = await real_mongo_db.list_collection_names()
        orders_trash = [n for n in names if n.startswith(RESERVED_TRASH_PREFIX) and "orders" in n]
        assert orders_trash, f"Expected trashed orders collection, got {names}"
        assert "quar_test_orders" not in names

        # A tombstone should exist.
        tomb = await real_mongo_db[TRASH_COLLECTION].find_one({"slug": "quar_test", "kind": "collection"})
        assert tomb is not None
        assert tomb["original_name"] == "orders"
        assert tomb["expires_at"] is not None

        # Data should still live in the trashed collection.
        doc = await real_mongo_db[orders_trash[0]].find_one({"x": 1})
        assert doc is not None

    async def test_protect_collections_blocks_drop(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m1 = _manifest_v1()
        m1["slug"] = "prot_test"
        m1["manifest_tracking"]["protect_collections"] = ["orders"]

        plan1 = await reco.plan("prot_test", m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="test")
        hash1 = r1["revision"]["hash"]

        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        m2["managed_indexes"] = {}

        plan2 = await reco.plan("prot_test", m2, prev_hash=hash1)
        drop_ops = [op for op in plan2.ops if op.op == "drop_collection" and op.collection == "orders"]
        assert drop_ops
        assert all(op.skipped for op in drop_ops)
        assert all("protect" in (op.skipped_reason or "").lower() for op in drop_ops)

        # Pre-seed so we'd notice if the data was trashed.
        await real_mongo_db["prot_test_orders"].insert_one({"survivor": True})

        await reco.apply(plan2, manifest=m2, applied_by="test")

        # The physical collection must still be live, no trash entry was created.
        names = await real_mongo_db.list_collection_names()
        assert not any(
            n.startswith(RESERVED_TRASH_PREFIX) and "orders" in n for n in names
        ), f"expected no trashed orders, got {names}"
        doc = await real_mongo_db["prot_test_orders"].find_one({"survivor": True})
        assert doc is not None

    async def test_rename_from_preserves_data(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m1 = _manifest_v1()
        m1["slug"] = "ren_test"

        plan1 = await reco.plan("ren_test", m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="test")
        hash1 = r1["revision"]["hash"]

        # Seed documents in "orders".
        await real_mongo_db["ren_test_orders"].insert_many([{"n": 1}, {"n": 2}])

        # New manifest renames orders -> sales_orders.
        m2 = _manifest_v1()
        m2["slug"] = "ren_test"
        m2["collections"]["sales_orders"] = {
            "auto_crud": True,
            "schema": {"type": "object"},
            "rename_from": ["orders"],
        }
        m2["collections"].pop("orders")
        m2["managed_indexes"].pop("orders", None)

        plan2 = await reco.plan("ren_test", m2, prev_hash=hash1)
        rename_ops = [op for op in plan2.ops if op.op == "rename_collection"]
        assert rename_ops, f"Expected rename op, got {[o.to_dict() for o in plan2.ops]}"

        await reco.apply(plan2, manifest=m2, applied_by="test")

        # Physical collection should now be "ren_test_sales_orders" with data preserved.
        names = await real_mongo_db.list_collection_names()
        assert "ren_test_sales_orders" in names
        assert "ren_test_orders" not in names
        count = await real_mongo_db["ren_test_sales_orders"].count_documents({})
        assert count == 2

    async def test_history_and_trash_apis(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m1 = _manifest_v1()
        m1["slug"] = "hist_test"

        plan1 = await reco.plan("hist_test", m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="ci")
        hash1 = r1["revision"]["hash"]

        # Drop a collection to generate a trash entry.
        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        m2["managed_indexes"] = {}
        plan2 = await reco.plan("hist_test", m2, prev_hash=hash1)
        await reco.apply(plan2, manifest=m2, applied_by="ci")

        # History should now have 2 entries.
        history = await reco.get_history("hist_test", limit=5)
        assert len(history) == 2
        assert history[0]["revision"] > history[1]["revision"]

        # Revisions collection directly.
        revs = await real_mongo_db[MANIFEST_REVISIONS_COLLECTION].find({"slug": "hist_test"}).to_list(length=None)
        assert len(revs) == 2

        # Trash entries should list orders.
        entries = await reco.trash_list("hist_test")
        assert any(e.get("original_name") == "orders" for e in entries)

        # Purge with expired_only=False should drop everything.
        purged = await reco.trash_purge("hist_test", expired_only=False)
        assert purged >= 1

    async def test_restore_from_trash(self, real_mongo_db):
        await bootstrap_reconciler_collections(real_mongo_db)
        reco = Reconciler(real_mongo_db)
        m1 = _manifest_v1()
        m1["slug"] = "rest_test"

        plan1 = await reco.plan("rest_test", m1, prev_hash=None)
        r1 = await reco.apply(plan1, manifest=m1, applied_by="test")
        hash1 = r1["revision"]["hash"]

        await real_mongo_db["rest_test_orders"].insert_one({"restore_me": True})

        m2 = dict(m1)
        m2["collections"] = {"customers": m1["collections"]["customers"]}
        m2["managed_indexes"] = {}
        plan2 = await reco.plan("rest_test", m2, prev_hash=hash1)
        await reco.apply(plan2, manifest=m2, applied_by="test")

        # Restore the trashed collection.
        entries = await reco.trash_list("rest_test")
        orders_entry = next(e for e in entries if e.get("original_name") == "orders")
        result = await reco.trash_restore("rest_test", orders_entry["_id"])
        assert result["restored"] is True

        # Collection should be back with its data.
        names = await real_mongo_db.list_collection_names()
        assert "rest_test_orders" in names
        doc = await real_mongo_db["rest_test_orders"].find_one({"restore_me": True})
        assert doc is not None
