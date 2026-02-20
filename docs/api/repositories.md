# Repositories

The repository pattern provides a clean abstraction over MongoDB collections with entity mapping, unit-of-work transactions, and type-safe queries.

## Repository (Abstract Base)

::: mdb_engine.repositories.base.Repository
    options:
      show_root_heading: true
      members_order: source

## Entity

::: mdb_engine.repositories.base.Entity
    options:
      show_root_heading: true

## MongoRepository

::: mdb_engine.repositories.mongo.MongoRepository
    options:
      show_root_heading: true
      members_order: source

## UnitOfWork

::: mdb_engine.repositories.unit_of_work.UnitOfWork
    options:
      show_root_heading: true
      members_order: source
