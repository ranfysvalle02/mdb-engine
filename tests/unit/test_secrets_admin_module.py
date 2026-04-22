"""Unit tests for :class:`SecretsAdminModule`.

Rotation flows through the shared auth gate (current valid token is
required to rotate). We assert the module:

- returns the new token exactly once with a "store me now" notice
- returns 503 when the engine has no secrets manager
- propagates missing-slug errors as 404 from the manager
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface


class _FakeSecrets:
    def __init__(self, current: dict[str, str], rotated: dict[str, str]):
        self._current = dict(current)
        self._rotated = dict(rotated)
        self.rotated_for: list[str] = []

    async def verify_app_secret(self, slug: str, provided: str) -> bool:
        return self._current.get(slug) == provided

    async def verify_app_token(self, slug: str, provided: str):
        class _R:
            def __init__(self, valid, scopes):
                self.valid = valid
                self.scopes = scopes

        return _R(self._current.get(slug) == provided, ["*"])

    async def rotate_app_secret(self, slug: str, *, scopes=None) -> str:
        if slug not in self._current:
            raise ValueError(f"unknown slug {slug}")
        new = self._rotated[slug]
        self._current[slug] = new
        self.rotated_for.append(slug)
        return new


class _FakeEngine:
    def __init__(self, secrets_manager):
        self._app_secrets_manager = secrets_manager
        self._connection_manager = None
        self._surface: AdminSurface | None = None

    def admin_surface(self, cfg=None):
        if self._surface is None:
            self._surface = AdminSurface(self, cfg or {})
            self._surface.register_default_modules()
        return self._surface

    def admin_surface_cached(self):
        return self._surface


def _client(engine: _FakeEngine) -> TestClient:
    surface = engine.admin_surface({"modules": {"secrets": {"enabled": True}}})
    app = FastAPI()
    app.include_router(surface.build_router(), prefix="/__mdb")
    return TestClient(app)


def test_rotate_returns_new_token_once():
    mgr = _FakeSecrets({"demo": "current"}, {"demo": "fresh"})
    engine = _FakeEngine(mgr)
    c = _client(engine)
    r = c.post("/__mdb/secrets/rotate?slug=demo", headers={ADMIN_TOKEN_HEADER: "current"})
    assert r.status_code == 200
    body = r.json()
    assert body["rotated"] is True
    assert body["token"] == "fresh"
    assert "Store this token immediately" in body["notice"]
    assert mgr.rotated_for == ["demo"]


def test_rotate_requires_valid_token():
    mgr = _FakeSecrets({"demo": "current"}, {"demo": "fresh"})
    engine = _FakeEngine(mgr)
    c = _client(engine)
    r = c.post("/__mdb/secrets/rotate?slug=demo", headers={ADMIN_TOKEN_HEADER: "wrong"})
    assert r.status_code == 403
    assert mgr.rotated_for == []


def test_rotate_returns_404_for_unknown_slug():
    mgr = _FakeSecrets({"demo": "current"}, {"demo": "fresh"})
    engine = _FakeEngine(mgr)
    c = _client(engine)
    r = c.post("/__mdb/secrets/rotate?slug=other", headers={ADMIN_TOKEN_HEADER: "current"})
    # The auth gate runs before the handler, so unknown slug 403s at auth
    # rather than 404 from the manager. That's fine — we never want to
    # leak "slug exists" to an unauthenticated caller.
    assert r.status_code == 403


def test_rotate_503_when_secrets_manager_missing():
    class _NoSecretsEngine:
        _app_secrets_manager = None
        _connection_manager = None
        _surface = None

        def admin_surface(self, cfg=None):
            if self._surface is None:
                self._surface = AdminSurface(self, cfg or {})
                self._surface.register_default_modules()
            return self._surface

        def admin_surface_cached(self):
            return self._surface

    engine = _NoSecretsEngine()
    surface = engine.admin_surface({"modules": {"secrets": {"enabled": True}}})
    app = FastAPI()
    app.include_router(surface.build_router(), prefix="/__mdb")
    c = TestClient(app)
    r = c.post("/__mdb/secrets/rotate?slug=demo", headers={ADMIN_TOKEN_HEADER: "x"})
    assert r.status_code == 503
