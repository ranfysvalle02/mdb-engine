"""
Type definitions for MDB_ENGINE core structures.

This module provides TypedDict definitions for manifest structures,
app configurations, and service configurations to improve type safety
throughout the codebase.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from typing import (TYPE_CHECKING, Any, Dict, List, Literal, Optional,
                    TypedDict, Union)

if TYPE_CHECKING:
    from ..memory import Mem0MemoryService
else:
    Mem0MemoryService = Any


# ============================================================================
# Index Definition Types
# ============================================================================


class IndexKeysDict(TypedDict, total=False):
    """Index keys definition - field name to sort order."""

    pass  # Dynamic keys: field names -> 1, -1, "text", "2dsphere", etc.


class IndexDefinitionDict(TypedDict, total=False):
    """Index definition structure."""

    name: str
    type: Literal[
        "regular",
        "vectorSearch",
        "search",
        "text",
        "geospatial",
        "ttl",
        "partial",
        "hybrid",
    ]
    keys: Union[Dict[str, Union[int, str]], List[List[Union[str, int]]]]
    unique: bool
    sparse: bool
    background: bool
    expireAfterSeconds: int
    partialFilterExpression: Dict[str, Any]
    weights: Dict[str, int]  # For text indexes
    default_language: str  # For text indexes
    language_override: str  # For text indexes
    textIndexVersion: int  # For text indexes
    # Vector search specific
    fields: List[Dict[str, Any]]  # For vectorSearch indexes
    # Search index specific
    mappings: Dict[str, Any]  # For search indexes
    # Geospatial specific
    bucketSize: float  # For geoHaystack
    # TTL specific
    expireAfter: int  # Alias for expireAfterSeconds


class ManagedIndexesDict(TypedDict):
    """Managed indexes - collection name to list of index definitions."""

    pass  # Dynamic: collection_name -> List[IndexDefinitionDict]


# ============================================================================
# Auth Configuration Types
# ============================================================================


class AuthAuthorizationDict(TypedDict, total=False):
    """Authorization configuration."""

    # Casbin-specific
    model: str
    policies_collection: str
    link_users_roles: bool
    default_roles: List[str]
    # OSO-specific
    api_key: Optional[str]
    url: Optional[str]
    initial_roles: List[Dict[str, str]]
    initial_policies: List[Dict[str, str]]


class AuthPolicyDict(TypedDict, total=False):
    """Authorization policy configuration."""

    required: bool
    provider: Literal["casbin", "oso", "custom"]
    authorization: AuthAuthorizationDict
    allowed_roles: List[str]
    allowed_users: List[str]
    denied_users: List[str]
    required_permissions: List[str]
    custom_resource: str
    custom_actions: List[Literal["access", "read", "write", "admin"]]
    allow_anonymous: bool
    owner_can_access: bool


class DemoUserDict(TypedDict, total=False):
    """Demo user configuration."""

    email: str
    password: str
    role: str
    auto_create: bool
    link_to_platform: bool
    extra_data: Dict[str, Any]


class UsersConfigDict(TypedDict, total=False):
    """User management configuration."""

    enabled: bool
    strategy: Literal["app_users", "anonymous_session"]
    collection_name: str
    session_cookie_name: str
    session_ttl_seconds: int
    allow_registration: bool
    link_platform_users: bool
    anonymous_user_prefix: str
    user_id_field: str
    demo_users: List[DemoUserDict]
    auto_link_platform_demo: bool
    demo_user_seed_strategy: Literal["auto", "manual", "disabled"]
    enable_demo_user_access: bool


class AuthConfigDict(TypedDict, total=False):
    """Authentication and authorization configuration."""

    policy: AuthPolicyDict
    users: UsersConfigDict


# ============================================================================
# Token Management Types
# ============================================================================


class RateLimitingConfigDict(TypedDict, total=False):
    """Rate limiting configuration for an endpoint."""

    max_attempts: int
    window_seconds: int


class RateLimitingDict(TypedDict, total=False):
    """Rate limiting configuration."""

    login: RateLimitingConfigDict
    register: RateLimitingConfigDict
    refresh: RateLimitingConfigDict


class PasswordPolicyDict(TypedDict, total=False):
    """Password policy configuration."""

    allow_plain_text: bool
    min_length: int
    require_uppercase: bool
    require_lowercase: bool
    require_numbers: bool
    require_special: bool


class SessionFingerprintingDict(TypedDict, total=False):
    """Session fingerprinting configuration."""

    enabled: bool
    validate_on_login: bool
    validate_on_refresh: bool
    validate_on_request: bool
    strict_mode: bool


class AccountLockoutDict(TypedDict, total=False):
    """Account lockout configuration."""

    enabled: bool
    max_failed_attempts: int
    lockout_duration_seconds: int
    reset_on_success: bool


class IPValidationDict(TypedDict, total=False):
    """IP validation configuration."""

    enabled: bool
    strict: bool
    allow_ip_change: bool


class TokenFingerprintingDict(TypedDict, total=False):
    """Token fingerprinting configuration."""

    enabled: bool
    bind_to_device: bool


class SecurityConfigDict(TypedDict, total=False):
    """Security configuration."""

    require_https: bool
    cookie_secure: Literal["auto", "true", "false"]
    cookie_samesite: Literal["strict", "lax", "none"]
    cookie_httponly: bool
    csrf_protection: bool
    rate_limiting: RateLimitingDict
    password_policy: PasswordPolicyDict
    session_fingerprinting: SessionFingerprintingDict
    account_lockout: AccountLockoutDict
    ip_validation: IPValidationDict
    token_fingerprinting: TokenFingerprintingDict


class TokenManagementDict(TypedDict, total=False):
    """Token management configuration."""

    enabled: bool
    access_token_ttl: int
    refresh_token_ttl: int
    token_rotation: bool
    max_sessions_per_user: int
    session_inactivity_timeout: int
    security: SecurityConfigDict
    auto_setup: bool


# ============================================================================
# WebSocket Configuration Types
# ============================================================================


class WebSocketAuthDict(TypedDict, total=False):
    """WebSocket authentication configuration."""

    required: bool
    allow_anonymous: bool


class WebSocketEndpointDict(TypedDict, total=False):
    """WebSocket endpoint configuration."""

    path: str
    auth: WebSocketAuthDict
    description: str
    ping_interval: int


class WebSocketsDict(TypedDict):
    """WebSocket endpoints configuration."""

    pass  # Dynamic: endpoint_name -> WebSocketEndpointDict


# ============================================================================
# Memory Configuration Types
# ============================================================================


class MemoryConfigDict(TypedDict, total=False):
    """Mem0 memory service configuration."""

    enabled: bool
    collection_name: str
    embedding_model_dims: int
    enable_graph: bool
    infer: bool
    embedding_model: str
    chat_model: str
    temperature: float
    async_mode: bool


# ============================================================================
# Embedding Configuration Types
# ============================================================================


class EmbeddingConfigDict(TypedDict, total=False):
    """Embedding service configuration."""

    enabled: bool
    max_tokens_per_chunk: int
    tokenizer_model: str
    default_embedding_model: str


# ============================================================================
# Observability Configuration Types
# ============================================================================


class HealthChecksConfigDict(TypedDict, total=False):
    """Health check configuration."""

    enabled: bool
    endpoint: str
    interval_seconds: int


class MetricsConfigDict(TypedDict, total=False):
    """Metrics collection configuration."""

    enabled: bool
    collect_operation_metrics: bool
    collect_performance_metrics: bool
    custom_metrics: List[str]


class LoggingConfigDict(TypedDict, total=False):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    format: Literal["json", "text"]
    include_request_id: bool
    log_sensitive_data: bool


class ObservabilityConfigDict(TypedDict, total=False):
    """Observability configuration."""

    health_checks: HealthChecksConfigDict
    metrics: MetricsConfigDict
    logging: LoggingConfigDict


# ============================================================================
# CORS Configuration Types
# ============================================================================


class CORSConfigDict(TypedDict, total=False):
    """CORS configuration."""

    enabled: bool
    allow_origins: List[str]
    allow_credentials: bool
    allow_methods: List[
        Literal["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "*"]
    ]
    allow_headers: List[str]
    expose_headers: List[str]
    max_age: int


# ============================================================================
# Initial Data Types
# ============================================================================


class InitialDataDict(TypedDict):
    """Initial data seeding configuration."""

    pass  # Dynamic: collection_name -> List[Dict[str, Any]]


# ============================================================================
# Main Manifest Type
# ============================================================================


class ManifestDict(TypedDict, total=False):
    """Main manifest structure."""

    schema_version: str
    slug: str
    name: str
    description: Optional[str]
    status: Literal["active", "draft", "archived", "inactive"]
    auth_required: bool  # Backward compatibility
    auth: Optional[AuthConfigDict]
    token_management: Optional[TokenManagementDict]
    data_scope: List[str]
    pip_deps: List[str]
    managed_indexes: Optional[ManagedIndexesDict]
    collection_settings: Optional[Dict[str, Dict[str, Any]]]
    websockets: Optional[WebSocketsDict]
    embedding_config: Optional[EmbeddingConfigDict]
    memory_config: Optional[MemoryConfigDict]
    cors: Optional[CORSConfigDict]
    observability: Optional[ObservabilityConfigDict]
    initial_data: Optional[InitialDataDict]
    developer_id: Optional[str]


# ============================================================================
# Health and Metrics Types
# ============================================================================


class HealthStatusDict(TypedDict, total=False):
    """Health status response."""

    status: Literal["healthy", "degraded", "unhealthy"]
    checks: Dict[str, Dict[str, Any]]
    timestamp: str


class MetricsDict(TypedDict, total=False):
    """Metrics response."""

    operations: Dict[str, Dict[str, Any]]
    summary: Dict[str, Any]
    timestamp: str
