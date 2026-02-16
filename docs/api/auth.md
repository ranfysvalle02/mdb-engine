# Authentication & Authorization

MDB Engine supports pluggable authorization providers (Casbin, OSO), JWT token management, session handling, rate limiting, CSRF protection, and WebSocket authentication.

## AuthorizationProvider Protocol

::: mdb_engine.auth.provider.AuthorizationProvider
    options:
      show_root_heading: true

## BaseAuthorizationProvider

::: mdb_engine.auth.base.BaseAuthorizationProvider
    options:
      show_root_heading: true
      members_order: source

## SessionManager

::: mdb_engine.auth.session_manager.SessionManager
    options:
      show_root_heading: true
      members_order: source

## SecurityMiddleware

::: mdb_engine.auth.middleware.SecurityMiddleware
    options:
      show_root_heading: true

## CSRFMiddleware

::: mdb_engine.auth.csrf.CSRFMiddleware
    options:
      show_root_heading: true

## Decorators

::: mdb_engine.auth.decorators.require_auth

::: mdb_engine.auth.decorators.token_security

## JWT Utilities

::: mdb_engine.auth.jwt.encode_jwt_token

::: mdb_engine.auth.jwt.decode_jwt_token

::: mdb_engine.auth.jwt.generate_token_pair

## Dependencies

::: mdb_engine.auth.dependencies.get_current_user

::: mdb_engine.auth.dependencies.require_admin

::: mdb_engine.auth.dependencies.require_permission

## WebSocket Auth

::: mdb_engine.auth.websocket_sessions.WebSocketSessionManager
    options:
      show_root_heading: true

::: mdb_engine.auth.websocket_tickets.WebSocketTicketStore
    options:
      show_root_heading: true
