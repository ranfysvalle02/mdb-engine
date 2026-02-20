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
    clear_validation_cache,
    get_schema_for_version,
    get_schema_version,
    migrate_manifest,
    validate_index_definition,
    validate_managed_indexes,
    validate_manifest,
    validate_manifests_parallel,
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


@pytest.mark.asyncio
class TestManifestValidator:
    """Test ManifestValidator class."""

    async def test_validate_valid_manifest(self, sample_manifest):
        """Test validation of valid manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(sample_manifest)

        assert is_valid is True
        assert error is None
        assert paths is None

    async def test_validate_invalid_manifest(self, invalid_manifest):
        """Test validation of invalid manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(invalid_manifest)

        assert is_valid is False
        assert error is not None
        assert paths is not None

    async def test_validate_async(self, sample_manifest):
        """Test async validation."""
        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate_async(sample_manifest)

        assert is_valid is True
        assert error is None
        assert paths is None

    async def test_validate_v1_manifest(self, sample_manifest_v1):
        """Test validation of v1.0 manifest."""
        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(sample_manifest_v1)

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

    async def test_validate_default_policy_public_with_protected_routes(self, sample_manifest):
        """Regression: default_policy='public' was rejected by the schema (GH issue)."""
        manifest = {
            **sample_manifest,
            "auth": {
                "mode": "shared",
                "default_policy": "public",
                "protected_routes": ["/api/admin/**", "/api/settings/**"],
            },
        }
        is_valid, error, _ = await validate_manifest(manifest, use_cache=False)
        assert is_valid is True, error

    async def test_validate_default_policy_protected_with_public_routes(self, sample_manifest):
        """default_policy='protected' (the default) coexists with public_routes."""
        manifest = {
            **sample_manifest,
            "auth": {
                "mode": "shared",
                "default_policy": "protected",
                "public_routes": ["/health", "/api/**"],
            },
        }
        is_valid, error, _ = await validate_manifest(manifest, use_cache=False)
        assert is_valid is True, error

    async def test_validate_protected_routes_without_default_policy(self, sample_manifest):
        """protected_routes is accepted even when default_policy is omitted."""
        manifest = {
            **sample_manifest,
            "auth": {"protected_routes": ["/admin/**"]},
        }
        is_valid, error, _ = await validate_manifest(manifest, use_cache=False)
        assert is_valid is True, error

    async def test_validate_invalid_default_policy_rejected(self, sample_manifest):
        """Only 'protected' and 'public' are valid; anything else fails."""
        manifest = {
            **sample_manifest,
            "auth": {"default_policy": "bogus"},
        }
        is_valid, error, _ = await validate_manifest(manifest, use_cache=False)
        assert is_valid is False


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
            "definition": {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 128}]},
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
        managed_indexes = {"test_collection": [{"name": "test_index", "type": "regular", "keys": [("field1", 1)]}]}

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


# ============================================================================
# SCHEMA VERSION VALIDATION EDGE CASES
# ============================================================================


