# Database

The database layer provides scoped, tenant-isolated access to MongoDB collections. It wraps Motor's async driver with automatic prefixing and query validation.

## AppDB

::: mdb_engine.database.abstraction.AppDB
    options:
      show_root_heading: true
      members_order: source

## Collection

::: mdb_engine.database.abstraction.Collection
    options:
      show_root_heading: true
      members_order: source

## ScopedMongoWrapper

::: mdb_engine.database.scoped_wrapper.ScopedMongoWrapper
    options:
      show_root_heading: true
      members_order: source

## AsyncAtlasIndexManager

::: mdb_engine.database.scoped_wrapper.AsyncAtlasIndexManager
    options:
      show_root_heading: true

## AutoIndexManager

::: mdb_engine.database.scoped_wrapper.AutoIndexManager
    options:
      show_root_heading: true

## QueryValidator

::: mdb_engine.database.query_validator.QueryValidator
    options:
      show_root_heading: true

## ResourceLimiter

::: mdb_engine.database.resource_limiter.ResourceLimiter
    options:
      show_root_heading: true
