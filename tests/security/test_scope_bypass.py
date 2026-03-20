"""
Tenant isolation bypass tests.

Verifies that ScopedCollectionWrapper enforces app_id scoping and
blocks methods that would circumvent it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mdb_engine.database.scoped_wrapper import ScopedCollectionWrapper

# ============================================================================
# app_id injection on writes
# ============================================================================


@pytest.mark.unit
class TestAppIdInjectionOnInsert:
    """Inserting documents must always get the write_scope app_id."""

    @pytest.mark.asyncio
    async def test_insert_overwrites_attacker_app_id(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        """A document with a foreign app_id must have it replaced."""
        await scoped_wrapper.insert_one({"app_id": "other_tenant", "title": "x"})
        call_args = mock_raw_collection.insert_one.call_args
        inserted_doc = call_args[0][0]
        assert inserted_doc["app_id"] == "tenant_a"

    @pytest.mark.asyncio
    async def test_insert_many_overwrites_app_id(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        docs = [
            {"app_id": "evil", "title": "a"},
            {"title": "b"},
        ]
        await scoped_wrapper.insert_many(docs)
        call_args = mock_raw_collection.insert_many.call_args
        inserted_docs = call_args[0][0]
        for doc in inserted_docs:
            assert doc["app_id"] == "tenant_a"

    @pytest.mark.asyncio
    async def test_insert_adds_app_id_when_missing(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        await scoped_wrapper.insert_one({"title": "no app_id"})
        call_args = mock_raw_collection.insert_one.call_args
        inserted_doc = call_args[0][0]
        assert inserted_doc["app_id"] == "tenant_a"


# ============================================================================
# app_id injection on reads
# ============================================================================


@pytest.mark.unit
class TestAppIdScopingOnReads:
    """Read operations must always include app_id scope filter."""

    @pytest.mark.asyncio
    async def test_find_one_merges_scope(self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock):
        await scoped_wrapper.find_one({"title": "x"})
        call_args = mock_raw_collection.find_one.call_args
        filter_arg = call_args[0][0]
        assert "$and" in filter_arg
        scope_parts = [c for c in filter_arg["$and"] if "app_id" in c]
        assert len(scope_parts) == 1
        assert scope_parts[0]["app_id"]["$in"] == ["tenant_a"]

    @pytest.mark.asyncio
    async def test_find_with_attacker_app_id_still_scoped(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        """Even if the caller provides app_id in the filter, scope is enforced."""
        scoped_wrapper.find({"app_id": "other_tenant"})
        call_args = mock_raw_collection.find.call_args
        filter_arg = call_args[0][0]
        assert "$and" in filter_arg
        scope_parts = [c for c in filter_arg["$and"] if "app_id" in c and "$in" in c.get("app_id", {})]
        assert len(scope_parts) == 1
        assert scope_parts[0]["app_id"]["$in"] == ["tenant_a"]

    @pytest.mark.asyncio
    async def test_count_documents_scoped(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        await scoped_wrapper.count_documents({"status": "active"})
        call_args = mock_raw_collection.count_documents.call_args
        filter_arg = call_args[0][0]
        assert "$and" in filter_arg

    @pytest.mark.asyncio
    async def test_empty_filter_still_scoped(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        await scoped_wrapper.find_one(None)
        call_args = mock_raw_collection.find_one.call_args
        filter_arg = call_args[0][0]
        assert "app_id" in filter_arg


# ============================================================================
# app_id injection on updates/deletes
# ============================================================================


@pytest.mark.unit
class TestAppIdScopingOnMutations:
    @pytest.mark.asyncio
    async def test_update_one_filter_scoped(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        await scoped_wrapper.update_one({"title": "x"}, {"$set": {"title": "y"}})
        call_args = mock_raw_collection.update_one.call_args
        filter_arg = call_args[0][0]
        assert "$and" in filter_arg

    @pytest.mark.asyncio
    async def test_delete_one_filter_scoped(
        self, scoped_wrapper: ScopedCollectionWrapper, mock_raw_collection: MagicMock
    ):
        await scoped_wrapper.delete_one({"title": "x"})
        call_args = mock_raw_collection.delete_one.call_args
        filter_arg = call_args[0][0]
        assert "$and" in filter_arg


# ============================================================================
# Blocked forwarded methods (__getattr__ bypass prevention)
# ============================================================================


@pytest.mark.unit
class TestBlockedForwardedMethods:
    """Motor methods that bypass scoping must raise AttributeError."""

    BLOCKED_METHODS = [
        "bulk_write",
        "replace_one",
        "find_one_and_replace",
        "find_one_and_update",
        "find_one_and_delete",
        "rename",
        "drop",
    ]

    @pytest.mark.parametrize("method_name", BLOCKED_METHODS)
    def test_blocked_method_raises(self, scoped_wrapper: ScopedCollectionWrapper, method_name: str):
        with pytest.raises(AttributeError, match="bypasses tenant scoping"):
            getattr(scoped_wrapper, method_name)

    def test_database_via_getattr_blocked_without_parent(self, mock_raw_collection: MagicMock):
        """Without parent_wrapper, accessing .database via __getattr__ is blocked."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_raw_collection,
            read_scopes=["t"],
            write_scope="t",
        )
        # Without parent_wrapper the property may fall through to __getattr__
        # which blocks "database". Either way the raw Motor database must
        # never leak.
        try:
            db = wrapper.database
            from mdb_engine.database.scoped_wrapper import ScopedMongoWrapper

            assert isinstance(db, ScopedMongoWrapper)
        except AttributeError:
            pass  # Blocked -- acceptable

    def test_client_attribute_blocked_via_getattr(self, mock_raw_collection: MagicMock):
        """Accessing .client via __getattr__ must be blocked."""
        wrapper = ScopedCollectionWrapper(
            real_collection=mock_raw_collection,
            read_scopes=["t"],
            write_scope="t",
        )
        with pytest.raises(AttributeError, match="blocked"):
            wrapper.__getattr__("client")

    def test_safe_read_methods_still_work(self, scoped_wrapper: ScopedCollectionWrapper):
        """Non-blocked methods like create_index should still proxy through."""
        idx = scoped_wrapper.create_index
        assert idx is not None
