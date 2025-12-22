"""
Core MongoDB Engine components.

This module contains the main MongoDBEngine class and core
orchestration logic for managing apps.
"""

from .engine import MongoDBEngine

from .manifest import (
    # Classes
    ManifestValidator,
    ManifestParser,
    
    # Constants
    CURRENT_SCHEMA_VERSION,
    DEFAULT_SCHEMA_VERSION,
    
    # Functions (for backward compatibility)
    validate_manifest,
    validate_manifest_with_db,
    validate_managed_indexes,
    validate_index_definition,
    validate_developer_id,
    get_schema_version,
    migrate_manifest,
    get_schema_for_version,
    clear_validation_cache,
    validate_manifests_parallel,
    
    # Schemas
    MANIFEST_SCHEMA_V1,
    MANIFEST_SCHEMA_V2,
    MANIFEST_SCHEMA,
    SCHEMA_REGISTRY,
)

__all__ = [
    # MongoDB Engine
    "MongoDBEngine",
    
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
