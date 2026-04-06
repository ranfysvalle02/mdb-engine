"""
WebSocket Routing and Connection Management

This module provides OPTIONAL WebSocket support for MDB_ENGINE apps via manifest.json configuration.
Apps can declare WebSocket endpoints in their manifest, and the engine automatically
handles connection management, authentication, and message routing.

WebSocket support is OPTIONAL and only enabled when:
1. Apps define "websockets" in their manifest.json
2. FastAPI WebSocket support is available

Key Features:
- App-level isolation: Each app has its own WebSocket manager
- Automatic authentication: Integrates with mdb_engine auth system
- Manifest-driven configuration: Define endpoints in manifest.json
- Bi-directional communication: Supports broadcasting and listening to client messages
- Automatic ping/pong: Keeps connections alive
- Connection metadata: Tracks user_id, user_email, connected_at for each connection

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Check if FastAPI WebSocket support is available (OPTIONAL dependency)
try:
    from fastapi import WebSocket, WebSocketDisconnect

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    # Create dummy classes for type hints when WebSockets aren't available
    WebSocket = Any  # type: ignore
    WebSocketDisconnect = Exception  # type: ignore

logger = logging.getLogger(__name__)

# Cache the Docker container check to avoid filesystem I/O on every WebSocket connection.
_IS_DOCKER: bool = os.path.exists("/.dockerenv")


@dataclass
class WebSocketConnection:
    """Metadata for a WebSocket connection."""

    websocket: Any
    app_slug: str
    user_id: str | None = None
    user_email: str | None = None
    connected_at: datetime = None

    def __post_init__(self):
        if self.connected_at is None:
            self.connected_at = datetime.now(timezone.utc)


class WebSocketConnectionManager:
    """
    Manages WebSocket connections for an app with secure isolation.

    Each app has its own manager instance, ensuring complete isolation.
    Provides connection tracking, broadcasting, and automatic cleanup.
    """

    def __init__(self, app_slug: str):
        """
        Initialize WebSocket connection manager for an app.

        Args:
            app_slug: App slug for scoping connections (ensures isolation)
        """
        self.app_slug = app_slug
        self.active_connections: list[WebSocketConnection] = []  # List of connection metadata
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()  # prevent GC of fire-and-forget tasks
        logger.debug(f"Initialized WebSocket manager for app: {app_slug}")

    async def connect(
        self,
        websocket: Any,
        user_id: str | None = None,
        user_email: str | None = None,
    ) -> WebSocketConnection:
        """
        Accept and register a WebSocket connection with metadata.

        Args:
            websocket: FastAPI WebSocket instance
            user_id: Optional user ID for authenticated connections
            user_email: Optional user email for authenticated connections

        Returns:
            WebSocketConnection instance with metadata
        """
        # Note: websocket should already be accepted by the endpoint handler
        # This is just for tracking - don't accept again
        if hasattr(websocket, "client_state") and websocket.client_state.name != "CONNECTED":
            await websocket.accept()
        connection = WebSocketConnection(
            websocket=websocket,
            app_slug=self.app_slug,
            user_id=user_id,
            user_email=user_email,
        )
        async with self._lock:
            # Check if connection already exists (by websocket object identity)
            if not any(conn.websocket is websocket for conn in self.active_connections):
                self.active_connections.append(connection)
        logger.info(
            f"WebSocket connected for app '{self.app_slug}' "
            f"(user: {user_email or 'anonymous'}). "
            f"Total connections: {len(self.active_connections)}"
        )
        return connection

    def disconnect(self, websocket: Any) -> None:
        """
        Remove a WebSocket connection from tracking.

        Args:
            websocket: WebSocket instance to remove
        """

        async def _disconnect():
            async with self._lock:
                self.active_connections = [conn for conn in self.active_connections if conn.websocket is not websocket]
            logger.info(
                f"WebSocket disconnected for app '{self.app_slug}'. "
                f"Remaining connections: {len(self.active_connections)}"
            )

        task = asyncio.create_task(_disconnect())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def broadcast(self, message: dict[str, Any], filter_by_user: str | None = None) -> int:
        """
        Broadcast a message to all connected clients for this app.

        Args:
            message: Message dictionary to broadcast (will be JSON serialized)
            filter_by_user: Optional user_id to filter recipients (if None, broadcasts to all)

        Returns:
            Number of clients that received the message
        """
        if not self.active_connections:
            return 0

        # Add app context to message for security
        message_with_context = {
            **message,
            "app_slug": self.app_slug,  # Ensure message is scoped to this app
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        message_json = json.dumps(message_with_context)
        disconnected = []
        sent_count = 0

        async with self._lock:
            connections = list(self.active_connections)

        # Use DEBUG level for broadcast logging to reduce log noise
        filter_type = type(filter_by_user).__name__ if filter_by_user else "None"
        logger.debug(
            f"Broadcasting to {len(connections)} connection(s) for app '{self.app_slug}' "
            f"(filter_by_user: {filter_by_user}, type: {filter_type})"
        )
        if connections:
            user_ids = [str(c.user_id) if c.user_id else "None" for c in connections]
            user_types = [type(c.user_id).__name__ if c.user_id else "None" for c in connections]
            logger.debug(f"Active connections user_ids: {user_ids} (types: {user_types})")

        for connection in connections:
            # Filter by user if specified
            if filter_by_user:
                # Normalize both user_ids to strings for comparison
                connection_user_id = str(connection.user_id) if connection.user_id else None
                filter_user_id = str(filter_by_user) if filter_by_user else None

                if connection_user_id != filter_user_id:
                    logger.debug(
                        f"Filtering out connection: user_id mismatch "
                        f"(connection: {connection_user_id}, filter: {filter_user_id})"
                    )
                    continue
                logger.debug(
                    f"User match: connection.user_id={connection_user_id}, " f"filter_by_user={filter_user_id}"
                )

            # Check WebSocket state before attempting to send
            try:
                # Check if WebSocket is in a valid state for sending
                if hasattr(connection.websocket, "client_state"):
                    state = connection.websocket.client_state.name
                    if state not in ["CONNECTED"]:
                        # WebSocket is not in a connected state, mark for cleanup
                        disconnected.append(connection.websocket)
                        continue
            except AttributeError:
                # Type 2: Recoverable - websocket doesn't have client_state, try to send anyway
                pass

            try:
                await connection.websocket.send_text(message_json)
                sent_count += 1
            except (WebSocketDisconnect, RuntimeError, OSError) as e:
                # WebSocket closed/disconnected errors are expected
                error_msg = str(e).lower()
                if "close" not in error_msg and "disconnect" not in error_msg:
                    logger.debug(f"Error sending WebSocket message to client: {e}")
                disconnected.append(connection.websocket)

        # Clean up disconnected clients
        if disconnected:
            for ws in disconnected:
                self.disconnect(ws)

        return sent_count

    async def send_to_connection(self, websocket: Any, message: dict[str, Any]) -> None:
        """
        Send a message to a specific WebSocket connection.

        Args:
            websocket: Target WebSocket instance
            message: Message dictionary to send
        """
        # Check WebSocket state before attempting to send
        if hasattr(websocket, "client_state"):
            try:
                state = websocket.client_state.name
                if state not in ["CONNECTED"]:
                    # WebSocket is not in a connected state, disconnect and return
                    self.disconnect(websocket)
                    return
            except AttributeError:
                # Type 2: Recoverable - websocket doesn't have client_state, try to send anyway
                pass

        try:
            # Add app context for security
            message_with_context = {
                **message,
                "app_slug": self.app_slug,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            message_json = json.dumps(message_with_context)
            await websocket.send_text(message_json)
        except (WebSocketDisconnect, RuntimeError, OSError) as e:
            # WebSocket closed/disconnected errors are expected
            error_msg = str(e).lower()
            if "close" not in error_msg and "disconnect" not in error_msg:
                logger.debug(f"Error sending message to specific WebSocket: {e}")
            self.disconnect(websocket)

    def get_connections_by_user(self, user_id: str) -> list[WebSocketConnection]:
        """
        Get all connections for a specific user.

        Args:
            user_id: User ID to filter by

        Returns:
            List of WebSocketConnection instances for that user
        """
        return [conn for conn in self.active_connections if conn.user_id == user_id]

    def get_connection_count_by_user(self, user_id: str) -> int:
        """
        Get connection count for a specific user.

        Args:
            user_id: User ID to count

        Returns:
            Number of connections for that user
        """
        return len(self.get_connections_by_user(user_id))

    def get_connection_count(self) -> int:
        """Get the number of active connections."""
        return len(self.active_connections)


# Global registry of WebSocket managers per app (app-level isolation)
_websocket_managers: dict[str, WebSocketConnectionManager] = {}
_manager_lock = asyncio.Lock()

# Global registry of message handlers per app (for listening to client messages)
# Note: Registration happens synchronously during app startup, so no lock needed
_message_handlers: dict[str, dict[str, Callable[[Any, dict[str, Any]], Awaitable[None]]]] = {}

# Global WebSocket ticket store (shared across all apps in multi-app setups)
# This is set when the engine initializes and is used for ticket authentication
_global_websocket_ticket_store: Any | None = None


def set_global_websocket_ticket_store(ticket_store: Any) -> None:
    """
    Set the global WebSocket ticket store.

    This is called by the engine during initialization to make the ticket store
    accessible to all WebSocket endpoints, even when routes are registered via routers.

    Args:
        ticket_store: WebSocketTicketStore instance
    """
    global _global_websocket_ticket_store
    _global_websocket_ticket_store = ticket_store
    if ticket_store:
        logger.info(
            f"Global WebSocket ticket store set for multi-app WebSocket authentication "
            f"(store type: {type(ticket_store).__name__})"
        )
    else:
        logger.warning("Global WebSocket ticket store set to None")


async def get_websocket_manager(app_slug: str) -> WebSocketConnectionManager:
    """
    Get or create a WebSocket connection manager for an app.

    This ensures app-level isolation - each app has its own manager instance.
    Connections are automatically scoped to the app_slug.

    Args:
        app_slug: App slug (ensures isolation)

    Returns:
        WebSocketConnectionManager instance for the app
    """
    async with _manager_lock:
        if app_slug not in _websocket_managers:
            _websocket_managers[app_slug] = WebSocketConnectionManager(app_slug)
            logger.debug(f"Created WebSocket manager for app: {app_slug}")
        return _websocket_managers[app_slug]


def get_websocket_manager_sync(app_slug: str) -> WebSocketConnectionManager:
    """
    Synchronous version of get_websocket_manager for use in non-async contexts.

    Args:
        app_slug: App slug

    Returns:
        WebSocketConnectionManager instance for the app
    """
    if app_slug not in _websocket_managers:
        _websocket_managers[app_slug] = WebSocketConnectionManager(app_slug)
        logger.debug(f"Created WebSocket manager for app: {app_slug}")
    return _websocket_managers[app_slug]


def _get_cookies_from_websocket(websocket: Any) -> dict[str, str]:
    """
    Extract cookies from WebSocket request.

    Supports both FastAPI WebSocket .cookies attribute and ASGI scope Cookie header.

    Args:
        websocket: FastAPI WebSocket instance

    Returns:
        Dictionary of cookie name -> cookie value
    """
    cookies: dict[str, str] = {}

    try:
        # Try FastAPI WebSocket .cookies attribute (if available)
        if hasattr(websocket, "cookies") and websocket.cookies is not None:
            # FastAPI WebSocket.cookies is a dict-like object
            cookies = dict(websocket.cookies)
            logger.debug(f"Extracted {len(cookies)} cookies from WebSocket.cookies attribute")
            return cookies
    except (AttributeError, TypeError) as e:
        logger.debug(f"Could not access WebSocket.cookies attribute: {e}")

    try:
        # Fallback: Extract from Cookie header in ASGI scope
        if hasattr(websocket, "scope") and "headers" in websocket.scope:
            headers_dict = dict(websocket.scope["headers"])
            cookie_header_bytes = headers_dict.get(b"cookie")
            if not cookie_header_bytes:
                # Try case-insensitive lookup
                for key, value in headers_dict.items():
                    if isinstance(key, bytes) and key.lower() == b"cookie":
                        cookie_header_bytes = value
                        break

            if cookie_header_bytes:
                cookie_header = cookie_header_bytes.decode("utf-8")
                # Parse cookie string: "name1=value1; name2=value2"
                for cookie_pair in cookie_header.split(";"):
                    cookie_pair = cookie_pair.strip()
                    if "=" in cookie_pair:
                        name, value = cookie_pair.split("=", 1)
                        cookies[name.strip()] = value.strip()
                logger.debug(f"Extracted {len(cookies)} cookies from Cookie header in ASGI scope")
                return cookies
    except (AttributeError, TypeError, KeyError, UnicodeDecodeError, ValueError) as e:
        logger.debug(f"Could not extract cookies from ASGI scope: {e}")

    return cookies


async def _validate_websocket_origin_in_handler(websocket: Any, app_slug: str) -> bool:
    """
    Validate WebSocket Origin header in handler (since middleware may not intercept upgrades).

    This provides CSWSH (Cross-Site WebSocket Hijacking) protection by validating
    the Origin header before accepting the WebSocket connection.

    Args:
        websocket: FastAPI WebSocket instance
        app_slug: App slug for context

    Returns:
        True if origin is valid, False otherwise
    """
    from ..auth.cors_utils import get_cors_allowed_origins, is_origin_allowed

    try:
        origin = None
        if hasattr(websocket, "headers"):
            origin = websocket.headers.get("origin")
        elif hasattr(websocket, "scope") and "headers" in websocket.scope:
            headers_dict = dict(websocket.scope["headers"])
            origin_bytes = headers_dict.get(b"origin")
            if not origin_bytes:
                for key, value in headers_dict.items():
                    if isinstance(key, bytes) and key.lower() == b"origin":
                        origin_bytes = value
                        break
            if origin_bytes:
                origin = origin_bytes.decode("utf-8")

        logger.info(f"WebSocket origin validation for '{app_slug}': origin={origin}")

        allowed_origins = []
        try:
            app = getattr(websocket, "app", None)
            apps_checked = []

            if app:
                current_app = app
                while current_app:
                    parent_app = getattr(current_app, "app", None)
                    if parent_app and parent_app is not current_app:
                        if getattr(parent_app.state, "is_multi_app", False):
                            cors_config = getattr(parent_app.state, "cors_config", None)
                            if cors_config and cors_config.get("enabled", False):
                                try:
                                    allowed_origins = get_cors_allowed_origins(cors_config)
                                    logger.info(f"Using parent app CORS config for '{app_slug}': " f"{allowed_origins}")
                                    break
                                except ValueError as e:
                                    logger.warning(f"Invalid CORS config in parent app: {e}")
                            break
                        current_app = parent_app
                    else:
                        break

                if not allowed_origins:
                    current_app = app
                    while current_app:
                        app_title = getattr(current_app, "title", "unknown")
                        apps_checked.append(app_title)
                        cors_config = getattr(current_app.state, "cors_config", None)
                        if cors_config and cors_config.get("enabled", False):
                            try:
                                allowed_origins = get_cors_allowed_origins(cors_config)
                                logger.info(
                                    f"Using CORS config from app '{app_title}' for '{app_slug}': " f"{allowed_origins}"
                                )
                                break
                            except ValueError as e:
                                logger.warning(f"Invalid CORS config in '{app_title}': {e}")

                        parent_app = getattr(current_app, "app", None)
                        if parent_app is current_app or parent_app is None:
                            break
                        current_app = parent_app

            if not allowed_origins:
                is_dev = (
                    os.getenv("ENVIRONMENT", "").lower() in ["development", "dev"]
                    or os.getenv("G_NOME_ENV", "").lower() in ["development", "dev"]
                    or _IS_DOCKER
                )

                if not is_dev:
                    logger.error(
                        f"WebSocket origin validation failed for '{app_slug}': "
                        f"No CORS config found and not in development mode. "
                        f"Please configure CORS in manifest.json for production use."
                    )
                    return False

                logger.warning(
                    f"No enabled CORS config found for '{app_slug}' "
                    f"(checked apps: {apps_checked}). Using development fallback."
                )

                try:
                    if hasattr(websocket, "scope"):
                        host = websocket.scope.get("server")
                        if host:
                            hostname = host[0] if isinstance(host, tuple) else host
                            scheme = websocket.scope.get("scheme", "http")
                            port = host[1] if isinstance(host, tuple) and len(host) > 1 else None

                            if hostname in ["localhost", "0.0.0.0", "127.0.0.1", "::1"]:
                                for variant in ["localhost", "127.0.0.1"]:
                                    if port and port not in [80, 443]:
                                        allowed_origins.append(f"{scheme}://{variant}:{port}")
                                    else:
                                        allowed_origins.append(f"{scheme}://{variant}")
                            else:
                                if port and port not in [80, 443]:
                                    allowed_origins.append(f"{scheme}://{hostname}:{port}")
                                else:
                                    allowed_origins.append(f"{scheme}://{hostname}")
                except (AttributeError, TypeError, KeyError) as e:
                    logger.debug(f"Could not determine fallback origins: {e}")

        except (AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Could not read CORS config for '{app_slug}': {e}", exc_info=True)

        if is_origin_allowed(origin, allowed_origins):
            logger.info(
                f"WebSocket origin validation passed for '{app_slug}': "
                f"origin={origin or 'missing'}, allowed_origins={allowed_origins}"
            )
            return True

        if not origin:
            is_dev = (
                os.getenv("ENVIRONMENT", "").lower() in ["development", "dev"]
                or os.getenv("G_NOME_ENV", "").lower() in ["development", "dev"]
                or _IS_DOCKER
            )
            if is_dev:
                logger.warning(
                    f"WebSocket upgrade missing Origin header in development mode: "
                    f"allowing connection for '{app_slug}'"
                )
                return True

            logger.warning(f"WebSocket upgrade missing Origin header for '{app_slug}'")
            return False

        logger.warning(
            f"WebSocket origin validation failed for '{app_slug}': "
            f"origin={origin} not in allowed_origins={allowed_origins}. "
            f"Add '{origin}' to manifest.json cors.allow_origins"
        )
        return False

    except (
        AttributeError,
        KeyError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as e:
        logger.error(f"Error validating WebSocket origin for '{app_slug}': {e}", exc_info=True)
        return False


async def authenticate_websocket(
    websocket: Any,
    app_slug: str,
    require_auth: bool = True,
) -> tuple[str | None, str | None]:
    """
    Authenticate a WebSocket connection using ticket-based authentication.

    Authentication method:
    - Ticket (query param `?ticket=<uuid>` or header `X-WebSocket-Ticket`)
      - short-lived (10s TTL), single-use, secure for multi-app SSO

    Ticket Flow:
    1. Client requests ticket via POST /auth/ticket (requires JWT cookie)
    2. Server validates JWT and generates one-time ticket (UUID)
    3. Client connects WebSocket with ticket in query param or header
    4. Server validates and consumes ticket (single-use)
    5. WebSocket connection established

    Args:
        websocket: FastAPI WebSocket instance (can access headers before accept)
        app_slug: App slug for context
        require_auth: Whether authentication is required

    Returns:
        Tuple of (user_id, user_email) or (None, None) if not authenticated

    Raises:
        WebSocketDisconnect: If authentication is required but fails
    """
    if not WEBSOCKETS_AVAILABLE:
        raise ImportError("WebSocket support is not available. FastAPI WebSocket support must be installed.")

    from fastapi import WebSocketDisconnect

    # CRITICAL: Even when require_auth=False, we should still try to authenticate
    # if a ticket is provided, so we can associate user_id with the connection.
    # This enables proper user-scoped broadcasting even for "optional" auth.
    # Only fail if require_auth=True and authentication fails.

    try:
        # Helper function to get app and traverse hierarchy
        def get_app_and_state():
            """Get app instance and traverse hierarchy to find state."""
            app = getattr(websocket, "app", None)
            apps_checked = []
            max_iterations = 10  # Safety limit to prevent infinite loops
            iteration = 0
            while app and iteration < max_iterations:
                iteration += 1
                app_title = getattr(app, "title", "unknown")
                apps_checked.append(app_title)
                yield app, app_title
                # Try to get parent app (FastAPI routers have .app attribute pointing to parent)
                parent_app = getattr(app, "app", None)
                if parent_app is app or parent_app is None:  # Prevent infinite loop
                    break
                app = parent_app

        # Try to get ticket store from app state
        websocket_ticket_store = _global_websocket_ticket_store

        apps_checked_list = []
        for app, app_title in get_app_and_state():
            apps_checked_list.append(app_title)
            if not websocket_ticket_store:
                websocket_ticket_store = getattr(app.state, "websocket_ticket_store", None)
                if websocket_ticket_store:
                    logger.debug(f"Found websocket_ticket_store on app '{app_title}' " f"for app_slug '{app_slug}'")

        # Ticket authentication (primary method for multi-app SSO)
        # Try ticket auth even if require_auth=False (for user association)
        ticket = None
        try:
            if hasattr(websocket, "query_params"):
                ticket = websocket.query_params.get("ticket")
            if not ticket and hasattr(websocket, "headers"):
                ticket = websocket.headers.get("X-WebSocket-Ticket")
        except (AttributeError, TypeError, KeyError):
            pass

        if ticket and websocket_ticket_store:
            try:
                ticket_data = await websocket_ticket_store.validate_and_consume_ticket(ticket)
                if ticket_data:
                    user_id = ticket_data.get("user_id")
                    user_email = ticket_data.get("user_email")
                    logger.info(
                        f"WebSocket authenticated successfully for app '{app_slug}': {user_email} "
                        f"(method: ticket, require_auth={require_auth})"
                    )
                    return user_id, user_email
            except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
                logger.debug(f"Ticket validation failed: {e}")
                # If require_auth=True and ticket validation fails, we'll fail below
                # If require_auth=False, we allow anonymous connection

        # Session key authentication (preferred for multi-app setups)
        session_key = None
        try:
            if hasattr(websocket, "query_params"):
                session_key = websocket.query_params.get("session_key")
            if not session_key and hasattr(websocket, "headers"):
                session_key = websocket.headers.get("X-WebSocket-Session-Key")
        except (AttributeError, TypeError, KeyError):
            pass

        if session_key:
            websocket_session_manager = None
            ws_app = getattr(websocket, "app", None)
            for _i in range(10):
                if ws_app is None:
                    break
                websocket_session_manager = getattr(ws_app.state, "websocket_session_manager", None)
                if websocket_session_manager:
                    break
                parent = getattr(ws_app, "app", None)
                if parent is ws_app or parent is None:
                    break
                ws_app = parent

            if websocket_session_manager:
                try:
                    session_data = await websocket_session_manager.validate_session(session_key)
                    if session_data:
                        user_id = session_data.get("user_id")
                        user_email = session_data.get("user_email")
                        logger.info(
                            f"WebSocket authenticated successfully for app '{app_slug}': {user_email} "
                            f"(method: session_key, require_auth={require_auth})"
                        )
                        return user_id, user_email
                    logger.warning(f"Session key validation returned no data for app '{app_slug}'")
                except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError) as e:
                    logger.warning(f"Session key validation failed for app '{app_slug}': {e}")

        # No authentication method succeeded
        # SECURITY: If require_auth=True, we must fail
        if require_auth:
            logger.error(
                f"WebSocket authentication failed for app '{app_slug}'. "
                "No valid ticket or session key found. "
                "Generate ticket via /auth/ticket or session key via /auth/websocket-session."
            )
            return None, None

        # require_auth=False: Allow anonymous connection
        # Ticket-based auth was attempted first (if ticket provided) but failed or not provided
        # This is safe - anonymous connections are explicitly allowed
        logger.debug(
            f"WebSocket connection allowed as anonymous for app '{app_slug}' "
            f"(require_auth=False, no ticket provided)"
        )
        return None, None

    except WebSocketDisconnect:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
        logger.error(f"WebSocket authentication failed for app '{app_slug}': {e}", exc_info=True)
        if require_auth:
            return None, None  # Signal auth failure
        return None, None


def register_message_handler(
    app_slug: str,
    endpoint_name: str,
    handler: Callable[[Any, dict[str, Any]], Awaitable[None]],
) -> None:
    """
    Register a message handler for a WebSocket endpoint.

    This allows your app to listen to and process messages from WebSocket clients.
    Handlers are automatically called when clients send messages.

    Args:
        app_slug: App slug
        endpoint_name: Endpoint name (key in manifest.json websockets config)
        handler: Async function to handle messages.
                 Signature: async def handler(websocket, message: Dict[str, Any]) -> None

    Example:
        ```python
        async def handle_client_message(websocket, message):
            message_type = message.get("type")
            if message_type == "subscribe":
                # Handle subscription request
                await broadcast_to_app("my_app", {
                    "type": "subscribed",
                    "channel": message.get("channel")
                })

        register_message_handler("my_app", "realtime", handle_client_message)
        ```
    """
    # Called synchronously during app startup, so no async lock needed
    if app_slug not in _message_handlers:
        _message_handlers[app_slug] = {}
    _message_handlers[app_slug][endpoint_name] = handler
    logger.info(f"Registered message handler for app '{app_slug}', endpoint '{endpoint_name}'")


def get_message_handler(app_slug: str, endpoint_name: str) -> Callable[[Any, dict[str, Any]], Awaitable[None]] | None:
    """
    Get a registered message handler for an endpoint.

    Args:
        app_slug: App slug
        endpoint_name: Endpoint name

    Returns:
        Handler function or None if not registered
    """
    # Called synchronously during route creation (startup is single-threaded)
    return _message_handlers.get(app_slug, {}).get(endpoint_name)


async def _accept_websocket_connection(websocket: Any, app_slug: str) -> None:
    """
    Accept WebSocket connection.

    Cookie-based authentication uses httpOnly cookies automatically sent by the browser.
    """
    try:
        await websocket.accept()
        logger.info(f"WebSocket accepted for app '{app_slug}'")
    except (RuntimeError, ConnectionError, OSError) as accept_error:
        logger.error(
            f"Failed to accept WebSocket for app '{app_slug}': {accept_error}",
            exc_info=True,
        )
        raise


async def _authenticate_websocket_connection(websocket: Any, app_slug: str, require_auth: bool) -> tuple:
    """Authenticate WebSocket connection and return (user_id, user_email)."""
    try:
        user_id, user_email = await authenticate_websocket(websocket, app_slug, require_auth)

        if require_auth and not user_id:
            logger.warning(f"WebSocket authentication failed for app '{app_slug}' - closing connection")
            try:
                await websocket.close(code=1008, reason="Authentication required")
            except (WebSocketDisconnect, RuntimeError, OSError) as e:
                logger.debug(f"WebSocket already closed during auth failure cleanup: {e}")
            raise WebSocketDisconnect(code=1008)

        return user_id, user_email

    except WebSocketDisconnect:
        logger.warning(f"WebSocket connection rejected for app '{app_slug}' - authentication failed")
        raise
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RuntimeError,
    ) as auth_error:
        logger.error(
            f"Unexpected error during WebSocket authentication for app " f"'{app_slug}': {auth_error}",
            exc_info=True,
        )
        try:
            await websocket.close(code=1011, reason="Internal server error during authentication")
        except (WebSocketDisconnect, RuntimeError, OSError) as close_error:
            logger.debug(f"WebSocket already closed during auth error cleanup: {close_error}")
        raise WebSocketDisconnect(code=1011) from None


async def _handle_websocket_message(
    websocket: Any,
    message: dict[str, Any],
    manager: Any,
    app_slug: str,
    endpoint_name: str,
    handler: Callable | None,
) -> bool:
    """Handle incoming WebSocket message. Returns True if should continue, False if disconnect."""
    if message.get("type") == "websocket.disconnect":
        logger.info(f"WebSocket client disconnected for app '{app_slug}'")
        return False
    elif message.get("type") == "websocket.receive":
        if "text" in message:
            try:
                data = json.loads(message["text"])

                if data.get("type") == "pong":
                    return True

                current_handler = handler if handler else get_message_handler(app_slug, endpoint_name)

                if current_handler:
                    # Type 4: Let handler errors bubble up to framework
                    await current_handler(websocket, data)
                else:
                    logger.debug(
                        f"Received message on WebSocket for app '{app_slug}' "
                        f"but no handler registered. "
                        f"Use register_message_handler() to handle client messages."
                    )

            except json.JSONDecodeError:
                logger.debug("Received non-JSON message from WebSocket client")
    return True


def create_websocket_endpoint(
    app_slug: str,
    path: str,
    endpoint_name: str,
    handler: Callable[[Any, dict[str, Any]], Awaitable[None]] | None = None,
    require_auth: bool = True,
    ping_interval: int = 30,
) -> Callable:
    """
    Create a WebSocket endpoint handler for an app.

    WebSocket support is OPTIONAL - this will raise ImportError if dependencies are missing.

    This function returns a FastAPI WebSocket route handler that:
    - Manages connections via WebSocketConnectionManager
    - Handles authentication if required
    - Routes messages to registered handlers (via register_message_handler)
    - Provides automatic ping/pong for keepalive

    Args:
        app_slug: App slug
        path: WebSocket path (e.g., "/ws", "/events")
        endpoint_name: Endpoint name (key in manifest.json) - used to lookup handlers
        handler: Optional async function to handle incoming messages
                 (deprecated, use register_message_handler instead).
                 Signature: async def handler(websocket, message: Dict[str, Any]) -> None
        require_auth: Whether to require authentication (default: True)
        ping_interval: Ping interval in seconds (default: 30)

    Returns:
        FastAPI WebSocket route handler function

    Raises:
        ImportError: If WebSocket dependencies are not available
    """
    if not WEBSOCKETS_AVAILABLE:
        raise ImportError(
            "WebSocket support is not available. "
            "FastAPI WebSocket support must be installed to use WebSocket endpoints. "
            "Install with: pip install 'fastapi[standard]' or ensure WebSocket "
            "support is available."
        )

    # Get manager (will be created if needed)
    # Note: In async context, use await get_websocket_manager()
    # For route creation, we use sync version since routes are created at startup
    manager = get_websocket_manager_sync(app_slug)

    # Use proper WebSocket type if available, otherwise Any
    websocket_type = WebSocket if WEBSOCKETS_AVAILABLE else Any

    async def websocket_endpoint(websocket: websocket_type):
        """WebSocket endpoint handler with authentication and app isolation."""
        logger.info(f"[WEBSOCKET HANDLER CALLED] App: '{app_slug}', Path: {path}")

        connection = None
        try:
            # Log connection attempt with query params (can access before accept)
            query_str = "None"
            try:
                if hasattr(websocket, "query_params"):
                    if websocket.query_params:
                        query_str = str(dict(websocket.query_params))
                    else:
                        query_str = "Empty"
                else:
                    query_str = "No query_params attr"
            except (AttributeError, TypeError) as e:
                query_str = f"Error accessing query_params: {e}"

            logger.info(
                f"WebSocket connection attempt for app '{app_slug}' "
                f"(require_auth={require_auth}, query_params={query_str})"
            )

            # CRITICAL: Accept FIRST, then validate (FastAPI requires immediate accept)
            # If we don't accept immediately, FastAPI closes with code 1000
            # We'll validate origin after accept, but before processing messages
            try:
                await websocket.accept()
                logger.info(f"WebSocket accepted for app '{app_slug}' (before origin validation)")
            except (RuntimeError, ConnectionError, OSError, ValueError) as accept_error:
                logger.error(
                    f"Failed to accept WebSocket for app '{app_slug}': {accept_error}",
                    exc_info=True,
                )
                return

            # CRITICAL: Validate origin AFTER accepting (CSWSH protection)
            # We accept first to satisfy FastAPI's requirements, then validate
            origin_valid = await _validate_websocket_origin_in_handler(websocket, app_slug)
            if not origin_valid:
                logger.error(
                    f"WebSocket origin validation FAILED for app '{app_slug}' - " f"closing connection after accept"
                )
                try:
                    await websocket.close(code=1008, reason="Invalid origin")
                except (RuntimeError, ConnectionError, OSError):
                    # WebSocket may already be closed or in invalid state
                    pass
                # Raise WebSocketDisconnect so TestClient can detect the rejection
                raise WebSocketDisconnect(code=1008, reason="Invalid origin")

            # CRITICAL: Authenticate BEFORE accepting connection
            # This prevents CSRF middleware from rejecting established connections
            # We can access headers/query_params before accept() is called

            # Debug: Log cookies before authentication
            try:
                cookies = _get_cookies_from_websocket(websocket)
                cookie_names = list(cookies.keys()) if cookies else []
                logger.info(f"WebSocket cookies for app '{app_slug}': {cookie_names} " f"(require_auth={require_auth})")
            except (AttributeError, TypeError, KeyError, RuntimeError) as cookie_error:
                logger.warning(f"Could not extract cookies for debugging: {cookie_error}")

            user_id, user_email = await authenticate_websocket(websocket, app_slug, require_auth)

            # Handle authentication failure
            if require_auth and not user_id:
                logger.error(
                    f"WebSocket authentication FAILED for app '{app_slug}' - "
                    f"rejecting connection. require_auth={require_auth}, "
                    f"user_id={user_id}, user_email={user_email}"
                )
                # Close connection since we already accepted it
                try:
                    await websocket.close(code=1008, reason="Authentication failed")
                except (RuntimeError, ConnectionError, OSError):
                    # WebSocket may already be closed or in invalid state
                    pass
                # Raise WebSocketDisconnect so TestClient can detect the close
                # WebSocketDisconnect is already imported at module level
                raise WebSocketDisconnect(code=1008, reason="Authentication failed")

            # Connection already accepted at line 984 - no need to accept again
            # Connect with metadata (websocket already accepted)
            # Ensure user_id is a string for consistent storage and filtering
            normalized_user_id = str(user_id) if user_id else None
            logger.info(
                f"Connecting WebSocket for app '{app_slug}': "
                f"user_id={normalized_user_id} (type: {type(normalized_user_id).__name__}), "
                f"user_email={user_email}"
            )
            connection = await manager.connect(websocket, user_id=normalized_user_id, user_email=user_email)

            # Send initial connection confirmation
            await manager.send_to_connection(
                websocket,
                {
                    "type": "connected",
                    "app_slug": app_slug,
                    "message": "WebSocket connected successfully",
                    "authenticated": user_id is not None,
                    "user_email": user_email,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            # Keep connection alive and handle messages
            while True:
                try:
                    # Wait for messages with timeout for ping/pong
                    message = await asyncio.wait_for(websocket.receive(), timeout=float(ping_interval))

                    should_continue = await _handle_websocket_message(
                        websocket, message, manager, app_slug, endpoint_name, handler
                    )
                    if not should_continue:
                        break

                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    try:
                        await manager.send_to_connection(
                            websocket,
                            {
                                "type": "ping",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    except (
                        WebSocketDisconnect,
                        RuntimeError,
                        OSError,
                    ):
                        # Type 2: Recoverable - connection error during ping, break loop
                        break

                except (
                    WebSocketDisconnect,
                    RuntimeError,
                    OSError,
                ) as e:
                    error_msg = str(e).lower()
                    if any(keyword in error_msg for keyword in ["disconnect", "closed", "connection", "broken"]):
                        logger.info(f"WebSocket disconnected for app '{app_slug}': {e}")
                        break
                    logger.warning(f"WebSocket receive error for app '{app_slug}': {e}")
                    await asyncio.sleep(0.1)

        except WebSocketDisconnect as e:
            # Re-raise WebSocketDisconnect so TestClient can detect it
            logger.warning(f"WebSocket connection rejected for app '{app_slug}': {e}")
            raise
        except (
            RuntimeError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            logger.error(f"WebSocket connection error for app '{app_slug}': {e}", exc_info=True)
        finally:
            if connection:
                manager.disconnect(websocket)

    return websocket_endpoint


async def broadcast_to_app(app_slug: str, message: dict[str, Any], user_id: str | None = None) -> int:
    """
    Convenience function to broadcast a message to all WebSocket clients for an app.

    This is the simplest way to send WebSocket messages from anywhere in your app code.
    Messages are automatically scoped to the app for security.

    When a non-InProcess :class:`BroadcastBackend` is configured (see
    ``mdb_engine.routing._broadcast``), this function also publishes the
    message through the backend so that other processes can deliver it to
    their local connections.

    Args:
        app_slug: App slug (ensures isolation)
        message: Message dictionary to broadcast
        user_id: Optional user_id to filter recipients (if None, broadcasts to all)

    Returns:
        Number of clients that received the message *on this process*

    Example:
        ```python
        from mdb_engine.routing.websockets import broadcast_to_app

        # Broadcast to all clients for this app
        await broadcast_to_app("my_app", {
            "type": "update",
            "data": {"status": "completed"}
        })

        # Broadcast to specific user only
        await broadcast_to_app("my_app", {
            "type": "notification",
            "data": {"message": "Hello"}
        }, user_id="user123")
        ```
    """
    # Publish through the broadcast backend for cross-process delivery
    try:
        from ._broadcast import InProcessBackend, get_broadcast_backend

        backend = get_broadcast_backend()
        if not isinstance(backend, InProcessBackend):
            envelope = {
                "app_slug": app_slug,
                "message": message,
                "user_id": user_id,
                "_source": "broadcast_to_app",
            }
            await backend.publish(f"ws:{app_slug}", envelope)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.debug("Broadcast backend publish failed; falling back to local-only", exc_info=True)

    # Always deliver to local connections
    manager = await get_websocket_manager(app_slug)
    return await manager.broadcast(message, filter_by_user=user_id)


# ── High-level utilities (Suggestion #14) ────────────────────────────


def authenticated_websocket(func: Callable) -> Callable:
    """Decorator that validates the ``mdb_auth_token`` cookie before
    handing the WebSocket to the decorated handler.

    The decorated function receives ``(websocket, user)`` where *user*
    is the validated user dict from :class:`SharedUserPool`.

    Usage::

        from mdb_engine.routing.websockets import authenticated_websocket

        @app.websocket("/ws")
        @authenticated_websocket
        async def ws_endpoint(websocket: WebSocket, user: dict):
            await websocket.send_json({"hello": user["email"]})
    """
    from functools import wraps

    @wraps(func)
    async def wrapper(websocket: WebSocket, *args: Any, **kwargs: Any):
        token = websocket.cookies.get("mdb_auth_token")
        if not token:
            await websocket.close(code=4001, reason="Missing auth cookie")
            return

        pool = getattr(websocket.app.state, "user_pool", None)
        if not pool:
            await websocket.close(code=4003, reason="Auth not configured")
            return

        user = await pool.validate_token(token)
        if not user:
            await websocket.close(code=4001, reason="Invalid token")
            return

        await func(websocket, user, *args, **kwargs)

    return wrapper


class RoomManager:
    """Lightweight room/channel abstraction on top of raw WebSocket connections.

    Usage::

        rooms = RoomManager()

        @app.websocket("/ws/chat")
        @authenticated_websocket
        async def chat_ws(ws: WebSocket, user: dict):
            room = f"user:{user['_id']}"
            await rooms.connect(ws, room)
            try:
                async for data in ws.iter_json():
                    await rooms.broadcast(room, data)
            finally:
                rooms.disconnect(ws, room)
    """

    def __init__(self):
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, ws: WebSocket, room: str) -> None:
        await ws.accept()
        self._rooms.setdefault(room, set()).add(ws)

    def disconnect(self, ws: WebSocket, room: str) -> None:
        conns = self._rooms.get(room)
        if conns:
            conns.discard(ws)
            if not conns:
                del self._rooms[room]

    async def broadcast(self, room: str, message: dict[str, Any]) -> int:
        """Send *message* to every connection in *room*.  Returns count sent."""
        conns = self._rooms.get(room, set())
        sent = 0
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
                sent += 1
            except (RuntimeError, ConnectionError):
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)
        return sent

    @property
    def room_names(self) -> list[str]:
        return list(self._rooms.keys())

    def connection_count(self, room: str | None = None) -> int:
        if room:
            return len(self._rooms.get(room, set()))
        return sum(len(c) for c in self._rooms.values())
