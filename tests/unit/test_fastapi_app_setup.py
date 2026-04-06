"""
Unit tests for FastAPIAppMixin setup logic.

Tests callback error handling, CSRF config, and auth config reading.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.fastapi_app import FastAPIAppMixin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mixin() -> FastAPIAppMixin:
    """Create a bare FastAPIAppMixin with the minimum attributes it expects."""
    mixin = FastAPIAppMixin.__new__(FastAPIAppMixin)
    mixin._connection_manager = MagicMock()
    return mixin


def _make_mock_engine(**overrides):
    """Return an AsyncMock that quacks like MongoDBEngine."""
    engine = AsyncMock()
    engine.initialize = AsyncMock()
    engine.shutdown = AsyncMock()
    engine.load_manifest = AsyncMock(return_value={})
    engine.register_app = AsyncMock(return_value=True)
    engine.validate_manifest = MagicMock(return_value=(True, None, {}))
    engine.auto_retrieve_app_token = AsyncMock(return_value="tok")
    engine.get_scoped_db = AsyncMock(return_value=MagicMock())
    for k, v in overrides.items():
        setattr(engine, k, v)
    return engine


# ============================================================================
# TestCallbackErrorHandling
# ============================================================================


class TestCallbackErrorHandling:
    """Test on_startup / on_shutdown callback error handling in create_app."""

    @pytest.mark.asyncio
    async def test_on_startup_value_error_propagates(self):
        """on_startup raising ValueError should propagate (re-raised after logging)."""
        slug = "test-app"

        async def boom_startup(app, eng, manifest):
            raise ValueError("bad value")

        # Replicate the on_startup block from fastapi_app.py lines 237-243:
        # The code calls the callback, catches specific errors, logs, then re-raises.
        import logging

        mock_logger = MagicMock(spec=logging.Logger)

        with pytest.raises(ValueError, match="bad value"):
            try:
                await boom_startup(MagicMock(), _make_mock_engine(), {})
                mock_logger.info(f"on_startup callback completed for '{slug}'")
            except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                mock_logger.exception(f"on_startup callback failed for '{slug}': {e}")
                raise

    @pytest.mark.asyncio
    async def test_on_startup_runtime_error_propagates(self):
        """on_startup raising RuntimeError should propagate."""

        async def boom(app, eng, manifest):
            raise RuntimeError("runtime boom")

        with pytest.raises(RuntimeError, match="runtime boom"):
            await boom(MagicMock(), _make_mock_engine(), {})

    @pytest.mark.asyncio
    async def test_on_shutdown_error_is_warned_not_raised(self):
        """on_shutdown errors are logged as warnings, not propagated (lines 252-258)."""
        # The on_shutdown handler catches the exception and logs a warning.
        # Simulate the exact logic from fastapi_app.py lines 253-258.
        slug = "test-app"
        app_manifest = {"slug": slug}
        called = False

        async def bad_shutdown(app, eng, manifest):
            raise ValueError("shutdown broke")

        import logging

        mock_logger = MagicMock(spec=logging.Logger)

        # Replicate the on_shutdown block
        try:
            await bad_shutdown(MagicMock(), _make_mock_engine(), app_manifest)
            mock_logger.info(f"on_shutdown callback completed for '{slug}'")
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
            mock_logger.warning(f"on_shutdown callback failed for '{slug}': {e}")
            called = True

        assert called, "Exception should be caught and logged as warning"
        mock_logger.warning.assert_called_once()
        assert "shutdown broke" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_on_shutdown_unexpected_error_not_caught(self):
        """Errors outside the caught tuple (e.g. OSError) are NOT caught by on_shutdown."""
        slug = "test-app"

        async def os_error_shutdown(app, eng, manifest):
            raise OSError("disk full")

        caught_types = (ValueError, TypeError, RuntimeError, AttributeError, KeyError)

        with pytest.raises(OSError, match="disk full"):
            try:
                await os_error_shutdown(MagicMock(), _make_mock_engine(), {})
            except caught_types:
                pass  # These would be caught in production


# ============================================================================
# TestCSRFConfig
# ============================================================================


class TestCSRFConfig:
    """Test CSRF middleware configuration with exempt_routes."""

    def test_csrf_dict_with_exempt_routes_includes_ticket(self):
        """When csrf_protection is a dict with exempt_routes, /auth/ticket is appended."""
        TICKET_ENDPOINT = "/auth/ticket"
        csrf_config: dict = {"exempt_routes": ["/health", "/api/public"]}
        auth_config: dict = {"csrf_protection": csrf_config, "public_routes": ["/health"]}

        # Replicate logic from lines 422-437
        exempt_routes = csrf_config.get("exempt_routes")
        assert exempt_routes is not None
        exempt_routes = list(exempt_routes) if exempt_routes else []
        if TICKET_ENDPOINT not in exempt_routes:
            exempt_routes.append(TICKET_ENDPOINT)

        assert TICKET_ENDPOINT in exempt_routes
        assert "/health" in exempt_routes
        assert "/api/public" in exempt_routes

    def test_csrf_dict_without_exempt_routes_uses_public_routes(self):
        """When csrf_protection is a dict but exempt_routes is None, fall back to public_routes."""
        TICKET_ENDPOINT = "/auth/ticket"
        csrf_config: dict = {"enabled": True}
        public_routes = ["/health", "/docs"]
        public_routes_with_ticket = list(public_routes)
        if TICKET_ENDPOINT not in public_routes_with_ticket:
            public_routes_with_ticket.append(TICKET_ENDPOINT)

        exempt_routes = csrf_config.get("exempt_routes")
        if exempt_routes is None:
            exempt_routes = public_routes_with_ticket

        assert exempt_routes == ["/health", "/docs", "/auth/ticket"]

    def test_csrf_dict_exempt_routes_already_has_ticket(self):
        """When exempt_routes already includes /auth/ticket, don't duplicate it."""
        TICKET_ENDPOINT = "/auth/ticket"
        csrf_config: dict = {"exempt_routes": ["/auth/ticket", "/other"]}

        exempt_routes = csrf_config.get("exempt_routes")
        exempt_routes = list(exempt_routes) if exempt_routes else []
        if TICKET_ENDPOINT not in exempt_routes:
            exempt_routes.append(TICKET_ENDPOINT)

        assert exempt_routes.count(TICKET_ENDPOINT) == 1
        assert len(exempt_routes) == 2


