"""
Unit tests for manifest validation and parsing.

Tests the manifest validation system including:
- Schema validation
- Version detection and migration
- Index definition validation
- Error reporting
"""

import pytest

from mdb_engine.core.manifest import (
    ManifestParser,
    ManifestValidator,
    get_schema_version,
    migrate_manifest,
    validate_index_definition,
    validate_managed_indexes,
)


class TestManifestSchemaVersion:
    """Test schema version detection and migration."""

    def test_get_schema_version_explicit(self):
        """Test getting explicit schema version."""
        manifest = {"schema_version": "2.0", "slug": "test"}
        assert get_schema_version(manifest) == "2.0"

    def test_get_schema_version_default(self):
        """Test default schema version for manifests without version."""
        manifest = {"slug": "test", "name": "Test"}
        assert get_schema_version(manifest) == "1.0"

    def test_get_schema_version_heuristic_v2(self):
        """Test heuristic detection of v2.0 based on new fields."""
        manifest = {
            "slug": "test",
            "name": "Test",
            "auth_policy": {"required": True},  # V2 field
        }
        assert get_schema_version(manifest) == "2.0"

    def test_migrate_manifest_same_version(self):
        """Test migration when already at target version."""
        manifest = {"schema_version": "2.0", "slug": "test"}
        migrated = migrate_manifest(manifest, "2.0")
        assert migrated == manifest

    def test_migrate_manifest_v1_to_v2(self):
        """Test migration from v1.0 to v2.0."""
        manifest = {"schema_version": "1.0", "slug": "test", "name": "Test"}
        migrated = migrate_manifest(manifest, "2.0")

        assert migrated["schema_version"] == "2.0"
        assert migrated["slug"] == "test"
        assert migrated["name"] == "Test"


