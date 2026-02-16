# Dependency Injection

MDB Engine includes a lightweight DI container with singleton, request-scoped, and transient lifetimes.

## Container

::: mdb_engine.di.container.Container
    options:
      show_root_heading: true
      members_order: source

## Scope

::: mdb_engine.di.scopes.Scope
    options:
      show_root_heading: true

## ScopeManager

::: mdb_engine.di.scopes.ScopeManager
    options:
      show_root_heading: true
      members_order: source

## Providers

::: mdb_engine.di.providers.SingletonProvider
    options:
      show_root_heading: true

::: mdb_engine.di.providers.RequestProvider
    options:
      show_root_heading: true

::: mdb_engine.di.providers.TransientProvider
    options:
      show_root_heading: true

::: mdb_engine.di.providers.FactoryProvider
    options:
      show_root_heading: true
