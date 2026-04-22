"""Integration test for the admin plane audit middleware.

Asserts that every authenticated admin call results in exactly one
row written to ``_mdb_admin_audit`` with the expected shape. Uses the
shared ``real_mongo_db`` fixture — see ``conftest.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import (
    ADMIN_AUDIT_COLLECTION,
    ADMIN_TOKEN_HEADER,
    AdminSurface,
    bootstrap_admin_collections,
)


class _FakeSecrets:
    def __init__(self):
        self._tok = "t0ken"

    async def verify_app_secret(self, slug: str, provided: str) -> bool:
        return provided == self._tok

    async def verify_app_token(self, slug: str, provided: str):
        class _R:
            def __init__(self, valid, scopes):
                self.valid = valid
                self.scopes = scopes

        return _R(provided == self._tok, ["*"])


class _AuditEngine:
    """Engine stub wired to a real Mongo for audit writes only."""

    def __init__(self, mongo_db):
        self._app_secrets_manager = _FakeSecrets()
        self._surface: AdminSurface | None = None

        class _CM:
            def __init__(self, db):
                self.mongo_db = db

        self._connection_manager = _CM(mongo_db)

    def admin_surface(self, cfg=None):
        if self._surface is None:
            self._surface = AdminSurface(self, cfg or {})
            self._surface.register_default_modules()
        return self._surface

    def admin_surface_cached(self):
        return self._surface

    async def manifest_diff(self, slug: str) -> dict[str, Any]:
        return {"slug": slug, "is_noop": True}


@pytest.mark.asyncio
class TestAdminAudit:
    async def test_authenticated_call_writes_one_row(self, real_mongo_db):
        await bootstrap_admin_collections(real_mongo_db)
        await real_mongo_db[ADMIN_AUDIT_COLLECTION].delete_many({"slug": "auditdemo"})

        engine = _AuditEngine(real_mongo_db)
        surface = engine.admin_surface({})
        app = FastAPI()
        app.include_router(surface.build_router(), prefix="/__mdb")

        @app.middleware("http")
        async def _flush(request, call_next):
            resp = await call_next(request)
            await surface.persist_audit(request, resp)
            return resp

        c = TestClient(app)
        r = c.get(
            "/__mdb/reconciler/plan?slug=auditdemo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200

        rows = await real_mongo_db[ADMIN_AUDIT_COLLECTION].find({"slug": "auditdemo"}).to_list(length=None)
        assert len(rows) == 1
        row = rows[0]
        assert row["module"] == "reconciler"
        assert row["method"] == "GET"
        assert row["status"] == 200
        assert row["endpoint"].endswith("/reconciler/plan")
        assert isinstance(row["duration_ms"], int | float)
        assert row["principal_token_fingerprint"].startswith("sha256:")
        assert "at" in row

    async def test_unauthenticated_call_is_not_audited(self, real_mongo_db):
        await bootstrap_admin_collections(real_mongo_db)
        await real_mongo_db[ADMIN_AUDIT_COLLECTION].delete_many({"slug": "noauth"})

        engine = _AuditEngine(real_mongo_db)
        surface = engine.admin_surface({})
        app = FastAPI()
        app.include_router(surface.build_router(), prefix="/__mdb")

        @app.middleware("http")
        async def _flush(request, call_next):
            resp = await call_next(request)
            await surface.persist_audit(request, resp)
            return resp

        c = TestClient(app)
        r = c.get("/__mdb/reconciler/plan?slug=noauth")  # no token
        assert r.status_code == 401

        rows = await real_mongo_db[ADMIN_AUDIT_COLLECTION].find({"slug": "noauth"}).to_list(length=None)
        assert rows == [], "unauthenticated requests should never be audited"
