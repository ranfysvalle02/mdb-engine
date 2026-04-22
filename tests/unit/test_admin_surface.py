"""Unit tests for :class:`AdminSurface` composition + introspection.

These tests do not exercise real auth or Mongo — they validate the
composer's mental model:

- modules can be registered/replaced
- disabled modules are excluded from the router
- ``/health/modules`` reflects what's currently enabled
- third-party modules work (drop-in extensibility)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import (
    ADMIN_TOKEN_HEADER,
    AdminModule,
    AdminSurface,
    ModuleConfig,
    ModuleEndpoint,
)


class _FakeSecrets:
    def __init__(self):
        self._scopes = ["*"]

    async def verify_app_secret(self, slug: str, provided: str) -> bool:
        return provided == "good"

    async def verify_app_token(self, slug: str, provided: str):
        class _R:
            def __init__(self, valid, scopes):
                self.valid = valid
                self.scopes = scopes

        return _R(provided == "good", ["*"])


class _FakeEngine:
    def __init__(self):
        self._app_secrets_manager = _FakeSecrets()
        self._connection_manager = None
        self._surface: AdminSurface | None = None

    def admin_surface(self, cfg=None):
        if self._surface is None:
            self._surface = AdminSurface(self, cfg or {})
            self._surface.register_default_modules()
        return self._surface

    def admin_surface_cached(self):
        return self._surface


class _PingModule(AdminModule):
    name = "ping"

    def build_router(self, engine, cfg: ModuleConfig) -> APIRouter:
        r = APIRouter()

        @r.get("")
        async def _ping(slug: str) -> dict[str, Any]:
            return {"pong": True, "slug": slug}

        return r

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [ModuleEndpoint("GET", "/ping", "*", "Ping.")]


def _mount(engine: _FakeEngine, cfg: dict[str, Any] | None = None) -> TestClient:
    surface = engine.admin_surface(cfg or {})
    app = FastAPI()
    app.include_router(surface.build_router(), prefix="/__mdb")
    return TestClient(app)


class TestRegistration:
    def test_third_party_module_mounts(self):
        engine = _FakeEngine()
        surface = engine.admin_surface({})
        surface.register(_PingModule())
        app = FastAPI()
        app.include_router(surface.build_router(), prefix="/__mdb")
        c = TestClient(app)
        r = c.get("/__mdb/ping?slug=demo", headers={ADMIN_TOKEN_HEADER: "good"})
        assert r.status_code == 200
        assert r.json() == {"pong": True, "slug": "demo"}

    def test_register_replaces_same_name(self):
        engine = _FakeEngine()
        surface = engine.admin_surface({})

        class _First(_PingModule):
            def describe(self, cfg):
                return [ModuleEndpoint("GET", "/first", "*", "")]

        surface.register(_First())
        surface.register(_PingModule())  # replace
        names = {m.name for m, _ in surface.list_modules()}
        assert "ping" in names
        # Only one ping module registered — latest wins.
        assert [m.name for m, _ in surface.list_modules()].count("ping") == 1


class TestDisabledModule:
    def test_disabled_module_endpoints_are_404(self):
        engine = _FakeEngine()
        c = _mount(engine, {"modules": {"reconciler": {"enabled": False}}})
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "good"},
        )
        assert r.status_code == 404


class TestIntrospection:
    def test_health_modules_lists_enabled_modules(self):
        engine = _FakeEngine()
        c = _mount(engine)
        r = c.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "good"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["slug"] == "demo"
        names = {m["name"] for m in payload["modules"]}
        # Default modules all registered; secrets default-off but still
        # listed unless the manifest disables it.
        assert "reconciler" in names
        assert "trash" in names
        assert "health" in names

    def test_health_modules_hides_disabled(self):
        engine = _FakeEngine()
        c = _mount(engine, {"modules": {"secrets": {"enabled": False}}})
        r = c.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "good"},
        )
        names = {m["name"] for m in r.json()["modules"]}
        assert "secrets" not in names
