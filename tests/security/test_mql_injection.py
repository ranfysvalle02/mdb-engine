"""
Adversarial MQL injection tests.

Verifies that every user-input surface rejects or neutralises MongoDB
query injection attempts.
"""

from __future__ import annotations

from typing import Any

import pytest

from mdb_engine.database.query_validator import QueryValidator
from mdb_engine.exceptions import QueryValidationError
from mdb_engine.routing.query_parser import parse_query_params

# ============================================================================
# Query-string injection via parse_query_params
# ============================================================================


@pytest.mark.unit
class TestQueryStringInjection:
    """Attempt MQL injection through URL query parameters."""

    def test_dollar_operator_as_field_name_rejected(self):
        """$-prefixed field names must never become filter keys."""
        for bad_name in ("$where", "$gt", "$ne", "$regex", "$or", "$and"):
            parsed = parse_query_params({bad_name: "anything"})
            assert bad_name not in parsed.filter

    def test_underscore_prefixed_field_rejected(self):
        """_-prefixed field names are rejected by _is_safe_field."""
        for bad_name in ("_id", "__proto__", "_internal"):
            parsed = parse_query_params({bad_name: "val"})
            assert bad_name not in parsed.filter

    def test_prototype_pollution_keys_rejected(self):
        """Keys commonly used in prototype-pollution attacks."""
        for bad_key in ("__proto__", "__class__", "__subclasses__"):
            parsed = parse_query_params({bad_key: "val"})
            assert bad_key not in parsed.filter

    def test_json_object_in_value_stays_string(self):
        """JSON objects in values must not become nested MQL operators."""
        parsed = parse_query_params({"status": '{"$ne": null}'})
        assert isinstance(parsed.filter.get("status"), str)

    def test_nested_operator_in_value_stays_string(self):
        """Operator-like values that aren't prefixed stay as strings."""
        parsed = parse_query_params({"name": '{"$regex": ".*"}'})
        assert isinstance(parsed.filter.get("name"), str)

    def test_only_safe_operators_recognised(self):
        """Only the closed set of operator prefixes should be accepted."""
        safe_ops = ("gt:", "gte:", "lt:", "lte:", "ne:", "in:")
        for prefix in safe_ops:
            parsed = parse_query_params({"age": f"{prefix}18"})
            assert "age" in parsed.filter

        bad_ops = ("regex:", "where:", "eval:", "exists:", "type:")
        for prefix in bad_ops:
            parsed = parse_query_params({"age": f"{prefix}something"})
            val = parsed.filter.get("age")
            assert isinstance(val, str)

    def test_sort_injection_rejected(self):
        """Sort field names starting with $ or _ are dropped."""
        parsed = parse_query_params({"sort": "$where,_id,name"})
        if parsed.sort:
            field_names = [f for f, _ in parsed.sort]
            assert "$where" not in field_names
            assert "_id" not in field_names

    def test_fields_projection_injection_rejected(self):
        """Fields projection rejects $-prefixed names."""
        parsed = parse_query_params({"fields": "$where,name,status"})
        if parsed.projection:
            assert "$where" not in parsed.projection

    def test_empty_field_name_rejected(self):
        parsed = parse_query_params({"": "value"})
        assert "" not in parsed.filter

    def test_dot_notation_field_allowed(self):
        """Dot-notation like profile.name should pass _is_safe_field."""
        parsed = parse_query_params({"profile.name": "alice"})
        assert "profile.name" in parsed.filter


# ============================================================================
# Body sanitization ($-prefixed keys)
# ============================================================================


@pytest.mark.unit
class TestBodySanitization:
    """Verify that sanitize_body strips $-prefixed keys from request bodies."""

    def _make_ctx(
        self,
        writable_fields=None,
        immutable_fields=None,
    ):
        """Build a minimal _CollectionCtx for testing sanitize_body."""
        from mdb_engine.routing.auto_crud import _CollectionCtx

        return _CollectionCtx(
            name="test",
            writable_fields=writable_fields,
            immutable_fields=immutable_fields or [],
        )

    def test_dollar_prefixed_keys_stripped(self):
        ctx = self._make_ctx()
        body: dict[str, Any] = {
            "title": "legit",
            "$set": {"role": "admin"},
            "$where": "1==1",
            "$unset": {"password": 1},
        }
        ctx.sanitize_body(body)
        assert "title" in body
        assert "$set" not in body
        assert "$where" not in body
        assert "$unset" not in body

    def test_dollar_key_stripped_even_without_writable_fields(self):
        ctx = self._make_ctx()
        body: dict[str, Any] = {"name": "ok", "$inc": {"counter": 1}}
        ctx.sanitize_body(body)
        assert "$inc" not in body
        assert "name" in body

    def test_dollar_key_stripped_with_writable_allowlist(self):
        ctx = self._make_ctx(writable_fields=["title", "$set"])
        body: dict[str, Any] = {"title": "ok", "$set": {"x": 1}}
        ctx.sanitize_body(body)
        assert "$set" not in body
        assert "title" in body

    def test_normal_keys_not_affected(self):
        ctx = self._make_ctx()
        body = {"title": "ok", "status": "active", "count": 42}
        ctx.sanitize_body(body)
        assert body == {"title": "ok", "status": "active", "count": 42}


# ============================================================================
# QueryValidator filter injection
# ============================================================================


@pytest.mark.unit
class TestQueryValidatorInjection:
    """Attempt to sneak dangerous operators past QueryValidator."""

    def test_where_in_nested_or(self):
        validator = QueryValidator()
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter({"$or": [{"status": "active"}, {"$where": "true"}]})

    def test_function_in_deeply_nested_dict(self):
        validator = QueryValidator()
        deep = {"a": {"b": {"c": {"$function": {"body": "return 1"}}}}}
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_filter(deep)

    def test_accumulator_in_pipeline_stage(self):
        validator = QueryValidator()
        pipeline = [{"$group": {"_id": None, "total": {"$accumulator": {"init": "0"}}}}]
        with pytest.raises(QueryValidationError, match="Dangerous operator"):
            validator.validate_pipeline(pipeline)

    def test_eval_blocked(self):
        validator = QueryValidator()
        with pytest.raises(QueryValidationError):
            validator.validate_filter({"$eval": "db.users.find()"})

    def test_custom_dangerous_operator_also_blocked(self):
        validator = QueryValidator(dangerous_operators={"$custom_bad"})
        with pytest.raises(QueryValidationError):
            validator.validate_filter({"$custom_bad": "payload"})
        with pytest.raises(QueryValidationError):
            validator.validate_filter({"$where": "true"})

    def test_max_depth_exceeded(self):
        validator = QueryValidator(max_depth=3)
        deep = {"a": {"b": {"c": {"d": {"e": "too deep"}}}}}
        with pytest.raises(QueryValidationError, match="nesting depth"):
            validator.validate_filter(deep)

    def test_regex_redos_blocked(self):
        validator = QueryValidator(max_regex_complexity=5)
        pattern = "(a+)+" * 10
        with pytest.raises(QueryValidationError, match="complexity"):
            validator.validate_regex(pattern)

    def test_regex_length_blocked(self):
        validator = QueryValidator(max_regex_length=50)
        pattern = "a" * 100
        with pytest.raises(QueryValidationError, match="length"):
            validator.validate_regex(pattern)