class TestSchemaVersionValidation:
    """Test schema version validation edge cases and migration paths."""

    def test_invalid_schema_version_non_numeric(self):
        """Non-numeric schema_version raises ValueError."""
        manifest = {"schema_version": "abc", "slug": "test"}
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            get_schema_version(manifest)

    def test_invalid_schema_version_special_chars(self):
        """Schema version with special characters raises ValueError."""
        manifest = {"schema_version": "2.0-beta", "slug": "test"}
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            get_schema_version(manifest)

    def test_valid_schema_version_numeric(self):
        """Numeric schema_version is returned as-is."""
        manifest = {"schema_version": "3.5", "slug": "test"}
        assert get_schema_version(manifest) == "3.5"

    def test_heuristic_auth_field_detects_v2(self):
        """Manifest with 'auth' field (no explicit version) is detected as 2.0."""
        manifest = {"slug": "test", "auth": {"mode": "app"}}
        assert get_schema_version(manifest) == "2.0"

    def test_heuristic_collection_settings_detects_v2(self):
        """Manifest with 'collection_settings' is detected as 2.0."""
        manifest = {"slug": "test", "collection_settings": {}}
        assert get_schema_version(manifest) == "2.0"

    def test_migrate_v1_to_v2_deprecated_auth_policy(self):
        """Migrating a v1 manifest with auth_policy raises ValueError."""
        manifest = {"schema_version": "1.0", "slug": "old_app", "auth_policy": {"required": True}}
        with pytest.raises(ValueError, match="deprecated"):
            migrate_manifest(manifest, "2.0")

    def test_migrate_v1_to_v2_deprecated_sub_auth(self):
        """Migrating a v1 manifest with sub_auth raises ValueError."""
        manifest = {"schema_version": "1.0", "slug": "old_app", "sub_auth": {"enabled": True}}
        with pytest.raises(ValueError, match="deprecated"):
            migrate_manifest(manifest, "2.0")

    def test_migrate_v1_to_v2_sets_schema_version(self):
        """V1-to-V2 migration sets schema_version to '2.0'."""
        manifest = {"schema_version": "1.0", "slug": "test", "name": "Test"}
        migrated = migrate_manifest(manifest, "2.0")
        assert migrated["schema_version"] == "2.0"
        assert migrated["slug"] == "test"

    def test_schema_version_fallback_compatible_major(self):
        """Unknown minor version falls back to compatible major version."""
        schema = get_schema_for_version("2.99")
        expected = get_schema_for_version("2.0")
        assert schema == expected

    def test_schema_version_fallback_unknown_major(self):
        """Completely unknown version falls back to current schema."""
        schema = get_schema_for_version("99.0")
        from mdb_engine.constants import CURRENT_SCHEMA_VERSION

        expected = get_schema_for_version(CURRENT_SCHEMA_VERSION)
        assert schema == expected


# ============================================================================
# INDEX VALIDATION EDGE CASES
# ============================================================================


