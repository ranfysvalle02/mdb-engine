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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
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
            self.connected_at = datetime.utcnow()


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
                self.active_connections = [
                    conn for conn in self.active_connections if conn.websocket is not websocket
                ]
            logger.info(
                f"WebSocket disconnected for app '{self.app_slug}'. "
                f"Remaining connections: {len(self.active_connections)}"
            )

        asyncio.create_task(_disconnect())

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
            "timestamp": datetime.utcnow().isoformat(),
        }
        message_json = json.dumps(message_with_context)
        disconnected = []
        sent_count = 0

        async with self._lock:
            connections = list(self.active_connections)

        for connection in connections:
            # Filter by user if specified
            if filter_by_user and connection.user_id != filter_by_user:
                continue

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
                "timestamp": datetime.utcnow().isoformat(),
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


async def authenticate_websocket(
    websocket: Any,
    app_slug: str,
    require_auth: bool = True,
) -> tuple[str | None, str | None]:
    """
    Authenticate a WebSocket connection via session key or httpOnly cookies.

    Authentication methods (in order of preference):
    1. Session key (query param or header) - secure-by-default, uses envelope encryption
    2. Cookie-based authentication - backward compatibility fallback

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
        raise ImportError(
            "WebSocket support is not available. FastAPI WebSocket support must be installed."
        )

    from fastapi import WebSocketDisconnect

    if not require_auth:
        return None, None

    try:
        # Try to get WebSocket session manager from app
        websocket_session_manager = None
        try:
            app = getattr(websocket, "app", None)
            if app:
                websocket_session_manager = getattr(app.state, "websocket_session_manager", None)
        except (AttributeError, TypeError):
            pass

        # Method 1: Try session key authentication (secure-by-default)
        session_key = None
        try:
            # Check query params first
            if hasattr(websocket, "query_params"):
                session_key = websocket.query_params.get("session_key")

            # Check headers if not in query params
            if not session_key and hasattr(websocket, "headers"):
                session_key = websocket.headers.get("X-WebSocket-Session-Key")
        except (AttributeError, TypeError, KeyError):
            pass

        if session_key and websocket_session_manager:
            try:
                # Validate session key
                session_data = await websocket_session_manager.validate_session(session_key)
                if session_data:
                    user_id = session_data.get("user_id")
                    user_email = session_data.get("user_email")

                    logger.info(
                        f"WebSocket authenticated successfully for app '{app_slug}': {user_email} "
                        f"(method: session_key)"
                    )
                    return user_id, user_email
                else:
                    logger.warning(
                        f"WebSocket session key validation failed for app '{app_slug}'. "
                        f"Session key: {session_key[:16]}..."
                    )
            except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as e:
                logger.warning(f"WebSocket session key validation error for app '{app_slug}': {e}")
                # Fall through to cookie-based auth

        # Method 2: Fall back to cookie-based authentication (backward compatibility)
        from ..auth.shared_middleware import AUTH_COOKIE_NAME

        cookies = _get_cookies_from_websocket(websocket)
        token = cookies.get(AUTH_COOKIE_NAME)  # Use mdb_auth_token (same as shared middleware)

        if not token:
            logger.error(
                f"❌ No authentication found for WebSocket connection to app '{app_slug}' "
                f"(require_auth={require_auth}). "
                f"Session key: {bool(session_key)}, Cookie: {bool(token)}, "
                f"Available cookies: {list(cookies.keys()) if cookies else 'none'}. "
                f"Ensure session key or httpOnly cookie is set during authentication."
            )
            if require_auth:
                return None, None  # Signal auth failure
            return None, None

        logger.info(
            f"WebSocket token found in cookie for app '{app_slug}' "
            "(cookie-based authentication, fallback)"
        )

        # Decode and validate token
        import jwt

        from ..auth.dependencies import SECRET_KEY
        from ..auth.jwt import decode_jwt_token

        try:
            payload = decode_jwt_token(token, str(SECRET_KEY))
            user_id = payload.get("sub") or payload.get("user_id")
            user_email = payload.get("email")

            logger.info(
                f"WebSocket authenticated successfully for app '{app_slug}': {user_email} "
                f"(method: cookie)"
            )
            return user_id, user_email
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            logger.exception(
                f"❌ JWT decode error for app '{app_slug}'. "
                f"Token present: {bool(token)}, Token length: {len(token) if token else 0}"
            )
            raise

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


def get_message_handler(
    app_slug: str, endpoint_name: str
) -> Callable[[Any, dict[str, Any]], Awaitable[None]] | None:
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
        logger.info(f"✅ WebSocket accepted for app '{app_slug}'")

        print(f"✅ [WEBSOCKET ACCEPTED] App: '{app_slug}'")
    except (RuntimeError, ConnectionError, OSError) as accept_error:
        print(f"❌ [WEBSOCKET ACCEPT FAILED] App: '{app_slug}', Error: {accept_error}")
        logger.error(
            f"❌ Failed to accept WebSocket for app '{app_slug}': {accept_error}",
            exc_info=True,
        )
        raise


async def _authenticate_websocket_connection(
    websocket: Any, app_slug: str, require_auth: bool
) -> tuple:
    """Authenticate WebSocket connection and return (user_id, user_email)."""
    try:
        user_id, user_email = await authenticate_websocket(websocket, app_slug, require_auth)

        if require_auth and not user_id:
            logger.warning(
                f"WebSocket authentication failed for app '{app_slug}' - closing connection"
            )
            try:
                await websocket.close(code=1008, reason="Authentication required")
            except (WebSocketDisconnect, RuntimeError, OSError) as e:
                logger.debug(f"WebSocket already closed during auth failure cleanup: {e}")
            raise WebSocketDisconnect(code=1008)

        return user_id, user_email

    except WebSocketDisconnect:
        logger.warning(
            f"WebSocket connection rejected for app '{app_slug}' - authentication failed"
        )
        raise
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        RuntimeError,
    ) as auth_error:
        logger.error(
            f"Unexpected error during WebSocket authentication for app "
            f"'{app_slug}': {auth_error}",
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

                current_handler = (
                    handler if handler else get_message_handler(app_slug, endpoint_name)
                )

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
        # CRITICAL: Log immediately - this proves the handler is being called
        # This print should appear in server logs when a WebSocket connection is attempted
        import sys

        print(
            f"🔌 [WEBSOCKET HANDLER CALLED] App: '{app_slug}', Path: {path}",
            file=sys.stderr,
            flush=True,
        )
        print(f"🔌 [WEBSOCKET HANDLER CALLED] App: '{app_slug}', Path: {path}", flush=True)
        logger.info(f"🔌 [WEBSOCKET HANDLER CALLED] App: '{app_slug}', Path: {path}")
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

            # CRITICAL: Log immediately to verify handler is being called
            print(
                f"🔌 [WEBSOCKET HANDLER CALLED] App: '{app_slug}', Path: {path}, Query: {query_str}"
            )
            logger.info(
                f"🔌 WebSocket connection attempt for app '{app_slug}' "
                f"(require_auth={require_auth}, query_params={query_str})"
            )

            # CRITICAL: Authenticate BEFORE accepting connection
            # This prevents CSRF middleware from rejecting established connections
            # We can access headers/query_params before accept() is called

            # Debug: Log cookies before authentication
            try:
                cookies = _get_cookies_from_websocket(websocket)
                cookie_names = list(cookies.keys()) if cookies else []
                logger.info(
                    f"🔍 WebSocket cookies for app '{app_slug}': {cookie_names} "
                    f"(require_auth={require_auth})"
                )
            except (AttributeError, TypeError, KeyError, RuntimeError) as cookie_error:
                logger.warning(f"Could not extract cookies for debugging: {cookie_error}")

            user_id, user_email = await authenticate_websocket(websocket, app_slug, require_auth)

            # Handle authentication failure
            if require_auth and not user_id:
                logger.error(
                    f"❌ WebSocket authentication FAILED for app '{app_slug}' - "
                    f"rejecting connection. require_auth={require_auth}, "
                    f"user_id={user_id}, user_email={user_email}"
                )
                # Reject without accepting - FastAPI will send 403 if accept() not called
                # We can't call websocket.close() before accept(), so we just return
                # The connection will be rejected by the server
                return

            # Accept connection
            await _accept_websocket_connection(websocket, app_slug)

            # Connect with metadata (websocket already accepted)
            connection = await manager.connect(websocket, user_id=user_id, user_email=user_email)

            # Send initial connection confirmation
            await manager.send_to_connection(
                websocket,
                {
                    "type": "connected",
                    "app_slug": app_slug,
                    "message": "WebSocket connected successfully",
                    "authenticated": user_id is not None,
                    "user_email": user_email,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            # Keep connection alive and handle messages
            while True:
                try:
                    # Wait for messages with timeout for ping/pong
                    message = await asyncio.wait_for(
                        websocket.receive(), timeout=float(ping_interval)
                    )

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
                                "timestamp": datetime.utcnow().isoformat(),
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
                    if any(
                        keyword in error_msg
                        for keyword in ["disconnect", "closed", "connection", "broken"]
                    ):
                        logger.info(f"WebSocket disconnected for app '{app_slug}': {e}")
                        break
                    logger.warning(f"WebSocket receive error for app '{app_slug}': {e}")
                    await asyncio.sleep(0.1)

        except (
            WebSocketDisconnect,
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


async def broadcast_to_app(
    app_slug: str, message: dict[str, Any], user_id: str | None = None
) -> int:
    """
    Convenience function to broadcast a message to all WebSocket clients for an app.

    This is the simplest way to send WebSocket messages from anywhere in your app code.
    Messages are automatically scoped to the app for security.

    Args:
        app_slug: App slug (ensures isolation)
        message: Message dictionary to broadcast
        user_id: Optional user_id to filter recipients (if None, broadcasts to all)

    Returns:
        Number of clients that received the message

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
    manager = await get_websocket_manager(app_slug)
    return await manager.broadcast(message, filter_by_user=user_id)
