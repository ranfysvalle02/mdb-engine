"""
Configuration management for MDB_ENGINE.

This module provides optional configuration management using Pydantic.
It's designed to be non-breaking - the MongoDBEngine can still be used
with direct parameters as before.
"""

import os
from typing import Optional

try:
    from pydantic import BaseSettings, Field

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

    # Create a dummy BaseSettings for when Pydantic is not available
    class BaseSettings:
        pass


class EngineConfig:
    """
    MongoDB Engine configuration.

    This class provides configuration management with environment variable
    support. It's optional - MongoDBEngine can still be initialized with
    direct parameters for backward compatibility.

    Example:
        # Using environment variables
        config = EngineConfig()
        engine = MongoDBEngine(
            mongo_uri=config.mongo_uri,
            db_name=config.db_name
        )

        # Or using direct parameters (backward compatible)
        engine = MongoDBEngine(
            mongo_uri="mongodb://localhost:27017",
            db_name="my_db"
        )
    """

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        max_pool_size: Optional[int] = None,
        min_pool_size: Optional[int] = None,
        server_selection_timeout_ms: Optional[int] = None,
        authz_cache_ttl: Optional[int] = None,
    ):
        """
        Initialize configuration.

        Args:
            mongo_uri: MongoDB connection URI (defaults to MONGO_URI env var)
            db_name: Database name (defaults to DB_NAME env var)
            max_pool_size: Maximum connection pool size (defaults to 50 or MONGO_MAX_POOL_SIZE)
            min_pool_size: Minimum connection pool size (defaults to 10 or MONGO_MIN_POOL_SIZE)
            server_selection_timeout_ms: Server selection timeout in ms (defaults to 5000)
            authz_cache_ttl: Authorization cache TTL in seconds (defaults to 300)
        """
        self.mongo_uri = mongo_uri or os.getenv("MONGO_URI", "")
        self.db_name = db_name or os.getenv("DB_NAME", "")
        self.max_pool_size = max_pool_size or int(
            os.getenv("MONGO_MAX_POOL_SIZE", "50")
        )
        self.min_pool_size = min_pool_size or int(
            os.getenv("MONGO_MIN_POOL_SIZE", "10")
        )
        self.server_selection_timeout_ms = server_selection_timeout_ms or int(
            os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
        )
        self.authz_cache_ttl = authz_cache_ttl or int(
            os.getenv("AUTHZ_CACHE_TTL", "300")
        )

    def validate(self) -> None:
        """
        Validate configuration values.

        Raises:
            ValueError: If required configuration is missing or invalid
        """
        if not self.mongo_uri:
            raise ValueError(
                "mongo_uri is required (set MONGO_URI environment variable or pass directly)"
            )

        if not self.db_name:
            raise ValueError(
                "db_name is required (set DB_NAME environment variable or pass directly)"
            )

        if self.max_pool_size < 1:
            raise ValueError(f"max_pool_size must be >= 1, got {self.max_pool_size}")

        if self.min_pool_size < 1:
            raise ValueError(f"min_pool_size must be >= 1, got {self.min_pool_size}")

        if self.min_pool_size > self.max_pool_size:
            raise ValueError(
                f"min_pool_size ({self.min_pool_size}) cannot be greater than "
                f"max_pool_size ({self.max_pool_size})"
            )

        if self.server_selection_timeout_ms < 1000:
            raise ValueError(
                f"server_selection_timeout_ms must be >= 1000, got "
                f"{self.server_selection_timeout_ms}"
            )

        if self.authz_cache_ttl < 0:
            raise ValueError(
                f"authz_cache_ttl must be >= 0, got {self.authz_cache_ttl}"
            )


# Pydantic-based configuration (optional, only if Pydantic is available)
if PYDANTIC_AVAILABLE:

    class EngineConfigPydantic(BaseSettings):
        """
        Pydantic-based configuration with automatic validation.

        This is an optional alternative to EngineConfig that provides
        automatic validation and type checking via Pydantic.

        Usage:
            config = EngineConfigPydantic()
            engine = MongoDBEngine(
                mongo_uri=config.mongo_uri,
                db_name=config.db_name
            )
        """

        mongo_uri: str = Field(
            ..., env="MONGO_URI", description="MongoDB connection URI"
        )
        db_name: str = Field(..., env="DB_NAME", description="Database name")
        max_pool_size: int = Field(
            50,
            env="MONGO_MAX_POOL_SIZE",
            ge=1,
            description="Maximum connection pool size",
        )
        min_pool_size: int = Field(
            10,
            env="MONGO_MIN_POOL_SIZE",
            ge=1,
            description="Minimum connection pool size",
        )
        server_selection_timeout_ms: int = Field(
            5000,
            env="MONGO_SERVER_SELECTION_TIMEOUT_MS",
            ge=1000,
            description="Server selection timeout in milliseconds",
        )
        authz_cache_ttl: int = Field(
            300,
            env="AUTHZ_CACHE_TTL",
            ge=0,
            description="Authorization cache TTL in seconds",
        )

        class Config:
            env_file = ".env"
            case_sensitive = False


# ============================================================================
# DEMO MODE CONSTANTS
# ============================================================================

DEMO_ENABLED: bool = os.getenv("DEMO_ENABLED", "false").lower() == "true"
"""Whether demo mode is enabled."""

DEMO_EMAIL_DEFAULT: str = os.getenv("DEMO_EMAIL_DEFAULT", "demo@example.com")
"""Default email for demo user."""

DEMO_PASSWORD_DEFAULT: str = os.getenv("DEMO_PASSWORD_DEFAULT", "demo123")
"""Default password for demo user."""

# MongoDB connection defaults (for backward compatibility)
MONGO_URI: str = os.getenv("MONGO_URI", "")
"""MongoDB connection URI (from environment variable)."""

DB_NAME: str = os.getenv("DB_NAME", "")
"""Database name (from environment variable)."""

# ============================================================================
# TOKEN MANAGEMENT CONFIGURATION
# ============================================================================

# Token lifetimes (can be overridden via environment variables)
ACCESS_TOKEN_TTL: int = int(os.getenv("ACCESS_TOKEN_TTL", "900"))  # 15 minutes
"""Access token TTL in seconds (default: 900 / 15 minutes)."""

REFRESH_TOKEN_TTL: int = int(os.getenv("REFRESH_TOKEN_TTL", "604800"))  # 7 days
"""Refresh token TTL in seconds (default: 604800 / 7 days)."""

TOKEN_ROTATION_ENABLED: bool = (
    os.getenv("TOKEN_ROTATION_ENABLED", "true").lower() == "true"
)
"""Whether to rotate refresh tokens on each use (default: true)."""

MAX_SESSIONS_PER_USER: int = int(os.getenv("MAX_SESSIONS_PER_USER", "10"))
"""Maximum number of concurrent sessions per user (default: 10)."""

SESSION_INACTIVITY_TIMEOUT: int = int(
    os.getenv("SESSION_INACTIVITY_TIMEOUT", "1800")
)  # 30 minutes
"""Session inactivity timeout in seconds (default: 1800 / 30 minutes)."""