class TestIndexValidation:
    """Test index definition validation edge cases."""

    def test_regular_index_empty_keys_dict(self):
        """Regular index with empty dict keys is invalid."""
        index_def = {"name": "idx", "type": "regular", "keys": {}}
        is_valid, error = validate_index_definition(index_def, "col", "idx")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_regular_index_empty_keys_list(self):
        """Regular index with empty list keys is invalid."""
        index_def = {"name": "idx", "type": "regular", "keys": []}
        is_valid, error = validate_index_definition(index_def, "col", "idx")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_ttl_index_missing_keys(self):
        """TTL index without keys is invalid."""
        index_def = {"name": "ttl", "type": "ttl", "options": {"expireAfterSeconds": 60}}
        is_valid, error = validate_index_definition(index_def, "col", "ttl")
        assert is_valid is False
        assert "keys" in error.lower()

    def test_ttl_index_missing_expire_after_seconds(self):
        """TTL index without expireAfterSeconds is invalid."""
        index_def = {"name": "ttl", "type": "ttl", "keys": [("ts", 1)], "options": {}}
        is_valid, error = validate_index_definition(index_def, "col", "ttl")
        assert is_valid is False
        assert "expireafterseconds" in error.lower()

    def test_geospatial_index_missing_keys(self):
        """Geospatial index without keys is invalid."""
        index_def = {"name": "geo", "type": "geospatial"}
        is_valid, error = validate_index_definition(index_def, "col", "geo")
        assert is_valid is False
        assert "keys" in error.lower()

    def test_geospatial_index_no_geo_type_in_keys(self):
        """Geospatial index without geo type in keys is invalid."""
        index_def = {"name": "geo", "type": "geospatial", "keys": {"location": 1}}
        is_valid, error = validate_index_definition(index_def, "col", "geo")
        assert is_valid is False
        assert "geospatial type" in error.lower()

    def test_geospatial_index_valid_2dsphere(self):
        """Geospatial index with 2dsphere type is valid."""
        index_def = {"name": "geo", "type": "geospatial", "keys": {"location": "2dsphere"}}
        is_valid, error = validate_index_definition(index_def, "col", "geo")
        assert is_valid is True
        assert error is None

    def test_vector_search_index_definition_not_dict(self):
        """vectorSearch index with non-dict definition is invalid."""
        index_def = {"name": "vs", "type": "vectorSearch", "definition": "not_a_dict"}
        is_valid, error = validate_index_definition(index_def, "col", "vs")
        assert is_valid is False
        assert "definition" in error.lower()

    def test_hybrid_index_missing_hybrid_field(self):
        """Hybrid index without 'hybrid' field is invalid."""
        index_def = {"name": "h", "type": "hybrid"}
        is_valid, error = validate_index_definition(index_def, "col", "h")
        assert is_valid is False
        assert "hybrid" in error.lower()

    def test_hybrid_index_vector_index_not_dict(self):
        """Hybrid index with non-dict vector_index is invalid."""
        index_def = {
            "name": "h",
            "type": "hybrid",
            "hybrid": {
                "vector_index": "bad",
                "text_index": {"definition": {}},
            },
        }
        is_valid, error = validate_index_definition(index_def, "col", "h")
        assert is_valid is False
        assert "vector_index" in error.lower()

    def test_hybrid_index_text_index_missing_definition(self):
        """Hybrid index text_index without definition is invalid."""
        index_def = {
            "name": "h",
            "type": "hybrid",
            "hybrid": {
                "vector_index": {"definition": {"fields": []}},
                "text_index": {"no_def": True},
            },
        }
        is_valid, error = validate_index_definition(index_def, "col", "h")
        assert is_valid is False
        assert "text_index.definition" in error.lower()

    def test_hybrid_index_text_index_definition_not_dict(self):
        """Hybrid index text_index with non-dict definition is invalid."""
        index_def = {
            "name": "h",
            "type": "hybrid",
            "hybrid": {
                "vector_index": {"definition": {"fields": []}},
                "text_index": {"definition": "string"},
            },
        }
        is_valid, error = validate_index_definition(index_def, "col", "h")
        assert is_valid is False
        assert "text_index.definition" in error.lower()

    def test_missing_index_type(self):
        """Index without type field is invalid."""
        index_def = {"name": "idx", "keys": [("f", 1)]}
        is_valid, error = validate_index_definition(index_def, "col", "idx")
        assert is_valid is False
        assert "type" in error.lower()

    def test_unknown_index_type(self):
        """Unknown index type is invalid."""
        index_def = {"name": "idx", "type": "fancy_new_type", "keys": [("f", 1)]}
        is_valid, error = validate_index_definition(index_def, "col", "idx")
        assert is_valid is False
        assert "unknown index type" in error.lower()

    def test_managed_indexes_not_dict(self):
        """managed_indexes that isn't a dict is invalid."""
        is_valid, error = validate_managed_indexes("not_a_dict")
        assert is_valid is False
        assert "must be an object" in error.lower()

    def test_managed_indexes_non_list_index_array(self):
        """managed_indexes with non-list value for a collection is invalid."""
        is_valid, error = validate_managed_indexes({"col": "not_a_list"})
        assert is_valid is False
        assert "must be an array" in error.lower()

    def test_managed_indexes_empty_collection(self):
        """managed_indexes with empty indexes array is invalid."""
        is_valid, error = validate_managed_indexes({"col": []})
        assert is_valid is False
        assert "empty" in error.lower()


# ============================================================================
# MANIFEST PARSER / VALIDATOR WRAPPER METHODS
# ============================================================================


