"""
FastAPI App Mixin

Provides methods for creating FastAPI applications with proper lifespan management,
middleware setup, and authentication initialization.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .connection import ConnectionManager
    from .engine import MongoDBEngine

logger = logging.getLogger(__name__)


class FastAPIAppMixin:
    """Mixin providing FastAPI application creation and middleware setup.

    Expects the following attributes from the host class (MongoDBEngine):
        _connection_manager: Database connection manager.
    """

    # -- Attributes provided by MongoDBEngine --
    _connection_manager: "ConnectionManager"

    def create_app(
        self,
        slug: str,
        manifest: "Path | dict[str, Any] | None" = None,
        name: str | None = None,
        title: str | None = None,
        on_startup: Callable[["FastAPI", "MongoDBEngine", dict[str, Any]], Awaitable[None]] | None = None,
        on_shutdown: Callable[["FastAPI", "MongoDBEngine", dict[str, Any]], Awaitable[None]] | None = None,
        is_sub_app: bool = False,
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """
        Create a FastAPI application with proper lifespan management.

        This method creates a FastAPI app that:
        1. Initializes the engine on startup (unless is_sub_app=True)
        2. Loads and registers the manifest
        3. Auto-detects multi-site mode from manifest
        4. Auto-configures auth based on manifest auth.mode:
           - "app" (default): Per-app token authentication
           - "shared": Shared user pool with SSO, auto-adds SharedAuthMiddleware
        5. Auto-retrieves app tokens (for "app" mode)
        6. Calls on_startup callback (if provided)
        7. Shuts down the engine on shutdown (calls on_shutdown first if provided)

        Args:
            slug: Application slug (must match manifest slug)
            manifest: Manifest configuration — one of:
                - ``Path`` to a manifest.json file (classic usage)
                - ``dict`` with inline manifest data (no file needed)
                - ``None`` to auto-generate a minimal manifest from *slug* / *name*
            name: Human-readable app name (used when manifest is None to
                auto-generate a minimal manifest). Ignored when manifest is
                provided. Defaults to a title-cased version of *slug*.
            title: FastAPI app title. Defaults to app name from manifest
            on_startup: Optional async callback called after engine initialization.
                       Signature: async def callback(app, engine, manifest) -> None
            on_shutdown: Optional async callback called before engine shutdown.
                        Signature: async def callback(app, engine, manifest) -> None
            is_sub_app: If True, skip engine initialization and lifespan management.
                       Used when mounting as a child app in create_multi_app().
                       Defaults to False for backward compatibility.
            **fastapi_kwargs: Additional arguments passed to FastAPI()

        Returns:
            Configured FastAPI application

        Example — zero-config (no manifest file)::

            engine = MongoDBEngine()
            app = engine.create_app(slug="my_app")

        Example — inline dict manifest::

            engine = MongoDBEngine()
            app = engine.create_app(
                slug="my_app",
                manifest={"schema_version": "2.0", "slug": "my_app", "name": "My App"},
            )

        Example — file-based manifest (classic)::

            engine = MongoDBEngine(mongo_uri=..., db_name=...)
            app = engine.create_app(
                slug="my_app",
                manifest=Path("manifest.json"),
                on_startup=my_startup,
            )

        Auth Modes (configured in manifest.json):
            # Per-app auth (default)
            {"auth": {"mode": "app"}}

            # Shared user pool with SSO
            {"auth": {"mode": "shared", "roles": ["viewer", "editor", "admin"],
                      "require_role": "viewer", "public_routes": ["/health"]}}
        """
        from fastapi import FastAPI

        engine = self

        # --- Resolve manifest into a dict (pre_manifest) -----------------
        manifest_path: Path | None = None

        if manifest is None:
            # Auto-generate minimal manifest
            app_name = name or slug.replace("-", " ").replace("_", " ").title()
            pre_manifest: dict[str, Any] = {
                "schema_version": "2.0",
                "slug": slug,
                "name": app_name,
            }
        elif isinstance(manifest, dict):
            pre_manifest = manifest
        else:
            # Path-based manifest (classic behaviour)
            manifest_path = Path(manifest)
            with open(manifest_path) as f:
                pre_manifest = json.load(f)

        # Extract auth configuration
        auth_config = pre_manifest.get("auth", {})
        auth_mode = auth_config.get("mode", "app")

        # Determine title from pre-loaded manifest or slug
        app_title = title or pre_manifest.get("name", slug)

        # State that will be populated during initialization
        app_manifest: dict[str, Any] = {}
        is_multi_site = False

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """Lifespan context manager for initialization and cleanup."""
            nonlocal app_manifest, is_multi_site

            # Initialize engine (skip if sub-app - parent manages lifecycle)
            if not is_sub_app:
                await engine.initialize()

            # Register WebSocket endpoints
            await self._register_websocket_endpoints(app, engine)

            # Load and register manifest
            if manifest_path is not None:
                app_manifest = await engine.load_manifest(manifest_path)
            else:
                # Inline / auto-generated manifest — validate then use directly
                is_valid, error_msg, _ = await engine.validate_manifest(pre_manifest)
                if not is_valid:
                    raise ValueError(f"Invalid manifest for '{slug}': {error_msg}")
                app_manifest = pre_manifest

            # Configure WebSocket ticket TTL from manifest
            await self._configure_websocket_ticket_ttl(app, app_manifest, slug)

            await engine.register_app(app_manifest)

            # Auto-create indexes for collection-level shortcuts (x-unique, ttl)
            await self._ensure_collection_indexes(engine, slug, app_manifest)

            # Auto-detect multi-site mode from manifest
            data_access = app_manifest.get("data_access", {})
            read_scopes = data_access.get("read_scopes", [slug])
            cross_app_policy = data_access.get("cross_app_policy", "none")

            # Multi-site if: cross_app_policy is "explicit" OR read_scopes has multiple apps
            is_multi_site = cross_app_policy == "explicit" or (len(read_scopes) > 1 and read_scopes != [slug])

            if is_multi_site:
                logger.info(
                    f"Multi-site mode detected for '{slug}': "
                    f"read_scopes={read_scopes}, cross_app_policy={cross_app_policy}"
                )
            else:
                logger.info(f"Single-app mode for '{slug}'")

            # Handle auth based on mode
            await self._handle_auth_mode(engine, app, slug, app_manifest, auth_mode, is_sub_app)

            # Auto-initialize authorization provider from manifest config
            await self._initialize_auth_provider(engine, app, slug, app_manifest, auth_config)

            # Initialize OAuth service if configured in manifest
            await self._initialize_oauth_service(engine, app, slug, auth_config)

            # Auto-seed demo users if configured in manifest
            users_config = auth_config.get("users", {})
            if users_config.get("enabled") and users_config.get("demo_users"):
                try:
                    from ..auth import ensure_demo_users_exist

                    db = await engine.get_scoped_db(slug)
                    demo_users = await ensure_demo_users_exist(
                        db=db,
                        slug_id=slug,
                        config=app_manifest,
                        connection_manager=self._connection_manager,
                    )
                    if demo_users:
                        logger.info(f"Seeded {len(demo_users)} demo user(s) for '{slug}'")
                except (
                    ImportError,
                    ValueError,
                    TypeError,
                    RuntimeError,
                    AttributeError,
                    KeyError,
                ) as e:
                    logger.warning(f"Failed to seed demo users for '{slug}': {e}")

            # Expose engine state on app.state
            app.state.engine = engine
            app.state.app_slug = slug
            app.state.manifest = app_manifest
            app.state.is_multi_site = is_multi_site
            app.state.auth_mode = auth_mode

            # Store initialized services so app code can access them directly
            if engine.initialized:
                app.state.memory_service = engine.get_memory_service(slug)
                app.state.graph_service = engine.get_graph_service(slug)
                app.state.embedding_service = engine.get_embedding_service(slug)
                app.state.llm_service = engine.get_llm_service(slug)

            # Initialize DI container (if not already set)
            from ..di import Container

            if not hasattr(app.state, "container") or app.state.container is None:
                app.state.container = Container()
                logger.debug(f"DI Container initialized for '{slug}'")

            # Start realtime Change Stream watcher (if collections have realtime: true)
            await self._start_realtime(app, engine, slug, app_manifest)

            # Call on_startup callback if provided
            if on_startup:
                try:
                    await on_startup(app, engine, app_manifest)
                    logger.info(f"on_startup callback completed for '{slug}'")
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.exception(f"on_startup callback failed for '{slug}': {e}")
                    raise

            yield

            # Stop realtime watcher before tearing down services
            await self._stop_realtime(app)

            # Flush pending OTel spans before tearing down services
            from ..observability.tracing import shutdown_tracer_provider

            shutdown_tracer_provider()

            # Call on_shutdown callback if provided
            if on_shutdown:
                try:
                    await on_shutdown(app, engine, app_manifest)
                    logger.info(f"on_shutdown callback completed for '{slug}'")
                except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                    logger.warning(f"on_shutdown callback failed for '{slug}': {e}")

            # Shutdown engine (skip if sub-app - parent manages lifecycle)
            if not is_sub_app:
                await engine.shutdown()

        # Create FastAPI app
        app = FastAPI(title=app_title, lifespan=lifespan, **fastapi_kwargs)

        # NOTE: WebSocket ticket endpoint registration is moved to lifespan context manager
        # (after engine.initialize()) because ticket store is only available after initialization.
        # This ensures consistency with create_multi_app() behavior.

        # Auto-register graph stats endpoint when graph is enabled
        _graph_cfg = pre_manifest.get("graph_config", {})
        if _graph_cfg.get("enabled", True):
            try:
                from ..routing.graph_routes import router as _graph_router

                app.include_router(_graph_router)
            except ImportError:
                pass

        # Auto-register CRUD endpoints for collections defined in manifest
        _collections_cfg = pre_manifest.get("collections", {})
        if _collections_cfg:
            from ..routing.auto_crud import mount_auto_crud_routes

            _users_cfg_pre = auth_config.get("users", {})
            _app_auth_on = bool(_users_cfg_pre.get("enabled"))
            _auth_col = _users_cfg_pre.get("collection_name", "users") if _app_auth_on else "users"
            mount_auto_crud_routes(
                app,
                _collections_cfg,
                app_auth_enabled=_app_auth_on,
                auth_users_collection=_auth_col,
            )

        # Auto-register JSON auth routes when auth.users is enabled
        _users_cfg = auth_config.get("users", {})
        if _users_cfg.get("enabled"):
            from ..auth.app_auth_routes import create_app_auth_router

            _auth_router = create_app_auth_router(
                engine=engine,
                slug=slug,
                manifest_data=pre_manifest,
                users_config=_users_cfg,
            )
            app.include_router(_auth_router)
            logger.info(f"App-user auth routes mounted for '{slug}' (/auth/*)")

        # Setup all middleware
        self._setup_middleware(app, slug, auth_config, auth_mode, is_sub_app, pre_manifest)

        logger.debug(f"FastAPI app created for '{slug}'")

        return app

    def _setup_middleware(
        self,
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
        auth_mode: str,
        is_sub_app: bool,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        """Setup all middleware for the FastAPI app."""
        # Add request scope middleware (innermost layer - runs first on request)
        self._add_request_scope_middleware(app, slug)

        # Add rate limiting middleware FIRST (outermost layer)
        self._add_rate_limit_middleware(app, slug, auth_config, auth_mode)

        # Add shared auth middleware (after rate limiting)
        if auth_mode == "shared":
            self._add_shared_auth_middleware(app, slug, auth_config)

        # Add app-user session middleware when auth.users is enabled in app mode.
        # Populates request.state.user / request.state.user_roles so that
        # require_user() / require_role() and per-collection auth config work.
        if auth_mode != "shared":
            _users_cfg = auth_config.get("users", {})
            if _users_cfg.get("enabled"):
                from ..auth.app_auth_routes import create_app_user_session_middleware

                _mw_cls = create_app_user_session_middleware(
                    engine=self,
                    slug=slug,
                    manifest_data=manifest or {},
                )
                app.add_middleware(_mw_cls)
                logger.info(f"AppUserSessionMiddleware added for '{slug}'")

        # Add CSRF middleware (after auth - auto-enabled for shared mode)
        self._add_csrf_middleware(app, slug, auth_config, auth_mode, is_sub_app)

        # Add security middleware (HSTS, headers)
        self._add_security_middleware(app, slug, auth_config, auth_mode)

        # Add Starlette SessionMiddleware if OAuth is configured
        # (Authlib stores OIDC state/nonce in the session during redirects)
        self._add_oauth_session_middleware(app, slug, auth_config)

        # Add CORS middleware if configured in manifest
        if manifest:
            self._add_cors_middleware(app, slug, manifest)

        # Add OpenTelemetry instrumentation (outermost - captures full request lifecycle)
        self._add_otel_middleware(app, slug)

        # Add observability middleware (outermost - correlation IDs + app context)
        self._add_observability_middleware(app, slug)

        # Register structured error handlers for MongoDBEngineError hierarchy
        from ..observability.error_handlers import register_error_handlers

        register_error_handlers(app)

    def _add_request_scope_middleware(self, app: "FastAPI", slug: str) -> None:
        """Add request scope middleware for DI."""
        from starlette.middleware.base import BaseHTTPMiddleware

        from ..di import ScopeManager

        class RequestScopeMiddleware(BaseHTTPMiddleware):
            """Middleware that manages request-scoped DI instances."""

            async def dispatch(self, request, call_next):
                ScopeManager.begin_request()
                try:
                    response = await call_next(request)
                    return response
                finally:
                    ScopeManager.end_request()

        app.add_middleware(RequestScopeMiddleware)
        logger.debug(f"RequestScopeMiddleware added for '{slug}'")

    def _add_otel_middleware(self, app: "FastAPI", slug: str) -> None:
        """Apply OpenTelemetry FastAPI auto-instrumentation.

        When OTel is available, this instruments the app for automatic span
        creation on every request. Correlation ID bridging is handled by the
        :class:`ObservabilityMiddleware` registered separately.
        """
        from ..observability.tracing import instrument_fastapi, otel_available

        if not otel_available():
            return

        instrument_fastapi(app)
        logger.info(f"OpenTelemetry instrumentation added for '{slug}'")

    def _add_observability_middleware(self, app: "FastAPI", slug: str) -> None:
        """Add observability middleware for correlation IDs and app context.

        This is registered as the outermost middleware so that every
        downstream middleware and route handler has access to a correlation ID
        and app context via :mod:`mdb_engine.observability.logging` context vars.
        """
        from ..observability.middleware import ObservabilityMiddleware

        app.add_middleware(ObservabilityMiddleware)
        logger.debug(f"ObservabilityMiddleware added for '{slug}'")

    def _add_rate_limit_middleware(
        self,
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
        auth_mode: str,
    ) -> None:
        """Add rate limiting middleware."""
        rate_limits_config = auth_config.get("rate_limits", {})
        if rate_limits_config or auth_mode == "shared":
            from ..auth.rate_limiter import create_rate_limit_middleware

            rate_limit_middleware = create_rate_limit_middleware(manifest_auth=auth_config)
            app.add_middleware(rate_limit_middleware)
            logger.info(
                f"AuthRateLimitMiddleware added for '{slug}' "
                f"(endpoints: {list(rate_limits_config.keys()) or 'defaults'})"
            )

    def _add_shared_auth_middleware(self, app: "FastAPI", slug: str, auth_config: dict[str, Any]) -> None:
        """Add shared auth middleware."""
        from ..auth.shared_middleware import create_shared_auth_middleware_lazy

        middleware_class = create_shared_auth_middleware_lazy(
            app_slug=slug,
            manifest_auth=auth_config,
        )
        app.add_middleware(middleware_class)
        logger.info(f"LazySharedAuthMiddleware added for '{slug}' " f"(require_role={auth_config.get('require_role')})")

    def _add_csrf_middleware(
        self,
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
        auth_mode: str,
        is_sub_app: bool,
    ) -> None:
        """Add CSRF middleware."""
        csrf_config = auth_config.get("csrf_protection", True if auth_mode == "shared" else False)
        if csrf_config and not is_sub_app:  # Don't add CSRF to child apps
            from ..auth.csrf import create_csrf_middleware

            # Ensure /auth/ticket is always exempt from CSRF
            # (it handles its own auth via ticket validation)
            # This is critical for WebSocket authentication in SSO multi-app setups
            TICKET_ENDPOINT = "/auth/ticket"

            # Add ticket endpoint to public routes (fallback for boolean csrf_config)
            public_routes = auth_config.get("public_routes", []) or []
            public_routes_with_ticket = list(public_routes)
            if TICKET_ENDPOINT not in public_routes_with_ticket:
                public_routes_with_ticket.append(TICKET_ENDPOINT)

            # Create a copy of auth_config to avoid mutating the original
            csrf_config_with_routes = {
                **auth_config,
                "public_routes": public_routes_with_ticket,
            }

            # If csrf_protection is an object with exempt_routes, ensure /auth/ticket is included
            # This handles the case where exempt_routes is explicitly set in the manifest
            if isinstance(csrf_config, dict):
                exempt_routes = csrf_config.get("exempt_routes")
                if exempt_routes is None:
                    # No exempt_routes specified, use public_routes as fallback
                    exempt_routes = public_routes_with_ticket
                else:
                    # exempt_routes exists, ensure /auth/ticket is included
                    exempt_routes = list(exempt_routes) if exempt_routes else []
                    if TICKET_ENDPOINT not in exempt_routes:
                        exempt_routes.append(TICKET_ENDPOINT)

                # Update the csrf_protection config with merged exempt_routes
                csrf_config_with_routes["csrf_protection"] = {
                    **csrf_config,
                    "exempt_routes": exempt_routes,
                }

            csrf_middleware = create_csrf_middleware(manifest_auth=csrf_config_with_routes)
            app.add_middleware(csrf_middleware)
            logger.info(f"CSRFMiddleware added for '{slug}'")
        elif csrf_config and is_sub_app:
            logger.debug(
                f"CSRFMiddleware skipped for child app '{slug}' - "
                f"parent app handles CSRF protection for WebSocket routes"
            )

    def _add_security_middleware(
        self,
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
        auth_mode: str,
    ) -> None:
        """Add security middleware (HSTS, headers)."""
        security_config = auth_config.get("security", {})
        hsts_config = security_config.get("hsts", {})
        if hsts_config.get("enabled", True) or auth_mode == "shared":
            from ..auth.middleware import SecurityMiddleware

            app.add_middleware(
                SecurityMiddleware,
                require_https=False,  # HSTS handles this in production
                csrf_protection=False,  # Handled by CSRFMiddleware above
                security_headers=True,
                hsts_config=hsts_config,
            )
            logger.info(f"SecurityMiddleware added for '{slug}'")

    async def _handle_auth_mode(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        app_manifest: dict[str, Any],
        auth_mode: str,
        is_sub_app: bool,
    ) -> None:
        """Handle authentication mode initialization (shared vs app)."""
        if auth_mode == "shared":
            logger.info(f"Shared auth mode for '{slug}' - SSO enabled")
            # Initialize shared user pool and set on app.state
            # Middleware was already added at app creation time (lazy version)
            # For sub-apps, check if parent already initialized user pool
            if is_sub_app:
                # Check if parent app has user_pool (set by parent's initialization)
                # If not, initialize it (shouldn't happen, but handle gracefully)
                if not hasattr(app.state, "user_pool") or app.state.user_pool is None:
                    logger.warning(
                        f"Sub-app '{slug}' uses shared auth but user_pool not found. "
                        "Initializing now (parent should have initialized it)."
                    )
                    await self._initialize_shared_user_pool(app, app_manifest)
                else:
                    logger.debug(f"Sub-app '{slug}' using shared user_pool from parent app")
            else:
                await self._initialize_shared_user_pool(app, app_manifest)
        else:
            logger.info(f"Per-app auth mode for '{slug}'")
            # Auto-retrieve app token for "app" mode
            await engine.auto_retrieve_app_token(slug)

    async def _initialize_auth_provider(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        app_manifest: dict[str, Any],
        auth_config: dict[str, Any],
    ) -> None:
        """Initialize authorization provider from manifest config."""
        try:
            logger.info(f"Checking auth config for '{slug}': " f"auth_config keys={list(auth_config.keys())}")
            auth_policy = auth_config.get("policy", {})
            logger.info(f"Auth policy for '{slug}': {auth_policy}")
            authz_provider_type = auth_policy.get("provider")
            logger.info(f"Authz provider type for '{slug}': {authz_provider_type}")
        except (KeyError, AttributeError, TypeError) as e:
            logger.exception(f"Error reading auth config for '{slug}': {e}")
            authz_provider_type = None

        if authz_provider_type == "oso":
            await self._initialize_oso_provider(engine, app, slug, app_manifest)
        elif authz_provider_type == "casbin":
            await self._initialize_casbin_provider(engine, app, slug, app_manifest)
        elif authz_provider_type is None and auth_policy:
            await self._initialize_casbin_provider_default(engine, app, slug, app_manifest)
        elif authz_provider_type:
            logger.warning(
                f"Unknown authz provider type '{authz_provider_type}' for '{slug}' - " f"skipping initialization"
            )

    async def _initialize_oso_provider(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        app_manifest: dict[str, Any],
    ) -> None:
        """Initialize OSO Cloud provider."""
        try:
            from ..auth.oso_factory import initialize_oso_from_manifest

            authz_provider = await initialize_oso_from_manifest(engine, slug, app_manifest)
            if authz_provider:
                app.state.authz_provider = authz_provider
                logger.info(f"OSO Cloud provider auto-initialized for '{slug}'")
            else:
                logger.warning(
                    f"OSO provider not initialized for '{slug}' - " "check OSO_AUTH and OSO_URL environment variables"
                )
        except ImportError as e:
            logger.warning(f"OSO Cloud SDK not available for '{slug}': {e}. " "Install with: pip install oso-cloud")
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
            logger.exception(f"Failed to initialize OSO provider for '{slug}': {e}")

    async def _initialize_casbin_provider(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        app_manifest: dict[str, Any],
    ) -> None:
        """Initialize Casbin provider."""
        logger.info(f"Initializing Casbin provider for '{slug}'...")
        try:
            from ..auth.casbin_factory import initialize_casbin_from_manifest

            logger.debug(f"Calling initialize_casbin_from_manifest for '{slug}'")
            authz_provider = await initialize_casbin_from_manifest(engine, slug, app_manifest)
            logger.debug(f"initialize_casbin_from_manifest returned: {authz_provider is not None}")
            if authz_provider:
                app.state.authz_provider = authz_provider
                logger.info(f"Casbin provider auto-initialized for '{slug}' " f"and set on app.state")
                logger.info(
                    f"Provider type: {type(authz_provider).__name__}, "
                    f"initialized: {getattr(authz_provider, '_initialized', 'unknown')}"
                )
                # Verify it's actually set
                if hasattr(app.state, "authz_provider") and app.state.authz_provider:
                    logger.info("Verified: app.state.authz_provider is set and not None")
                else:
                    logger.error("CRITICAL: app.state.authz_provider was set but is now " "None or missing!")
            else:
                logger.error(
                    f"Casbin provider initialization returned None for '{slug}' - " f"check logs above for errors"
                )
                logger.error(f"This means authorization will NOT work for '{slug}'")
        except ImportError as e:
            logger.warning(f"Casbin not available for '{slug}': {e}. " "Install with: pip install mdb-engine[casbin]")
        except (
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
            KeyError,
            ConnectionError,
            OSError,
        ) as e:
            logger.exception(f"Failed to initialize Casbin provider for '{slug}': {e}")
            logger.error(  # noqa: TRY400
                f"This means authorization will NOT work for '{slug}' - " f"app.state.authz_provider will remain None"
            )

    async def _initialize_casbin_provider_default(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        app_manifest: dict[str, Any],
    ) -> None:
        """Initialize Casbin provider as default when no provider specified."""
        logger.info(f"No provider specified in auth.policy for '{slug}', " f"defaulting to Casbin")
        try:
            from ..auth.casbin_factory import initialize_casbin_from_manifest

            authz_provider = await initialize_casbin_from_manifest(engine, slug, app_manifest)
            if authz_provider:
                app.state.authz_provider = authz_provider
                logger.info(f"Casbin provider auto-initialized for '{slug}' (default)")
            else:
                logger.warning(f"Casbin provider not initialized for '{slug}' " f"(default attempt failed)")
        except ImportError as e:
            logger.warning(f"Casbin not available for '{slug}': {e}. " "Install with: pip install mdb-engine[casbin]")
        except (
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
            KeyError,
        ) as e:
            logger.exception(f"Failed to initialize Casbin provider for '{slug}' (default): {e}")

    # ------------------------------------------------------------------
    # OAuth support
    # ------------------------------------------------------------------

    def _add_oauth_session_middleware(
        self,
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
    ) -> None:
        """Add Starlette SessionMiddleware when OAuth is configured.

        Authlib stores the OIDC state and nonce in the session cookie during
        the redirect flow.  The middleware is only added when ``auth.oauth``
        is present in the manifest.
        """
        oauth_config = auth_config.get("oauth")
        if not oauth_config:
            return

        from starlette.middleware.sessions import SessionMiddleware

        from ..auth.oauth import _resolve_env

        session_secret = _resolve_env(oauth_config.get("session_secret", ""))
        if not session_secret:
            logger.warning(
                "OAuth session_secret is empty for '%s' — "
                "SessionMiddleware will use a weak fallback. "
                "Set OAUTH_SESSION_SECRET in your environment.",
                slug,
            )
            session_secret = "mdb-engine-oauth-insecure-fallback"

        app.add_middleware(SessionMiddleware, secret_key=session_secret)
        logger.info(f"SessionMiddleware (OAuth) added for '{slug}'")

    def _add_cors_middleware(
        self,
        app: "FastAPI",
        slug: str,
        manifest: dict[str, Any],
    ) -> None:
        """Add CORS middleware when ``cors.enabled`` is set in the manifest."""
        cors_cfg = manifest.get("cors", {})
        if not cors_cfg.get("enabled"):
            return

        from ..auth.config_helpers import CORS_DEFAULTS, merge_config_with_defaults

        merged = merge_config_with_defaults(cors_cfg, CORS_DEFAULTS)

        from starlette.middleware.cors import CORSMiddleware

        kwargs: dict[str, Any] = {
            "allow_origins": merged.get("allow_origins", ["*"]),
            "allow_credentials": merged.get("allow_credentials", False),
            "allow_methods": merged.get("allow_methods", ["*"]),
            "allow_headers": merged.get("allow_headers", ["*"]),
            "max_age": merged.get("max_age", 3600),
        }
        expose = merged.get("expose_headers")
        if expose:
            kwargs["expose_headers"] = expose

        app.add_middleware(CORSMiddleware, **kwargs)
        app.state.cors_config = merged
        logger.info(
            f"CORSMiddleware added for '{slug}': "
            f"origins={merged.get('allow_origins')}, "
            f"credentials={merged.get('allow_credentials')}"
        )

    async def _initialize_oauth_service(
        self,
        engine: "MongoDBEngine",
        app: "FastAPI",
        slug: str,
        auth_config: dict[str, Any],
    ) -> None:
        """Initialize OAuthService and register OAuth routes if configured."""
        oauth_config = auth_config.get("oauth")
        if not oauth_config:
            return

        try:
            from ..auth.oauth import OAuthService, register_oauth_routes

            db = await engine.get_scoped_db(slug)
            oauth_service = OAuthService(
                oauth_config=oauth_config,
                db=db,
                slug=slug,
            )

            # Register routes
            register_oauth_routes(app, oauth_service)

            # Expose on app.state for dependency injection
            app.state.oauth_service = oauth_service

            logger.info(f"OAuthService initialized for '{slug}' with providers: " f"{oauth_service.provider_names}")
        except ImportError as e:
            logger.warning(f"OAuth not available for '{slug}': {e}. " "Install with: pip install mdb-engine[oauth]")
        except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
            logger.exception(f"Failed to initialize OAuth for '{slug}': {e}")

    # ------------------------------------------------------------------
    # Realtime (Change Stream → WebSocket)
    # ------------------------------------------------------------------

    async def _start_realtime(
        self,
        app: "FastAPI",
        engine: "MongoDBEngine",
        slug: str,
        app_manifest: dict[str, Any],
    ) -> None:
        """Start the Change Stream watcher if any collection has ``realtime: true``."""
        collections_cfg = app_manifest.get("collections", {})
        realtime_names = [name for name, cfg in collections_cfg.items() if cfg.get("realtime", False)]
        if not realtime_names:
            return

        try:
            from ..realtime import (
                ChangeStreamWatcher,
                RealtimeManager,
                register_realtime,
            )

            collection_map = {f"{slug}_{name}": (slug, name) for name in realtime_names}
            manager = RealtimeManager(collection_map)

            watcher = ChangeStreamWatcher(
                db=engine.connection_manager.mongo_db,
                manager=manager,
                watched_collections={slug: set(collection_map.keys())},
            )

            app.state.realtime_manager = manager
            app.state.realtime_watcher = watcher

            register_realtime(app, slug, collections_cfg, manager)
            await watcher.start()

            logger.info(
                f"Realtime enabled for '{slug}': " f"{len(realtime_names)} collection(s) — {', '.join(realtime_names)}"
            )
        except (ImportError, OSError, RuntimeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to start realtime for '{slug}': {e}")

    @staticmethod
    async def _stop_realtime(app: "FastAPI") -> None:
        """Stop the Change Stream watcher if it was started."""
        watcher = getattr(app.state, "realtime_watcher", None)
        if watcher is not None:
            try:
                await watcher.stop()
            except (OSError, RuntimeError, asyncio.CancelledError) as e:
                logger.warning(f"Error stopping realtime watcher: {e}")

    # ------------------------------------------------------------------
    # Collection-level index shortcuts (x-unique, ttl)
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_collection_indexes(
        engine: Any,
        slug: str,
        manifest: dict[str, Any],
    ) -> None:
        """Create indexes declared via collection-level shortcuts.

        * ``x-unique`` in schema properties -> unique index
        * ``ttl`` in collection config -> TTL index
        """
        collections_cfg = manifest.get("collections", {})
        if not collections_cfg:
            return

        _TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

        try:
            db = await engine.get_scoped_db(slug)
        except (PyMongoError, KeyError, ValueError) as exc:
            logger.warning("Could not get scoped db for index creation: %s", exc)
            return

        for col_name, col_cfg in collections_cfg.items():
            collection = db[col_name]
            raw_col = getattr(collection, "_collection", collection)

            # x-unique indexes
            schema = col_cfg.get("schema")
            if schema and isinstance(schema.get("properties"), dict):
                for prop_name, prop_def in schema["properties"].items():
                    if isinstance(prop_def, dict) and prop_def.get("x-unique"):
                        idx_name = f"auto_unique_{prop_name}"
                        try:
                            await raw_col.create_index(
                                prop_name,
                                unique=True,
                                name=idx_name,
                                background=True,
                            )
                            logger.info(
                                "Created unique index '%s' on %s.%s",
                                idx_name,
                                col_name,
                                prop_name,
                            )
                        except PyMongoError as exc:
                            logger.warning(
                                "Failed to create unique index on %s.%s: %s",
                                col_name,
                                prop_name,
                                exc,
                            )

            # TTL index
            ttl_cfg = col_cfg.get("ttl")
            if ttl_cfg:
                ttl_field = ttl_cfg.get("field")
                expire_raw = ttl_cfg.get("expire_after", "")
                if ttl_field and expire_raw:
                    unit = expire_raw[-1]
                    try:
                        num = int(expire_raw[:-1])
                    except (ValueError, IndexError):
                        logger.warning("Invalid ttl expire_after: %s", expire_raw)
                        continue
                    seconds = num * _TIME_UNITS.get(unit, 1)
                    idx_name = f"auto_ttl_{ttl_field}"
                    try:
                        await raw_col.create_index(
                            ttl_field,
                            expireAfterSeconds=seconds,
                            name=idx_name,
                            background=True,
                        )
                        logger.info(
                            "Created TTL index '%s' on %s.%s (%ds)",
                            idx_name,
                            col_name,
                            ttl_field,
                            seconds,
                        )
                    except PyMongoError as exc:
                        logger.warning(
                            "Failed to create TTL index on %s.%s: %s",
                            col_name,
                            ttl_field,
                            exc,
                        )
