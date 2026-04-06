"""
Authorization Provider Interface

Defines the pluggable Authorization (AuthZ) interface for the platform.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations  # MUST be first import for string type hints

import asyncio
import logging
import time
from typing import Any, Protocol

import casbin  # type: ignore

from ..constants import AUTHZ_CACHE_TTL, MAX_CACHE_SIZE

# Import base class
from .base import AuthorizationError, BaseAuthorizationProvider

logger = logging.getLogger(__name__)


# Keep Protocol for backward compatibility and type checking
class AuthorizationProvider(Protocol):
    """
    Protocol defining the "contract" for any pluggable authorization provider.

    This Protocol is kept for backward compatibility and type checking.
    All concrete implementations should extend BaseAuthorizationProvider instead.
    """

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: dict[str, Any] | None = None,
    ) -> bool:
        """
        Checks if a subject is allowed to perform an action on a resource.
        """
        ...


class CasbinAdapter(BaseAuthorizationProvider):
    """
    Adapter for Casbin authorization engine.

    Implements the BaseAuthorizationProvider interface using Casbin AsyncEnforcer.
    Uses the Adapter Pattern to wrap Casbin without modifying its source code.

    Design Principles:
    - Fail-Closed Security: Errors deny access
    - Thread Pool Execution: Prevents blocking the event loop
    - Caching: Improves performance for repeated checks
    - Type Safety: Proper marshalling of Casbin's (sub, obj, act) format
    """

    def __init__(self, enforcer: casbin.AsyncEnforcer):
        """
        Initialize the Casbin adapter.

        Args:
            enforcer: Pre-configured Casbin AsyncEnforcer instance

        Raises:
            AuthorizationError: If Casbin is not available
        """
        super().__init__(engine_name="Casbin")
        self._enforcer = enforcer
        # Cache for authorization results: {(subject, resource, action): (result, timestamp)}
        self._cache: dict[tuple[str, str, str], tuple[bool, float]] = {}
        self._cache_lock = asyncio.Lock()
        self._mark_initialized()

    @property
    def enforcer(self):
        """
        Get the Casbin enforcer instance.

        Returns:
            casbin.AsyncEnforcer instance
        """
        return self._enforcer

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: dict[str, Any] | None = None,
    ) -> bool:
        """
        Check authorization using Casbin's enforce method.

        Implements fail-closed security: if evaluation fails, access is denied.
        Uses thread pool execution to prevent blocking the event loop.

        Casbin Format: enforce(subject, object, action)
        - subject: Who is making the request (email or user ID)
        - object: What resource they're accessing
        - action: What action they want to perform
        """
        if not self._initialized:
            logger.error("CasbinAdapter not initialized - denying access")
            return False

        cache_key = (subject, resource, action)
        current_time = time.time()

        # Check cache first
        async with self._cache_lock:
            if cache_key in self._cache:
                cached_result, cached_time = self._cache[cache_key]
                # Check if cache entry is still valid
                if current_time - cached_time < AUTHZ_CACHE_TTL:
                    logger.debug(f"Casbin cache HIT for ({subject}, {resource}, {action})")
                    return cached_result
                # Cache expired, remove it
                del self._cache[cache_key]

        try:
            # Casbin's enforce() is synchronous and blocks the event loop.
            # Run it in a thread pool to prevent blocking.
            # Casbin order: (subject, object, action)
            result = await asyncio.to_thread(
                self._enforcer.enforce,
                subject,  # Casbin subject
                resource,  # Casbin object
                action,  # Casbin action
            )

            # Cache the result
            async with self._cache_lock:
                self._cache[cache_key] = (result, current_time)
                # Limit cache size to prevent memory issues
                if len(self._cache) > MAX_CACHE_SIZE:
                    # Remove oldest entries (simple FIFO eviction)
                    oldest_key = min(
                        self._cache.items(),
                        key=lambda x: x[1][1],  # Compare by timestamp
                    )[0]
                    del self._cache[oldest_key]

            logger.debug(f"Casbin authorization check: {subject} -> {resource}:{action} = {result}")
            return result

        except (
            RuntimeError,
            ValueError,
            AttributeError,
            TypeError,
            KeyError,
            ConnectionError,
        ) as e:
            # Fail-Closed Security: Any exception denies access
            # Catching specific exceptions from Casbin/enforce operations
            return self._handle_evaluation_error(subject, resource, action, e, "enforce")

    async def clear_cache(self) -> None:
        """
        Clear the authorization cache.

        Should be called when policies or roles are modified to ensure
        fresh authorization decisions.
        """
        async with self._cache_lock:
            self._cache.clear()
            logger.info(f"{self._engine_name} authorization cache cleared")

    async def add_policy(self, *params: Any) -> bool:
        """
        Add a policy rule to Casbin.

        Casbin format: add_policy(role, resource, action)
        Example: add_policy("admin", "documents", "read")
        """
        try:
            result = await self._enforcer.add_policy(*params)
            # Clear cache when policies are modified
            if result:
                await self.clear_cache()
                logger.debug(f"Casbin policy added: {params}")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            return self._handle_operation_error("add_policy", e, *params)

    async def add_role_for_user(self, *params: Any) -> bool:
        """
        Assign a role to a user in Casbin.

        Casbin format: add_role_for_user(user, role)
        This creates a grouping policy: g(user, role)
        """
        try:
            result = await self._enforcer.add_role_for_user(*params)
            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
                logger.debug(f"Casbin role assigned: {params}")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            return self._handle_operation_error("add_role_for_user", e, *params)

    async def save_policy(self) -> bool:
        """
        Persist Casbin policies to storage (MongoDB via MotorAdapter).

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            result = await self._enforcer.save_policy()
            # Clear cache when policies are saved
            if result:
                await self.clear_cache()
                logger.debug("Casbin policies saved to storage")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            return self._handle_operation_error("save_policy", e)

    async def has_policy(self, *params: Any) -> bool:
        """
        Check if a policy exists in Casbin.

        Casbin format: has_policy(role, resource, action)
        """
        try:
            # Run in thread pool to prevent blocking
            result = await asyncio.to_thread(self._enforcer.has_policy, *params)
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            self._handle_operation_error("has_policy", e, *params)
            return False

    async def has_role_for_user(self, *params: Any) -> bool:
        """
        Check if a user has a specific role in Casbin.

        Casbin format: has_role_for_user(user, role)
        """
        try:
            # AsyncEnforcer.has_role_for_user is async, await it directly
            result = await self._enforcer.has_role_for_user(*params)
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            self._handle_operation_error("has_role_for_user", e, *params)
            return False

    async def remove_role_for_user(self, *params: Any) -> bool:
        """
        Remove a role assignment from a user in Casbin.

        Casbin format: remove_role_for_user(user, role)
        """
        try:
            result = await self._enforcer.remove_role_for_user(*params)
            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
                logger.debug(f"Casbin role removed: {params}")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from Casbin operations
            return self._handle_operation_error("remove_role_for_user", e, *params)