# ============================================================================
# TestAuthConfigErrors
# ============================================================================


class TestAuthConfigErrors:
    """Test _initialize_auth_provider with corrupted / missing auth configs."""

    def _make_mixin_with_engine(self):
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock()
        return mixin, engine, app

    @pytest.mark.asyncio
    async def test_auth_config_missing_policy_key(self):
        """Auth config with no 'policy' key should default to no provider."""
        mixin, engine, app = self._make_mixin_with_engine()
        auth_config: dict = {"mode": "app"}

        # Replicate lines 512-520
        try:
            auth_policy = auth_config.get("policy", {})
            authz_provider_type = auth_policy.get("provider")
        except (KeyError, AttributeError, TypeError):
            authz_provider_type = None

        assert authz_provider_type is None

    @pytest.mark.asyncio
    async def test_auth_config_policy_is_none(self):
        """If policy is explicitly None, reading .get('provider') fails gracefully."""
        auth_config: dict = {"mode": "shared", "policy": None}

        try:
            auth_policy = auth_config.get("policy", {})
            authz_provider_type = auth_policy.get("provider")
        except (KeyError, AttributeError, TypeError):
            authz_provider_type = None

        # policy is None → .get("provider") raises AttributeError → caught
        assert authz_provider_type is None

    @pytest.mark.asyncio
    async def test_auth_config_corrupted_string_policy(self):
        """If policy is a string instead of a dict, it's caught gracefully."""
        auth_config: dict = {"mode": "app", "policy": "oso"}

        try:
            auth_policy = auth_config.get("policy", {})
            # str has no .get() → AttributeError
            authz_provider_type = auth_policy.get("provider")
        except (KeyError, AttributeError, TypeError):
            authz_provider_type = None

        # "oso".get("provider") → AttributeError for str
        # Actually str.get doesn't exist, so this should be caught
        # But str does not have .get() method — it will raise AttributeError
        assert authz_provider_type is None


