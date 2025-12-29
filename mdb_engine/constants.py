"""
Constants for MDB_ENGINE.

This module contains all shared constants used across the codebase to avoid
magic numbers and improve maintainability.
"""

from typing import Final

# ============================================================================
# INDEX MANAGEMENT CONSTANTS
# ============================================================================

# Polling intervals (in seconds)
DEFAULT_POLL_INTERVAL: Final[int] = 5
"""Default polling interval for checking index status (seconds)."""

# Timeouts (in seconds)
DEFAULT_SEARCH_TIMEOUT: Final[int] = 600  # 10 minutes
"""Default timeout for waiting for search indexes to become ready (seconds)."""

DEFAULT_DROP_TIMEOUT: Final[int] = 300  # 5 minutes
"""Default timeout for waiting for search indexes to be dropped (seconds)."""

# Auto-indexing constants
AUTO_INDEX_HINT_THRESHOLD: Final[int] = 3
"""Number of times a query pattern must be seen before creating an index."""

MAX_INDEX_FIELDS: Final[int] = 4
"""Maximum number of fields in a compound index (MongoDB best practice)."""

MAX_CACHE_SIZE: Final[int] = 1000
"""Maximum size of authorization and validation caches before eviction."""

# ============================================================================
# DATABASE CONSTANTS
# ============================================================================

# Connection pool defaults
DEFAULT_MAX_POOL_SIZE: Final[int] = 50
"""Default maximum MongoDB connection pool size."""

DEFAULT_MIN_POOL_SIZE: Final[int] = 10
"""Default minimum MongoDB connection pool size."""

DEFAULT_SERVER_SELECTION_TIMEOUT_MS: Final[int] = 5000
"""Default server selection timeout in milliseconds."""

DEFAULT_MAX_IDLE_TIME_MS: Final[int] = 45000
"""Default maximum idle time before closing connections (milliseconds)."""

# ============================================================================
# VALIDATION CONSTANTS
# ============================================================================

# Collection name constraints
MAX_COLLECTION_NAME_LENGTH: Final[int] = 255
"""Maximum length for MongoDB collection names."""

MIN_COLLECTION_NAME_LENGTH: Final[int] = 1
"""Minimum length for MongoDB collection names."""

# Reserved collection name prefixes (MongoDB system collections)
RESERVED_COLLECTION_PREFIXES: Final[tuple[str, ...]] = (
    "system",
    "admin",
    "config",
    "local",
)
"""Reserved MongoDB collection name prefixes that cannot be used."""

# Reserved collection names (engine-internal)
RESERVED_COLLECTION_NAMES: Final[tuple[str, ...]] = (
    "apps_config",  # Engine internal - app registration
    "_mdb_engine_app_secrets",  # Engine internal - encrypted app secrets
)
"""Reserved collection names that cannot be accessed through scoped wrappers."""

# App slug constraints
MAX_APP_SLUG_LENGTH: Final[int] = 100
"""Maximum length for app slugs."""

# TTL index constraints
MIN_TTL_SECONDS: Final[int] = 1
"""Minimum TTL value in seconds."""

MAX_TTL_SECONDS: Final[int] = 31536000  # 1 year
"""Maximum recommended TTL value in seconds (1 year)."""

# Vector index constraints
MIN_VECTOR_DIMENSIONS: Final[int] = 1
"""Minimum number of dimensions for vector indexes."""

MAX_VECTOR_DIMENSIONS: Final[int] = 10000
"""Maximum number of dimensions for vector indexes."""

# ============================================================================
# CACHING CONSTANTS
# ============================================================================

AUTHZ_CACHE_TTL: Final[int] = 300  # 5 minutes
"""Authorization cache TTL in seconds."""

# ============================================================================
# TOKEN MANAGEMENT CONSTANTS
# ============================================================================

# Token lifetimes (in seconds)
ACCESS_TOKEN_TTL: Final[int] = 900  # 15 minutes
"""Default access token TTL in seconds."""

REFRESH_TOKEN_TTL: Final[int] = 604800  # 7 days
"""Default refresh token TTL in seconds."""

# Token management settings
TOKEN_ROTATION_ENABLED: Final[bool] = True
"""Whether to rotate refresh tokens on each use."""

MAX_SESSIONS_PER_USER: Final[int] = 10
"""Maximum number of concurrent sessions per user."""

