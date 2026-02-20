"""
Route registration and WebSocket support.

Handles FastAPI route mounting, middleware configuration, and WebSocket endpoints.

WebSocket support is OPTIONAL and only enabled when:
1. Apps define "websockets" in their manifest.json
2. WebSocket dependencies are available (FastAPI WebSocket support)

If WebSockets are not configured or dependencies are missing, the engine
gracefully degrades without WebSocket functionality.
"""

# WebSocket support is optional - lazy import to avoid breaking if dependencies missing
_websockets_available = None
_websockets_module = None


def _check_websockets_available():
    """Check if WebSocket support is available."""
    global _websockets_available, _websockets_module
    if _websockets_available is None:
        try:
            from fastapi import WebSocket

            _websockets_module = __import__(".websockets", fromlist=[""], package=__name__)
            _websockets_available = True
        except (ImportError, AttributeError):
            _websockets_available = False
            _websockets_module = None
    return _websockets_available


def _get_websocket_attr(name):
    """Lazy getter for WebSocket attributes - raises ImportError if not available."""
    if not _check_websockets_available():
        raise ImportError(
            "WebSocket support is not available. "
            "WebSockets must be defined in manifest.json and FastAPI "
            "WebSocket support must be installed."
        )
    return getattr(_websockets_module, name)


# Lazy exports - only available if WebSockets are configured and dependencies exist
def __getattr__(name):
    """Lazy attribute access for WebSocket exports."""
    if name in [
        "WebSocketConnectionManager",
        "WebSocketConnection",
        "get_websocket_manager",
        "create_websocket_endpoint",
        "authenticate_websocket",
        "broadcast_to_app",
        "register_message_handler",
        "get_message_handler",
    ]:
        return _get_websocket_attr(name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "WebSocketConnectionManager",
    "WebSocketConnection",
    "get_websocket_manager",
    "create_websocket_endpoint",
    "authenticate_websocket",
    "broadcast_to_app",  # Simplest way to broadcast from app code
    "register_message_handler",  # Register handlers to listen to client messages
    "get_message_handler",
]
