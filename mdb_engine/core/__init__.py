"""
Core MongoDB Engine components.

This module contains the main MongoDBEngine class and core
orchestration logic for managing apps.

The MongoDBEngine now includes:
- FastAPI integration with create_app() method
- Optional Ray support with enable_ray parameter
- Automatic app token retrieval
- Multi-site mode auto-detection from manifest
- Optional CSFLE (Client-Side Field Level Encryption) support
"""

# Optional CSFLE support (gracefully handles missing pymongo[encryption])
from .csfle import (
    CSFLEConfig,
    generate_local_master_key,
    get_csfle_status,
    is_csfle_available,
)
from .engine import (
    MongoDBEngine,
    build_csfle_config_from_manifest,
    build_csfle_config_from_manifests,
)
from .manifest import (  # Classes; Constants; Functions (for backward compatibility); Schemas
    CURRENT_SCHEMA_VERSION,
    DEFAULT_SCHEMA_VERSION,
    MANIFEST_SCHEMA,
    MANIFEST_SCHEMA_V1,
    MANIFEST_SCHEMA_V2,
    SCHEMA_REGISTRY,
    ManifestParser,
    ManifestValidator,
    clear_validation_cache,
    get_schema_for_version,
    get_schema_version,
    migrate_manifest,
    validate_developer_id,
    validate_index_definition,
    validate_managed_indexes,
    validate_manifest,
    validate_manifest_with_db,
    validate_manifests_parallel,
)

# Service Protocols for dependency injection and type checking
from .protocols import (
    EmbeddingServiceProtocol,
    GraphServiceProtocol,
    LLMServiceProtocol,
    MemoryServiceProtocol,
    TextChunkerProtocol,
)

# Optional Ray integration (gracefully handles missing Ray)
from .ray_integration import (
    RAY_AVAILABLE,
    AppRayActor,
    get_ray_actor_handle,
    ray_actor_decorator,
)

__all__ = [
    # MongoDB Engine (includes FastAPI integration and optional Ray)
    "MongoDBEngine",
    # Ray Integration (optional - only active if Ray installed)
    "RAY_AVAILABLE",
    "AppRayActor",
    "get_ray_actor_handle",
    "ray_actor_decorator",
    # CSFLE Support (optional - only active if pymongo[encryption] installed)
    "CSFLEConfig",
    "build_csfle_config_from_manifest",
    "build_csfle_config_from_manifests",
    "generate_local_master_key",
    "get_csfle_status",
    "is_csfle_available",
    # Service Protocols (for DI and type checking)
    "LLMServiceProtocol",
    "EmbeddingServiceProtocol",
    "TextChunkerProtocol",
    "GraphServiceProtocol",
    "MemoryServiceProtocol",
    # Classes
    "ManifestValidator",
    "ManifestParser",
    # Constants
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_SCHEMA_VERSION",
    # Functions
    "validate_manifest",
    "validate_manifest_with_db",
    "validate_managed_indexes",
    "validate_index_definition",
    "validate_developer_id",
    "get_schema_version",
    "migrate_manifest",
    "get_schema_for_version",
    "clear_validation_cache",
    "validate_manifests_parallel",
    # Schemas
    "MANIFEST_SCHEMA_V1",
    "MANIFEST_SCHEMA_V2",
    "MANIFEST_SCHEMA",
    "SCHEMA_REGISTRY",
]
