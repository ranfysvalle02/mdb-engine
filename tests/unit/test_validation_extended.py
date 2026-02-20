"""Tests for mdb_engine.core.validation — identifier, scope, and text validators."""

from __future__ import annotations

import pytest

from mdb_engine.core.validation import (
    InputValidationError,
    validate_edge_id,
    validate_node_id,
    validate_scope,
    validate_session_id,
    validate_text_content,
)

# ---------------------------------------------------------------------------
# validate_session_id
# ---------------------------------------------------------------------------


class TestValidateSessionId:
    def test_valid_uuid(self):
        assert validate_session_id("550e8400-e29b-41d4-a716-446655440000") is not None

    def test_valid_alphanumeric(self):
        assert validate_session_id("session_123") == "session_123"

    def test_valid_with_dots_and_colons(self):
        assert validate_session_id("sess:abc.def") == "sess:abc.def"

    def test_valid_with_at_sign(self):
        assert validate_session_id("user@session") == "user@session"

    def test_rejects_none(self):
        with pytest.raises(InputValidationError, match="must not be None"):
            validate_session_id(None)

    def test_allows_none_when_flag_set(self):
        assert validate_session_id(None, allow_none=True) is None

    def test_rejects_empty_string(self):
        with pytest.raises(InputValidationError, match="must not be empty"):
            validate_session_id("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(InputValidationError, match="must not be empty"):
            validate_session_id("   ")

    def test_rejects_invalid_chars_dollar(self):
        with pytest.raises(InputValidationError, match="invalid characters"):
            validate_session_id("$inject")

    def test_rejects_invalid_chars_braces(self):
        with pytest.raises(InputValidationError, match="invalid characters"):
            validate_session_id("{bad}")

    def test_rejects_too_long(self):
        long_id = "a" * 600
        with pytest.raises(InputValidationError, match="exceeds maximum length"):
            validate_session_id(long_id)

    def test_rejects_non_string(self):
        with pytest.raises(InputValidationError, match="must be a string"):
            validate_session_id(12345)


# ---------------------------------------------------------------------------
# validate_node_id
# ---------------------------------------------------------------------------


class TestValidateNodeId:
    def test_valid_id(self):
        assert validate_node_id("node_42") == "node_42"

    def test_rejects_none(self):
        with pytest.raises(InputValidationError):
            validate_node_id(None)

    def test_allows_none_when_flag_set(self):
        assert validate_node_id(None, allow_none=True) is None

    def test_rejects_invalid_chars(self):
        with pytest.raises(InputValidationError, match="invalid characters"):
            validate_node_id("node;drop")


# ---------------------------------------------------------------------------
# validate_edge_id
# ---------------------------------------------------------------------------


class TestValidateEdgeId:
    def test_valid_id(self):
        assert validate_edge_id("edge-99") == "edge-99"

    def test_rejects_none(self):
        with pytest.raises(InputValidationError):
            validate_edge_id(None)

    def test_allows_none_when_flag_set(self):
        assert validate_edge_id(None, allow_none=True) is None

    def test_rejects_invalid_chars(self):
        with pytest.raises(InputValidationError, match="invalid characters"):
            validate_edge_id('edge"bad')


# ---------------------------------------------------------------------------
# validate_scope
# ---------------------------------------------------------------------------


class TestValidateScope:
    @pytest.mark.parametrize("scope", ["user", "shared", "app", "family"])
    def test_accepts_valid_scopes(self, scope):
        assert validate_scope(scope) == scope

    def test_case_insensitive(self):
        assert validate_scope("USER") == "user"
        assert validate_scope("Shared") == "shared"
        assert validate_scope("APP") == "app"
        assert validate_scope("FAMILY") == "family"

    def test_strips_whitespace(self):
        assert validate_scope("  user  ") == "user"

    def test_rejects_invalid_scope(self):
        with pytest.raises(InputValidationError, match="must be one of"):
            validate_scope("global")

    def test_rejects_none(self):
        with pytest.raises(InputValidationError, match="must not be None"):
            validate_scope(None)

    def test_allows_none_when_flag_set(self):
        assert validate_scope(None, allow_none=True) is None

    def test_rejects_non_string(self):
        with pytest.raises(InputValidationError, match="must be a string"):
            validate_scope(42)


# ---------------------------------------------------------------------------
# validate_text_content
# ---------------------------------------------------------------------------


class TestValidateTextContent:
    def test_accepts_normal_text(self):
        assert validate_text_content("Hello, world!") == "Hello, world!"

    def test_rejects_null_bytes(self):
        with pytest.raises(InputValidationError, match="null bytes"):
            validate_text_content("hello\x00world")

    def test_enforces_max_length(self):
        long_text = "x" * 200
        with pytest.raises(InputValidationError, match="exceeds maximum length"):
            validate_text_content(long_text, max_length=100)

    def test_default_max_length_is_generous(self):
        # 100,000 chars should be fine
        text = "a" * 50_000
        assert validate_text_content(text) == text

    def test_rejects_none(self):
        with pytest.raises(InputValidationError, match="must not be None"):
            validate_text_content(None)

    def test_allows_none_when_flag_set(self):
        assert validate_text_content(None, allow_none=True) is None

    def test_rejects_non_string(self):
        with pytest.raises(InputValidationError, match="must be a string"):
            validate_text_content(42)

    def test_custom_field_name_in_error(self):
        with pytest.raises(InputValidationError, match="query"):
            validate_text_content(None, field_name="query")