class TestManifestValidator:
    """Test ManifestValidator class."""

    def test_validate_valid_manifest(self, sample_manifest):
        """Test validation of valid manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = validator.validate(sample_manifest)

        assert is_valid is True
        assert error is None
        assert paths is None

    def test_validate_invalid_manifest(self, invalid_manifest):
        """Test validation of invalid manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = validator.validate(invalid_manifest)

        assert is_valid is False
        assert error is not None
        assert paths is not None

    @pytest.mark.asyncio
    async def test_validate_async(self, sample_manifest):
        """Test async validation."""
        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate_async(sample_manifest)

        assert is_valid is True
        assert error is None
        assert paths is None

    def test_validate_v1_manifest(self, sample_manifest_v1):
        """Test validation of v1.0 manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = validator.validate(sample_manifest_v1)

        assert is_valid is True

    def test_get_schema_version(self, sample_manifest):
        """Test getting schema version from manifest."""
        version = ManifestValidator.get_schema_version(sample_manifest)
        assert version == "2.0"

    def test_migrate(self, sample_manifest_v1):
        """Test manifest migration."""
        migrated = ManifestValidator.migrate(sample_manifest_v1, "2.0")
        assert migrated["schema_version"] == "2.0"

    def test_clear_cache(self):
        """Test clearing validation cache."""
        ManifestValidator.clear_cache()
        # Should not raise


class TestManifestParser:
    """Test ManifestParser class."""

    @pytest.mark.asyncio
    async def test_load_from_file(self, tmp_path, sample_manifest):
        """Test loading manifest from file."""
        import json

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(sample_manifest))

        parser = ManifestParser()
        loaded = await parser.load_from_file(manifest_file)

        assert loaded["slug"] == sample_manifest["slug"]

    @pytest.mark.asyncio
    async def test_load_from_file_invalid(self, tmp_path, invalid_manifest):
        """Test loading invalid manifest from file."""
        import json

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(invalid_manifest))

        parser = ManifestParser()

        with pytest.raises(ValueError, match="validation failed"):
            await parser.load_from_file(manifest_file, validate=True)

    @pytest.mark.asyncio
    async def test_load_from_file_no_validate(self, tmp_path, invalid_manifest):
        """Test loading manifest without validation."""
        import json

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(invalid_manifest))

        parser = ManifestParser()
        loaded = await parser.load_from_file(manifest_file, validate=False)

        assert loaded["slug"] == invalid_manifest["slug"]

    @pytest.mark.asyncio
    async def test_load_from_dict(self, sample_manifest):
        """Test loading manifest from dictionary."""
        parser = ManifestParser()
        loaded = await parser.load_from_dict(sample_manifest)

        assert loaded["slug"] == sample_manifest["slug"]

    @pytest.mark.asyncio
    async def test_load_from_string(self, sample_manifest):
        """Test loading manifest from JSON string."""
        import json

        parser = ManifestParser()
        loaded = await parser.load_from_string(json.dumps(sample_manifest))

        assert loaded["slug"] == sample_manifest["slug"]


class TestIndexDefinitionValidation:
    """Test index definition validation."""

    def test_validate_regular_index(self):
        """Test validation of regular index."""
        index_def = {
            "name": "test_index",
            "type": "regular",
            "keys": [("field1", 1), ("field2", -1)],
        }

        is_valid, error = validate_index_definition(index_def, "test_collection", "test_index")
        assert is_valid is True
        assert error is None

    def test_validate_regular_index_missing_keys(self):
        """Test validation of regular index without keys."""
        index_def = {"name": "test_index", "type": "regular"}

        is_valid, error = validate_index_definition(index_def, "test_collection", "test_index")
        assert is_valid is False
        assert "keys" in error.lower()

    def test_validate_regular_index_id_field(self):
        """Test validation rejects _id index."""
        index_def = {"name": "test_index", "type": "regular", "keys": [("_id", 1)]}

        is_valid, error = validate_index_definition(index_def, "test_collection", "test_index")
        assert is_valid is False
        assert "_id" in error.lower()

    def test_validate_ttl_index(self):
        """Test validation of TTL index."""
        index_def = {
            "name": "ttl_index",
            "type": "ttl",
            "keys": [("created_at", 1)],
            "options": {"expireAfterSeconds": 3600},
        }

        is_valid, error = validate_index_definition(index_def, "test_collection", "ttl_index")
        assert is_valid is True

    def test_validate_ttl_index_missing_expire(self):
        """Test validation of TTL index without expireAfterSeconds."""
        index_def = {"name": "ttl_index", "type": "ttl", "keys": [("created_at", 1)]}

        is_valid, error = validate_index_definition(index_def, "test_collection", "ttl_index")
        assert is_valid is False
        assert "expireafterseconds" in error.lower()

    def test_validate_vector_search_index(self):
        """Test validation of vector search index."""
        index_def = {
            "name": "vector_index",
            "type": "vectorSearch",
            "definition": {
                "fields": [{"type": "vector", "path": "embedding", "numDimensions": 128}]
            },
        }

        is_valid, error = validate_index_definition(index_def, "test_collection", "vector_index")
        assert is_valid is True

    def test_validate_vector_search_index_missing_definition(self):
        """Test validation of vector search index without definition."""
        index_def = {"name": "vector_index", "type": "vectorSearch"}

        is_valid, error = validate_index_definition(index_def, "test_collection", "vector_index")
        assert is_valid is False
        assert "definition" in error.lower()

    def test_validate_managed_indexes_valid(self):
        """Test validation of valid managed indexes."""
        managed_indexes = {
            "test_collection": [{"name": "test_index", "type": "regular", "keys": [("field1", 1)]}]
        }

        is_valid, error = validate_managed_indexes(managed_indexes)
        assert is_valid is True
        assert error is None

    def test_validate_managed_indexes_invalid(self):
        """Test validation of invalid managed indexes."""
        managed_indexes = {
            "test_collection": [
                {
                    "name": "test_index",
                    "type": "regular",
                    # Missing keys
                }
            ]
        }

        is_valid, error = validate_managed_indexes(managed_indexes)
        assert is_valid is False
        assert error is not None
