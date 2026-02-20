"""Tests for mdb_engine.auth.sso_routes — auto-registered /auth/callback and /logout."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.auth.sso_routes import register_sso_routes


def _make_app(auth_config=None, user_pool=None):
    """Build a minimal FastAPI app with SSO routes registered."""
    app = FastAPI()
    app.state.app_slug = "test-app"
    app.state.app_auth_hub_url = "/auth-hub"
    if user_pool:
        app.state.user_pool = user_pool

    register_sso_routes(app, auth_config or {})
    return app


class TestAuthCallbackRoute:
    """Tests for the auto-registered /auth/callback route."""

    def test_route_is_registered(self):
        app = _make_app()
        paths = [r.path for r in app.routes]
        assert "/auth/callback" in paths

    def test_missing_token_redirects_to_login_error(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth/callback", follow_redirects=False)
        assert resp.status_code == 302
        assert "error=invalid_token" in resp.headers["location"]

    def test_bad_token_format_redirects(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth/callback?token=not-a-jwt", follow_redirects=False)
        assert resp.status_code == 302
        assert "error=invalid_token" in resp.headers["location"]

    def test_no_pool_redirects(self):
        app = _make_app()
        token = "aaa.bbb.ccc_enough_length"
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/auth/callback?token={token}", follow_redirects=False)
        assert resp.status_code == 302
        assert "error=pool_not_initialized" in resp.headers["location"]

    def test_invalid_token_pool_rejects(self):
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value=None)
        app = _make_app(user_pool=pool)
        token = "aaa.bbb.ccc_enough_length"
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/auth/callback?token={token}", follow_redirects=False)
        assert resp.status_code == 302
        assert "error=invalid_token" in resp.headers["location"]

    def test_valid_token_sets_cookie_and_redirects(self):
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value={"email": "user@test.com", "_id": "u1"})
        app = _make_app(user_pool=pool)
        token = "aaa.bbb.ccc_enough_length"
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/auth/callback?token={token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        assert "mdb_auth_token" in resp.headers.get("set-cookie", "")

    def test_on_login_redirect_override(self):
        pool = MagicMock()
        pool.validate_token = AsyncMock(return_value={"email": "u@t.com"})
        app = _make_app(auth_config={"on_login_redirect": "/dashboard"}, user_pool=pool)
        token = "aaa.bbb.ccc_enough_length"
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/auth/callback?token={token}", follow_redirects=False)
        assert resp.headers["location"] == "/dashboard"


class TestLogoutRoute:
    """Tests for the auto-registered /logout route."""

    def test_logout_route_registered(self):
        app = _make_app()
        paths = [r.path for r in app.routes]
        assert "/logout" in paths

    def test_logout_clears_cookie_and_redirects(self):
        pool = MagicMock()
        pool.revoke_token = AsyncMock()
        app = _make_app(user_pool=pool)
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("mdb_auth_token", "some.valid.token")
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth-hub/login" in resp.headers["location"]

    def test_logout_revokes_token(self):
        pool = MagicMock()
        pool.revoke_token = AsyncMock()
        app = _make_app(user_pool=pool)
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("mdb_auth_token", "tok.en.val")
        client.post("/logout", follow_redirects=False)
        pool.revoke_token.assert_called_once()

    def test_on_logout_redirect_override(self):
        app = _make_app(auth_config={"on_logout_redirect": "/goodbye"})
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/logout", follow_redirects=False)
        assert resp.headers["location"] == "/goodbye"