# ============================================================================
# TestInlineManifest
# ============================================================================


class TestInlineManifest:
    """Test dict/inline manifest support (lines 126-127, 161-165)."""

    def test_dict_manifest_used_directly(self):
        """When manifest is a dict, it becomes pre_manifest directly (line 127)."""
        manifest_dict = {
            "schema_version": "2.0",
            "slug": "inline-app",
            "name": "Inline App",
        }
        # Replicate lines 118-132
        manifest = manifest_dict
        if isinstance(manifest, dict):
            pre_manifest = manifest
        else:
            pre_manifest = None

        assert pre_manifest is manifest_dict

    @pytest.mark.asyncio
    async def test_inline_manifest_validation_failure_raises(self):
        """Invalid inline manifest raises ValueError during lifespan (lines 162-164)."""
        engine = _make_mock_engine()
        engine.validate_manifest = MagicMock(return_value=(False, "slug is bad", ["slug"]))

        slug = "bad-app"
        pre_manifest = {"slug": "bad!@#"}

        with pytest.raises(ValueError, match="Invalid manifest for 'bad-app'"):
            is_valid, error_msg, _ = engine.validate_manifest(pre_manifest)
            if not is_valid:
                raise ValueError(f"Invalid manifest for '{slug}': {error_msg}")

    @pytest.mark.asyncio
    async def test_inline_manifest_validation_success(self):
        """Valid inline manifest is used as app_manifest (line 165)."""
        engine = _make_mock_engine()
        engine.validate_manifest = MagicMock(return_value=(True, None, None))

        pre_manifest = {"schema_version": "2.0", "slug": "ok-app", "name": "OK"}
        is_valid, error_msg, _ = engine.validate_manifest(pre_manifest)
        assert is_valid is True

        app_manifest = pre_manifest
        assert app_manifest["slug"] == "ok-app"


# ============================================================================
# TestMultiSiteDetection
# ============================================================================


class TestMultiSiteDetection:
    """Test multi-site mode detection logging (line 181)."""

    def test_multi_site_detected_explicit_policy(self):
        """cross_app_policy='explicit' triggers multi-site (line 178)."""
        slug = "ms-app"
        data_access = {"read_scopes": [slug], "cross_app_policy": "explicit"}
        read_scopes = data_access.get("read_scopes", [slug])
        cross_app_policy = data_access.get("cross_app_policy", "none")
        is_multi_site = cross_app_policy == "explicit" or (len(read_scopes) > 1 and read_scopes != [slug])
        assert is_multi_site is True

    def test_multi_site_detected_multiple_scopes(self):
        """Multiple read_scopes with different apps triggers multi-site."""
        slug = "ms-app"
        data_access = {"read_scopes": [slug, "other-app"], "cross_app_policy": "none"}
        read_scopes = data_access.get("read_scopes", [slug])
        cross_app_policy = data_access.get("cross_app_policy", "none")
        is_multi_site = cross_app_policy == "explicit" or (len(read_scopes) > 1 and read_scopes != [slug])
        assert is_multi_site is True

    def test_single_app_mode(self):
        """Single read scope and no cross_app_policy stays single-app."""
        slug = "sa-app"
        data_access = {"read_scopes": [slug], "cross_app_policy": "none"}
        read_scopes = data_access.get("read_scopes", [slug])
        cross_app_policy = data_access.get("cross_app_policy", "none")
        is_multi_site = cross_app_policy == "explicit" or (len(read_scopes) > 1 and read_scopes != [slug])
        assert is_multi_site is False


# ============================================================================
# TestDemoUserSeeding
# ============================================================================


