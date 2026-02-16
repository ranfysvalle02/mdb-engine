"""
Integration tests that validate ALL example apps work with mdb-engine.

Three-tier testing strategy:
  Tier 1 (TestExampleManifests): Static manifest validation — no MongoDB required.
  Tier 2 (TestExampleAppRegistration): Engine registration with real MongoDB (testcontainers).
  Tier 3 (TestExampleEndpoints): HTTP endpoint smoke tests via httpx AsyncClient.

Run all tiers:
    make test-examples          # requires Docker

Run Tier 1 only (fast, no Docker):
    make test-examples-fast
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.manifest import ManifestValidator

# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

EXAMPLES_ROOT = Path(__file__).parent.parent.parent / "examples"


def _discover_example_manifests() -> list[Path]:
    """Find all manifest.json files under examples/."""
    return sorted(EXAMPLES_ROOT.rglob("manifest.json"))


def _manifest_id(path: Path) -> str:
    """Human-readable pytest id for a manifest path."""
    return str(path.relative_to(EXAMPLES_ROOT))


# Pre-discover so parametrize works at collection time.
_ALL_MANIFESTS = _discover_example_manifests()


# ============================================================================
# Tier 1 — Static Manifest Validation (no MongoDB)
# ============================================================================


@pytest.mark.asyncio
class TestExampleManifests:
    """Validate every example manifest against the JSON Schema."""

    # ---- schema validation ------------------------------------------------

    @pytest.mark.parametrize("manifest_path", _ALL_MANIFESTS, ids=_manifest_id)
    async def test_manifest_schema_valid(self, manifest_path: Path):
        """Each manifest must pass ManifestValidator.validate()."""
        manifest = json.loads(manifest_path.read_text())
        is_valid, error, paths = await ManifestValidator.validate(manifest, use_cache=False)
        assert is_valid, (
            f"Manifest {manifest_path.relative_to(EXAMPLES_ROOT)} failed validation: " f"{error} (paths: {paths})"
        )

    # ---- required fields --------------------------------------------------

    @pytest.mark.parametrize("manifest_path", _ALL_MANIFESTS, ids=_manifest_id)
    async def test_manifest_has_required_fields(self, manifest_path: Path):
        """Every manifest must contain at least slug and name."""
        manifest = json.loads(manifest_path.read_text())
        assert "slug" in manifest, f"Missing 'slug' in {manifest_path}"
        assert "name" in manifest, f"Missing 'name' in {manifest_path}"

    # ---- slug format ------------------------------------------------------

    @pytest.mark.parametrize("manifest_path", _ALL_MANIFESTS, ids=_manifest_id)
    async def test_manifest_slug_format(self, manifest_path: Path):
        """Slugs must be non-empty strings without whitespace."""
        manifest = json.loads(manifest_path.read_text())
        slug = manifest["slug"]
        assert isinstance(slug, str) and len(slug) > 0, f"Invalid slug in {manifest_path}"
        assert " " not in slug, f"Slug contains whitespace in {manifest_path}"

    # ---- schema version ---------------------------------------------------

    @pytest.mark.parametrize("manifest_path", _ALL_MANIFESTS, ids=_manifest_id)
    async def test_manifest_schema_version(self, manifest_path: Path):
        """If schema_version is present it must be a known value."""
        manifest = json.loads(manifest_path.read_text())
        version = manifest.get("schema_version")
        if version is not None:
            assert version in ("1.0", "2.0"), f"Unknown schema_version '{version}' in {manifest_path}"


# ============================================================================
# Tier 1b — Example Module Import Smoke Tests (no MongoDB)
# ============================================================================


def _discover_example_entry_points() -> list[tuple[str, Path]]:
    """
    Return (name, path) for each importable example entry-point.

    Looks for web.py or app.py in immediate example directories.
    Skips multi-app sub-apps (they need the hub running).
    """
    entries: list[tuple[str, Path]] = []

    for category_dir in sorted(EXAMPLES_ROOT.iterdir()):
        if not category_dir.is_dir():
            continue
        for example_dir in sorted(category_dir.iterdir()):
            if not example_dir.is_dir():
                continue
            # Skip multi-app sub-app directories (tested differently)
            if example_dir.name in ("sso-multi-app", "websocket-tickets"):
                continue
            for candidate in ("web.py", "app.py"):
                entry = example_dir / candidate
                if entry.exists():
                    entries.append((f"{category_dir.name}/{example_dir.name}/{candidate}", entry))
                    break  # only one entry point per example
    return entries


_ALL_ENTRY_POINTS = _discover_example_entry_points()


class TestExampleImports:
    """
    Verify that each example's main module can be imported without errors.

    MongoDB connections are mocked so no real database is needed.
    """

    @pytest.mark.parametrize(
        "name,entry_path",
        _ALL_ENTRY_POINTS,
        ids=[e[0] for e in _ALL_ENTRY_POINTS],
    )
    def test_example_module_importable(self, name: str, entry_path: Path, monkeypatch):
        """Importing the example module should not raise."""
        import importlib

        # Ensure the example's directory is on sys.path so relative imports work.
        example_dir = str(entry_path.parent)
        monkeypatch.syspath_prepend(example_dir)

        # Change CWD to the example directory so relative Path("manifest.json")
        # resolves correctly — this matches how users run examples in practice.
        monkeypatch.chdir(example_dir)

        # Set minimal env vars that some examples check at import time.
        monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")

        module_name = entry_path.stem  # "web" or "app"

        # Mock the Motor client so no real connection is attempted.
        mock_client = MagicMock()
        mock_client.admin = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})

        # Deduplicate module name to avoid collisions across examples.
        unique_module = f"_example_import_{entry_path.parent.name}_{module_name}"

        with patch("mdb_engine.core.connection.AsyncIOMotorClient", return_value=mock_client):
            # Load module from file path to avoid collisions.
            spec = importlib.util.spec_from_file_location(unique_module, str(entry_path))
            assert spec is not None, f"Could not create spec for {entry_path}"
            assert spec.loader is not None, f"No loader for {entry_path}"
            mod = importlib.util.module_from_spec(spec)
            sys.modules[unique_module] = mod
            try:
                spec.loader.exec_module(mod)
            finally:
                sys.modules.pop(unique_module, None)

        # If we get here the import succeeded.
        assert True


# ============================================================================
# Tier 2 — Engine Registration (real MongoDB via testcontainers)
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
class TestExampleAppRegistration:
    """Register each example manifest with a real MongoDBEngine."""

    @pytest.mark.parametrize("manifest_path", _ALL_MANIFESTS, ids=_manifest_id)
    async def test_register_manifest(self, real_mongodb_engine, manifest_path: Path):
        """
        Every example manifest must register successfully.

        We disable index creation here because vector search indexes require
        Atlas (the testcontainer image may not support them).
        """
        manifest = json.loads(manifest_path.read_text())
        slug = manifest["slug"]

        result = await real_mongodb_engine.register_app(manifest, create_indexes=False)
        assert result is True, f"register_app returned {result} for {slug}"
        assert slug in real_mongodb_engine.apps, f"{slug} not in engine.apps after registration"

    # ---- scoped DB access -------------------------------------------------

    async def test_scoped_db_hello_world(self, real_mongodb_engine):
        """hello_world uses quickstart (no manifest). Verify scoped DB works."""
        minimal = {
            "schema_version": "2.0",
            "slug": "hello_test",
            "name": "Hello Test",
        }
        await real_mongodb_engine.register_app(minimal, create_indexes=False)

        token = await real_mongodb_engine.auto_retrieve_app_token("hello_test")
        db = await real_mongodb_engine.get_scoped_db("hello_test", app_token=token)
        assert db is not None

        # Basic write/read round-trip
        result = await db.items.insert_one({"msg": "hello"})
        assert result.inserted_id is not None
        doc = await db.items.find_one({"_id": result.inserted_id})
        assert doc is not None
        assert doc["msg"] == "hello"

    async def test_scoped_db_simple_app(self, real_mongodb_engine):
        """simple_app manifest should register and allow scoped DB CRUD."""
        manifest_path = EXAMPLES_ROOT / "advanced" / "simple_app" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())

        await real_mongodb_engine.register_app(manifest, create_indexes=False)
        token = await real_mongodb_engine.auto_retrieve_app_token(manifest["slug"])
        db = await real_mongodb_engine.get_scoped_db(manifest["slug"], app_token=token)
        assert db is not None

        # Insert a task document
        from datetime import datetime

        task_doc = {
            "title": "Test task",
            "completed": False,
            "created_at": datetime.utcnow(),
        }
        result = await db.tasks.insert_one(task_doc)
        assert result.inserted_id is not None

        # Read it back
        tasks = await db.tasks.find({}).to_list(length=10)
        assert len(tasks) >= 1
        assert tasks[0]["title"] == "Test task"

    # ---- memory service initialisation ------------------------------------

    async def test_memory_service_init_for_memory_apps(self, real_mongodb_engine):
        """
        Apps with memory_config.enabled should have a non-None memory service
        after registration.

        We mock the OpenAI key since we don't call the LLM.
        """
        memory_manifests = [
            EXAMPLES_ROOT / "basic" / "memory_quickstart" / "manifest.json",
            EXAMPLES_ROOT / "basic" / "chit_chat" / "manifest.json",
            EXAMPLES_ROOT / "basic" / "gdpr_demo" / "manifest.json",
        ]

        for mpath in memory_manifests:
            if not mpath.exists():
                continue
            manifest = json.loads(mpath.read_text())
            slug = manifest["slug"]

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-fake"}):
                result = await real_mongodb_engine.register_app(manifest, create_indexes=False)
                assert result is True, f"Failed to register {slug}"

            svc = real_mongodb_engine.get_memory_service(slug)
            assert svc is not None, f"Memory service is None for {slug}"

    # ---- graph service initialisation -------------------------------------

    async def test_graph_service_init_for_graph_apps(self, real_mongodb_engine):
        """
        Apps with graph_config.enabled should have a non-None graph service
        after registration.
        """
        graph_manifests = [
            EXAMPLES_ROOT / "basic" / "graphs-mdb" / "manifest.json",
        ]

        for mpath in graph_manifests:
            if not mpath.exists():
                continue
            manifest = json.loads(mpath.read_text())
            slug = manifest["slug"]

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-fake"}):
                result = await real_mongodb_engine.register_app(manifest, create_indexes=False)
                assert result is True, f"Failed to register {slug}"

            svc = real_mongodb_engine.get_graph_service(slug)
            assert svc is not None, f"Graph service is None for {slug}"

    # ---- multi-app registration -------------------------------------------

    async def test_sso_multi_app_all_manifests_register(self, real_mongodb_engine):
        """All 4 SSO multi-app sub-app manifests should register."""
        sso_root = EXAMPLES_ROOT / "advanced" / "sso-multi-app" / "apps"
        if not sso_root.exists():
            pytest.skip("sso-multi-app example not found")

        manifests = sorted(sso_root.rglob("manifest.json"))
        assert len(manifests) >= 1, "No manifests found in sso-multi-app"

        for mpath in manifests:
            manifest = json.loads(mpath.read_text())
            slug = manifest["slug"]
            result = await real_mongodb_engine.register_app(manifest, create_indexes=False)
            assert result is True, f"Failed to register SSO sub-app {slug} ({mpath})"
            assert slug in real_mongodb_engine.apps

    async def test_websocket_tickets_all_manifests_register(self, real_mongodb_engine):
        """All websocket-tickets sub-app manifests should register."""
        ws_root = EXAMPLES_ROOT / "advanced" / "websocket-tickets" / "apps"
        if not ws_root.exists():
            pytest.skip("websocket-tickets example not found")

        manifests = sorted(ws_root.rglob("manifest.json"))
        assert len(manifests) >= 1, "No manifests found in websocket-tickets"

        for mpath in manifests:
            manifest = json.loads(mpath.read_text())
            slug = manifest["slug"]
            result = await real_mongodb_engine.register_app(manifest, create_indexes=False)
            assert result is True, f"Failed to register WS sub-app {slug} ({mpath})"
            assert slug in real_mongodb_engine.apps

    # ---- managed indexes --------------------------------------------------

    async def test_managed_indexes_simple_app(self, real_mongodb_engine):
        """simple_app defines managed_indexes — verify they are created."""
        manifest_path = EXAMPLES_ROOT / "advanced" / "simple_app" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())

        # Register WITH index creation enabled.
        result = await real_mongodb_engine.register_app(manifest, create_indexes=True)
        assert result is True

        # Verify the scoped DB can list indexes on the tasks collection.
        token = await real_mongodb_engine.auto_retrieve_app_token(manifest["slug"])
        db = await real_mongodb_engine.get_scoped_db(manifest["slug"], app_token=token)
        indexes = await db.tasks.index_information()
        # At minimum there should be the default _id index.
        assert len(indexes) >= 1


# ============================================================================
# Tier 3 — HTTP Endpoint Smoke Tests (real MongoDB via testcontainers)
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
class TestExampleEndpoints:
    """
    Smoke-test HTTP endpoints for examples that don't need external services
    (no LLM, no OSO Cloud, no auth hub).
    """

    async def test_hello_world_endpoints(self, real_mongodb_engine):
        """
        Replicate the hello_world example routes and verify CRUD works.

        We build the app programmatically (matching hello_world/web.py) so we
        can control the engine lifecycle directly without relying on ASGI
        lifespan events that httpx.ASGITransport does not trigger.
        """
        from fastapi import Depends, FastAPI

        engine = real_mongodb_engine
        slug = "hello_endpoint_test"

        # Register a minimal manifest (same pattern as hello_world).
        await engine.register_app(
            {"schema_version": "2.0", "slug": slug, "name": "Hello Endpoint Test"},
            create_indexes=False,
        )

        # Retrieve the app token so scoped DB access is authorized.
        app_token = await engine.auto_retrieve_app_token(slug)

        # Override the get_scoped_db dependency to include the app token.
        async def get_scoped_db_with_token():
            return await engine.get_scoped_db(slug, app_token=app_token)

        # Build a lightweight FastAPI app with the same routes as hello_world.
        test_app = FastAPI()
        test_app.state.engine = engine
        test_app.state.app_slug = slug

        @test_app.get("/")
        async def index():
            return {"message": "Hello from mdb-engine!"}

        @test_app.post("/items")
        async def create_item(item: dict, db=Depends(get_scoped_db_with_token)):
            result = await db.items.insert_one(item)
            return {"id": str(result.inserted_id)}

        @test_app.get("/items")
        async def list_items(db=Depends(get_scoped_db_with_token)):
            items = await db.items.find({}).to_list(length=50)
            for i in items:
                i["_id"] = str(i["_id"])
            return {"items": items}

        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            # Root endpoint
            resp = await client.get("/")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Hello from mdb-engine!"

            # Create item
            resp = await client.post("/items", json={"name": "widget"})
            assert resp.status_code == 200
            assert "id" in resp.json()

            # List items
            resp = await client.get("/items")
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) >= 1
            assert items[0]["name"] == "widget"
