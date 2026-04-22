"""Unit tests for the admin plane router auth matrix.

Exercises :class:`AdminSurface` end-to-end through a
FastAPI :class:`TestClient`: auth gate, scope gate, and module dispatch.
The real :class:`Reconciler` is never constructed — the surface's job is
to authenticate, enforce scopes, and delegate, which is what we verify
here.

Audit persistence is NOT asserted here (that's covered by the
integration test against a real Mongo); this suite only verifies the
in-memory auth + dispatch behaviour.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface


class _FakeSecrets:
    """Duck type of :class:`AppSecretsManager` with scoped tokens."""

    def __init__(self, tokens: dict[str, tuple[str, list[str]]]):
        # tokens: {slug: (secret, scopes)}
        self._tokens = dict(tokens)

    async def verify_app_secret(self, slug: str, provided: str) -> bool:
        got = self._tokens.get(slug)
        return bool(got) and got[0] == provided

    async def verify_app_token(self, slug: str, provided: str):
        class _Result:
            def __init__(self, valid: bool, scopes: list[str], token_id=None, label=None):
                self.valid = valid
                self.scopes = scopes
                self.token_id = token_id
                self.label = label

        got = self._tokens.get(slug)
        if not got or got[0] != provided:
            return _Result(False, [])
        return _Result(True, list(got[1]), token_id="hmac:test", label="test-label")

    def fingerprint(self, token):  # pragma: no cover
        return f"hmac:{abs(hash(token)) % (10 ** 16):016x}"


class _FakeEngine:
    """Minimal duck-typed engine exposing just the methods modules use."""

    def __init__(self, tokens: dict[str, tuple[str, list[str]]]):
        self._app_secrets_manager = _FakeSecrets(tokens)
        self._connection_manager = None  # disable audit persistence
        self.calls: list[tuple[str, tuple, dict]] = []
        self._surface_cache: AdminSurface | None = None

    async def manifest_diff(self, slug: str) -> dict[str, Any]:
        self.calls.append(("manifest_diff", (slug,), {}))
        return {"slug": slug, "is_noop": True}

    async def reconcile(self, slug: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("reconcile", (slug,), kwargs))
        return {"status": "noop", "slug": slug}

    async def manifest_history(self, slug: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append(("manifest_history", (slug,), {"limit": limit}))
        return []

    async def trash_list(self, slug: str) -> list[dict[str, Any]]:
        self.calls.append(("trash_list", (slug,), {}))
        return []

    async def trash_summary(self, slug: str) -> dict[str, Any]:
        self.calls.append(("trash_summary", (slug,), {}))
        return {"total": 0}

    async def trash_restore(self, slug: str, trash_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        self.calls.append(("trash_restore", (slug, trash_id), {"dry_run": dry_run}))
        return {"restored": not dry_run, "dry_run": dry_run}

    async def trash_purge(self, slug: str, *, expired_only: bool = True, ids: list | None = None) -> int:
        self.calls.append(("trash_purge", (slug,), {"expired_only": expired_only, "ids": ids}))
        return 1

    def admin_surface(self, cfg: dict[str, Any] | None = None) -> AdminSurface:
        if self._surface_cache is not None:
            return self._surface_cache
        s = AdminSurface(self, cfg or {})
        s.register_default_modules()
        self._surface_cache = s
        return s

    def admin_surface_cached(self):
        return self._surface_cache


def _make(engine: _FakeEngine, cfg: dict[str, Any] | None = None) -> TestClient:
    surface = engine.admin_surface(cfg or {})
    app = FastAPI()
    app.include_router(surface.build_router(), prefix="/__mdb")
    return TestClient(app)


def _tok(secret: str, scopes: list[str] | None = None) -> dict[str, tuple[str, list[str]]]:
    return {"demo": (secret, scopes or ["*"])}


class TestAuthMatrix:
    def test_health_is_auth_free_when_public(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine, {"modules": {"health": {"public": True}}})
        r = c.get("/__mdb/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_missing_token_returns_401(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.get("/__mdb/reconciler/plan?slug=demo")
        assert r.status_code == 401

    def test_wrong_token_returns_403(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "nope"},
        )
        assert r.status_code == 403

    def test_missing_slug_is_rejected(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.get("/__mdb/reconciler/plan", headers={ADMIN_TOKEN_HEADER: "t0ken"})
        assert r.status_code in (400, 422)

    def test_valid_token_reaches_engine(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200
        assert r.json()["slug"] == "demo"
        assert ("manifest_diff", ("demo",), {}) in engine.calls

    def test_reconcile_apply_forwards_flags(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.post(
            "/__mdb/reconciler/apply?slug=demo&dry_run=true&yes=true&expected_head=abc",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200
        kwargs = engine.calls[-1][2]
        assert kwargs["dry_run"] is True
        assert kwargs["confirm"] is True
        assert kwargs["expected_head"] == "abc"

    def test_trash_restore_dry_run_routes(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.post(
            "/__mdb/trash/abc123/restore?slug=demo&dry_run=true",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200
        assert r.json()["dry_run"] is True
        assert engine.calls[-1][0] == "trash_restore"

    def test_trash_purge_forwards_id(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine)
        r = c.post(
            "/__mdb/trash/abc123/purge?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["id"] == "abc123"
        assert engine.calls[-1][2]["ids"] == ["abc123"]


class TestScopeEnforcement:
    def test_wildcard_scope_passes(self):
        engine = _FakeEngine(_tok("t0ken", ["*"]))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200

    def test_module_wildcard_scope_passes(self):
        engine = _FakeEngine(_tok("t0ken", ["reconciler:*"]))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200

    def test_foreign_module_scope_is_rejected(self):
        engine = _FakeEngine(_tok("t0ken", ["trash:*"]))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 403

    def test_read_scope_cannot_call_apply(self):
        # Per-endpoint scope enforcement: a read-only token is rejected
        # on POST /reconciler/apply (required scope "apply").
        engine = _FakeEngine(_tok("t0ken", ["reconciler:read"]))
        c = _make(engine)
        r = c.post(
            "/__mdb/reconciler/apply?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 403

    def test_read_scope_can_call_plan(self):
        engine = _FakeEngine(_tok("t0ken", ["reconciler:read"]))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200

    def test_bare_scope_matches_endpoint_requirement(self):
        # Legacy unqualified scopes still work for matching endpoints.
        engine = _FakeEngine(_tok("t0ken", ["read"]))
        c = _make(engine)
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 200

    def test_apply_scope_cannot_call_trash_purge(self):
        engine = _FakeEngine(_tok("t0ken", ["reconciler:apply"]))
        c = _make(engine)
        r = c.post(
            "/__mdb/trash/abc/purge?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 403


class TestModuleDisable:
    def test_disabled_module_is_absent(self):
        engine = _FakeEngine(_tok("t0ken"))
        c = _make(engine, {"modules": {"trash": {"enabled": False}}})
        r = c.post(
            "/__mdb/trash/abc/purge?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "t0ken"},
        )
        assert r.status_code == 404


class TestSecretsManagerAbsent:
    def test_returns_503_when_secrets_manager_missing(self):
        class _EmptyEngine:
            _app_secrets_manager = None
            _connection_manager = None
            _surface_cache = None

            def admin_surface(self, cfg=None):
                if self._surface_cache is not None:
                    return self._surface_cache
                s = AdminSurface(self, cfg or {})
                s.register_default_modules()
                self._surface_cache = s
                return s

            def admin_surface_cached(self):
                return self._surface_cache

        engine = _EmptyEngine()
        app = FastAPI()
        app.include_router(engine.admin_surface({}).build_router(), prefix="/__mdb")
        c = TestClient(app)
        r = c.get("/__mdb/reconciler/plan?slug=demo", headers={ADMIN_TOKEN_HEADER: "x"})
        assert r.status_code == 503
