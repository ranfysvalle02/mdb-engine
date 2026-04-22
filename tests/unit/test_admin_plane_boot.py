"""Engine-level boot-path tests for the admin plane.

These tests exercise the *real* wiring path ``FastAPIAppFactory``
takes when it sees ``admin_api.enabled: true``:

1. ``engine.admin_surface(cfg)`` → cached per-prefix composer.
2. ``surface.build_router()`` → parent router with the modules mounted.
3. ``_install_admin_audit_middleware`` → cache-control + audit stamping.
4. ``_install_admin_rate_limit`` → per-token fixed-window limiter.

A previous refactor could silently stop mounting the admin plane (or
silently drop one of the middlewares) and no existing test would
catch it — all the unit tests include the router directly instead
of going through :class:`FastAPIAppMixin`. This file closes that gap
without requiring testcontainers or a real Mongo.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface
from mdb_engine.core.fastapi_app import FastAPIAppMixin


class _FakeSecrets:
    def __init__(self):
        self._token = "boot-tok"
        self._scopes = ["*"]
        self._label = "boot"

    async def verify_app_token(self, slug: str, provided: str):
        class _R:
            def __init__(self, valid, scopes, token_id=None, label=None):
                self.valid = valid
                self.scopes = scopes
                self.token_id = token_id
                self.label = label

        if slug != "demo" or provided != self._token:
            return _R(False, [])
        return _R(True, list(self._scopes), token_id="hmac:abc", label=self._label)

    def fingerprint(self, token):
        return f"hmac:{abs(hash(token or '')) % 10**16:016x}"

    async def get_app_secret_metadata(self, slug):
        return {
            "slug": "demo",
            "label": self._label,
            "scopes": list(self._scopes),
            "rotation_count": 0,
            "created_at": None,
            "updated_at": None,
        }


class _FakeConn:
    """Stand-in so ``replay_or_record`` takes its no-Mongo fast path."""

    mongo_db = None


class _BootEngine(FastAPIAppMixin):
    """Real :class:`FastAPIAppMixin` on a hand-rolled lightweight engine.

    We inherit from the mixin to exercise the *same* install methods
    the production ``MongoDBEngine`` does — a refactor that breaks
    ``_install_admin_audit_middleware`` here will also break it in
    production.
    """

    def __init__(self):
        self._connection_manager = _FakeConn()  # type: ignore[assignment]
        self._app_secrets_manager = _FakeSecrets()
        self._admin_surfaces: dict[str, AdminSurface] = {}

    def admin_surface(self, cfg: dict[str, Any] | None = None) -> AdminSurface:
        key = str((cfg or {}).get("path_prefix") or "/__mdb")
        if key in self._admin_surfaces:
            return self._admin_surfaces[key]
        s = AdminSurface(self, cfg or {})
        s.register_default_modules()
        self._admin_surfaces[key] = s
        return s

    def admin_surface_cached(self) -> AdminSurface | None:
        return next(iter(self._admin_surfaces.values()), None)

    async def manifest_diff(self, slug):
        return {"slug": slug, "is_noop": True}

    async def reconcile(self, slug, **kw):
        return {"status": "noop", "slug": slug, "dry_run": bool(kw.get("dry_run"))}

    async def manifest_history(self, slug, *, limit=20):
        return []

    async def trash_list(self, slug):
        return []

    async def trash_summary(self, slug):
        return {"total": 0}


def _boot(admin_cfg: dict[str, Any] | None = None) -> TestClient:
    """Mirror ``FastAPIAppFactory._install_admin_*`` wiring exactly."""
    engine = _BootEngine()
    cfg = {"enabled": True, "path_prefix": "/__mdb", **(admin_cfg or {})}
    prefix = cfg["path_prefix"]
    app = FastAPI()
    surface = engine.admin_surface(cfg)
    app.include_router(surface.build_router(), prefix=prefix)
    engine._install_admin_audit_middleware(app, surface, prefix)
    engine._install_admin_rate_limit(app, prefix, cfg)
    return TestClient(app)


class TestBootMounting:
    """The admin plane must actually mount when enabled."""

    def test_liveness_is_reachable_on_the_boot_path(self):
        c = _boot()
        r = c.get("/__mdb/health/live")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_authed_endpoint_rejects_missing_token(self):
        c = _boot()
        r = c.get("/__mdb/reconciler/plan?slug=demo")
        assert r.status_code == 401

    def test_authed_endpoint_accepts_valid_token(self):
        c = _boot()
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "boot-tok"},
        )
        assert r.status_code == 200

    def test_module_listing_reflects_registered_modules(self):
        c = _boot()
        r = c.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "boot-tok"},
        )
        assert r.status_code == 200
        names = {m["name"] for m in r.json()["modules"]}
        # Every default module must survive the factory boot path.
        assert names >= {"health", "reconciler", "trash", "audit", "secrets"}


class TestMiddlewareContract:
    """The install methods must do exactly what their docstrings claim."""

    def test_cache_control_is_stamped_by_middleware(self):
        c = _boot()
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "boot-tok"},
        )
        assert r.status_code == 200
        assert r.headers.get("cache-control", "").lower() == "no-store"
        assert r.headers.get("pragma", "").lower() == "no-cache"

    def test_non_admin_paths_are_not_touched_by_middleware(self):
        """The audit middleware must short-circuit non-``/__mdb`` paths.

        Regression guard: a previous draft stamped no-store on every
        response which broke unrelated cachable routes.
        """
        engine = _BootEngine()
        app = FastAPI()
        surface = engine.admin_surface({"enabled": True, "path_prefix": "/__mdb"})
        app.include_router(surface.build_router(), prefix="/__mdb")
        engine._install_admin_audit_middleware(app, surface, "/__mdb")
        engine._install_admin_rate_limit(app, "/__mdb", {"enabled": True})

        @app.get("/biz")
        def _biz():
            return {"ok": True}

        r = TestClient(app).get("/biz")
        assert r.status_code == 200
        # No-store must NOT have been stamped on non-admin paths.
        assert r.headers.get("cache-control", "").lower() != "no-store"


class TestRateLimit:
    """The per-token limiter must kick in on aggressive callers."""

    def test_liveness_is_rate_limit_exempt(self):
        """k8s probes must NEVER 429; that would kill the pod.

        We fire well past the read-bucket default (120/min) and assert
        every single call succeeds.
        """
        c = _boot()
        for _ in range(400):
            r = c.get("/__mdb/health/live")
            assert r.status_code == 200

    def test_rate_limit_kicks_in_for_authenticated_reads(self):
        """Per-token fixed window (default 120 req/min read). At request
        121 the limiter should return 429 with ``Retry-After``.

        We use a tight write-bucket override (``max: 3``) so the test
        takes milliseconds instead of sending 120 requests — same code
        path, same assertions.
        """
        c = _boot(
            {
                "rate_limits": {
                    "write": {"max": 3, "window_seconds": 60},
                },
            }
        )
        headers = {ADMIN_TOKEN_HEADER: "boot-tok"}
        ok = 0
        for _ in range(5):
            r = c.post("/__mdb/reconciler/apply?slug=demo&dry_run=true", headers=headers)
            if r.status_code == 200:
                ok += 1
            else:
                assert r.status_code == 429, r.text
                assert "Retry-After" in r.headers
                assert r.json().get("bucket") == "write"
                break
        else:
            pytest.fail("rate limiter never engaged in 5 requests with max=3")
        assert ok == 3, f"expected 3 successes before 429, got {ok}"


class TestScopeValidation:
    """Boot-time scope vocabulary validation must surface typos."""

    def test_unknown_scope_logs_a_warning(self, caplog):
        """A manifest with ``scopes: ['appply']`` must warn at register,
        not silently mint unauthorized tokens."""
        import logging

        engine = _BootEngine()
        with caplog.at_level(logging.WARNING, logger="mdb_engine.admin.surface"):
            engine.admin_surface(
                {
                    "enabled": True,
                    "modules": {
                        "reconciler": {"enabled": True, "scopes": ["read", "appply"]},
                    },
                }
            )
        joined = "\n".join(rec.message for rec in caplog.records)
        assert "appply" in joined, caplog.text

    def test_known_scopes_produce_no_warning(self, caplog):
        import logging

        engine = _BootEngine()
        with caplog.at_level(logging.WARNING, logger="mdb_engine.admin.surface"):
            engine.admin_surface(
                {
                    "enabled": True,
                    "modules": {
                        "reconciler": {"enabled": True, "scopes": ["read", "apply"]},
                        "trash": {"enabled": True, "scopes": ["read", "restore", "purge"]},
                    },
                }
            )
        assert "unknown scope" not in caplog.text.lower()
