"""
Unit tests for global error handlers.

Tests that each MongoDBEngineError subclass is mapped to the correct HTTP
status code and returns structured JSON with the expected fields.
"""

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from mdb_engine.exceptions import (
    ConfigurationError,
    InitializationError,
    ManifestValidationError,
    MongoDBEngineError,
    QueryValidationError,
    ResourceLimitExceeded,
)
from mdb_engine.observability.error_handlers import (
    handle_engine_error,
    register_error_handlers,
)


def _fake_request() -> Request:
    """Build a minimal Starlette Request for unit-testing the handler directly."""
    scope = {"type": "http", "method": "GET", "path": "/test"}
    return Request(scope)


class TestHandleEngineErrorStatusCodes:
    """Verify the correct HTTP status code for each exception type."""

    @pytest.mark.asyncio
    async def test_query_validation_error_returns_400(self):
        exc = QueryValidationError("bad filter", query_type="filter")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_manifest_validation_error_returns_422(self):
        exc = ManifestValidationError("missing slug")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_resource_limit_error_returns_429(self):
        exc = ResourceLimitExceeded("too many", limit_type="rate")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_configuration_error_returns_500(self):
        exc = ConfigurationError("bad config", config_key="db_name")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_initialization_error_returns_503(self):
        exc = InitializationError("cannot connect")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_generic_engine_error_returns_500(self):
        exc = MongoDBEngineError("something broke")
        resp = await handle_engine_error(_fake_request(), exc)
        assert resp.status_code == 500


class TestHandleEngineErrorBody:
    """Verify the JSON body structure."""

    @pytest.mark.asyncio
    async def test_body_contains_error_and_message(self):
        exc = QueryValidationError("invalid operator", operator="$where")
        resp = await handle_engine_error(_fake_request(), exc)
        body = resp.body.decode()
        assert "QueryValidationError" in body
        assert "invalid operator" in body

    @pytest.mark.asyncio
    async def test_error_includes_context(self):
        exc = MongoDBEngineError("oops", context={"key": "val"})
        resp = await handle_engine_error(_fake_request(), exc)
        body = resp.body.decode()
        assert '"context"' in body
        assert '"key"' in body
        assert '"val"' in body

    @pytest.mark.asyncio
    async def test_error_without_context_omits_field(self):
        exc = MongoDBEngineError("oops")
        resp = await handle_engine_error(_fake_request(), exc)
        body = resp.body.decode()
        assert '"context"' not in body


class TestRegisterErrorHandlers:
    """Verify that register_error_handlers wires the handler onto an app."""

    def test_register_error_handlers_on_app(self):
        app = MagicMock()
        register_error_handlers(app)
        app.add_exception_handler.assert_called_once_with(MongoDBEngineError, handle_engine_error)