SESSION_INACTIVITY_TIMEOUT: Final[int] = 1800  # 30 minutes
"""Session inactivity timeout in seconds before automatic cleanup."""

# Token versioning
CURRENT_TOKEN_VERSION: Final[str] = "1.0"
"""Current token schema version for migration support."""

# ============================================================================
# SCHEMA CONSTANTS
# ============================================================================

CURRENT_SCHEMA_VERSION: Final[str] = "2.0"
"""Current manifest schema version."""

DEFAULT_SCHEMA_VERSION: Final[str] = "1.0"
"""Default schema version for manifests without version field."""

# ============================================================================
# INDEX TYPE CONSTANTS
# ============================================================================

INDEX_TYPE_REGULAR: Final[str] = "regular"
INDEX_TYPE_VECTOR_SEARCH: Final[str] = "vectorSearch"
INDEX_TYPE_SEARCH: Final[str] = "search"
INDEX_TYPE_TEXT: Final[str] = "text"
INDEX_TYPE_GEOSPATIAL: Final[str] = "geospatial"
INDEX_TYPE_TTL: Final[str] = "ttl"
INDEX_TYPE_PARTIAL: Final[str] = "partial"
INDEX_TYPE_HYBRID: Final[str] = "hybrid"

# Supported index types
SUPPORTED_INDEX_TYPES: Final[tuple[str, ...]] = (
    INDEX_TYPE_REGULAR,
    INDEX_TYPE_VECTOR_SEARCH,
    INDEX_TYPE_SEARCH,
    INDEX_TYPE_TEXT,
    INDEX_TYPE_GEOSPATIAL,
    INDEX_TYPE_TTL,
    INDEX_TYPE_PARTIAL,
    INDEX_TYPE_HYBRID,
)

# ============================================================================
# APP STATUS CONSTANTS
# ============================================================================

APP_STATUS_ACTIVE: Final[str] = "active"
APP_STATUS_DRAFT: Final[str] = "draft"
APP_STATUS_ARCHIVED: Final[str] = "archived"
APP_STATUS_INACTIVE: Final[str] = "inactive"

# Supported app statuses
SUPPORTED_APP_STATUSES: Final[tuple[str, ...]] = (
    APP_STATUS_ACTIVE,
    APP_STATUS_DRAFT,
    APP_STATUS_ARCHIVED,
    APP_STATUS_INACTIVE,
)

# ============================================================================
# QUERY SECURITY & RESOURCE LIMITS
# ============================================================================

# Query execution limits
DEFAULT_MAX_TIME_MS: Final[int] = 30000
"""Default query timeout in milliseconds (30 seconds)."""

MAX_QUERY_TIME_MS: Final[int] = 300000
"""Maximum allowed query timeout in milliseconds (5 minutes)."""

MAX_QUERY_RESULT_SIZE: Final[int] = 10000
"""Maximum number of documents that can be returned in a single query."""

MAX_CURSOR_BATCH_SIZE: Final[int] = 1000
"""Maximum batch size for cursor operations."""

MAX_DOCUMENT_SIZE: Final[int] = 16 * 1024 * 1024
"""Maximum document size in bytes (16MB MongoDB limit)."""

# Pipeline limits
MAX_PIPELINE_STAGES: Final[int] = 50
"""Maximum number of stages allowed in an aggregation pipeline."""

MAX_SORT_FIELDS: Final[int] = 10
"""Maximum number of fields that can be sorted in a single query."""

MAX_QUERY_DEPTH: Final[int] = 10
"""Maximum nesting depth for query filters (prevents deeply nested queries)."""

# Regex limits (prevent ReDoS attacks)
MAX_REGEX_LENGTH: Final[int] = 1000
"""Maximum length of regex patterns to prevent ReDoS attacks."""

MAX_REGEX_COMPLEXITY: Final[int] = 50
"""Maximum complexity score for regex patterns (prevents ReDoS)."""

# Dangerous MongoDB operators that should be blocked
DANGEROUS_OPERATORS: Final[tuple[str, ...]] = (
    "$where",  # JavaScript execution (security risk)
    "$eval",  # JavaScript evaluation (deprecated, security risk)
    "$function",  # JavaScript functions (security risk)
    "$accumulator",  # Can be abused for code execution
)
"""MongoDB operators that are blocked for security reasons."""