@pytest.mark.asyncio
class TestManifestParserWrappers:
    """Test ManifestParser and ManifestValidator wrapper methods."""

    async def test_parser_load_from_dict_invalid_raises(self):
        """ManifestParser.load_from_dict raises ValueError for invalid manifest."""
        bad_manifest = {"slug": "bad!@#", "name": "", "status": "nope"}
        with pytest.raises(ValueError, match="validation failed"):
            await ManifestParser.load_from_dict(bad_manifest, validate=True)

    async def test_parser_load_and_migrate(self):
        """ManifestParser.load_and_migrate delegates to ManifestValidator.migrate."""
        manifest = {"schema_version": "1.0", "slug": "test", "name": "Test"}
        migrated = await ManifestParser.load_and_migrate(manifest, "2.0")
        assert migrated["schema_version"] == "2.0"

    async def test_validator_validate_managed_indexes_wrapper(self):
        """ManifestValidator.validate_managed_indexes delegates correctly."""
        valid_indexes = {"col": [{"name": "idx", "type": "regular", "keys": [("f", 1)]}]}
        is_valid, error = ManifestValidator.validate_managed_indexes(valid_indexes)
        assert is_valid is True
        assert error is None

    async def test_validator_validate_index_definition_wrapper(self):
        """ManifestValidator.validate_index_definition delegates correctly."""
        idx = {"name": "idx", "type": "regular", "keys": [("f", 1)]}
        is_valid, error = ManifestValidator.validate_index_definition(idx, "col", "idx")
        assert is_valid is True
        assert error is None

    async def test_parallel_validation_exception_handling(self):
        """validate_manifests_parallel handles exceptions from individual validations."""
        good = {
            "schema_version": "2.0",
            "slug": "test_ok",
            "name": "OK",
            "status": "active",
            "description": "ok",
            "auth_required": False,
            "data_scope": ["self"],
            "developer_id": "dev@test.com",
        }
        bad = {"slug": "bad!!", "name": "", "status": "nope"}
        results = await validate_manifests_parallel([good, bad], use_cache=False)
        assert len(results) == 2
        assert results[0][0] is True
        assert results[1][0] is False


# ============================================================================
# VALIDATION ERROR PATHS
# ============================================================================


@pytest.mark.asyncio
class TestValidationErrorPaths:
    """Test validation error processing (lines 3902-3954)."""

    async def test_validation_error_with_context_chain(self):
        """ValidationError with context sub-errors extracts nested messages (lines 3901-3907)."""
        manifest = {
            "schema_version": "2.0",
            "slug": "ctx-app",
            "name": "Context App",
            "status": "active",
            "auth": {"mode": "invalid_mode_value", "users": {"enabled": "not_a_bool"}},
        }
        clear_validation_cache()
        is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
        assert is_valid is False
        assert error is not None

    async def test_validation_error_root_path(self):
        """ValidationError at root level produces 'root' path (lines 3892-3895)."""
        manifest = {"schema_version": "2.0", "slug": "invalid!!!", "name": "X"}
        clear_validation_cache()
        is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
        assert is_valid is False
        assert paths is not None

    async def test_validation_error_nested_path(self):
        """ValidationError with nested path produces dotted path (lines 3892-3893)."""
        manifest = {
            "schema_version": "2.0",
            "slug": "nested_err",
            "name": "Nested",
            "status": "active",
            "managed_indexes": "not_a_dict",
        }
        clear_validation_cache()
        is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
        assert is_valid is False
        assert error is not None

    async def test_type_error_during_validation(self):
        """TypeError during validation returns structure error (lines 3945-3954)."""
        from unittest.mock import patch

        manifest = {"schema_version": "2.0", "slug": "type_err", "name": "TE"}
        clear_validation_cache()

        with patch(
            "mdb_engine.core.manifest.get_schema_for_version",
            side_effect=TypeError("unexpected type"),
        ):
            is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
            assert is_valid is False
            assert "structure error" in error.lower()

    async def test_value_error_during_validation(self):
        """ValueError during validation returns structure error (lines 3945-3954)."""
        from unittest.mock import patch

        manifest = {"schema_version": "2.0", "slug": "val_err", "name": "VE"}
        clear_validation_cache()

        with patch(
            "mdb_engine.core.manifest.get_schema_for_version",
            side_effect=ValueError("bad value"),
        ):
            is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
            assert is_valid is False
            assert "structure error" in error.lower()

    async def test_key_error_during_validation(self):
        """KeyError during validation returns structure error (lines 3945-3954)."""
        from unittest.mock import patch

        manifest = {"schema_version": "2.0", "slug": "key_err", "name": "KE"}
        clear_validation_cache()

        with patch(
            "mdb_engine.core.manifest.get_schema_for_version",
            side_effect=KeyError("missing_key"),
        ):
            is_valid, error, paths = await validate_manifest(manifest, use_cache=False)
            assert is_valid is False
            assert "structure error" in error.lower()