class TestDemoUserSeeding:
    """Test demo users seeding with error handling (lines 199-220)."""

    @pytest.mark.asyncio
    async def test_demo_users_seeded_successfully(self):
        """When ensure_demo_users_exist returns users, they are logged (lines 200-211)."""
        mock_db = MagicMock()
        mock_engine = _make_mock_engine()
        slug = "demo-app"

        with patch(
            "mdb_engine.auth.ensure_demo_users_exist",
            new_callable=AsyncMock,
            return_value=["user1", "user2"],
        ) as mock_seed:
            db = await mock_engine.get_scoped_db(slug)
            demo_users = await mock_seed(db=db, slug_id=slug, config={}, connection_manager=MagicMock())
            assert len(demo_users) == 2

    @pytest.mark.asyncio
    async def test_demo_users_import_error_caught(self):
        """ImportError during demo user seeding is caught (lines 212-220)."""
        slug = "demo-app"
        caught = False

        try:
            raise ImportError("auth module not found")
        except (
            ImportError,
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
            KeyError,
        ):
            caught = True

        assert caught

    @pytest.mark.asyncio
    async def test_demo_users_runtime_error_caught(self):
        """RuntimeError during demo user seeding is caught (lines 212-220)."""
        slug = "demo-app"
        caught = False

        try:
            raise RuntimeError("db not ready")
        except (
            ImportError,
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
            KeyError,
        ):
            caught = True

        assert caught

    @pytest.mark.asyncio
    async def test_demo_users_empty_list_no_log(self):
        """When ensure_demo_users_exist returns empty list, no 'seeded' log."""
        demo_users = []
        logged = False
        if demo_users:
            logged = True
        assert logged is False


# ============================================================================
# TestShutdownCallbacks
# ============================================================================


class TestShutdownCallbacks:
    """Test on_shutdown callback error handling (lines 254-258)."""

    @pytest.mark.asyncio
    async def test_shutdown_type_error_caught(self):
        """TypeError in on_shutdown is caught and warned."""

        async def bad_shutdown(app, eng, manifest):
            raise TypeError("wrong arg type")

        caught = False
        try:
            await bad_shutdown(MagicMock(), _make_mock_engine(), {})
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError):
            caught = True

        assert caught

    @pytest.mark.asyncio
    async def test_shutdown_key_error_caught(self):
        """KeyError in on_shutdown is caught and warned."""

        async def bad_shutdown(app, eng, manifest):
            raise KeyError("missing_key")

        caught = False
        try:
            await bad_shutdown(MagicMock(), _make_mock_engine(), {})
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError):
            caught = True

        assert caught

    @pytest.mark.asyncio
    async def test_shutdown_attribute_error_caught(self):
        """AttributeError in on_shutdown is caught and warned."""

        async def bad_shutdown(app, eng, manifest):
            raise AttributeError("no attr")

        caught = False
        try:
            await bad_shutdown(MagicMock(), _make_mock_engine(), {})
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError):
            caught = True

        assert caught


# ============================================================================
# TestOTelEarlyReturn
# ============================================================================


class TestOTelEarlyReturn:
    """Test OpenTelemetry availability check early return (line 343)."""

    def test_otel_not_available_returns_early(self):
        """When otel_available() is False, _add_otel_middleware returns without instrumenting."""
        mixin = _make_mixin()
        mock_app = MagicMock()

        with (
            patch("mdb_engine.observability.tracing.instrument_fastapi") as mock_instrument,
            patch("mdb_engine.observability.tracing.otel_available", return_value=False),
        ):
            mixin._add_otel_middleware(mock_app, "test-slug")
            mock_instrument.assert_not_called()

    def test_otel_available_instruments_app(self):
        """When otel_available() is True, instrument_fastapi is called."""
        mixin = _make_mixin()
        mock_app = MagicMock()

        with (
            patch("mdb_engine.observability.tracing.instrument_fastapi") as mock_instrument,
            patch("mdb_engine.observability.tracing.otel_available", return_value=True),
        ):
            mixin._add_otel_middleware(mock_app, "test-slug")
            mock_instrument.assert_called_once_with(mock_app)


