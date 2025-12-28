"""
Shared MongoDB Connection Pool Manager

Provides a singleton MongoDB connection pool manager for sharing database
connections efficiently across multiple components in the same process.
This prevents each component from creating its own connection pool, reducing
total connections and improving resource utilization.

This module implements a thread-safe singleton pattern that ensures all
components in the same process share a single MongoDB client instance with
a reasonable connection pool size.

This module is part of MDB_ENGINE - MongoDB Engine.

Usage:
    from mdb_engine.database import get_shared_mongo_client, get_pool_metrics

    client = get_shared_mongo_client(mongo_uri)
    db = client[db_name]
    metrics = await get_pool_metrics()  # Monitor pool usage
"""

import logging
import os
import threading
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import (ConnectionFailure, InvalidOperation,
                            OperationFailure, ServerSelectionTimeoutError)

logger = logging.getLogger(__name__)

# Global singleton instance
_shared_client: Optional[AsyncIOMotorClient] = None
# Use threading.Lock for cross-thread safety in multi-threaded environments
# asyncio.Lock isn't sufficient for thread-safe initialization
_init_lock = threading.Lock()


def get_shared_mongo_client(
    mongo_uri: str,
    max_pool_size: Optional[int] = None,
    min_pool_size: Optional[int] = None,
    server_selection_timeout_ms: int = 5000,
    max_idle_time_ms: int = 45000,
    retry_writes: bool = True,
    retry_reads: bool = True,
) -> AsyncIOMotorClient:
    """
    Gets or creates a shared MongoDB client instance.

    This function implements a singleton pattern to ensure all components
    in the same process share a single MongoDB client connection pool.

    Args:
        mongo_uri: MongoDB connection URI
        max_pool_size: Maximum connection pool size (default: from env or 10)
        min_pool_size: Minimum connection pool size (default: from env or 1)
        server_selection_timeout_ms: Server selection timeout in milliseconds
        max_idle_time_ms: Maximum idle time before closing connections
        retry_writes: Enable automatic retry for write operations
        retry_reads: Enable automatic retry for read operations

    Returns:
        Shared AsyncIOMotorClient instance

    Example:
        client = get_shared_mongo_client("mongodb://mongo:27017/")
        db = client["my_database"]
    """
    global _shared_client

    # Get pool sizes from environment or use defaults
    if max_pool_size is None:
        max_pool_size = int(os.getenv("MONGO_ACTOR_MAX_POOL_SIZE", "10"))
    if min_pool_size is None:
        min_pool_size = int(os.getenv("MONGO_ACTOR_MIN_POOL_SIZE", "1"))

    logger.info(
        f"MongoDB Shared Pool Configuration: max_pool_size={max_pool_size}, "
        f"min_pool_size={min_pool_size} (from env or defaults)"
    )

    # Fast path: return existing client if already initialized
    if _shared_client is not None:
        # Verify client is still connected
        try:
            # Non-blocking check - if client was closed, it will be None or invalid
            if (
                hasattr(_shared_client, "_topology")
                and _shared_client._topology is not None
            ):
                return _shared_client
        except (AttributeError, RuntimeError):
            # Client was closed or invalid, reset and recreate
            # Type 2: Recoverable - reset client and continue
            _shared_client = None

    # Thread-safe initialization with lock
    # Use threading lock for multi-threaded environments
    with _init_lock:
        # Double-check pattern: another thread may have initialized while we waited
        if _shared_client is not None:
            try:
                if (
                    hasattr(_shared_client, "_topology")
                    and _shared_client._topology is not None
                ):
                    return _shared_client
            except (AttributeError, RuntimeError):
                # Client was closed or invalid, reset and recreate
                # Type 2: Recoverable - reset client and continue
                _shared_client = None

        logger.info(
            f"Creating shared MongoDB client with pool_size={max_pool_size}, "
            f"min_pool_size={min_pool_size} (singleton for all components in this process)"
        )

        try:
            _shared_client = AsyncIOMotorClient(
                mongo_uri,
                serverSelectionTimeoutMS=server_selection_timeout_ms,
                appname="MDB_ENGINE_Shared",
                maxPoolSize=max_pool_size,
                minPoolSize=min_pool_size,
                maxIdleTimeMS=max_idle_time_ms,
                retryWrites=retry_writes,
                retryReads=retry_reads,
            )

            logger.info(
                f"Shared MongoDB client created successfully "
                f"(max_pool_size={max_pool_size}, min_pool_size={min_pool_size})"
            )
            logger.info(
                f"Connection pool limits: max={max_pool_size}, min={min_pool_size}. "
                f"Monitor pool usage with get_pool_metrics() to prevent exhaustion."
            )

            # Note: Ping verification happens asynchronously on first use
            # This avoids blocking during synchronous initialization

            return _shared_client
        except (
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
        ) as e:
            logger.error(f"Failed to create shared MongoDB client: {e}", exc_info=True)
            _shared_client = None
            raise

    return _shared_client


async def verify_shared_client() -> bool:
    """
    Verifies that the shared MongoDB client is connected.
    Should be called from async context after initialization.

    Returns:
        True if client is connected and responsive, False otherwise
    """
    global _shared_client

    if _shared_client is None:
        logger.warning("Shared MongoDB client is None - cannot verify")
        return False

    try:
        await _shared_client.admin.command("ping")
        logger.debug("Shared MongoDB client verification successful")
        return True
    except (
        ConnectionFailure,
        ServerSelectionTimeoutError,
        OperationFailure,
        InvalidOperation,
    ) as e:
        logger.error(f"Shared MongoDB client verification failed: {e}")
        return False


# Registry for tracking all MongoDB clients (for monitoring)
_registered_clients: list = []


