# Authorization Provider Architecture

## Overview

The authorization system uses the **Adapter Pattern** with a strict abstract base class to ensure type safety, fail-closed security, and proper abstraction from third-party libraries.

## Design Principles

1. **Adapter Pattern**: We wrap third-party libraries (Casbin, OSO) without modifying their source code
2. **Fail-Closed Security**: If authorization evaluation fails, access is denied (never granted)
3. **Type Safety**: Clear contracts with proper type hints and abstract methods
4. **Interface Segregation**: Application code only needs `check()`, not engine internals
5. **Observability**: Structured logging for all authorization decisions and errors

## Architecture

```
BaseAuthorizationProvider (ABC)
├── CasbinAdapter
│   └── Wraps casbin.AsyncEnforcer
└── OsoAdapter
    └── Wraps oso_cloud.Client or oso.Oso
```

## Base Class: `BaseAuthorizationProvider`

Defines the contract that all authorization providers must implement:

- `check(subject, resource, action)` - Primary authorization decision method
- `add_policy(*params)` - Add policy rules
- `add_role_for_user(*params)` - Assign roles to users
- `save_policy()` - Persist policies to storage
- `has_policy(*params)` - Check if policy exists
- `has_role_for_user(*params)` - Check if user has role
- `clear_cache()` - Clear authorization cache

### Fail-Closed Security

All `check()` implementations must:
1. Catch all exceptions during evaluation
2. Log errors with full context
3. Return `False` (deny access) on any error
4. Never raise exceptions from evaluation failures

### Error Handling

- **Evaluation Errors**: Handled by `_handle_evaluation_error()` - denies access, logs critically
- **Operation Errors**: Handled by `_handle_operation_error()` - returns False, logs warning

## CasbinAdapter

Wraps `casbin.AsyncEnforcer` and handles:
- Casbin's `(subject, object, action)` format
- Thread pool execution to prevent blocking
- Caching for performance
- MongoDB persistence via MotorAdapter

### Format Mapping

- `check(subject, resource, action)` → `enforcer.enforce(subject, resource, action)`
- `add_policy(role, resource, action)` → `enforcer.add_policy(role, resource, action)`
- `add_role_for_user(user, role)` → `enforcer.add_role_for_user(user, role)`

## OsoAdapter

Wraps OSO Cloud client and handles:
- OSO's `authorize(actor, action, resource)` format
- Type marshalling (strings → TypedObject)
- Thread pool execution
- Caching for performance

### Format Mapping

- `check(subject, resource, action)` → `client.authorize(TypedObject("User", subject), action, TypedObject("Document", resource))`
- `add_policy(role, resource, action)` → `client.insert(["grants_permission", role, action, resource])`
- `add_role_for_user(user, role, [resource])` → `client.insert(["has_role", Value("User", user), role, Value("Document", resource)])`

## Backward Compatibility

The `AuthorizationProvider` Protocol is maintained for backward compatibility. All adapters implement both:
- The Protocol (for structural typing)
- The BaseAuthorizationProvider ABC (for inheritance)

## Usage

```python
from mdb_engine.auth import BaseAuthorizationProvider, CasbinAdapter, OsoAdapter

# Type checking works with both Protocol and ABC
async def check_permission(
    provider: BaseAuthorizationProvider,  # or AuthorizationProvider
    user: str,
    resource: str,
    action: str,
) -> bool:
    return await provider.check(user, resource, action)

# Runtime type checking
if provider.is_casbin():
    # Casbin-specific operations
    enforcer = provider._enforcer  # Still accessible
elif provider.is_oso():
    # OSO-specific operations
    client = provider._oso  # Still accessible
```

## Migration Notes

- Existing code using `AuthorizationProvider` Protocol continues to work
- Code checking `hasattr(provider, "_enforcer")` continues to work
- New code should use `BaseAuthorizationProvider` for better type safety
- Helper methods `is_casbin()` and `is_oso()` available for type checking