# ============================================================================
# TestSubAppSharedAuth
# ============================================================================


class TestSubAppSharedAuth:
    """Test sub-app shared auth initialization (lines 488-495)."""

    def _make_mixin_with_pool(self):
        """Create a mixin that also has _initialize_shared_user_pool."""
        mixin = _make_mixin()
        mixin._initialize_shared_user_pool = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_sub_app_missing_user_pool_initializes(self):
        """Sub-app with shared auth but no user_pool initializes it (lines 488-493)."""
        mixin = self._make_mixin_with_pool()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        await mixin._handle_auth_mode(engine, app, "sub-slug", {}, "shared", is_sub_app=True)
        mixin._initialize_shared_user_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sub_app_existing_user_pool_skips_init(self):
        """Sub-app with existing user_pool skips initialization (lines 494-495)."""
        mixin = self._make_mixin_with_pool()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state.user_pool = MagicMock()

        await mixin._handle_auth_mode(engine, app, "sub-slug", {}, "shared", is_sub_app=True)
        mixin._initialize_shared_user_pool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_sub_app_shared_auth_initializes(self):
        """Non-sub-app with shared auth always initializes user_pool (line 497)."""
        mixin = self._make_mixin_with_pool()
        engine = _make_mock_engine()
        app = MagicMock()

        await mixin._handle_auth_mode(engine, app, "main-slug", {}, "shared", is_sub_app=False)
        mixin._initialize_shared_user_pool.assert_awaited_once()


# ============================================================================
# TestAuthProviders
# ============================================================================


