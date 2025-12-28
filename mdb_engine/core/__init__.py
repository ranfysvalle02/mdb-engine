"""
Core MongoDB Engine components.

This module contains the main MongoDBEngine class and core
orchestration logic for managing apps.
"""

from .engine import MongoDBEngine
from .manifest import (  # Classes; Constants; Functions (for backward compatibility); Schemas
    CURRENT_SCHEMA_VERSION, DEFAULT_SCHEMA_VERSION, MANIFEST_SCHEMA,
    MANIFEST_SCHEMA_V1, MANIFEST_SCHEMA_V2, SCHEMA_REGISTRY, ManifestParser,
    ManifestValidator, clear_validation_cache, get_schema_for_version,
    get_schema_version, migrate_manifest, validate_developer_id,
    validate_index_definition, validate_managed_indexes, validate_manifest,
    validate_manifest_with_db, validate_manifests_parallel)

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
