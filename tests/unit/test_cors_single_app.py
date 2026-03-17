"""
Unit tests for CORS middleware in single-app (create_app) mode.

Verifies that _add_cors_middleware correctly reads the manifest ``cors``
config, merges it with CORS_DEFAULTS, and adds Starlette's CORSMiddleware
to the FastAPI app.
"""

from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from mdb_engine.auth.config_defaults import CORS_DEFAULTS
from mdb_engine.core.fastapi_app import FastAPIAppMixin


def _make_mixin() -> FastAPIAppMixin:
    mixin = FastAPIAppMixin.__new__(FastAPIAppMixin)
    mixin._connection_manager = MagicMock()
    return mixin


def _has_cors_middleware(app: FastAPI) -> bool:
    return any(getattr(mw, "cls", None) is CORSMiddleware for mw in app.user_middleware)


def _get_cors_middleware_kwargs(app: FastAPI) -> dict | None:
    for mw in app.user_middleware:
        if getattr(mw, "cls", None) is CORSMiddleware:
            return mw.kwargs
    return None


class TestAddCorsMiddlewareSkip:
    """Cases where CORS middleware should NOT be added."""

    def test_no_cors_key_in_manifest(self):
        app = FastAPI()
        _make_mixin()._add_cors_middleware(app, "test", {})
        assert not _has_cors_middleware(app)

    def test_cors_enabled_false(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": False}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)
        assert not _has_cors_middleware(app)

    def test_cors_empty_object(self):
        app = FastAPI()
        manifest = {"cors": {}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)
        assert not _has_cors_middleware(app)


class TestAddCorsMiddlewareApplied:
    """Cases where CORS middleware SHOULD be added."""

    def test_cors_enabled_true_minimal(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)
        assert _has_cors_middleware(app)

    def test_sets_app_state_cors_config(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)
        assert hasattr(app.state, "cors_config")
        assert app.state.cors_config["enabled"] is True

    def test_merges_with_defaults(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        cfg = app.state.cors_config
        assert cfg["allow_origins"] == CORS_DEFAULTS["allow_origins"]
        assert cfg["allow_credentials"] == CORS_DEFAULTS["allow_credentials"]
        assert cfg["max_age"] == CORS_DEFAULTS["max_age"]

    def test_custom_origins_override_defaults(self):
        app = FastAPI()
        origins = ["https://example.com", "http://localhost:3000"]
        manifest = {"cors": {"enabled": True, "allow_origins": origins}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["allow_origins"] == origins

    def test_custom_methods(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "allow_methods": ["GET", "POST"]}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["allow_methods"] == ["GET", "POST"]

    def test_custom_headers(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "allow_headers": ["Authorization", "X-Custom"]}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["allow_headers"] == ["Authorization", "X-Custom"]

    def test_allow_credentials(self):
        app = FastAPI()
        origins = ["https://example.com"]
        manifest = {"cors": {"enabled": True, "allow_origins": origins, "allow_credentials": True}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["allow_credentials"] is True

    def test_max_age(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "max_age": 7200}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["max_age"] == 7200

    def test_expose_headers(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "expose_headers": ["X-Request-Id", "X-Total-Count"]}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["expose_headers"] == ["X-Request-Id", "X-Total-Count"]

    def test_expose_headers_omitted_when_empty(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert "expose_headers" not in kwargs

    def test_wildcard_origins(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "allow_origins": ["*"]}}
        _make_mixin()._add_cors_middleware(app, "test", manifest)

        kwargs = _get_cors_middleware_kwargs(app)
        assert kwargs["allow_origins"] == ["*"]
        assert kwargs["allow_credentials"] is False


class TestCorsInSetupMiddleware:
    """Verify _setup_middleware calls _add_cors_middleware when manifest is provided."""

    def test_setup_middleware_without_manifest_skips_cors(self):
        app = FastAPI()
        mixin = _make_mixin()
        mixin._setup_middleware(app, "test", {}, "app", False, manifest=None)
        assert not _has_cors_middleware(app)

    def test_setup_middleware_with_cors_manifest_adds_cors(self):
        app = FastAPI()
        manifest = {"cors": {"enabled": True, "allow_origins": ["*"]}}
        mixin = _make_mixin()
        mixin._setup_middleware(app, "test", {}, "app", False, manifest=manifest)
        assert _has_cors_middleware(app)

    def test_setup_middleware_with_manifest_no_cors_key(self):
        app = FastAPI()
        manifest = {"slug": "test", "name": "Test"}
        mixin = _make_mixin()
        mixin._setup_middleware(app, "test", {}, "app", False, manifest=manifest)
        assert not _has_cors_middleware(app)
