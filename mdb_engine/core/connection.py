"""
Connection management for MongoDB Engine.

This module handles MongoDB connection initialization, shutdown, and
connection pool configuration. Supports optional Client-Side Field Level
Encryption (CSFLE) for automatic field encryption.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from ..constants import (
    DEFAULT_MAX_IDLE_TIME_MS,
    DEFAULT_MAX_POOL_SIZE,
    DEFAULT_MIN_POOL_SIZE,
    DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
)
from ..exceptions import InitializationError
from ..observability import get_logger as get_contextual_logger
from ..observability import record_operation

if TYPE_CHECKING:
    from .csfle import CSFLEConfig

logger = logging.getLogger(__name__)
contextual_logger = get_contextual_logger(__name__)


class ConnectionManager:
    """
    Manages MongoDB connection lifecycle and configuration.

    Handles connection initialization, validation, and shutdown.
    Supports optional Client-Side Field Level Encryption (CSFLE).
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        max_pool_size: int = DEFAULT_MAX_POOL_SIZE,
        min_pool_size: int = DEFAULT_MIN_POOL_SIZE,
        csfle_config: CSFLEConfig | None = None,
    ) -> None:
        """
        Initialize the connection manager.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            max_pool_size: Maximum MongoDB connection pool size
            min_pool_size: Minimum MongoDB connection pool size
            csfle_config: Optional CSFLE configuration for field-level encryption
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self.csfle_config = csfle_config

        # Connection state
        self._mongo_client: AsyncIOMotorClient | None = None
        self._mongo_db: AsyncIOMotorDatabase | None = None
        self._initialized: bool = False
        self._csfle_enabled: bool = False

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
            logger.warning("ConnectionManager already initialized. Skipping re-initialization.")
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
            # Build client kwargs
            client_kwargs: dict[str, Any] = {
                "serverSelectionTimeoutMS": DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
                "appname": "MDB_ENGINE",
                "maxPoolSize": self.max_pool_size,
                "minPoolSize": self.min_pool_size,
                "maxIdleTimeMS": DEFAULT_MAX_IDLE_TIME_MS,
                "retryWrites": True,
                "retryReads": True,
            }

            # Add CSFLE auto-encryption if configured
            if self.csfle_config and self.csfle_config.enabled:
                try:
                    from .csfle import build_auto_encryption_opts

                    auto_enc_opts = await asyncio.to_thread(
                        build_auto_encryption_opts, self.csfle_config, self.mongo_uri, self.db_name
                    )
                    if auto_enc_opts:
                        client_kwargs["auto_encryption_opts"] = auto_enc_opts
                        self._csfle_enabled = True
                        contextual_logger.info(
                            "CSFLE auto-encryption enabled",
                            extra={
                                "kms_provider": self.csfle_config.kms_provider,
                                "encrypted_collections": list(self.csfle_config.encrypted_collections.keys()),
                            },
                        )
                    else:
                        contextual_logger.warning(
                            "CSFLE requested but could not be configured. " "Continuing without field-level encryption."
                        )
                except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                    contextual_logger.warning(
                        f"Failed to configure CSFLE: {e}. " f"Continuing without field-level encryption."
                    )

            # Connect to MongoDB
            self._mongo_client = AsyncIOMotorClient(self.mongo_uri, **client_kwargs)

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
                    "csfle_enabled": self._csfle_enabled,
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
        assert self._mongo_client is not None, "MongoDB client should not be None after initialization"
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
        # Allow None for testing scenarios where mongo_db is patched
        return self._mongo_db

    @mongo_db.deleter
    def mongo_db(self) -> None:
        """Allow deletion of mongo_db property for testing purposes."""
        self._mongo_db = None

    @mongo_db.setter
    def mongo_db(self, value: AsyncIOMotorDatabase | None) -> None:
        """Allow setting mongo_db property for testing purposes."""
        self._mongo_db = value

    @property
    def initialized(self) -> bool:
        """Check if connection is initialized."""
        return self._initialized

    @property
    def csfle_enabled(self) -> bool:
        """Check if Client-Side Field Level Encryption is enabled."""
        return self._csfle_enabled