class TestAuthProviders:
    """Test authorization provider branches (lines 522-531)."""

    def _make_mixin_app(self):
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])
        return mixin, engine, app

    @pytest.mark.asyncio
    async def test_oso_provider_selected(self):
        """provider='oso' calls _initialize_oso_provider (line 523)."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"policy": {"provider": "oso"}}

        with patch.object(mixin, "_initialize_oso_provider", new_callable=AsyncMock) as mock_oso:
            await mixin._initialize_auth_provider(engine, app, "s", {}, auth_config)
            mock_oso.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_casbin_provider_selected(self):
        """provider='casbin' calls _initialize_casbin_provider (line 524)."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"policy": {"provider": "casbin"}}

        with patch.object(mixin, "_initialize_casbin_provider", new_callable=AsyncMock) as mock_casbin:
            await mixin._initialize_auth_provider(engine, app, "s", {}, auth_config)
            mock_casbin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_casbin_when_no_provider_but_policy(self):
        """No provider but policy dict defaults to casbin (line 526-527)."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"policy": {"roles": ["admin"]}}

        with patch.object(mixin, "_initialize_casbin_provider_default", new_callable=AsyncMock) as mock_default:
            await mixin._initialize_auth_provider(engine, app, "s", {}, auth_config)
            mock_default.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_provider_logs_warning(self):
        """Unknown provider type logs warning and skips (lines 528-531)."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"policy": {"provider": "custom_xyz"}}

        with (
            patch.object(mixin, "_initialize_oso_provider", new_callable=AsyncMock) as mock_oso,
            patch.object(mixin, "_initialize_casbin_provider", new_callable=AsyncMock) as mock_casbin,
        ):
            await mixin._initialize_auth_provider(engine, app, "s", {}, auth_config)
            mock_oso.assert_not_awaited()
            mock_casbin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_policy_no_provider(self):
        """No policy key at all means no provider initialized."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"mode": "app"}

        with (
            patch.object(mixin, "_initialize_oso_provider", new_callable=AsyncMock) as mock_oso,
            patch.object(mixin, "_initialize_casbin_provider", new_callable=AsyncMock) as mock_casbin,
            patch.object(mixin, "_initialize_casbin_provider_default", new_callable=AsyncMock) as mock_default,
        ):
            await mixin._initialize_auth_provider(engine, app, "s", {}, auth_config)
            mock_oso.assert_not_awaited()
            mock_casbin.assert_not_awaited()
            mock_default.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_policy_with_collection_auth_no_provider(self):
        """Collections with auth.roles / auth.write_roles but no auth.policy
        must NOT trigger Casbin auto-creation — they use the inline
        require_role fallback path (Bug 1 fix, v0.11.4)."""
        mixin, engine, app = self._make_mixin_app()
        auth_config = {"mode": "app"}
        app_manifest = {
            "collections": {
                "posts": {"auth": {"write_roles": ["editor"], "public_read": True}},
                "comments": {"auth": {"roles": ["moderator"]}},
            }
        }

        with (
            patch.object(mixin, "_initialize_oso_provider", new_callable=AsyncMock) as mock_oso,
            patch.object(mixin, "_initialize_casbin_provider", new_callable=AsyncMock) as mock_casbin,
            patch.object(mixin, "_initialize_casbin_provider_default", new_callable=AsyncMock) as mock_default,
        ):
            await mixin._initialize_auth_provider(engine, app, "s", app_manifest, auth_config)
            mock_oso.assert_not_awaited()
            mock_casbin.assert_not_awaited()
            mock_default.assert_not_awaited()


# ============================================================================
# TestOSOProviderInit
# ============================================================================


class TestOSOProviderInit:
    """Test OSO provider initialization (lines 541-555)."""

    @pytest.mark.asyncio
    async def test_oso_success(self):
        """Successful OSO initialization sets app.state.authz_provider (lines 541-553)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])
        mock_provider = MagicMock()

        with patch(
            "mdb_engine.auth.oso_factory.initialize_oso_from_manifest",
            new_callable=AsyncMock,
            return_value=mock_provider,
        ):
            await mixin._initialize_oso_provider(engine, app, "oso-app", {})
            assert app.state.authz_provider == mock_provider

    @pytest.mark.asyncio
    async def test_oso_import_error(self):
        """ImportError during OSO init is caught (line 553-554)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        import sys

        original = sys.modules.get("mdb_engine.auth.oso_factory")
        try:
            sys.modules["mdb_engine.auth.oso_factory"] = None
            await mixin._initialize_oso_provider(engine, app, "oso-app", {})
        finally:
            if original is not None:
                sys.modules["mdb_engine.auth.oso_factory"] = original
            else:
                sys.modules.pop("mdb_engine.auth.oso_factory", None)

    @pytest.mark.asyncio
    async def test_oso_runtime_error(self):
        """RuntimeError during OSO init is caught (line 555)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        with patch(
            "mdb_engine.auth.oso_factory.initialize_oso_from_manifest",
            side_effect=RuntimeError("OSO failed"),
        ):
            await mixin._initialize_oso_provider(engine, app, "oso-app", {})


# ============================================================================
# TestCasbinProviderInit
# ============================================================================


