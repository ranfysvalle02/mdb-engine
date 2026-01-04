"""
Extended tests for manifest validation to cover edge cases and specific index types.
"""

from mdb_engine.core.manifest import validate_index_definition


class TestManifestExtendedValidation:
    """Test extended validation logic for indexes."""

    def test_validate_partial_index_success(self):
        """Test valid partial index."""
        index_def = {
            "name": "partial_idx",
            "type": "partial",
            "keys": {"field": 1},
            "options": {"partialFilterExpression": {"field": {"$gt": 0}}},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert is_valid
        assert error is None

    def test_validate_partial_index_failures(self):
        """Test invalid partial indexes."""
        # Missing keys
        index_def = {
            "name": "partial_idx",
            "type": "partial",
            "options": {"partialFilterExpression": {}},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'keys'" in error

        # Missing partialFilterExpression
        index_def = {
            "name": "partial_idx",
            "type": "partial",
            "keys": {"field": 1},
            "options": {},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'partialFilterExpression'" in error

    def test_validate_text_index_success(self):
        """Test valid text index."""
        # Dict keys
        index_def = {
            "name": "text_idx",
            "type": "text",
            "keys": {"content": "text", "title": "text"},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert is_valid

        # List keys
        index_def = {"name": "text_idx", "type": "text", "keys": [("content", "text")]}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert is_valid

    def test_validate_text_index_failures(self):
        """Test invalid text indexes."""
        # Missing keys
        index_def = {"name": "text_idx", "type": "text"}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'keys'" in error

        # Keys without "text" type (dict)
        index_def = {"name": "text_idx", "type": "text", "keys": {"field": 1}}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "must have at least one field with 'text'" in error

        # Keys without "text" type (list)
        index_def = {"name": "text_idx", "type": "text", "keys": [("field", 1)]}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "must have at least one field with 'text'" in error

    def test_validate_hybrid_index_success(self):
        """Test valid hybrid index."""
        index_def = {
            "name": "hybrid_idx",
            "type": "hybrid",
            "hybrid": {
                "vector_index": {
                    "name": "vec_idx",
                    "type": "vectorSearch",
                    "definition": {
                        "fields": [
                            {
                                "type": "vector",
                                "numDimensions": 1536,
                                "path": "embedding",
                            }
                        ]
                    },
                },
                "text_index": {  # Changed from search_index to text_index
                    "name": "search_idx",
                    "type": "search",
                    "definition": {"mappings": {"dynamic": True}},
                },
            },
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert is_valid

    def test_validate_hybrid_index_failures(self):
        """Test invalid hybrid indexes."""
        # Missing hybrid field
        index_def = {"name": "hybrid_idx", "type": "hybrid"}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'hybrid' field" in error

        # Hybrid not a dict
        index_def = {"name": "hybrid_idx", "type": "hybrid", "hybrid": "invalid"}
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'hybrid' to be an object" in error

        # Missing vector_index
        index_def = {
            "name": "hybrid_idx",
            "type": "hybrid",
            "hybrid": {"text_index": {}},  # Use text_index here too
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'hybrid.vector_index' to be an object" in error

        # Missing text_index (was search_index in previous test)
        index_def = {
            "name": "hybrid_idx",
            "type": "hybrid",
            "hybrid": {"vector_index": {"definition": {}}},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'hybrid.text_index' to be an object" in error

    def test_validate_ttl_index_extended_failures(self):
        """Test additional TTL index failure modes."""
        # expireAfterSeconds too small
        index_def = {
            "name": "ttl_idx",
            "type": "ttl",
            "keys": {"created_at": 1},
            "options": {"expireAfterSeconds": -1},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'expireAfterSeconds' to be >=" in error

        # expireAfterSeconds too large (warning/error)
        index_def = {
            "name": "ttl_idx",
            "type": "ttl",
            "keys": {"created_at": 1},
            "options": {"expireAfterSeconds": 999999999999},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        # Note: The code actually returns False for too large TTL
        assert not is_valid
        assert "expireAfterSeconds' too large" in error

    def test_validate_vector_search_extended_failures(self):
        """Test additional Vector Search failure modes."""
        # Empty fields
        index_def = {
            "name": "vec_idx",
            "type": "vectorSearch",
            "definition": {"fields": []},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'definition.fields' to be a non-empty array" in error

        # Invalid dimensions
        index_def = {
            "name": "vec_idx",
            "type": "vectorSearch",
            "definition": {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 0}]},
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'numDimensions' to be between" in error

        index_def = {
            "name": "vec_idx",
            "type": "vectorSearch",
            "definition": {
                "fields": [{"type": "vector", "path": "embedding", "numDimensions": 10001}]
            },
        }
        is_valid, error = validate_index_definition(index_def, "coll", "idx")
        assert not is_valid
        assert "requires 'numDimensions' to be between" in error
