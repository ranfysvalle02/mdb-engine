"""
Unit tests for manifest data_access schema validation.

Tests data_access configuration validation and defaults.
"""

import pytest

from mdb_engine.core.manifest import ManifestValidator


@pytest.fixture
def manifest_validator():
    """Create manifest validator instance."""
    return ManifestValidator()


@pytest.mark.asyncio
class TestManifestDataAccess:
    """Test manifest data_access schema validation."""

    async def test_manifest_data_access_valid(self, manifest_validator):
        """Test valid data_access configuration."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app", "shared_app"],
                "write_scope": "test_app",
                "cross_app_policy": "explicit",
            },
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True
        assert error is None

    async def test_manifest_data_access_defaults(self, manifest_validator):
        """Test that defaults are applied correctly."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app"],
            },
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True

    async def test_manifest_data_access_missing(self, manifest_validator):
        """Test handling of missing data_access."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True  # data_access is optional

    async def test_manifest_read_scopes_validation(self, manifest_validator):
        """Test read_scopes format validation."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": ["test_app", "other_app"],  # Valid array of strings
            },
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True

    async def test_manifest_read_scopes_invalid_type(self, manifest_validator):
        """Test that read_scopes must be an array."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "schema_version": "2.0",  # Explicitly use V2 schema
            "data_access": {
                "read_scopes": "not_an_array",  # Invalid type
            },
        }

        is_valid, error, paths = await manifest_validator.validate_async(manifest, use_cache=False)
        assert is_valid is False, f"Expected validation to fail, but got: {error}"

    async def test_manifest_write_scope_validation(self, manifest_validator):
        """Test write_scope format validation."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "write_scope": "test_app",  # Valid string
            },
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True

    async def test_manifest_write_scope_invalid_type(self, manifest_validator):
        """Test that write_scope must be a string."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "schema_version": "2.0",  # Explicitly use V2 schema
            "data_access": {
                "write_scope": 123,  # Invalid type
            },
        }

        is_valid, error, paths = await manifest_validator.validate_async(manifest, use_cache=False)
        assert is_valid is False, f"Expected validation to fail, but got: {error}"

    async def test_manifest_cross_app_policy_validation(self, manifest_validator):
        """Test cross_app_policy enum validation."""
        for policy in ["explicit", "deny_all"]:
            manifest = {
                "slug": "test_app",
                "name": "Test App",
                "data_access": {
                    "cross_app_policy": policy,
                },
            }

            is_valid, error, paths = manifest_validator.validate(manifest)
            assert is_valid is True, f"Policy '{policy}' should be valid"

    async def test_manifest_cross_app_policy_invalid(self, manifest_validator):
        """Test that invalid cross_app_policy is rejected."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "schema_version": "2.0",  # Explicitly use V2 schema
            "data_access": {
                "cross_app_policy": "invalid_policy",
            },
        }

        is_valid, error, paths = await manifest_validator.validate_async(manifest, use_cache=False)
        assert is_valid is False, f"Expected validation to fail, but got: {error}"

    async def test_manifest_data_access_migration(self, manifest_validator):
        """Test backward compatibility (no data_access)."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            # No data_access - should still be valid
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        assert is_valid is True

    async def test_manifest_read_scopes_empty(self, manifest_validator):
        """Test handling of empty read_scopes."""
        manifest = {
            "slug": "test_app",
            "name": "Test App",
            "data_access": {
                "read_scopes": [],  # Empty array
            },
        }

        is_valid, error, paths = manifest_validator.validate(manifest)
        # Empty array is valid per schema (validation happens at engine level)
        assert is_valid is True
