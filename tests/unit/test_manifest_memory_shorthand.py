"""
Unit tests for manifest memory_config shorthand validation.

Covers:
- memory_config: true
- memory_config: "basic" / "smart" / "full"
- memory_config: {"preset": "full", ...}
- nested perfect_brain inside memory_config
"""

import pytest

from mdb_engine.core.manifest import MANIFEST_SCHEMA_V2


@pytest.fixture
def schema():
    return MANIFEST_SCHEMA_V2


def _validate(schema, manifest):
    """Run jsonschema validation and return (is_valid, error_message)."""
    from jsonschema import ValidationError, validate

    try:
        validate(instance=manifest, schema=schema)
        return True, None
    except ValidationError as e:
        return False, str(e.message)


class TestMemoryConfigShorthand:
    """Tests for memory_config accepting true, string presets, and objects."""

    def test_boolean_true_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": True,
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_boolean_false_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": False,
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_string_basic_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": "basic",
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_string_smart_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": "smart",
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_string_full_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": "full",
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_object_with_preset_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": {
                "preset": "full",
                "embedding_model": "text-embedding-3-large",
            },
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_object_with_enabled_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": {"enabled": True},
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_invalid_string_rejected(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": "invalid_preset",
        }
        valid, _ = _validate(schema, manifest)
        assert not valid

    def test_number_rejected(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": 42,
        }
        valid, _ = _validate(schema, manifest)
        assert not valid


class TestNestedPerfectBrain:
    """Tests for perfect_brain nested inside memory_config."""

    def test_nested_perfect_brain_accepted(self, schema):
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "memory_config": {
                "enabled": True,
                "perfect_brain": {
                    "enabled": True,
                    "memory_veto": True,
                    "shared_memory": True,
                },
            },
        }
        valid, error = _validate(schema, manifest)
        assert valid, f"Expected valid but got: {error}"

    def test_top_level_perfect_brain_ignored_by_engine(self, schema):
        """Top-level perfect_brain passes schema validation (additionalProperties
        is allowed at the top level) but is ignored by the engine — only the
        nested memory_config.perfect_brain location is read at runtime."""
        manifest = {
            "schema_version": "2.0",
            "slug": "test_app",
            "name": "Test",
            "perfect_brain": {"enabled": True},
        }
        valid, _ = _validate(schema, manifest)
        assert valid  # Schema allows extra keys at top level
        # But engine code only reads memory_config.perfect_brain (no top-level check)
