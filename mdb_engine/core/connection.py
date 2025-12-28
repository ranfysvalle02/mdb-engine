"""
Connection management for MongoDB Engine.

This module handles MongoDB connection initialization, shutdown, and
connection pool configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import time
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ..constants import (DEFAULT_MAX_IDLE_TIME_MS, DEFAULT_MAX_POOL_SIZE,
                         DEFAULT_MIN_POOL_SIZE,
                         DEFAULT_SERVER_SELECTION_TIMEOUT_MS)
from ..exceptions import InitializationError
from ..observability import get_logger as get_contextual_logger
from ..observability import record_operation

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)


class ConnectionManager:
    """
    Manages MongoDB connection lifecycle and configuration.

    Handles connection initialization, validation, and shutdown.
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
    ) -> None:
        """
        Initialize the connection manager.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            max_pool_size: Maximum MongoDB connection pool size
            min_pool_size: Minimum MongoDB connection pool size
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size

        # Connection state
        self._mongo_client: Optional[AsyncIOMotorClient] = None
        self._mongo_db: Optional[AsyncIOMotorDatabase] = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """
        Initialize the MongoDB connection.

        This method:
        1. Connects to MongoDB
        2. Validates the connection
        3. Sets up initial state

        Raises:
            InitializationError: If initialization fails
        """
        start_time = time.time()

        if self._initialized:
            logger.warning(
                "ConnectionManager already initialized. Skipping re-initialization."
            )
            return

        contextual_logger.info(
            "Initializing MongoDB connection",
            extra={
                "mongo_uri": self.mongo_uri,
                "db_name": self.db_name,
                "max_pool_size": self.max_pool_size,
                "min_pool_size": self.min_pool_size,
            },
        )

        try:
            # Connect to MongoDB
            self._mongo_client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
                appname="MDB_ENGINE",
                maxPoolSize=self.max_pool_size,
                minPoolSize=self.min_pool_size,
                maxIdleTimeMS=DEFAULT_MAX_IDLE_TIME_MS,
                retryWrites=True,
                retryReads=True,
            )

            # Verify connection
            await self._mongo_client.admin.command("ping")
            self._mongo_db = self._mongo_client[self.db_name]

            # Register client for pool metrics monitoring
            try:
                from ..database.connection import register_client_for_metrics

                register_client_for_metrics(self._mongo_client)
            except ImportError:
                pass  # Optional feature

            self._initialized = True
            duration_ms = (time.time() - start_time) * 1000
            record_operation("connection.initialize", duration_ms, success=True)
            contextual_logger.info(
                "MongoDB connection initialized successfully",
                extra={
                    "db_name": self.db_name,
                    "pool_size": f"{self.min_pool_size}-{self.max_pool_size}",
                    "duration_ms": round(duration_ms, 2),
                },
            )
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            duration_ms = (time.time() - start_time) * 1000
            record_operation("connection.initialize", duration_ms, success=False)
            contextual_logger.critical(
                "MongoDB connection failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )
            raise InitializationError(
                f"Failed to connect to MongoDB: {e}",
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                context={
                    "error_type": type(e).__name__,
                    "max_pool_size": self.max_pool_size,
                    "min_pool_size": self.min_pool_size,
                },
            ) from e
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            # Programming errors - these should not happen
            duration_ms = (time.time() - start_time) * 1000
            record_operation("connection.initialize", duration_ms, success=False)
            contextual_logger.critical(
                "ConnectionManager initialization failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True,
            )
            raise InitializationError(
                f"ConnectionManager initialization failed: {e}",
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
                context={
                    "error_type": type(e).__name__,
                },
            ) from e

    async def shutdown(self) -> None:
        """
        Shutdown the MongoDB connection and clean up resources.

        This method is idempotent - it's safe to call multiple times.
        """
        start_time = time.time()

        if not self._initialized:
            return

        contextual_logger.info("Shutting down MongoDB connection...")

        # Close MongoDB connection
        if self._mongo_client:
            self._mongo_client.close()
            contextual_logger.info("MongoDB connection closed.")

        self._initialized = False
        self._mongo_client = None
        self._mongo_db = None

        duration_ms = (time.time() - start_time) * 1000
        record_operation("connection.shutdown", duration_ms, success=True)
        contextual_logger.info(
            "MongoDB connection shutdown complete",
            extra={"duration_ms": round(duration_ms, 2)},
        )

    @property
    def mongo_client(self) -> AsyncIOMotorClient:
        """
        Get the MongoDB client.

        Returns:
            AsyncIOMotorClient instance

        Raises:
            RuntimeError: If connection is not initialized
        """
        if not self._initialized:
            raise RuntimeError(
                "ConnectionManager not initialized. Call initialize() first.",
            )
        assert (
            self._mongo_client is not None
        ), "MongoDB client should not be None after initialization"
        return self._mongo_client

    @property
    def mongo_db(self) -> AsyncIOMotorDatabase:
        """
        Get the MongoDB database.

        Returns:
            AsyncIOMotorDatabase instance

        Raises:
            RuntimeError: If connection is not initialized
        """
        if not self._initialized:
            raise RuntimeError(
                "ConnectionManager not initialized. Call initialize() first.",
            )
        assert (
            self._mongo_db is not None
        ), "MongoDB database should not be None after initialization"
        return self._mongo_db

    @property
    def initialized(self) -> bool:
        """Check if connection is initialized."""
        return self._initialized