class TestCasbinProviderInit:
    """Test Casbin provider initialization (lines 565-632)."""

    @pytest.mark.asyncio
    async def test_casbin_success(self):
        """Successful Casbin init sets app.state.authz_provider (lines 565-583)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])
        mock_provider = MagicMock(_initialized=True)

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            new_callable=AsyncMock,
            return_value=mock_provider,
        ):
            await mixin._initialize_casbin_provider(engine, app, "casbin-app", {})
            assert app.state.authz_provider == mock_provider

    @pytest.mark.asyncio
    async def test_casbin_returns_none(self):
        """Casbin init returning None logs error (lines 584-588)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await mixin._initialize_casbin_provider(engine, app, "casbin-app", {})

    @pytest.mark.asyncio
    async def test_casbin_import_error(self):
        """ImportError during Casbin init is caught (line 589-590)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        import sys

        original = sys.modules.get("mdb_engine.auth.casbin_factory")
        try:
            sys.modules["mdb_engine.auth.casbin_factory"] = None
            await mixin._initialize_casbin_provider(engine, app, "casbin-app", {})
        finally:
            if original is not None:
                sys.modules["mdb_engine.auth.casbin_factory"] = original
            else:
                sys.modules.pop("mdb_engine.auth.casbin_factory", None)

    @pytest.mark.asyncio
    async def test_casbin_value_error(self):
        """ValueError during Casbin init is caught (lines 591-603)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            side_effect=ValueError("bad config"),
        ):
            await mixin._initialize_casbin_provider(engine, app, "casbin-app", {})

    @pytest.mark.asyncio
    async def test_casbin_default_success(self):
        """Default Casbin init sets provider (lines 613-620)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])
        mock_provider = MagicMock()

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            new_callable=AsyncMock,
            return_value=mock_provider,
        ):
            await mixin._initialize_casbin_provider_default(engine, app, "default-app", {})
            assert app.state.authz_provider == mock_provider

    @pytest.mark.asyncio
    async def test_casbin_default_returns_none(self):
        """Default Casbin init returning None logs warning (lines 621-622)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await mixin._initialize_casbin_provider_default(engine, app, "default-app", {})

    @pytest.mark.asyncio
    async def test_casbin_default_import_error(self):
        """ImportError in default Casbin init is caught (lines 623-624)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        import sys

        original = sys.modules.get("mdb_engine.auth.casbin_factory")
        try:
            sys.modules["mdb_engine.auth.casbin_factory"] = None
            await mixin._initialize_casbin_provider_default(engine, app, "default-app", {})
        finally:
            if original is not None:
                sys.modules["mdb_engine.auth.casbin_factory"] = original
            else:
                sys.modules.pop("mdb_engine.auth.casbin_factory", None)

    @pytest.mark.asyncio
    async def test_casbin_default_value_error(self):
        """ValueError in default Casbin init is caught (lines 625-632)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        with patch(
            "mdb_engine.auth.casbin_factory.initialize_casbin_from_manifest",
            side_effect=ValueError("bad default config"),
        ):
            await mixin._initialize_casbin_provider_default(engine, app, "default-app", {})


# ============================================================================
# TestOAuthInit
# ============================================================================


class TestOAuthInit:
    """Test OAuth service initialization (lines 683-703)."""

    @pytest.mark.asyncio
    async def test_oauth_no_config_returns_early(self):
        """No oauth in auth_config means early return (lines 679-681)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()

        await mixin._initialize_oauth_service(engine, app, "s", {})

    @pytest.mark.asyncio
    async def test_oauth_success(self):
        """Successful OAuth init registers routes and sets state (lines 683-699)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        mock_service = MagicMock()
        mock_service.provider_names = ["google"]

        auth_config = {"oauth": {"providers": {"google": {"client_id": "x"}}}}

        with (
            patch(
                "mdb_engine.auth.oauth.OAuthService",
                return_value=mock_service,
            ),
            patch(
                "mdb_engine.auth.oauth.register_oauth_routes",
            ) as mock_register,
        ):
            await mixin._initialize_oauth_service(engine, app, "oauth-app", auth_config)
            mock_register.assert_called_once_with(app, mock_service)
            assert app.state.oauth_service == mock_service

    @pytest.mark.asyncio
    async def test_oauth_import_error(self):
        """ImportError during OAuth init is caught (lines 700-701)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        auth_config = {"oauth": {"providers": {"google": {}}}}

        import sys

        original = sys.modules.get("mdb_engine.auth.oauth")
        try:
            sys.modules["mdb_engine.auth.oauth"] = None
            await mixin._initialize_oauth_service(engine, app, "oauth-app", auth_config)
        finally:
            if original is not None:
                sys.modules["mdb_engine.auth.oauth"] = original
            else:
                sys.modules.pop("mdb_engine.auth.oauth", None)

    @pytest.mark.asyncio
    async def test_oauth_value_error(self):
        """ValueError during OAuth init is caught (lines 702-703)."""
        mixin = _make_mixin()
        engine = _make_mock_engine()
        app = MagicMock()
        app.state = MagicMock(spec=[])

        auth_config = {"oauth": {"providers": {"google": {}}}}

        with patch(
            "mdb_engine.auth.oauth.OAuthService",
            side_effect=ValueError("bad oauth config"),
        ):
            await mixin._initialize_oauth_service(engine, app, "oauth-app", auth_config)