class OsoAdapter(BaseAuthorizationProvider):
    """
    Adapter for OSO Cloud authorization engine.

    Implements the BaseAuthorizationProvider interface using OSO Cloud or OSO library.
    Uses the Adapter Pattern to wrap OSO without modifying its source code.

    Design Principles:
    - Fail-Closed Security: Errors deny access
    - Thread Pool Execution: Prevents blocking the event loop
    - Caching: Improves performance for repeated checks
    - Type Marshalling: Converts strings to OSO's TypedObject format
    """

    def __init__(self, oso_client: Any):
        """
        Initialize the OSO adapter.

        The oso_factory already validates that the oso-cloud package is
        importable before constructing a client, so we only guard against
        a None client here (fail-closed).

        Args:
            oso_client: Pre-configured OSO Cloud client or OSO library instance

        Raises:
            AuthorizationError: If oso_client is None
        """
        if oso_client is None:
            raise AuthorizationError("oso_client must not be None")

        super().__init__(engine_name="OSO Cloud")
        self._oso = oso_client
        self._cache: dict[tuple[str, str, str], tuple[bool, float]] = {}
        self._cache_lock = asyncio.Lock()
        self._mark_initialized()

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: dict[str, Any] | None = None,
    ) -> bool:
        """
        Check authorization using OSO's authorize method.

        Implements fail-closed security: if evaluation fails, access is denied.
        Uses thread pool execution to prevent blocking the event loop.

        OSO Format: authorize(actor, action, resource)
        - OSO expects objects with .type and .id attributes
        - We marshal strings to TypedObject instances
        """
        if not self._initialized:
            logger.error("OsoAdapter not initialized - denying access")
            return False

        cache_key = (subject, resource, action)
        current_time = time.time()

        # Check cache first
        async with self._cache_lock:
            if cache_key in self._cache:
                cached_result, cached_time = self._cache[cache_key]
                # Check if cache entry is still valid
                if current_time - cached_time < AUTHZ_CACHE_TTL:
                    logger.debug(f"OSO cache HIT for ({subject}, {resource}, {action})")
                    return cached_result
                # Cache expired, remove it
                del self._cache[cache_key]

        try:
            # OSO Cloud expects objects with .type and .id attributes
            # Create typed objects for OSO Cloud
            class TypedObject:
                """Helper class to create OSO-compatible typed objects."""

                def __init__(self, type_name: str, id_value: str):
                    self.type = type_name
                    self.id = id_value

            # Marshal strings to OSO TypedObject format
            if isinstance(subject, str):
                actor = TypedObject("User", subject)
            else:
                actor = subject

            if isinstance(resource, str):
                resource_obj = TypedObject("Document", resource)
            else:
                resource_obj = resource

            # Run in thread pool to prevent blocking the event loop
            # OSO signature: authorize(actor, action, resource)
            result = await asyncio.to_thread(
                self._oso.authorize,
                actor,
                action,
                resource_obj,
            )

            # Cache the result
            async with self._cache_lock:
                self._cache[cache_key] = (result, current_time)
                # Limit cache size to prevent memory issues
                if len(self._cache) > MAX_CACHE_SIZE:
                    # Remove oldest entries (simple FIFO eviction)
                    oldest_key = min(
                        self._cache.items(),
                        key=lambda x: x[1][1],  # Compare by timestamp
                    )[0]
                    del self._cache[oldest_key]

            logger.debug(f"OSO authorization check: {subject} -> {resource}:{action} = {result}")
            return result

        except (
            RuntimeError,
            ValueError,
            AttributeError,
            TypeError,
            KeyError,
            ConnectionError,
        ) as e:
            # Fail-Closed Security: Any exception denies access
            # Catching specific exceptions from OSO operations
            return self._handle_evaluation_error(subject, resource, action, e, "authorize")

    async def clear_cache(self) -> None:
        """
        Clear the authorization cache.

        Should be called when policies or roles are modified to ensure
        fresh authorization decisions.
        """
        async with self._cache_lock:
            self._cache.clear()
            logger.info(f"{self._engine_name} authorization cache cleared")

    async def add_policy(self, *params: Any) -> bool:
        """
        Add a policy rule to OSO.

        OSO format: grants_permission(role, action, object)
        Maps from Casbin format: (role, object, action)
        """
        try:
            if len(params) != 3:
                logger.warning(f"OSO add_policy expects 3 params (role, object, action), got {len(params)}")
                return False

            role, obj, act = params
            # OSO fact: grants_permission(role, action, object)
            # OSO Cloud SDK uses insert() method with a list
            if hasattr(self._oso, "insert"):
                # OSO Cloud client - insert fact as a list
                result = await asyncio.to_thread(self._oso.insert, ["grants_permission", role, act, obj])
            elif hasattr(self._oso, "tell"):
                # Legacy OSO Cloud SDK
                result = await asyncio.to_thread(self._oso.tell, "grants_permission", role, act, obj)
            elif hasattr(self._oso, "register_constant"):
                # OSO library - we'd need to use a different approach
                logger.warning("OSO library mode: add_policy needs to be handled via policy files")
                result = True  # Assume success for now
            else:
                logger.warning("OSO client doesn't support insert() or tell() method")
                result = False

            # Clear cache when policies are modified
            if result:
                await self.clear_cache()
                logger.debug(f"OSO policy added: grants_permission({role}, {act}, {obj})")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            return self._handle_operation_error("add_policy", e, *params)

    async def add_role_for_user(self, *params: Any) -> bool:
        """
        Assign a role to a user in OSO.

        OSO format: has_role(user, role) or has_role(user, role, resource)
        Supports both global roles (2 params) and resource-based roles (3 params)
        """
        try:
            if len(params) < 2 or len(params) > 3:
                logger.warning(
                    f"OSO add_role_for_user expects 2-3 params " f"(user, role, [resource]), got {len(params)}"
                )
                return False

            user, role = params[0], params[1]
            resource = params[2] if len(params) == 3 else None

            # OSO Cloud SDK uses insert() method with Value objects for typed entities
            if hasattr(self._oso, "insert"):
                try:
                    from oso_cloud import Value

                    # OSO Cloud client - insert fact with Value objects
                    # User must be a Value object with type "User" and id as the email string
                    user_value = Value("User", str(user))

                    if resource is not None:
                        # Resource-based role: has_role(user, role, resource)
                        # Resource can be a string (resource type) or a Value object
                        if isinstance(resource, str):
                            resource_value = Value("Document", str(resource))
                        else:
                            resource_value = resource
                        fact = ["has_role", user_value, str(role), resource_value]
                    else:
                        # Global role: has_role(user, role)
                        # For resource-based policies, we still need a resource
                        # Default to resource type "documents" if not specified
                        resource_value = Value("Document", "documents")
                        fact = ["has_role", user_value, str(role), resource_value]

                    result = await asyncio.to_thread(self._oso.insert, fact)
                except ImportError:
                    # Fallback if Value not available - try with string
                    if resource is not None:
                        fact = ["has_role", str(user), str(role), str(resource)]
                    else:
                        fact = ["has_role", str(user), str(role), "documents"]
                    result = await asyncio.to_thread(self._oso.insert, fact)
            elif hasattr(self._oso, "tell"):
                # Legacy OSO Cloud SDK
                if resource is not None:
                    result = await asyncio.to_thread(self._oso.tell, "has_role", user, role, resource)
                else:
                    result = await asyncio.to_thread(self._oso.tell, "has_role", user, role)
            elif hasattr(self._oso, "register_constant"):
                # OSO library - we'd need to use a different approach
                logger.warning("OSO library mode: add_role_for_user needs to be handled via policy files")
                result = True  # Assume success for now
            else:
                logger.warning("OSO client doesn't support insert() or tell() method")
                result = False

            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
                logger.debug(f"OSO role assigned: has_role({user}, {role}, " f"{resource or 'documents'})")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            return self._handle_operation_error("add_role_for_user", e, *params)

    async def save_policy(self) -> bool:
        """
        Persist OSO policies/facts to storage.

        For OSO Cloud, facts are saved automatically.
        For OSO library, this would save to a file or database.
        """
        try:
            # OSO Cloud automatically persists facts, so this is a no-op
            # For OSO library, we might need to implement file/database saving
            if hasattr(self._oso, "save"):
                result = await asyncio.to_thread(self._oso.save)
            else:
                # OSO Cloud - facts are automatically persisted
                result = True

            # Clear cache when policies are saved
            if result:
                await self.clear_cache()
                logger.debug("OSO policies/facts saved to storage")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            return self._handle_operation_error("save_policy", e)

    async def has_policy(self, *params: Any) -> bool:
        """
        Check if a policy exists in OSO.

        OSO format: grants_permission(role, action, object)
        """
        try:
            if len(params) != 3:
                return False

            role, obj, act = params
            # For OSO, we'd need to query facts or check if authorize would work
            # This is a simplified check - in practice, you might want to query facts directly
            # For now, we'll attempt an authorize check as a proxy
            # This isn't perfect but provides compatibility
            if hasattr(self._oso, "query"):
                # OSO library - query facts
                result = await asyncio.to_thread(
                    lambda: list(self._oso.query_rule("grants_permission", role, act, obj, accept_expression=True))
                )
                return len(result) > 0
            else:
                # OSO Cloud - we'd need to use the API to check facts
                # For now, return True as a placeholder
                logger.debug("OSO Cloud: has_policy check not fully implemented")
                return True
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            self._handle_operation_error("has_policy", e, *params)
            return False

    async def has_role_for_user(self, *params: Any) -> bool:
        """
        Check if a user has a specific role in OSO.

        OSO format: has_role(user, role)
        """
        try:
            if len(params) != 2:
                return False

            user, role = params
            # For OSO, we'd need to query facts
            if hasattr(self._oso, "query_rule"):
                # OSO library - query facts
                result = await asyncio.to_thread(
                    lambda: list(self._oso.query_rule("has_role", user, role, accept_expression=True))
                )
                return len(result) > 0
            elif hasattr(self._oso, "query"):
                # Alternative query method
                result = await asyncio.to_thread(lambda: list(self._oso.query("has_role", user, role)))
                return len(result) > 0
            else:
                # OSO Cloud - we'd need to use the API to check facts
                # For now, return True as a placeholder
                logger.debug("OSO Cloud: has_role_for_user check not fully implemented")
                return True
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            self._handle_operation_error("has_role_for_user", e, *params)
            return False

    async def remove_role_for_user(self, *params: Any) -> bool:
        """
        Remove a role assignment from a user in OSO.

        OSO format: remove has_role(user, role)
        """
        try:
            if len(params) != 2:
                logger.warning(f"OSO remove_role_for_user expects 2 params (user, role), got {len(params)}")
                return False

            user, role = params
            # OSO Cloud uses delete() method
            if hasattr(self._oso, "delete"):
                result = await asyncio.to_thread(self._oso.delete, "has_role", user, role)
            else:
                logger.warning("OSO client doesn't support delete() method")
                result = False

            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
                logger.debug(f"OSO role removed: has_role({user}, {role})")
            return result
        except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError) as e:
            # Catching specific exceptions from OSO operations
            return self._handle_operation_error("remove_role_for_user", e, *params)
