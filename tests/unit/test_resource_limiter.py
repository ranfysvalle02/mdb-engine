"""
Unit tests for ResourceLimiter.

Tests resource limit enforcement including query timeouts,
result size limits, document size validation, and batch size limits.
"""

import pytest

from mdb_engine.database.resource_limiter import ResourceLimiter
from mdb_engine.exceptions import ResourceLimitExceeded


@pytest.mark.unit
class TestResourceLimiter:
    """Test ResourceLimiter functionality."""

    def test_enforce_query_timeout_no_timeout(self):
        """Test that default timeout is added when not present."""
        limiter = ResourceLimiter(default_timeout_ms=30000)
        kwargs = {}

        result = limiter.enforce_query_timeout(kwargs)
        assert result["maxTimeMS"] == 30000

    def test_enforce_query_timeout_existing_timeout(self):
        """Test that existing timeout is preserved."""
        limiter = ResourceLimiter(default_timeout_ms=30000)
        kwargs = {"maxTimeMS": 10000}

        result = limiter.enforce_query_timeout(kwargs)
        assert result["maxTimeMS"] == 10000

    def test_enforce_query_timeout_exceeds_maximum(self):
        """Test that timeouts exceeding maximum are capped."""
        limiter = ResourceLimiter(default_timeout_ms=30000, max_timeout_ms=60000)
        kwargs = {"maxTimeMS": 120000}

        result = limiter.enforce_query_timeout(kwargs)
        assert result["maxTimeMS"] == 60000

    def test_enforce_query_timeout_custom_default(self):
        """Test that custom default timeout works."""
        limiter = ResourceLimiter(default_timeout_ms=30000)
        kwargs = {}

        result = limiter.enforce_query_timeout(kwargs, default_timeout=15000)
        assert result["maxTimeMS"] == 15000

    def test_enforce_result_limit_none(self):
        """Test that None limit returns max allowed."""
        limiter = ResourceLimiter(max_result_size=10000)

        result = limiter.enforce_result_limit(None)
        assert result == 10000

    def test_enforce_result_limit_within_limit(self):
        """Test that limits within maximum are preserved."""
        limiter = ResourceLimiter(max_result_size=10000)

        result = limiter.enforce_result_limit(100)
        assert result == 100

    def test_enforce_result_limit_exceeds_maximum(self):
        """Test that limits exceeding maximum are capped."""
        limiter = ResourceLimiter(max_result_size=10000)

        result = limiter.enforce_result_limit(20000)
        assert result == 10000

    def test_enforce_result_limit_custom_max(self):
        """Test that custom max limit works."""
        limiter = ResourceLimiter(max_result_size=10000)

        result = limiter.enforce_result_limit(5000, max_limit=20000)
        assert result == 5000

        result = limiter.enforce_result_limit(25000, max_limit=20000)
        assert result == 20000

    def test_enforce_batch_size_none(self):
        """Test that None batch size returns max allowed."""
        limiter = ResourceLimiter(max_batch_size=1000)

        result = limiter.enforce_batch_size(None)
        assert result == 1000

    def test_enforce_batch_size_within_limit(self):
        """Test that batch sizes within maximum are preserved."""
        limiter = ResourceLimiter(max_batch_size=1000)

        result = limiter.enforce_batch_size(100)
        assert result == 100

    def test_enforce_batch_size_exceeds_maximum(self):
        """Test that batch sizes exceeding maximum are capped."""
        limiter = ResourceLimiter(max_batch_size=1000)

        result = limiter.enforce_batch_size(2000)
        assert result == 1000

    def test_validate_document_size_valid(self):
        """Test that valid document sizes pass validation."""
        limiter = ResourceLimiter(max_document_size=16 * 1024 * 1024)

        # Small document should pass
        doc = {"field1": "value1", "field2": 123}
        limiter.validate_document_size(doc)

    def test_validate_document_size_too_large(self):
        """Test that oversized documents are rejected."""
        limiter = ResourceLimiter(max_document_size=1024)  # 1KB limit

        # Create a document that exceeds limit
        large_value = "x" * 2000  # 2KB string
        doc = {"large_field": large_value}

        with pytest.raises(ResourceLimitExceeded, match="exceeds maximum"):
            limiter.validate_document_size(doc)

    def test_validate_documents_size_all_valid(self):
        """Test that all valid documents pass validation."""
        limiter = ResourceLimiter(max_document_size=16 * 1024 * 1024)

        docs = [
            {"field1": "value1"},
            {"field2": "value2"},
            {"field3": "value3"},
        ]
        limiter.validate_documents_size(docs)

    def test_validate_documents_size_one_invalid(self):
        """Test that invalid document in list is detected."""
        limiter = ResourceLimiter(max_document_size=1024)  # 1KB limit

        large_value = "x" * 2000
        docs = [
            {"field1": "value1"},
            {"large_field": large_value},  # This one exceeds limit
            {"field3": "value3"},
        ]

        with pytest.raises(ResourceLimitExceeded, match="document index: 1"):
            limiter.validate_documents_size(docs)

    def test_validate_document_size_nested_structure(self):
        """Test that nested document structures are validated."""
        limiter = ResourceLimiter(max_document_size=1024)

        # Create nested document
        nested_doc = {
            "level1": {
                "level2": {"level3": {"large_field": "x" * 2000}}  # Exceeds limit
            }
        }

        with pytest.raises(ResourceLimitExceeded):
            limiter.validate_document_size(nested_doc)

    def test_validate_document_size_handles_serialization_error(self):
        """Test that serialization errors are handled gracefully."""
        limiter = ResourceLimiter(max_document_size=1024)

        # Create document with non-serializable object
        class NonSerializable:
            pass

        doc = {"field": NonSerializable()}

        # Should not raise exception, just log warning
        # MongoDB will catch this anyway
        try:
            limiter.validate_document_size(doc)
        except ResourceLimitExceeded:
            # If it raises, that's also acceptable
            pass
