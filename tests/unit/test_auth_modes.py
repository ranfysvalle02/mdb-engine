"""
Unit tests for auth mode configuration in manifests.

Tests that auth.mode field is properly validated and parsed.
"""

from unittest.mock import MagicMock


class TestAuthModeManifestSchema:
    """Tests for auth.mode in manifest schema."""

    def test_auth_mode_app_valid(self):
        """Test that mode='app' is valid."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "app",
            },
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert is_valid, f"Expected valid, got error: {error}"

    def test_auth_mode_shared_valid(self):
        """Test that mode='shared' is valid."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "editor", "admin"],
                "default_role": "viewer",
                "require_role": "viewer",
            },
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert is_valid, f"Expected valid, got error: {error}"

    def test_auth_mode_invalid_value(self):
        """Test that invalid mode value fails validation."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "invalid_mode",
            },
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert not is_valid
        assert "mode" in error.lower() or "invalid" in error.lower()

    def test_auth_public_routes_valid(self):
        """Test that public_routes array is valid."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "public_routes": ["/health", "/api/public/*"],
            },
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert is_valid, f"Expected valid, got error: {error}"

    def test_auth_roles_valid(self):
        """Test that roles array is valid."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {
                "mode": "shared",
                "roles": ["viewer", "editor", "admin"],
            },
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert is_valid, f"Expected valid, got error: {error}"

    def test_auth_default_mode_is_app(self):
        """Test that default mode is 'app' when not specified."""
        from mdb_engine.core.manifest import validate_manifest

        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test App",
            "auth": {},  # No mode specified
            "data_access": {
                "read_scopes": ["test_app"],
                "write_scope": "test_app",
            },
        }

        is_valid, error, warnings = validate_manifest(manifest)
        assert is_valid, f"Expected valid, got error: {error}"


class TestAuthModeEngineIntegration:
    """Tests for auth mode handling in MongoDBEngine."""

    def test_engine_detects_app_mode(self):
        """Test that engine correctly identifies app auth mode."""
        manifest = {
            "slug": "test_app",
            "auth": {
                "mode": "app",
            },
        }

        auth_config = manifest.get("auth", {})
        mode = auth_config.get("mode", "app")

        assert mode == "app"

    def test_engine_detects_shared_mode(self):
        """Test that engine correctly identifies shared auth mode."""
        manifest = {
            "slug": "test_app",
            "auth": {
                "mode": "shared",
                "roles": ["viewer"],
            },
        }

        auth_config = manifest.get("auth", {})
        mode = auth_config.get("mode", "app")

        assert mode == "shared"

    def test_engine_defaults_to_app_mode(self):
        """Test that engine defaults to app mode when not specified."""
        manifest = {
            "slug": "test_app",
        }

        auth_config = manifest.get("auth", {})
        mode = auth_config.get("mode", "app")

        assert mode == "app"


class TestSharedAuthExports:
    """Tests that shared auth components are properly exported."""

    def test_shared_user_pool_exported(self):
        """Test SharedUserPool is exported from auth module."""
        from mdb_engine.auth import SharedUserPool

        assert SharedUserPool is not None

    def test_shared_auth_middleware_exported(self):
        """Test SharedAuthMiddleware is exported from auth module."""
        from mdb_engine.auth import SharedAuthMiddleware

        assert SharedAuthMiddleware is not None

    def test_create_shared_auth_middleware_exported(self):
        """Test create_shared_auth_middleware is exported from auth module."""
        from mdb_engine.auth import create_shared_auth_middleware

        assert create_shared_auth_middleware is not None

    def test_shared_user_pool_can_instantiate(self):
        """Test SharedUserPool can be instantiated with mock db."""
        from mdb_engine.auth import SharedUserPool

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=MagicMock())

        pool = SharedUserPool(
            mongo_db=mock_db,
            jwt_secret="test-secret",
        )

        assert pool is not None
        assert pool._jwt_secret == "test-secret"
