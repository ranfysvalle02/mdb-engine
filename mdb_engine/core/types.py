"""
Type definitions for MDB_ENGINE core structures.

This module provides TypedDict definitions for manifest structures,
app configurations, and service configurations to improve type safety
throughout the codebase.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from ..memory import BaseMemoryService
else:
    BaseMemoryService = Any


# ============================================================================
# GraphRAG Search Strategy Type
# ============================================================================

GraphSearchStrategy = Literal["local", "global", "drift", "basic", "auto"]
"""Valid search strategies for GraphRAG query routing.

- ``"auto"``: Use the automatic query classifier (default).
- ``"local"``: Entity-focused search with community summaries.
- ``"global"``: Thematic map-reduce over all communities (expensive).
- ``"drift"``: Entity-focused with entry-node community context.
- ``"basic"``: Hybrid vector + graph traversal (fast, cheap).
"""

VALID_SEARCH_STRATEGIES: set[str | None] = {"local", "global", "drift", "basic", "auto", None}
"""Set of accepted ``search_strategy`` values (includes ``None`` for auto)."""


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
    keys: dict[str, int | str] | list[list[str | int]]
    unique: bool
    sparse: bool
    background: bool
    expireAfterSeconds: int
    partialFilterExpression: dict[str, Any]
    weights: dict[str, int]  # For text indexes
    default_language: str  # For text indexes
    language_override: str  # For text indexes
    textIndexVersion: int  # For text indexes
    # Vector search specific
    fields: list[dict[str, Any]]  # For vectorSearch indexes
    # Search index specific
    mappings: dict[str, Any]  # For search indexes
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
    default_roles: list[str]
    # OSO-specific
    api_key: str | None
    url: str | None
    initial_roles: list[dict[str, str]]
    initial_policies: list[dict[str, str]]


class AuthPolicyDict(TypedDict, total=False):
    """Authorization policy configuration."""

    required: bool
    provider: Literal["casbin", "oso", "custom"]
    authorization: AuthAuthorizationDict
    allowed_roles: list[str]
    allowed_users: list[str]
    denied_users: list[str]
    required_permissions: list[str]
    custom_resource: str
    custom_actions: list[Literal["access", "read", "write", "admin"]]
    allow_anonymous: bool
    owner_can_access: bool


class DemoUserDict(TypedDict, total=False):
    """Demo user configuration."""

    email: str
    password: str
    role: str
    auto_create: bool
    link_to_platform: bool
    extra_data: dict[str, Any]


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
    demo_users: list[DemoUserDict]
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
    csrf_required: bool  # Whether CSRF cookie is required (default: False)


class WebSocketEndpointDict(TypedDict, total=False):
    """WebSocket endpoint configuration."""

    path: str
    auth: WebSocketAuthDict
    description: str
    ping_interval: int
    ticket_ttl_seconds: int


class WebSocketsDict(TypedDict):
    """WebSocket endpoints configuration."""

    pass  # Dynamic: endpoint_name -> WebSocketEndpointDict


# ============================================================================
# Memory Configuration Types
# ============================================================================


class CognitiveEmotionConfigDict(TypedDict, total=False):
    """Cognitive emotion (Flashbulb Memory) configuration."""

    enabled: bool
    flashbulb_threshold: float
    max_stability_multiplier: float


class ConflictResolutionConfigDict(TypedDict, total=False):
    """Conflict resolution (integrity check) configuration."""

    enabled: bool
    similarity_threshold: float
    llm_model: str | None


class PruningConfigDict(TypedDict, total=False):
    """Memory pruning (soft-delete) configuration."""

    enabled: bool
    max_capacity: int
    prune_percentage: float
    strategy: Literal["soft_delete", "hard_delete"]


class ColdStorageConfigDict(TypedDict, total=False):
    """Cold storage (paper trail) configuration."""

    enabled: bool
    retention_days: int


class CognitiveConfigDict(TypedDict, total=False):
    """Full cognitive memory configuration."""

    enabled: bool
    emotion: CognitiveEmotionConfigDict
    conflict_resolution: ConflictResolutionConfigDict
    pruning: PruningConfigDict
    cold_storage: ColdStorageConfigDict


class ReflectionConfigDict(TypedDict, total=False):
    """Memory reflection configuration."""

    enabled: bool
    interval_hours: int
    message_threshold: int
    min_salience_to_keep: float
    store_reflections: bool


class EntitiesConfigDict(TypedDict, total=False):
    """Entity store configuration."""

    enabled: bool
    collection_name: str
    auto_extract: bool


class CategoriesConfigDict(TypedDict, total=False):
    """Memory categories configuration."""

    enabled: bool
    custom_categories: list[str]


class MemoryGraphConfigDict(TypedDict, total=False):
    """Graph-powered memory layer configuration."""

    enabled: bool
    auto_extract: bool


class MemoryTypesConfigDict(TypedDict, total=False):
    """Memory types configuration (Cognitive Blueprint v2.0)."""

    enabled: bool
    auto_detect: bool
    default_type: Literal["semantic", "entity", "episodic", "working"]
    episodic_retention_days: int
    working_ttl_hours: int


class ConsolidationConfigDict(TypedDict, total=False):
    """Consolidation configuration for reflection service."""

    extract_entities: bool
    route_by_type: bool
    link_to_graph: bool


# ============================================================================
# Strategy Configuration Types (Pluggable Memory Mechanics)
# ============================================================================


class ScoringStrategyConfigDict(TypedDict, total=False):
    """Configuration for the scoring strategy.

    The ``strategy`` key selects a built-in implementation.
    Other keys are passed as constructor kwargs to the selected strategy.

    Available strategies: ``"perfect_recall"`` (default), ``"recency_decay"``.
    """

    strategy: str
    half_life_hours: float


class DecayStrategyConfigDict(TypedDict, total=False):
    """Configuration for the decay strategy.

    Available strategies: ``"none"`` (default), ``"exponential"``, ``"linear"``.
    """

    strategy: str
    half_life_hours: float
    lifetime_hours: float
    archive_threshold: float


class ImportanceStrategyConfigDict(TypedDict, total=False):
    """Configuration for the importance assessment strategy.

    Available strategies: ``"llm"`` (default), ``"rule_based"``.
    """

    strategy: str
    default_importance: float


class PersonaBlendingConfigDict(TypedDict, total=False):
    """Configuration for persona blending strategy.

    Available strategies: ``"weighted"`` (default), ``"custom_weight"``.
    """

    strategy: str
    query_weight: float
    persona_weight: float


class MemoryConfigDict(TypedDict, total=False):
    """Memory service configuration."""

    enabled: bool
    collection_name: str
    index_name: str
    embedding_model_dims: int
    infer: bool
    embedding_model: str
    chat_model: str
    memory_llm_model: str
    temperature: float
    async_mode: bool
    provider: Literal["custom", "cognitive"]
    enable_cognitive: bool
    max_depth: int | None
    similarity_threshold: float
    reinforcement_factor: float
    merge_threshold_low: float
    merge_threshold_high: float
    # Scoring enhancements
    emotion_weight: float
    recency_weight: float
    recency_half_life_hours: float
    spreading_activation: bool
    activation_discount: float
    salience_gate: bool
    salience_threshold: float
    # Nested configurations
    cognitive: CognitiveConfigDict
    reflection: ReflectionConfigDict
    entities: EntitiesConfigDict
    categories: CategoriesConfigDict
    graph: MemoryGraphConfigDict
    # Cognitive Blueprint v2.0 configurations
    memory_types: MemoryTypesConfigDict
    consolidation: ConsolidationConfigDict
    persona: dict[str, Any]  # Persona configuration
    verification: dict[str, Any]  # Verification configuration
    # Pluggable strategy configurations (select built-in or configure custom)
    scoring: ScoringStrategyConfigDict
    decay: DecayStrategyConfigDict
    importance: ImportanceStrategyConfigDict
    persona_blending: PersonaBlendingConfigDict


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
    include_details: bool
    export_metrics: bool


class MetricsConfigDict(TypedDict, total=False):
    """Metrics collection configuration."""

    enabled: bool
    collect_operation_metrics: bool
    collect_performance_metrics: bool
    custom_metrics: list[str]
    export_prometheus: bool
    export_otlp: bool
    otlp_endpoint: str


class LoggingConfigDict(TypedDict, total=False):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    format: Literal["json", "text"]
    include_request_id: bool
    log_sensitive_data: bool
    inject_trace_ids: bool


class TracingConfigDict(TypedDict, total=False):
    """Distributed tracing configuration."""

    enabled: bool
    service_name: str
    endpoint: str
    exporter: Literal["otlp", "console", "none"]
    sample_rate: float


class ObservabilityConfigDict(TypedDict, total=False):
    """Observability configuration."""

    health_checks: HealthChecksConfigDict
    metrics: MetricsConfigDict
    logging: LoggingConfigDict
    tracing: TracingConfigDict


# ============================================================================
# CORS Configuration Types
# ============================================================================


class CORSConfigDict(TypedDict, total=False):
    """CORS configuration."""

    enabled: bool
    allow_origins: list[str]
    allow_credentials: bool
    allow_methods: list[Literal["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "*"]]
    allow_headers: list[str]
    expose_headers: list[str]
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


class ProfileUserConfigDict(TypedDict, total=False):
    """User profile configuration."""

    enabled: bool
    rebuild_interval_hours: int
    incremental_on_memory_add: bool
    collection_name: str


class ProfileCommunityConfigDict(TypedDict, total=False):
    """Community profile configuration."""

    enabled: bool
    rebuild_interval_hours: int
    min_users_for_aggregation: int
    collection_name: str


class ProfileConfigDict(TypedDict, total=False):
    """Profile system configuration."""

    enabled: bool
    user_profiles: ProfileUserConfigDict
    community_profile: ProfileCommunityConfigDict


class ManifestDict(TypedDict, total=False):
    """Main manifest structure."""

    schema_version: str
    slug: str
    name: str
    description: str | None
    status: Literal["active", "draft", "archived", "inactive"]
    auth: AuthConfigDict | None
    token_management: TokenManagementDict | None
    data_scope: list[str]
    pip_deps: list[str]
    managed_indexes: ManagedIndexesDict | None
    collection_settings: dict[str, dict[str, Any]] | None
    websockets: WebSocketsDict | None
    embedding_config: EmbeddingConfigDict | None
    memory_config: MemoryConfigDict | None
    profile_config: ProfileConfigDict | None
    cors: CORSConfigDict | None
    observability: ObservabilityConfigDict | None
    initial_data: InitialDataDict | None
    developer_id: str | None


# ============================================================================
# Health and Metrics Types
# ============================================================================


class HealthStatusDict(TypedDict, total=False):
    """Health status response."""

    status: Literal["healthy", "degraded", "unhealthy"]
    checks: dict[str, dict[str, Any]]
    timestamp: str


class MetricsDict(TypedDict, total=False):
    """Metrics response."""

    operations: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    timestamp: str
