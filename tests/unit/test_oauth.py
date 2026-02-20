"""
Unit tests for OAuth 2.0 / OIDC integration module.

Tests cover:
- Environment variable resolution
- OAuthService initialization and provider registration
- User creation / linking strategies
- OAuth route registration
- Standalone get_or_create_oauth_user helper
- Manifest schema validation for the ``auth.oauth`` block
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Skip the entire module if authlib is not installed (optional dependency)
pytest.importorskip("authlib", reason="authlib not installed — pip install mdb-engine[oauth]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db(existing_users: list[dict[str, Any]] | None = None):
    """Return a mock scoped-db whose ``users`` collection is an AsyncMock."""
    users = existing_users or []

    collection = AsyncMock()

    async def _find_one(query, *args, **kwargs):
        for user in users:
            if "_id" in query and user.get("_id") == query["_id"]:
                return user
            if "email" in query and user.get("email") == query["email"]:
                return user
            if "oauth_linked_providers" in query:
                elem_match = query["oauth_linked_providers"].get("$elemMatch", {})
                for linked in user.get("oauth_linked_providers", []):
                    if linked.get("provider") == elem_match.get("provider") and linked.get("id") == elem_match.get(
                        "id"
                    ):
                        return user
        return None

    collection.find_one = AsyncMock(side_effect=_find_one)
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="new_id_123"))
    collection.update_one = AsyncMock()

    db = MagicMock()
    db.users = collection
    return db, collection


# ---------------------------------------------------------------------------
# _resolve_env
# ---------------------------------------------------------------------------


class TestResolveEnv:
    """Tests for environment variable resolution helper."""

    def test_plain_string_unchanged(self):
        from mdb_engine.auth.oauth import _resolve_env

        assert _resolve_env("my-client-id") == "my-client-id"

    def test_env_var_resolved(self, monkeypatch):
        from mdb_engine.auth.oauth import _resolve_env

        monkeypatch.setenv("TEST_OAUTH_ID", "resolved-id-123")
        assert _resolve_env("${TEST_OAUTH_ID}") == "resolved-id-123"

    def test_env_var_missing_returns_empty(self, monkeypatch):
        from mdb_engine.auth.oauth import _resolve_env

        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _resolve_env("${NONEXISTENT_VAR}") == ""

    def test_partial_match_not_resolved(self):
        from mdb_engine.auth.oauth import _resolve_env

        assert _resolve_env("prefix_${VAR}") == "prefix_${VAR}"

    def test_empty_string_unchanged(self):
        from mdb_engine.auth.oauth import _resolve_env

        assert _resolve_env("") == ""


# ---------------------------------------------------------------------------
# OAuthService initialization
# ---------------------------------------------------------------------------


class TestOAuthServiceInit:
    """Tests for OAuthService construction and provider registration."""

    def test_registers_provider_with_valid_config(self, monkeypatch):
        from mdb_engine.auth.oauth import OAuthService

        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")

        db, _ = _make_mock_db()
        config = {
            "session_secret": "test-secret",
            "providers": {
                "google": {
                    "client_id": "${GOOGLE_CLIENT_ID}",
                    "client_secret": "${GOOGLE_CLIENT_SECRET}",
                    "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
                    "scopes": ["openid", "email", "profile"],
                },
            },
        }

        service = OAuthService(oauth_config=config, db=db, slug="test-app")
        assert "google" in service.provider_names

    def test_skips_provider_with_missing_credentials(self, monkeypatch):
        from mdb_engine.auth.oauth import OAuthService

        monkeypatch.delenv("MISSING_ID", raising=False)
        monkeypatch.delenv("MISSING_SECRET", raising=False)

        db, _ = _make_mock_db()
        config = {
            "session_secret": "test-secret",
            "providers": {
                "broken": {
                    "client_id": "${MISSING_ID}",
                    "client_secret": "${MISSING_SECRET}",
                },
            },
        }

        service = OAuthService(oauth_config=config, db=db, slug="test-app")
        assert service.provider_names == []

    def test_multiple_providers(self, monkeypatch):
        from mdb_engine.auth.oauth import OAuthService

        monkeypatch.setenv("G_ID", "g-id")
        monkeypatch.setenv("G_SECRET", "g-secret")
        monkeypatch.setenv("GH_ID", "gh-id")
        monkeypatch.setenv("GH_SECRET", "gh-secret")

        db, _ = _make_mock_db()
        config = {
            "session_secret": "test-secret",
            "providers": {
                "google": {
                    "client_id": "${G_ID}",
                    "client_secret": "${G_SECRET}",
                    "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
                },
                "github": {
                    "client_id": "${GH_ID}",
                    "client_secret": "${GH_SECRET}",
                    "authorize_url": "https://github.com/login/oauth/authorize",
                    "access_token_url": "https://github.com/login/oauth/access_token",
                    "userinfo_url": "https://api.github.com/user",
                    "scopes": ["user:email"],
                },
            },
        }

        service = OAuthService(oauth_config=config, db=db, slug="test-app")
        assert set(service.provider_names) == {"google", "github"}

    def test_defaults(self, monkeypatch):
        from mdb_engine.auth.oauth import OAuthService

        monkeypatch.setenv("CID", "id")
        monkeypatch.setenv("CSEC", "sec")

        db, _ = _make_mock_db()
        config = {
            "session_secret": "s",
            "providers": {
                "test": {"client_id": "${CID}", "client_secret": "${CSEC}"},
            },
        }
        service = OAuthService(oauth_config=config, db=db, slug="x")
        assert service._user_strategy == "link_or_create"
        assert service._default_role == "user"
        assert service._redirect_after_login == "/"


# ---------------------------------------------------------------------------
# get_or_create_oauth_user (standalone helper in users.py)
# ---------------------------------------------------------------------------


class TestGetOrCreateOAuthUser:
    """Tests for the standalone get_or_create_oauth_user helper."""

    @pytest.mark.asyncio
    async def test_creates_new_user(self):
        from mdb_engine.auth.users import get_or_create_oauth_user

        db, collection = _make_mock_db()

        user = await get_or_create_oauth_user(
            db=db,
            provider_name="google",
            oauth_id="google-123",
            email="jane@example.com",
            display_name="Jane Doe",
            role="editor",
        )

        assert user is not None
        assert user["email"] == "jane@example.com"
        assert user["oauth_provider"] == "google"
        assert user["oauth_id"] == "google-123"
        assert user["role"] == "editor"
        assert user["app_user_id"] == "new_id_123"
        collection.insert_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_links_existing_by_email(self):
        from mdb_engine.auth.users import get_or_create_oauth_user

        existing_user = {
            "_id": "existing-id",
            "email": "jane@example.com",
            "role": "user",
            "oauth_linked_providers": [],
        }
        db, collection = _make_mock_db([existing_user])

        user = await get_or_create_oauth_user(
            db=db,
            provider_name="github",
            oauth_id="gh-456",
            email="jane@example.com",
        )

        assert user is not None
        assert user["_id"] == "existing-id"
        collection.update_one.assert_awaited_once()
        collection.insert_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_finds_by_provider_id(self):
        from mdb_engine.auth.users import get_or_create_oauth_user

        existing_user = {
            "_id": "existing-id",
            "email": "jane@example.com",
            "role": "user",
            "oauth_linked_providers": [{"provider": "google", "id": "google-123"}],
        }
        db, collection = _make_mock_db([existing_user])

        user = await get_or_create_oauth_user(
            db=db,
            provider_name="google",
            oauth_id="google-123",
            email="jane@example.com",
        )

        assert user is not None
        assert user["_id"] == "existing-id"
        collection.update_one.assert_awaited_once()
        collection.insert_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_only_strategy_ignores_email_match(self):
        from mdb_engine.auth.users import get_or_create_oauth_user

        existing_user = {
            "_id": "existing-id",
            "email": "jane@example.com",
            "role": "user",
        }
        db, collection = _make_mock_db([existing_user])

        user = await get_or_create_oauth_user(
            db=db,
            provider_name="github",
            oauth_id="gh-789",
            email="jane@example.com",
            strategy="create_only",
        )

        assert user is not None
        collection.insert_one.assert_awaited_once()


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    """Tests for OAuth route registration on a FastAPI app."""

    def test_registers_routes_for_each_provider(self, monkeypatch):
        from fastapi import FastAPI

        from mdb_engine.auth.oauth import OAuthService, register_oauth_routes

        monkeypatch.setenv("CID", "id")
        monkeypatch.setenv("CSEC", "sec")

        db, _ = _make_mock_db()
        config = {
            "session_secret": "s",
            "providers": {
                "google": {"client_id": "${CID}", "client_secret": "${CSEC}"},
                "github": {"client_id": "${CID}", "client_secret": "${CSEC}"},
            },
        }
        service = OAuthService(oauth_config=config, db=db, slug="test")

        app = FastAPI()
        register_oauth_routes(app, service)

        route_paths = [r.path for r in app.routes]
        assert "/auth/oauth/providers" in route_paths
        assert "/auth/oauth/google/login" in route_paths
        assert "/auth/oauth/google/callback" in route_paths
        assert "/auth/oauth/github/login" in route_paths
        assert "/auth/oauth/github/callback" in route_paths

    def test_providers_endpoint_returns_names(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mdb_engine.auth.oauth import OAuthService, register_oauth_routes

        monkeypatch.setenv("CID", "id")
        monkeypatch.setenv("CSEC", "sec")

        db, _ = _make_mock_db()
        config = {
            "session_secret": "s",
            "providers": {
                "google": {"client_id": "${CID}", "client_secret": "${CSEC}"},
            },
        }
        service = OAuthService(oauth_config=config, db=db, slug="test")

        app = FastAPI()
        register_oauth_routes(app, service)

        client = TestClient(app)
        resp = client.get("/auth/oauth/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "google" in data["providers"]


# ---------------------------------------------------------------------------
# Manifest schema validation
# ---------------------------------------------------------------------------


class TestManifestOAuthSchema:
    """Tests that the manifest schema accepts / rejects OAuth configs."""

    @pytest.mark.asyncio
    async def test_valid_oauth_block_passes_validation(self):
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "oauth-test",
            "name": "OAuth Test App",
            "auth": {
                "mode": "app",
                "oauth": {
                    "session_secret": "${OAUTH_SESSION_SECRET}",
                    "providers": {
                        "google": {
                            "client_id": "${GOOGLE_CLIENT_ID}",
                            "client_secret": "${GOOGLE_CLIENT_SECRET}",
                            "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
                            "scopes": ["openid", "email", "profile"],
                        },
                    },
                    "user_strategy": "link_or_create",
                    "default_role": "viewer",
                    "redirect_after_login": "/dashboard",
                },
            },
        }

        is_valid, error, paths = await ManifestValidator.validate(manifest, use_cache=False)
        assert is_valid, f"Validation failed: {error} at {paths}"

    @pytest.mark.asyncio
    async def test_oauth_without_providers_fails(self):
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "oauth-test",
            "name": "OAuth Test App",
            "auth": {
                "oauth": {
                    "session_secret": "secret",
                    # Missing required "providers"
                },
            },
        }

        is_valid, error, paths = await ManifestValidator.validate(manifest, use_cache=False)
        assert not is_valid

    @pytest.mark.asyncio
    async def test_oauth_without_session_secret_fails(self):
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "oauth-test",
            "name": "OAuth Test App",
            "auth": {
                "oauth": {
                    # Missing required "session_secret"
                    "providers": {
                        "google": {
                            "client_id": "id",
                            "client_secret": "secret",
                        },
                    },
                },
            },
        }

        is_valid, error, paths = await ManifestValidator.validate(manifest, use_cache=False)
        assert not is_valid

    @pytest.mark.asyncio
    async def test_manifest_without_oauth_still_valid(self):
        """Ensure existing manifests without oauth continue to validate."""
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "no-oauth",
            "name": "No OAuth App",
            "auth": {
                "mode": "app",
            },
        }

        is_valid, error, paths = await ManifestValidator.validate(manifest, use_cache=False)
        assert is_valid, f"Validation failed: {error} at {paths}"
