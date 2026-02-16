# Core Engine

The core engine is the heart of MDB Engine. It reads your `manifest.json`, sets up scoped database connections, registers apps, and initializes all services.

## MongoDBEngine

::: mdb_engine.core.engine.MongoDBEngine
    options:
      members_order: source
      show_root_heading: true

## ConnectionManager

::: mdb_engine.core.connection.ConnectionManager
    options:
      show_root_heading: true

## ManifestValidator

::: mdb_engine.core.manifest.ManifestValidator
    options:
      show_root_heading: true

## ManifestParser

::: mdb_engine.core.manifest.ManifestParser
    options:
      show_root_heading: true

## IndexManager

::: mdb_engine.core.index_management.IndexManager
    options:
      show_root_heading: true

## Utility Functions

::: mdb_engine.core.manifest.validate_manifest

::: mdb_engine.core.manifest.migrate_manifest

::: mdb_engine.core.manifest.get_schema_version