# ============================================================================
# DEVELOPER ID VALIDATION
# ============================================================================


@pytest.mark.asyncio
class TestDeveloperIdValidation:
    """Test developer ID validation (lines 4017-4043)."""

    async def test_empty_developer_id(self):
        """Empty developer_id returns error (line 4017-4018)."""
        from mdb_engine.core.manifest import validate_developer_id

        is_valid, error = await validate_developer_id("")
        assert is_valid is False
        assert "empty" in error.lower()

    async def test_non_string_developer_id(self):
        """Non-string developer_id returns error (line 4020-4021)."""
        from mdb_engine.core.manifest import validate_developer_id

        is_valid, error = await validate_developer_id(123)
        assert is_valid is False
        assert "string" in error.lower()

    async def test_invalid_email_format_no_at(self):
        """developer_id without @ returns error (line 4024)."""
        from mdb_engine.core.manifest import validate_developer_id

        is_valid, error = await validate_developer_id("notanemail")
        assert is_valid is False
        assert "valid email" in error.lower()

    async def test_invalid_email_format_no_dot(self):
        """developer_id without . returns error (line 4024)."""
        from mdb_engine.core.manifest import validate_developer_id

        is_valid, error = await validate_developer_id("user@host")
        assert is_valid is False
        assert "valid email" in error.lower()

    async def test_valid_email_no_db(self):
        """Valid email without db_validator returns True (line 4043)."""
        from mdb_engine.core.manifest import validate_developer_id

        is_valid, error = await validate_developer_id("dev@example.com")
        assert is_valid is True
        assert error is None

    async def test_db_validator_returns_false(self):
        """db_validator returning False means developer not found (lines 4033-4038)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_developer_id

        validator = AsyncMock(return_value=False)
        is_valid, error = await validate_developer_id("ghost@example.com", db_validator=validator)
        assert is_valid is False
        assert "does not exist" in error.lower()

    async def test_db_validator_returns_true(self):
        """db_validator returning True means valid (line 4043)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_developer_id

        validator = AsyncMock(return_value=True)
        is_valid, error = await validate_developer_id("admin@example.com", db_validator=validator)
        assert is_valid is True
        assert error is None

    async def test_db_validator_raises_error(self):
        """db_validator raising ValueError is caught (lines 4039-4041)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_developer_id

        validator = AsyncMock(side_effect=ValueError("db down"))
        is_valid, error = await validate_developer_id("err@example.com", db_validator=validator)
        assert is_valid is False
        assert "Error validating" in error

    async def test_db_validator_raises_type_error(self):
        """db_validator raising TypeError is caught (lines 4039-4041)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_developer_id

        validator = AsyncMock(side_effect=TypeError("bad arg"))
        is_valid, error = await validate_developer_id("te@example.com", db_validator=validator)
        assert is_valid is False
        assert "Error validating" in error


# ============================================================================
# MANIFEST VALIDATION WITH DB
# ============================================================================


