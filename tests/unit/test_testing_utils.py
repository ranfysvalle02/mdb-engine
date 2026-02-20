"""Tests for mdb_engine.testing — test utilities for app developers."""


class TestFakeCollection:
    """Tests for _FakeCollection CRUD operations."""

    async def test_insert_one_and_find_one(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection()
        await col.insert_one({"_id": "1", "name": "Alice"})
        doc = await col.find_one({"name": "Alice"})
        assert doc is not None
        assert doc["name"] == "Alice"

    async def test_find_returns_cursor(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection([{"status": "active"}, {"status": "done"}, {"status": "active"}])
        docs = await col.find({"status": "active"}).to_list(10)
        assert len(docs) == 2

    async def test_update_one(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection([{"_id": "1", "name": "Alice", "age": 30}])
        result = await col.update_one({"name": "Alice"}, {"$set": {"age": 31}})
        assert result.modified_count == 1
        doc = await col.find_one({"name": "Alice"})
        assert doc["age"] == 31

    async def test_delete_one(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection([{"_id": "1"}, {"_id": "2"}])
        result = await col.delete_one({"_id": "1"})
        assert result.deleted_count == 1
        assert await col.count_documents() == 1

    async def test_count_documents_with_filter(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection([{"s": "a"}, {"s": "b"}, {"s": "a"}])
        assert await col.count_documents({"s": "a"}) == 2
        assert await col.count_documents({"s": "b"}) == 1

    async def test_insert_many(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection()
        result = await col.insert_many([{"x": 1}, {"x": 2}])
        assert len(result.inserted_ids) == 2
        assert await col.count_documents() == 2

    async def test_delete_many(self):
        from mdb_engine.testing import _FakeCollection

        col = _FakeCollection([{"s": "a"}, {"s": "a"}, {"s": "b"}])
        result = await col.delete_many({"s": "a"})
        assert result.deleted_count == 2
        assert await col.count_documents() == 1


class TestFakeScopedDB:
    """Tests for _FakeScopedDB."""

    def test_attribute_access_returns_collection(self):
        from mdb_engine.testing import _FakeScopedDB

        db = _FakeScopedDB()
        col = db.tasks
        assert col is not None

    def test_bracket_access_returns_collection(self):
        from mdb_engine.testing import _FakeScopedDB

        db = _FakeScopedDB()
        col = db["tasks"]
        assert col is not None

    def test_seeded_data(self):
        from mdb_engine.testing import _FakeScopedDB

        db = _FakeScopedDB({"items": [{"name": "A"}, {"name": "B"}]})
        assert len(db.items._docs) == 2

    def test_same_collection_returned_on_repeated_access(self):
        from mdb_engine.testing import _FakeScopedDB

        db = _FakeScopedDB()
        assert db.tasks is db.tasks


class TestMockHelpers:
    """Tests for mock_scoped_db and mock_user."""

    async def test_mock_scoped_db_returns_callable(self):
        from mdb_engine.testing import mock_scoped_db

        override = mock_scoped_db({"items": [{"x": 1}]})
        db = await override()
        docs = await db.items.find().to_list(10)
        assert len(docs) == 1

    async def test_mock_user_returns_callable(self):
        from mdb_engine.testing import mock_user

        override = mock_user({"email": "test@test.com"})
        user = await override()
        assert user["email"] == "test@test.com"

    async def test_mock_user_default(self):
        from mdb_engine.testing import mock_user

        override = mock_user()
        user = await override()
        assert "email" in user
