"""
Integration tests for CORS in single-app (create_app) mode.

Verifies that ``cors`` config in a manifest produces real CORS headers on
HTTP responses when using ``engine.create_app()``.  This complements the
existing multi-app CORS tests in ``test_multi_app_integration.py``.
"""

import json
import os

import pytest
from httpx import ASGITransport, AsyncClient

from mdb_engine.core.engine import MongoDBEngine


@pytest.mark.integration
class TestCORSCreateAppWildcard:
    """create_app with ``cors: {enabled: true, allow_origins: ['*']}``."""

    @pytest.mark.asyncio
    async def test_preflight_returns_cors_headers(self, mongodb_connection_string, tmp_path):
        manifest = {
            "schema_version": "2.0",
            "slug": "cors-single-wildcard",
            "name": "CORS Single Wildcard",
            "cors": {"enabled": True, "allow_origins": ["*"]},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_single_wc_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-single-wildcard", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "http://localhost:5500",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert res.headers.get("access-control-allow-origin") == "*"

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_regular_request_has_cors_header(self, mongodb_connection_string, tmp_path):
        manifest = {
            "schema_version": "2.0",
            "slug": "cors-single-reg",
            "name": "CORS Single Regular",
            "cors": {"enabled": True, "allow_origins": ["*"]},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_single_reg_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-single-reg", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.get(
                    "/docs",
                    headers={"Origin": "http://localhost:5500"},
                )
                assert res.headers.get("access-control-allow-origin") == "*"

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_null_origin_allowed_with_wildcard(self, mongodb_connection_string, tmp_path):
        """file:// sends Origin: null — wildcard should allow it."""
        manifest = {
            "schema_version": "2.0",
            "slug": "cors-single-null",
            "name": "CORS Null Origin",
            "cors": {"enabled": True, "allow_origins": ["*"]},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_single_null_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-single-null", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "null",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert res.headers.get("access-control-allow-origin") == "*"

        await engine.shutdown()


@pytest.mark.integration
class TestCORSCreateAppSpecificOrigins:
    """create_app with specific origins and credentials."""

    @pytest.mark.asyncio
    async def test_allowed_origin_gets_cors_header(self, mongodb_connection_string, tmp_path, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")

        manifest = {
            "schema_version": "2.0",
            "slug": "cors-specific",
            "name": "CORS Specific",
            "cors": {
                "enabled": True,
                "allow_origins": ["https://myapp.com"],
                "allow_credentials": True,
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_specific_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-specific", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "https://myapp.com",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                assert res.headers.get("access-control-allow-origin") == "https://myapp.com"
                assert res.headers.get("access-control-allow-credentials", "").lower() == "true"

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_disallowed_origin_rejected(self, mongodb_connection_string, tmp_path, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")

        manifest = {
            "schema_version": "2.0",
            "slug": "cors-reject",
            "name": "CORS Reject",
            "cors": {
                "enabled": True,
                "allow_origins": ["https://myapp.com"],
                "allow_credentials": True,
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_reject_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-reject", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "https://evil.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                acao = res.headers.get("access-control-allow-origin")
                assert acao != "https://evil.com", f"Disallowed origin should not get CORS header, got: {acao}"

        await engine.shutdown()


@pytest.mark.integration
class TestCORSCreateAppDisabled:
    """create_app WITHOUT cors should produce no CORS headers."""

    @pytest.mark.asyncio
    async def test_no_cors_config_means_no_cors_headers(self, mongodb_connection_string, tmp_path):
        manifest = {
            "schema_version": "2.0",
            "slug": "no-cors",
            "name": "No CORS",
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_no_cors_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="no-cors", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "http://localhost:5500",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert "access-control-allow-origin" not in res.headers

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_cors_enabled_false_means_no_cors_headers(self, mongodb_connection_string, tmp_path):
        manifest = {
            "schema_version": "2.0",
            "slug": "cors-off",
            "name": "CORS Off",
            "cors": {"enabled": False},
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_off_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-off", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.options(
                    "/api/anything",
                    headers={
                        "Origin": "http://localhost:5500",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert "access-control-allow-origin" not in res.headers

        await engine.shutdown()


@pytest.mark.integration
class TestCORSWithAutoCrudCollection:
    """CORS headers appear on auto-CRUD collection endpoints."""

    @pytest.mark.asyncio
    async def test_cors_on_collection_list(self, mongodb_connection_string, tmp_path):
        manifest = {
            "schema_version": "2.0",
            "slug": "cors-crud",
            "name": "CORS CRUD",
            "cors": {"enabled": True, "allow_origins": ["*"]},
            "collections": {
                "items": {"auto_crud": True},
            },
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        db_name = f"test_cors_crud_{os.getpid()}"
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=db_name)
        app = engine.create_app(slug="cors-crud", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.get(
                    "/api/items",
                    headers={"Origin": "http://localhost:5500"},
                )
                assert res.headers.get("access-control-allow-origin") == "*"

                res = await client.options(
                    "/api/items",
                    headers={
                        "Origin": "http://localhost:5500",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                assert res.headers.get("access-control-allow-origin") == "*"

        await engine.shutdown()
