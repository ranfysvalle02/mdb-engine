"""
Template resolver adversarial tests.

Verifies that {{user.*}}, {{env.*}}, {{doc.*}} templates cannot be
abused for secret leakage, double-resolution, or injection.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mdb_engine.routing.template_resolver import (
    _ENV_DENYLIST,
    resolve_template,
)

# ============================================================================
# Double-resolution prevention
# ============================================================================


@pytest.mark.unit
class TestDoubleResolution:
    """Substituted values containing template syntax must not be re-resolved."""

    def test_user_field_containing_user_template_not_reresolved(self):
        user = {"_id": "real_id", "nickname": "{{user._id}}"}
        result = resolve_template("{{user.nickname}}", user)
        assert result == "{{user._id}}"

    def test_user_field_containing_env_template_not_reresolved(self, monkeypatch):
        monkeypatch.setenv("SAFE_VAR", "leaked")
        user = {"_id": "id", "tag": "{{env.SAFE_VAR}}"}
        result = resolve_template("{{user.tag}}", user)
        assert result == "{{env.SAFE_VAR}}"

    def test_doc_field_containing_user_template_not_reresolved(self):
        user = {"_id": "uid"}
        doc = {"note": "{{user._id}}"}
        result = resolve_template("{{doc.note}}", user, doc=doc)
        assert result == "{{user._id}}"

    def test_nested_dict_from_user_not_template_scanned(self, monkeypatch):
        monkeypatch.setenv("SAFE_VAR", "secret_val")
        user = {
            "_id": "id",
            "profile": {"inner": "{{env.SAFE_VAR}}"},
        }
        result = resolve_template("{{user.profile}}", user)
        assert isinstance(result, dict)
        assert result["inner"] == "{{env.SAFE_VAR}}"


# ============================================================================
# Env denylist
# ============================================================================


@pytest.mark.unit
class TestEnvDenylist:
    """Sensitive env vars must be blocked even if present."""

    @pytest.mark.parametrize("var_name", list(_ENV_DENYLIST))
    def test_denied_env_var_raises_400(self, monkeypatch, var_name: str):
        monkeypatch.setenv(var_name, "super_secret")
        template = f"{{{{env.{var_name}}}}}"
        with pytest.raises(HTTPException) as exc_info:
            resolve_template(template, None)
        assert exc_info.value.status_code == 400
        assert "denied" in exc_info.value.detail.lower()

    def test_non_denied_env_var_works(self, monkeypatch):
        monkeypatch.setenv("MY_APP_SETTING", "hello")
        result = resolve_template("{{env.MY_APP_SETTING}}", None)
        assert result == "hello"

    def test_missing_env_var_raises_400(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            resolve_template("{{env.NONEXISTENT_VAR_XYZ}}", None)
        assert exc_info.value.status_code == 400


# ============================================================================
# Path depth and traversal
# ============================================================================


@pytest.mark.unit
class TestPathTraversal:
    """Template path traversal must respect depth limits."""

    def test_depth_exceeding_max_raises_400(self):
        user = {"a": {"b": {"c": {"d": "deep"}}}}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template("{{user.a.b.c.d}}", user)
        assert exc_info.value.status_code == 400
        assert "too deep" in exc_info.value.detail.lower()

    def test_max_depth_exactly_works(self):
        user = {"a": {"b": {"c": "val"}}}
        result = resolve_template("{{user.a.b.c}}", user)
        assert result == "val"

    def test_nonexistent_path_raises_400(self):
        user = {"_id": "id"}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template("{{user.nonexistent}}", user)
        assert exc_info.value.status_code == 400

    def test_path_resolving_to_none_raises_400(self):
        user = {"_id": "id", "empty_field": None}
        with pytest.raises(HTTPException) as exc_info:
            resolve_template("{{user.empty_field}}", user)
        assert exc_info.value.status_code == 400


# ============================================================================
# Substring and partial templates
# ============================================================================


@pytest.mark.unit
class TestSubstringTemplates:
    """Partial template strings must not be interpolated."""

    def test_prefix_suffix_around_template_stays_literal(self):
        user = {"_id": "id123"}
        result = resolve_template("prefix {{user._id}} suffix", user)
        assert result == "prefix {{user._id}} suffix"

    def test_multiple_templates_in_one_string_stay_literal(self):
        user = {"_id": "id", "name": "alice"}
        result = resolve_template("{{user._id}} and {{user.name}}", user)
        assert result == "{{user._id}} and {{user.name}}"

    def test_empty_string_unchanged(self):
        result = resolve_template("", {"_id": "id"})
        assert result == ""


# ============================================================================
# Operator values in resolved templates
# ============================================================================


@pytest.mark.unit
class TestOperatorValuesInTemplates:
    """User data containing operator-shaped values must stay as literals."""

    def test_user_id_with_ne_operator_becomes_literal(self):
        user = {"_id": {"$ne": None}, "email": "x@x.com"}
        policy = {"owner_id": "{{user._id}}"}
        result = resolve_template(policy, user)
        assert result == {"owner_id": {"$ne": None}}

    def test_resolved_dict_not_reinterpreted(self):
        """Values from user dict are data, not query operators."""
        user = {"_id": "id", "filter_trick": {"$gt": 0}}
        template = {"score": "{{user.filter_trick}}"}
        result = resolve_template(template, user)
        assert result["score"] == {"$gt": 0}


# ============================================================================
# Auth requirement
# ============================================================================


@pytest.mark.unit
class TestUserTemplateAuthRequired:
    """{{user.*}} templates without an authenticated user must fail."""

    def test_user_template_with_no_user_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_template("{{user._id}}", None)
        assert exc_info.value.status_code == 401

    def test_doc_template_without_doc_stays_literal(self):
        result = resolve_template("{{doc.title}}", {"_id": "user"})
        assert result == "{{doc.title}}"

    def test_prev_template_without_prev_stays_literal(self):
        result = resolve_template("{{prev.status}}", {"_id": "user"})
        assert result == "{{prev.status}}"
