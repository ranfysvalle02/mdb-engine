"""
WebSocket Ticket Store for Multi-App SSO

Manages short-lived, single-use tickets for WebSocket authentication.
Tickets are exchanged for JWT tokens and consumed immediately upon validation.

This module is part of MDB_ENGINE - MongoDB Engine.

Security Model:
- Tickets generated on authentication (JWT → Ticket exchange)
- Stored in-memory (no database)
- Short TTL (10 seconds default)
- Single-use (consumed immediately after validation)
- Secure-by-default for multi-app SSO setups
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Ticket configuration
DEFAULT_TICKET_TTL_SECONDS = 10  # Tickets expire after 10 seconds


class WebSocketTicketStore:
    """
    Manages WebSocket tickets using in-memory storage.

    Tickets are:
    - Generated on JWT → Ticket exchange
    - Stored in-memory dictionary
    - Validated and consumed immediately (single-use)
    - Automatically expired after TTL
    """

    def __init__(self, ticket_ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS):
        """
        Initialize the WebSocket ticket store.

        Args:
            ticket_ttl_seconds: Ticket time-to-live in seconds (default: 10)
        """
        self._tickets: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._ticket_ttl = ticket_ttl_seconds
        logger.info(f"Initialized WebSocket ticket store (TTL: {ticket_ttl_seconds}s)")

    @property
    def ticket_ttl(self) -> int:
        """
        Get the ticket time-to-live in seconds.

        Returns:
            Ticket TTL in seconds
        """
        return self._ticket_ttl

    def create_ticket(
        self,
        user_id: str,
        user_email: str | None = None,
        app_slug: str | None = None,
    ) -> str:
        """
        Create a new WebSocket ticket.

        Args:
            user_id: User ID
            user_email: Optional user email
            app_slug: Optional app slug for scoping

        Returns:
            Ticket UUID string
        """
        ticket_id = str(uuid.uuid4())
        expires_at = time.time() + self._ticket_ttl

        ticket_data = {
            "user_id": user_id,
            "user_email": user_email,
            "app_slug": app_slug,
            "exp": expires_at,
            "created_at": time.time(),
        }

        # Thread-safe ticket creation
        self._tickets[ticket_id] = ticket_data

        logger.debug(
            f"Created WebSocket ticket for user '{user_id}' "
            f"(app: {app_slug}, expires in {self._ticket_ttl}s)"
        )

        return ticket_id

    async def validate_and_consume_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """
        Validate and consume a WebSocket ticket (atomic operation).

        This method validates the ticket and removes it immediately,
        ensuring single-use behavior.

        Args:
            ticket_id: Ticket UUID to validate

        Returns:
            Ticket data dict if valid, None otherwise
        """
        async with self._lock:
            # Check if ticket exists
            if ticket_id not in self._tickets:
                logger.warning(f"WebSocket ticket not found: {ticket_id[:16]}...")
                return None

            ticket_data = self._tickets[ticket_id]

            # Check expiration
            if time.time() > ticket_data["exp"]:
                logger.warning(
                    f"WebSocket ticket expired: {ticket_id[:16]}... "
                    f"(expired: {ticket_data['exp']})"
                )
                # Remove expired ticket
                del self._tickets[ticket_id]
                return None

            # CONSUME TICKET (atomic operation - remove immediately)
            # This ensures single-use behavior
            user_id = ticket_data["user_id"]
            user_email = ticket_data["user_email"]
            app_slug = ticket_data.get("app_slug")

            # Remove ticket before returning (single-use)
            del self._tickets[ticket_id]

            logger.debug(
                f"Validated and consumed WebSocket ticket for user '{user_id}' "
                f"(app: {app_slug})"
            )

            return {
                "user_id": user_id,
                "user_email": user_email,
                "app_slug": app_slug,
            }

    async def cleanup_expired_tickets(self) -> int:
        """
        Clean up expired tickets.

        Returns:
            Number of tickets cleaned up
        """
        async with self._lock:
            now = time.time()
            expired_tickets = [
                ticket_id
                for ticket_id, ticket_data in self._tickets.items()
                if ticket_data["exp"] < now
            ]

            for ticket_id in expired_tickets:
                del self._tickets[ticket_id]

            if expired_tickets:
                logger.debug(f"Cleaned up {len(expired_tickets)} expired WebSocket tickets")

            return len(expired_tickets)

    def get_ticket_count(self) -> int:
        """Get the number of active tickets."""
        return len(self._tickets)


def create_websocket_ticket_endpoint(
    ticket_store: WebSocketTicketStore,
) -> Callable:
    """
    Create a FastAPI endpoint for generating WebSocket tickets.

    This endpoint requires authentication and generates a new one-time ticket
    for the authenticated user. The ticket is short-lived (10 seconds) and
    single-use.

    Args:
        ticket_store: WebSocketTicketStore instance

    Returns:
        FastAPI route handler function

    Example:
        ```python
        from mdb_engine.auth.websocket_tickets import (
            WebSocketTicketStore,
            create_websocket_ticket_endpoint,
        )

        # Initialize ticket store
        ticket_store = WebSocketTicketStore(ticket_ttl_seconds=10)

        # Create endpoint
        endpoint = create_websocket_ticket_endpoint(ticket_store)
        app.post("/auth/ticket")(endpoint)
        ```

    The endpoint:
    - Requires authentication (user must be logged in)
    - Returns JSON: `{"ticket": "...", "expires_in": 10}`
    - Uses user info from `request.state.user` (set by SharedAuthMiddleware)
    """
    from fastapi import Request, status
    from fastapi.responses import JSONResponse

    async def websocket_ticket_endpoint(request: Request) -> JSONResponse:
        """
        Generate a WebSocket ticket for the authenticated user.

        Requires:
        - User to be authenticated (via request.state.user or auth cookie)
        - WebSocket ticket store to be available

        Returns:
        - JSONResponse with ticket and expires_in
        """
        # Check if user is authenticated (set by middleware)
        user = getattr(request.state, "user", None)

        # If not set by middleware, try to authenticate using cookie
        # This handles the case where endpoint is on parent app without auth middleware
        if not user:
            from .shared_middleware import AUTH_COOKIE_NAME

            # Get user pool from app state
            user_pool = None
            try:
                if hasattr(request, "app") and hasattr(request.app, "state"):
                    user_pool = getattr(request.app.state, "user_pool", None)
            except (AttributeError, TypeError):
                pass

            # Only try to authenticate if we have a real user pool (not None)
            if user_pool is not None:
                # Extract token from cookie
                token = None
                try:
                    if hasattr(request, "cookies"):
                        token = request.cookies.get(AUTH_COOKIE_NAME)
                except (AttributeError, TypeError):
                    pass

                if token:
                    try:
                        # Validate token and get user
                        user = await user_pool.validate_token(token)
                    except (TypeError, AttributeError):
                        # If user_pool is a mock that can't be awaited, ignore
                        pass

            if not user:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Authentication required"},
                )

        # Extract user info
        # Prefer user_id, sub (JWT standard), or _id (MongoDB document ID)
        user_id = user.get("user_id") or user.get("sub") or user.get("_id")
        if not user_id:
            # Email is not a valid user_id - it's just metadata
            logger.error("Cannot generate WebSocket ticket: user_id not found in user data")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid user data"},
            )
        user_email = user.get("email")
        app_slug = getattr(request.state, "app_slug", None)

        try:
            # Generate ticket
            ticket = ticket_store.create_ticket(
                user_id=str(user_id),
                user_email=user_email,
                app_slug=app_slug,
            )

            logger.info(f"Generated WebSocket ticket for user '{user_id}' " f"(app: {app_slug})")

            return JSONResponse(
                {
                    "ticket": ticket,
                    "expires_in": ticket_store.ticket_ttl,
                }
            )

        except (ValueError, TypeError, AttributeError, RuntimeError):
            logger.exception("Failed to generate WebSocket ticket")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Failed to generate WebSocket ticket"},
            )

    return websocket_ticket_endpoint
