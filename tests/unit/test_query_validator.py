"""
Unit tests for QueryValidator.

Tests query validation including dangerous operator blocking,
query depth limits, regex complexity limits, and pipeline validation.
"""

import pytest

from mdb_engine.database.query_validator import QueryValidator
from mdb_engine.exceptions import QueryValidationError


@pytest.mark.unit
class TestQueryValidator:
    """Test QueryValidator functionality."""

    def test_validate_filter_empty(self):
        """Test that empty filters are allowed."""
        validator = QueryValidator()
        validator.validate_filter(None)
        validator.validate_filter({})

    def test_validate_filter_dangerous_operator_where(self):
        """Test that $where operator is blocked."""
        validator = QueryValidator()
        filter = {"$where": "this.status === 'active'"}

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

    def test_validate_filter_dangerous_operator_eval(self):
        """Test that $eval operator is blocked."""
        validator = QueryValidator()
        filter = {"$eval": "some code"}

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

    def test_validate_filter_dangerous_operator_function(self):
        """Test that $function operator is blocked."""
        validator = QueryValidator()
        filter = {"$function": {"body": "function() { return 1; }"}}

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

    def test_validate_filter_dangerous_operator_nested(self):
        """Test that dangerous operators are detected in nested queries."""
        validator = QueryValidator()
        filter = {"status": "active", "nested": {"$where": "true"}}

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

    def test_validate_filter_dangerous_operator_in_array(self):
        """Test that dangerous operators are detected in arrays."""
        validator = QueryValidator()
        filter = {"$or": [{"$where": "true"}, {"status": "active"}]}

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

    def test_validate_filter_safe_operators(self):
        """Test that safe operators are allowed."""
        validator = QueryValidator()
        # These should all pass
        validator.validate_filter({"status": "active"})
        validator.validate_filter({"age": {"$gt": 18}})
        validator.validate_filter({"tags": {"$in": ["red", "blue"]}})
        validator.validate_filter({"$or": [{"status": "active"}, {"deleted": False}]})
        validator.validate_filter({"$and": [{"status": "active"}, {"age": {"$gt": 18}}]})

    def test_validate_filter_max_depth(self):
        """Test that query depth is limited."""
        validator = QueryValidator(max_depth=3)

        # Create a deeply nested query
        deep_filter = {"level1": {"level2": {"level3": {"level4": {"value": 1}}}}}

        with pytest.raises(QueryValidationError, match="exceeds maximum nesting depth"):
            validator.validate_filter(deep_filter)

    def test_validate_filter_valid_depth(self):
        """Test that queries within depth limit are allowed."""
        validator = QueryValidator(max_depth=5)

        # Create a nested query within limits
        filter = {"level1": {"level2": {"level3": {"value": 1}}}}
        validator.validate_filter(filter)

    def test_validate_regex_too_long(self):
        """Test that overly long regex patterns are rejected."""
        validator = QueryValidator(max_regex_length=100)

        long_pattern = "a" * 101
        with pytest.raises(QueryValidationError, match="exceeds maximum length"):
            validator.validate_regex(long_pattern)

    def test_validate_regex_too_complex(self):
        """Test that overly complex regex patterns are rejected."""
        validator = QueryValidator(max_regex_complexity=10)

        # Create a complex regex with many quantifiers
        complex_pattern = "(a+)*" * 20  # Many quantifiers
        with pytest.raises(QueryValidationError, match="exceeds maximum complexity"):
            validator.validate_regex(complex_pattern)

    def test_validate_regex_valid(self):
        """Test that valid regex patterns are allowed."""
        validator = QueryValidator()

        # Simple regex patterns should pass
        validator.validate_regex("^test$")
        validator.validate_regex("\\d+")
        validator.validate_regex("[a-z]+")

    def test_validate_regex_invalid_syntax(self):
        """Test that invalid regex syntax is caught."""
        validator = QueryValidator()

        with pytest.raises(QueryValidationError, match="Invalid regex pattern"):
            validator.validate_regex("[unclosed")

    def test_validate_pipeline_empty(self):
        """Test that empty pipelines are allowed."""
        validator = QueryValidator()
        validator.validate_pipeline([])

    def test_validate_pipeline_too_many_stages(self):
        """Test that pipelines with too many stages are rejected."""
        validator = QueryValidator(max_pipeline_stages=5)

        pipeline = [{"$match": {}}] * 10
        with pytest.raises(QueryValidationError, match="exceeds maximum stages"):
            validator.validate_pipeline(pipeline)

    def test_validate_pipeline_dangerous_operator(self):
        """Test that dangerous operators in pipeline are blocked."""
        validator = QueryValidator()
        pipeline = [{"$match": {"$where": "true"}}]

        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_pipeline(pipeline)

    def test_validate_pipeline_valid(self):
        """Test that valid pipelines are allowed."""
        validator = QueryValidator()
        pipeline = [
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        validator.validate_pipeline(pipeline)

    def test_validate_pipeline_invalid_type(self):
        """Test that non-list pipelines are rejected."""
        validator = QueryValidator()

        with pytest.raises(QueryValidationError, match="must be a list"):
            validator.validate_pipeline({"$match": {}})

    def test_validate_sort_too_many_fields(self):
        """Test that sorts with too many fields are rejected."""
        validator = QueryValidator()
        from mdb_engine.constants import MAX_SORT_FIELDS

        # Create sort with too many fields
        sort_fields = [(f"field{i}", 1) for i in range(MAX_SORT_FIELDS + 1)]
        with pytest.raises(QueryValidationError, match="exceeds maximum fields"):
            validator.validate_sort(sort_fields)

    def test_validate_sort_valid(self):
        """Test that valid sort specifications are allowed."""
        validator = QueryValidator()

        validator.validate_sort([("field1", 1), ("field2", -1)])
        validator.validate_sort({"field1": 1, "field2": -1})
        validator.validate_sort(("field1", 1))

    def test_validate_filter_invalid_type(self):
        """Test that non-dict filters are rejected."""
        validator = QueryValidator()

        with pytest.raises(QueryValidationError, match="must be a dictionary"):
            validator.validate_filter("not a dict")
            validator.validate_filter([])
            validator.validate_filter(123)

    def test_custom_dangerous_operators(self):
        """Test that custom dangerous operator lists work."""
        validator = QueryValidator(dangerous_operators={"$custom"})

        filter = {"$custom": "value"}
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter)

        # Standard dangerous operators should still be blocked
        filter2 = {"$where": "true"}
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(filter2)
