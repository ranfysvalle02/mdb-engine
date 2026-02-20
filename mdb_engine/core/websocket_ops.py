"""
WebSocket Operations Mixin

Provides methods for WebSocket route registration, configuration,
and ticket/session management.
"""

import logging
from typing import TYPE_CHECKING, Any

from ..observability import get_logger as get_contextual_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .app_registration import AppRegistrationManager
    from .engine import MongoDBEngine
    from .service_initialization import ServiceInitializer

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)


class WebSocketMixin:
    """Mixin providing WebSocket operations and configuration.

    Expects the following attributes from the host class (MongoDBEngine):
        _websocket_ticket_store: WebSocket ticket store instance (optional).
        _websocket_session_manager: WebSocket session manager instance (optional).
        _app_registration_manager: Manages app manifest registration (optional).
        _service_initializer: Service initializer for websocket config (optional).
    """

    # -- Attributes provided by MongoDBEngine --
    _websocket_ticket_store: Any
    _websocket_session_manager: Any
    _app_registration_manager: "AppRegistrationManager | None"
    _service_initializer: "ServiceInitializer | None"

    def register_websocket_routes(self, app: Any, slug: str) -> None:
        """
        Register WebSocket routes with a FastAPI app.

        WebSocket support is OPTIONAL - only enabled if:
        1. App defines "websockets" in manifest.json
        2. WebSocket dependencies are available

        This should be called after the FastAPI app is created to actually
        mount the WebSocket endpoints.

        Args:
            app: FastAPI application instance
            slug: App slug
        """
        # CRITICAL: Ensure websocket_ticket_store is available
        # Ticket authentication is required for WebSocket connections
        if not self._websocket_ticket_store:
            error_msg = (
                f"WebSocket routes cannot be registered for app '{slug}': "
                "websocket_ticket_store is not available. "
                "WebSocket authentication requires ticket store to be initialized."
            )
            contextual_logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Ensure ticket store is in app state (may have been set in create_app)
        if not hasattr(app.state, "websocket_ticket_store") or app.state.websocket_ticket_store is None:
            app.state.websocket_ticket_store = self._websocket_ticket_store
            contextual_logger.debug(f"WebSocket ticket store stored in app state for '{slug}'")

        # Check if WebSockets are configured for this app
        websockets_config = self.get_websocket_config(slug)
        if not websockets_config:
            contextual_logger.debug(f"No WebSocket configuration found for app '{slug}' - WebSocket support disabled")
            return

        # Try to import WebSocket support (optional dependency)
        try:
            from ..routing.websockets import create_websocket_endpoint
        except ImportError as e:
            contextual_logger.warning(
                f"WebSocket support requested for app '{slug}' but "
                f"dependencies are not available: {e}. "
                f"WebSocket routes will not be registered. "
                f"Install FastAPI with WebSocket support."
            )
            return

        for endpoint_name, endpoint_config in websockets_config.items():
            path = endpoint_config.get("path", f"/{endpoint_name}")

            # Handle auth configuration - check endpoint config first, then app-level auth_policy
            auth_config = endpoint_config.get("auth", {})
            if isinstance(auth_config, dict) and "required" in auth_config:
                require_auth = auth_config.get("required", True)
            elif "require_auth" in endpoint_config:
                require_auth = endpoint_config.get("require_auth", True)
            else:
                # Fallback to app's auth_policy if available (from app registration manager)
                try:
                    if self._app_registration_manager:
                        manifest = self._app_registration_manager.get_app(slug)
                        if manifest and "auth_policy" in manifest:
                            require_auth = manifest["auth_policy"].get("required", True)
                        else:
                            require_auth = True
                    else:
                        require_auth = True
                except (AttributeError, KeyError, TypeError):
                    # Default: require auth (secure default)
                    require_auth = True

            ping_interval = endpoint_config.get("ping_interval", 30)

            # Create the endpoint handler with app isolation
            # Note: Apps can register message handlers later using register_message_handler()
            # SECURITY: Ticket-based auth is always attempted first (even if require_auth=False)
            # This enables user-scoped broadcasting while still allowing anonymous connections
            try:
                handler = create_websocket_endpoint(
                    app_slug=slug,
                    path=path,
                    endpoint_name=endpoint_name,  # Pass endpoint name for handler lookup
                    handler=None,  # Handlers registered via register_message_handler()
                    require_auth=require_auth,  # allow_anonymous handled in auth function
                    ping_interval=ping_interval,
                )
                contextual_logger.debug(
                    f"Created WebSocket handler for '{path}' "
                    f"(type: {type(handler).__name__}, "
                    f"callable: {callable(handler)})"
                )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                contextual_logger.error(
                    f"Failed to create WebSocket handler for '{path}': {e}",
                    exc_info=True,
                )
                raise

            # Register with FastAPI - automatically scoped to this app
            try:
                # FastAPI WebSocket registration - use APIRouter approach (most reliable)
                from fastapi import APIRouter

                # Create a router for this WebSocket route
                ws_router = APIRouter()
                ws_router.websocket(path)(handler)

                # Include the router in the app
                app.include_router(ws_router)

                contextual_logger.info(
                    f"Registered WebSocket route '{path}' for app '{slug}' " f"(auth: {require_auth})",
                    extra={
                        "app_slug": slug,
                        "path": path,
                        "endpoint": endpoint_name,
                        "require_auth": require_auth,
                    },
                )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                contextual_logger.error(
                    f"Failed to register WebSocket route '{path}' for app '{slug}': {e}",
                    exc_info=True,
                    extra={
                        "app_slug": slug,
                        "path": path,
                        "endpoint": endpoint_name,
                        "error": str(e),
                    },
                )
                raise

    def get_websocket_config(self, slug: str) -> dict[str, Any] | None:
        """
        Get WebSocket configuration for an app.

        Args:
            slug: App slug

        Returns:
            WebSocket configuration dict or None if not configured
        """
        if self._service_initializer:
            return self._service_initializer.get_websocket_config(slug)
        return None

    async def _register_websocket_endpoints(self, app: "FastAPI", engine: "MongoDBEngine") -> None:
        """Register WebSocket ticket and session endpoints."""
        # Register WebSocket ticket endpoint AFTER initialization
        # (ticket store is now available)
        if engine.websocket_ticket_store:
            app.state.websocket_ticket_store = engine.websocket_ticket_store
            logger.info("WebSocket ticket store stored in app state")

            # Set global ticket store for WebSocket authentication (works with routers)
            from ..routing.websockets import set_global_websocket_ticket_store

            set_global_websocket_ticket_store(engine.websocket_ticket_store)
            logger.info("Global WebSocket ticket store set for multi-app authentication")

            # Register WebSocket ticket endpoint
            from ..auth.websocket_tickets import create_websocket_ticket_endpoint

            ticket_endpoint = create_websocket_ticket_endpoint(engine.websocket_ticket_store)
            app.post("/auth/ticket")(ticket_endpoint)
            logger.info("WebSocket ticket endpoint registered at /auth/ticket")

        # Register WebSocket session endpoint AFTER initialization
        # (session manager is now available)
        if engine.websocket_session_manager:
            app.state.websocket_session_manager = engine.websocket_session_manager
            logger.info("WebSocket session manager stored in app state")

            # Register WebSocket session endpoint
            from ..auth.websocket_sessions import create_websocket_session_endpoint

            session_endpoint = create_websocket_session_endpoint(engine.websocket_session_manager)
            app.get("/auth/websocket-session")(session_endpoint)
            logger.info("WebSocket session endpoint registered at /auth/websocket-session")

    async def _configure_websocket_ticket_ttl(self, app: "FastAPI", app_manifest: dict[str, Any], slug: str) -> None:
        """Configure WebSocket ticket TTL from manifest."""
        websockets_config = app_manifest.get("websockets", {})
        if not websockets_config:
            return

        from ..auth.websocket_tickets import WebSocketTicketStore

        ticket_ttl_values: list[int] = []
        for endpoint_config in websockets_config.values():
            if isinstance(endpoint_config, dict):
                ticket_ttl = endpoint_config.get("ticket_ttl_seconds")
                if ticket_ttl is not None:
                    ticket_ttl_values.append(ticket_ttl)

        if ticket_ttl_values:
            configured_ticket_ttl = min(ticket_ttl_values)  # Use minimum for maximum security
            # Reinitialize ticket store if needed
            ticket_store = self._websocket_ticket_store
            if ticket_store is None or ticket_store.ticket_ttl != configured_ticket_ttl:
                self._websocket_ticket_store = WebSocketTicketStore(ticket_ttl_seconds=configured_ticket_ttl)
                logger.info(
                    f"WebSocket ticket store initialized with TTL: "
                    f"{configured_ticket_ttl}s (from app '{slug}' manifest)"
                )
                # Update app state if ticket store was already set
                if hasattr(app.state, "websocket_ticket_store"):
                    app.state.websocket_ticket_store = self._websocket_ticket_store

    @property
    def websocket_ticket_store(self):
        """
        Get the WebSocket ticket store.

        Returns:
            WebSocketTicketStore instance or None if not initialized
        """
        return self._websocket_ticket_store

    @property
    def websocket_session_manager(self):
        """
        Get the WebSocket session manager.

        Returns:
            WebSocketSessionManager instance or None if not initialized
        """
        return self._websocket_session_manager