def register_client_for_metrics(client: AsyncIOMotorClient) -> None:
    """
    Register a MongoDB client for pool metrics monitoring.

    This allows MongoDBEngine and other components to register their clients
    so pool metrics can be tracked even if they don't use the shared client.

    Args:
        client: AsyncIOMotorClient instance to register
    """
    global _registered_clients
    if client not in _registered_clients:
        _registered_clients.append(client)
        logger.debug("Registered MongoDB client for pool metrics")


async def get_pool_metrics(
    client: Optional[AsyncIOMotorClient] = None,
) -> Dict[str, Any]:
    """
    Gets connection pool metrics for monitoring.
    Returns information about pool size, active connections, etc.

    Args:
        client: Optional specific client to check. If None, checks shared client first,
                then any registered clients (e.g., MongoDBEngine's client).

    Returns:
        Dictionary with pool metrics including:
        - max_pool_size: Maximum pool size
        - min_pool_size: Minimum pool size
        - active_connections: Current active connections (if available)
        - pool_usage_percent: Estimated pool usage percentage
    """
    global _shared_client, _registered_clients

    # If specific client provided, use it
    if client is not None:
        return await _get_client_pool_metrics(client)

    # Try shared client first
    if _shared_client is not None:
        return await _get_client_pool_metrics(_shared_client)

    # Try registered clients (e.g., MongoDBEngine's client)
    for registered_client in _registered_clients:
        try:
            # Verify client is still valid
            if (
                hasattr(registered_client, "_topology")
                and registered_client._topology is not None
            ):
                return await _get_client_pool_metrics(registered_client)
        except (AttributeError, RuntimeError):
            # Type 2: Recoverable - if this client is invalid, try next one
            continue

    # No client available
    return {
        "status": "no_client",
        "error": "No MongoDB client available for metrics (shared or registered)",
    }


async def _get_client_pool_metrics(client: AsyncIOMotorClient) -> Dict[str, Any]:
    """
    Internal helper to get pool metrics from a specific client.

    Uses MongoDB serverStatus for accurate connection information.

    Args:
        client: AsyncIOMotorClient instance

    Returns:
        Dictionary with pool metrics
    """

    try:
        # Verify client is valid
        if client is None:
            return {"status": "error", "error": "Client is None"}

        # Try to get pool configuration from client options
        max_pool_size = None
        min_pool_size = None

        # Try multiple ways to get pool size (Motor version compatibility)
        try:
            # Method 1: From client options (most reliable)
            if hasattr(client, "options") and client.options:
                max_pool_size = getattr(client.options, "maxPoolSize", None) or getattr(
                    client.options, "max_pool_size", None
                )
                min_pool_size = getattr(client.options, "minPoolSize", None) or getattr(
                    client.options, "min_pool_size", None
                )

            # Method 2: From client attributes directly
            if max_pool_size is None:
                max_pool_size = getattr(client, "maxPoolSize", None) or getattr(
                    client, "max_pool_size", None
                )
            if min_pool_size is None:
                min_pool_size = getattr(client, "minPoolSize", None) or getattr(
                    client, "min_pool_size", None
                )
        except (AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Could not get pool size from client options: {e}")

        # Get connection info from MongoDB serverStatus (most accurate)
        current_connections = None
        available_connections = None
        total_created = None

        try:
            server_status = await client.admin.command("serverStatus")
            connections = server_status.get("connections", {})
            current_connections = connections.get("current", 0)
            available_connections = connections.get("available", 0)
            total_created = connections.get("totalCreated", 0)
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            InvalidOperation,
        ) as e:
            logger.debug(f"Could not get server status for connections: {e}")

        # Build metrics
        metrics = {
            "status": "connected",
        }

        if max_pool_size is not None:
            metrics["max_pool_size"] = max_pool_size
        if min_pool_size is not None:
            metrics["min_pool_size"] = min_pool_size

        # Add connection info from serverStatus
        if current_connections is not None:
            metrics["current_connections"] = current_connections
        if available_connections is not None:
            metrics["available_connections"] = available_connections
        if total_created is not None:
            metrics["total_connections_created"] = total_created

        # Calculate pool usage if we have max_pool_size and current connections
        if max_pool_size and current_connections is not None:
            usage_percent = (current_connections / max_pool_size) * 100
            metrics["pool_usage_percent"] = round(usage_percent, 2)
            metrics["active_connections"] = (
                current_connections  # Alias for compatibility
            )

            # Warn if pool usage is high
            if usage_percent > 80:
                logger.warning(
                    f"MongoDB connection pool usage is HIGH: {usage_percent:.1f}% "
                    f"({current_connections}/{max_pool_size}). Consider increasing max_pool_size."
                )
        elif current_connections is not None:
            # We have connection count but not max pool size - still useful info
            metrics["active_connections"] = current_connections

        return metrics
    except (
        OperationFailure,
        ConnectionFailure,
        ServerSelectionTimeoutError,
        InvalidOperation,
        AttributeError,
        KeyError,
    ) as e:
        logger.debug(f"Error getting detailed pool metrics (non-critical): {e}")
        # Return minimal metrics - client exists and is being used
        return {
            "status": "connected",
            "note": "Detailed metrics unavailable, but client is operational",
            "error": str(e),
        }


def close_shared_client():
    """
    Closes the shared MongoDB client.
    Should be called during application shutdown.
    """
    global _shared_client

    if _shared_client is not None:
        try:
            _shared_client.close()
            logger.info("Shared MongoDB client closed")
        except (InvalidOperation, AttributeError, RuntimeError) as e:
            logger.warning(f"Error closing shared MongoDB client: {e}")
        finally:
            _shared_client = None
