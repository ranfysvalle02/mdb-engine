"""
Integration tests for data isolation (CRITICAL for multi-tenancy).

These tests verify that data from different apps is properly isolated
and cannot be accessed across app boundaries.
"""

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
class TestScopedDataIsolation:
    """Test data isolation between apps (CRITICAL for enterprise use)."""

    async def test_data_isolation_between_apps(self, real_mongodb_engine):
        """Test that two apps with same collection names have isolated data."""
        engine = real_mongodb_engine

        # Register two apps
        app1_manifest = {
            "schema_version": "2.0",
            "slug": "app1",
            "name": "App 1",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "app2",
            "name": "App 2",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(app1_manifest, create_indexes=False)
        await engine.register_app(app2_manifest, create_indexes=False)

        # Get app secrets if secrets manager is configured
        app1_secret = None
        app2_secret = None
        if engine._app_secrets_manager:
            app1_secret = await engine._app_secrets_manager.get_app_secret("app1")
            app2_secret = await engine._app_secrets_manager.get_app_secret("app2")

        # Get scoped databases
        db1 = await engine.get_scoped_db("app1", app_token=app1_secret)
        db2 = await engine.get_scoped_db("app2", app_token=app2_secret)

        # Insert data into same collection name
        collection1 = db1.test_collection
        collection2 = db2.test_collection

        await collection1.insert_one({"name": "App 1 Document", "value": 100})
        await collection2.insert_one({"name": "App 2 Document", "value": 200})

        # Verify isolation: app1 shouldn't see app2's data
        app1_docs = await collection1.find({}).to_list(length=100)
        app2_docs = await collection2.find({}).to_list(length=100)

        assert len(app1_docs) == 1
        assert len(app2_docs) == 1
        assert app1_docs[0]["name"] == "App 1 Document"
        assert app2_docs[0]["name"] == "App 2 Document"

        # Verify app_id scoping
        assert app1_docs[0]["app_id"] == "app1"
        assert app2_docs[0]["app_id"] == "app2"

    async def test_query_filtering_applies_scope(self, real_mongodb_engine):
        """Test that query filters include app_id scope."""
        engine = real_mongodb_engine

        app_manifest = {
            "schema_version": "2.0",
            "slug": "filter_test",
            "name": "Filter Test",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(app_manifest, create_indexes=False)

        secret = None
        if engine._app_secrets_manager:
            secret = await engine._app_secrets_manager.get_app_secret("filter_test")

        scoped_db = await engine.get_scoped_db("filter_test", app_token=secret)
        collection = scoped_db.test_collection

        # Insert documents
        await collection.insert_one({"name": "Doc 1", "status": "active"})
        await collection.insert_one({"name": "Doc 2", "status": "inactive"})

        # Query with filter - should only return docs from this app
        results = await collection.find({"status": "active"}).to_list(length=100)

        assert len(results) == 1
        assert results[0]["name"] == "Doc 1"
        assert results[0]["app_id"] == "filter_test"

    async def test_write_isolation(self, real_mongodb_engine):
        """Test that writes from one app don't appear in another app's queries."""
        engine = real_mongodb_engine

        app1_manifest = {
            "schema_version": "2.0",
            "slug": "write_app1",
            "name": "Write App 1",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "write_app2",
            "name": "Write App 2",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(app1_manifest, create_indexes=False)
        await engine.register_app(app2_manifest, create_indexes=False)

        write_app1_secret = None
        write_app2_secret = None
        if engine._app_secrets_manager:
            write_app1_secret = await engine._app_secrets_manager.get_app_secret("write_app1")
            write_app2_secret = await engine._app_secrets_manager.get_app_secret("write_app2")

        db1 = await engine.get_scoped_db("write_app1", app_token=write_app1_secret)
        db2 = await engine.get_scoped_db("write_app2", app_token=write_app2_secret)

        collection1 = db1.shared_collection
        collection2 = db2.shared_collection

        # App1 writes data
        await collection1.insert_one({"data": "from_app1", "secret": "app1_secret"})

        # App2 queries same collection - should see nothing
        app2_results = await collection2.find({}).to_list(length=100)
        assert len(app2_results) == 0

        # App1 should see its own data
        app1_results = await collection1.find({}).to_list(length=100)
        assert len(app1_results) == 1
        assert app1_results[0]["data"] == "from_app1"

    async def test_concurrent_operations(self, real_mongodb_engine):
        """Test concurrent operations from multiple apps."""
        import asyncio

        engine = real_mongodb_engine

        # Register multiple apps
        apps = []
        app_secrets = {}
        for i in range(3):
            manifest = {
                "schema_version": "2.0",
                "slug": f"concurrent_app_{i}",
                "name": f"Concurrent App {i}",
                "status": "active",
                "developer_id": "dev@example.com",
            }
            await engine.register_app(manifest, create_indexes=False)
            slug = f"concurrent_app_{i}"
            apps.append(slug)
            if engine._app_secrets_manager:
                app_secrets[slug] = await engine._app_secrets_manager.get_app_secret(slug)

        # Concurrent writes
        async def write_data(app_slug):
            db = await engine.get_scoped_db(app_slug, app_token=app_secrets.get(app_slug))
            collection = db.test_collection
            for j in range(5):
                await collection.insert_one({"app": app_slug, "index": j, "data": f"data_{j}"})

        # Run concurrent writes
        await asyncio.gather(*[write_data(app) for app in apps])

        # Verify each app only sees its own data
        for app_slug in apps:
            db = await engine.get_scoped_db(app_slug, app_token=app_secrets.get(app_slug))
            collection = db.test_collection
            docs = await collection.find({}).to_list(length=100)

            assert len(docs) == 5
            assert all(doc["app_id"] == app_slug for doc in docs)
            assert all(doc["app"] == app_slug for doc in docs)

    async def test_multi_scope_read_operations(self, real_mongodb_engine):
        """Test read operations with multiple scopes."""
        engine = real_mongodb_engine

        app1_manifest = {
            "schema_version": "2.0",
            "slug": "multi_scope_app1",
            "name": "Multi Scope App 1",
            "status": "active",
            "developer_id": "dev@example.com",
            "data_access": {
                "read_scopes": ["multi_scope_app1", "multi_scope_app2"],
            },
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "multi_scope_app2",
            "name": "Multi Scope App 2",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(app1_manifest, create_indexes=False)
        await engine.register_app(app2_manifest, create_indexes=False)

        ms_app1_secret = None
        ms_app2_secret = None
        if engine._app_secrets_manager:
            ms_app1_secret = await engine._app_secrets_manager.get_app_secret("multi_scope_app1")
            ms_app2_secret = await engine._app_secrets_manager.get_app_secret("multi_scope_app2")

        # Create scoped DB with multiple read scopes
        db = await engine.get_scoped_db(
            app_slug="multi_scope_app1",
            app_token=ms_app1_secret,
            read_scopes=["multi_scope_app1", "multi_scope_app2"],
            write_scope="multi_scope_app1",
        )

        collection = db.shared_collection

        # Insert app1 data into app1's collection
        # (will have app_id=multi_scope_app1 from write_scope)
        await collection.insert_one({"data": "from_app1"})

        # Insert app2 data into app2's collection using app2's scoped DB
        db2 = await engine.get_scoped_db("multi_scope_app2", app_token=ms_app2_secret)
        await db2.shared_collection.insert_one({"data": "from_app2"})

        # Multi-scope read should see documents from both collections
        # Use get_collection with fully prefixed name to read from app2's collection
        app2_collection = db.get_collection("multi_scope_app2_shared_collection")

        import asyncio

        await asyncio.sleep(0.1)  # Brief wait for writes to be visible

        # Read from app1's collection (should see app1's doc)
        app1_results = await collection.find({}).to_list(length=100)
        # Read from app2's collection via multi-scope read (should see app2's doc)
        app2_results = await app2_collection.find({}).to_list(length=100)

        assert len(app1_results) == 1
        assert app1_results[0]["data"] == "from_app1"
        assert app1_results[0]["app_id"] == "multi_scope_app1"

        assert len(app2_results) == 1
        assert app2_results[0]["data"] == "from_app2"
        assert app2_results[0]["app_id"] == "multi_scope_app2"

        # But write should only go to app1
        await collection.insert_one({"data": "written_by_multi_scope"})

        # Check that writes go to app1's collection
        db1 = await engine.get_scoped_db("multi_scope_app1", app_token=ms_app1_secret)
        app1_final_results = await db1.shared_collection.find({}).to_list(length=100)
        app2_final_results = await db2.shared_collection.find({}).to_list(length=100)

        # App1 should have 2 docs (original + new write)
        assert len(app1_final_results) == 2
        # App2 should still have 1 doc (original only)
        assert len(app2_final_results) == 1

    async def test_find_one_isolation(self, real_mongodb_engine):
        """Test that find_one respects app boundaries."""
        engine = real_mongodb_engine

        app1_manifest = {
            "schema_version": "2.0",
            "slug": "findone_app1",
            "name": "FindOne App 1",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        app2_manifest = {
            "schema_version": "2.0",
            "slug": "findone_app2",
            "name": "FindOne App 2",
            "status": "active",
            "developer_id": "dev@example.com",
        }

        await engine.register_app(app1_manifest, create_indexes=False)
        await engine.register_app(app2_manifest, create_indexes=False)

        findone_app1_secret = None
        findone_app2_secret = None
        if engine._app_secrets_manager:
            findone_app1_secret = await engine._app_secrets_manager.get_app_secret("findone_app1")
            findone_app2_secret = await engine._app_secrets_manager.get_app_secret("findone_app2")

        db1 = await engine.get_scoped_db("findone_app1", app_token=findone_app1_secret)
        db2 = await engine.get_scoped_db("findone_app2", app_token=findone_app2_secret)

        # App1 inserts a document
        await db1.test_collection.insert_one({"name": "App1 Doc", "id": 1})

        # App2 tries to find by same criteria - should get None
        doc2 = await db2.test_collection.find_one({"id": 1})
        assert doc2 is None

        # App1 should find its own document
        doc1 = await db1.test_collection.find_one({"id": 1})
        assert doc1 is not None
        assert doc1["name"] == "App1 Doc"
        assert doc1["app_id"] == "findone_app1"

    async def test_app_id_always_injected_on_write(self, real_mongodb_engine):
        """Invariant: every document written through scoped wrapper MUST have app_id."""
        engine = real_mongodb_engine

        manifest = {
            "schema_version": "2.0",
            "slug": "inject_test",
            "name": "Injection Test",
            "status": "active",
            "developer_id": "dev@example.com",
        }
        await engine.register_app(manifest, create_indexes=False)

        secret = None
        if engine._app_secrets_manager:
            secret = await engine._app_secrets_manager.get_app_secret("inject_test")

        db = await engine.get_scoped_db("inject_test", app_token=secret)
        col = db.items

        await col.insert_one({"name": "no explicit app_id"})
        await col.insert_many([{"name": "batch1"}, {"name": "batch2"}])

        raw_db = engine._connection_manager.mongo_db
        raw_docs = await raw_db["inject_test_items"].find({}).to_list(100)

        assert len(raw_docs) == 3
        for doc in raw_docs:
            assert doc["app_id"] == "inject_test", f"Document missing app_id: {doc}"

    async def test_cross_app_read_scopes_grant_access(self, real_mongodb_engine):
        """App whose manifest declares cross-app read_scopes CAN see the other app's data."""
        engine = real_mongodb_engine

        await engine.register_app(
            {
                "schema_version": "2.0",
                "slug": "producer",
                "name": "Producer",
                "status": "active",
                "developer_id": "dev@example.com",
            },
            create_indexes=False,
        )

        await engine.register_app(
            {
                "schema_version": "2.0",
                "slug": "consumer",
                "name": "Consumer",
                "status": "active",
                "developer_id": "dev@example.com",
                "data_access": {
                    "read_scopes": ["consumer", "producer"],
                    "write_scope": "consumer",
                },
            },
            create_indexes=False,
        )

        producer_secret = None
        if engine._app_secrets_manager:
            producer_secret = await engine._app_secrets_manager.get_app_secret("producer")
        producer_db = await engine.get_scoped_db("producer", app_token=producer_secret)
        await producer_db.events.insert_one({"type": "click", "page": "/home"})

        consumer_secret = None
        if engine._app_secrets_manager:
            consumer_secret = await engine._app_secrets_manager.get_app_secret("consumer")

        consumer_db = await engine.get_scoped_db("consumer", app_token=consumer_secret)

        own_docs = await consumer_db.events.find({}).to_list(100)
        assert len(own_docs) == 0, "Consumer should have no own events"

        cross_col = consumer_db.get_collection("producer_events")
        cross_docs = await cross_col.find({}).to_list(100)
        assert len(cross_docs) == 1, "Consumer should see producer's events via cross-app scopes"
        assert cross_docs[0]["type"] == "click"

    async def test_cross_app_denied_without_manifest_scope(self, real_mongodb_engine):
        """Requesting read_scopes not declared in the manifest is rejected by get_scoped_db."""
        engine = real_mongodb_engine

        for slug in ("owner", "intruder"):
            await engine.register_app(
                {
                    "schema_version": "2.0",
                    "slug": slug,
                    "name": slug.title(),
                    "status": "active",
                    "developer_id": "dev@example.com",
                },
                create_indexes=False,
            )

        intruder_secret = None
        if engine._app_secrets_manager:
            intruder_secret = await engine._app_secrets_manager.get_app_secret("intruder")

        with pytest.raises(ValueError, match="not authorized"):
            await engine.get_scoped_db(
                "intruder",
                app_token=intruder_secret,
                read_scopes=["intruder", "owner"],
            )