@pytest.mark.asyncio
class TestManifestValidationWithDB:
    """Test validate_manifest_with_db (lines 4068-4083) and wrapper (line 4554)."""

    async def test_manifest_with_db_schema_invalid(self):
        """Schema validation fails before db check (lines 4068-4070)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_manifest_with_db

        bad_manifest = {"slug": "bad!!!", "name": ""}
        validator = AsyncMock(return_value=True)
        clear_validation_cache()

        is_valid, error, paths = await validate_manifest_with_db(bad_manifest, validator, use_cache=False)
        assert is_valid is False
        validator.assert_not_awaited()

    async def test_manifest_with_db_dev_id_invalid(self):
        """developer_id validation fails after schema passes (lines 4073-4081)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_manifest_with_db

        manifest = {
            "schema_version": "2.0",
            "slug": "dbval_app",
            "name": "DB Val App",
            "status": "active",
            "description": "Test",
            "auth_required": False,
            "data_scope": ["self"],
            "developer_id": "ghost@example.com",
        }
        validator = AsyncMock(return_value=False)
        clear_validation_cache()

        is_valid, error, paths = await validate_manifest_with_db(manifest, validator, use_cache=False)
        assert is_valid is False
        assert "developer_id" in (paths or [])

    async def test_manifest_with_db_all_valid(self):
        """Both schema and developer_id valid (line 4083)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_manifest_with_db

        manifest = {
            "schema_version": "2.0",
            "slug": "allgood",
            "name": "All Good",
            "status": "active",
            "description": "Test",
            "auth_required": False,
            "data_scope": ["self"],
            "developer_id": "ok@example.com",
        }
        validator = AsyncMock(return_value=True)
        clear_validation_cache()

        is_valid, error, paths = await validate_manifest_with_db(manifest, validator, use_cache=False)
        assert is_valid is True
        assert error is None

    async def test_manifest_with_db_no_dev_id(self):
        """Manifest without developer_id skips db check (lines 4073)."""
        from unittest.mock import AsyncMock

        from mdb_engine.core.manifest import validate_manifest_with_db

        manifest = {
            "schema_version": "2.0",
            "slug": "nodev",
            "name": "No Dev",
            "status": "active",
            "description": "Test",
            "auth_required": False,
            "data_scope": ["self"],
        }
        validator = AsyncMock(return_value=True)
        clear_validation_cache()

        is_valid, error, paths = await validate_manifest_with_db(manifest, validator, use_cache=False)
        assert is_valid is True
        validator.assert_not_awaited()

    async def test_validator_wrapper_validate_with_db(self):
        """ManifestValidator.validate_with_db delegates correctly (line 4554)."""
        from unittest.mock import AsyncMock

        manifest = {
            "schema_version": "2.0",
            "slug": "wrapper_test",
            "name": "Wrapper",
            "status": "active",
            "description": "Test",
            "auth_required": False,
            "data_scope": ["self"],
            "developer_id": "ok@example.com",
        }
        validator_fn = AsyncMock(return_value=True)
        clear_validation_cache()

        v = ManifestValidator()
        is_valid, error, paths = await v.validate_with_db(manifest, validator_fn, use_cache=False)
        assert is_valid is True


# ============================================================================
# PARALLEL VALIDATION EXCEPTION
# ============================================================================


@pytest.mark.asyncio
class TestParallelValidationException:
    """Test parallel validation exception handling (lines 3993-3994)."""

    async def test_parallel_with_exception_in_validate(self):
        """Exception during parallel validation is caught (lines 3992-3994)."""
        from unittest.mock import patch

        good = {
            "schema_version": "2.0",
            "slug": "ok_app",
            "name": "OK",
            "status": "active",
            "description": "ok",
            "auth_required": False,
            "data_scope": ["self"],
            "developer_id": "dev@test.com",
        }
        clear_validation_cache()

        original_validate = validate_manifest
        call_count = 0

        async def patched_validate(m, use_cache=True):
            nonlocal call_count
            call_count += 1
            if m.get("slug") == "bomb":
                raise RuntimeError("boom")
            return await original_validate(m, use_cache=use_cache)

        bomb = {"slug": "bomb", "name": "Bomb"}

        with patch("mdb_engine.core.manifest.validate_manifest", side_effect=patched_validate):
            results = await validate_manifests_parallel([good, bomb], use_cache=False)

        assert len(results) == 2
        bomb_result = results[1]
        assert bomb_result[0] is False
        assert "Validation error" in (bomb_result[1] or "")
