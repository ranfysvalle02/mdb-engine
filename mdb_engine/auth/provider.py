"""
Authorization Provider Interface

Defines the pluggable Authorization (AuthZ) interface for the platform.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import \
    annotations  # MUST be first import for string type hints

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, Tuple

from ..constants import AUTHZ_CACHE_TTL, MAX_CACHE_SIZE

if TYPE_CHECKING:
    import casbin

logger = logging.getLogger(__name__)


class AuthorizationProvider(Protocol):
    """
    Defines the "contract" for any pluggable authorization provider.
    """

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Checks if a subject is allowed to perform an action on a resource.
        """
        ...


class CasbinAdapter:
    """
    Implements the AuthorizationProvider interface using the Casbin AsyncEnforcer.
    Uses thread pool execution and caching to prevent blocking the event loop.
    """

    # Use a string literal for the type hint to prevent module-level import
    def __init__(self, enforcer: casbin.AsyncEnforcer):
        """
        Initializes the adapter with a pre-configured Casbin AsyncEnforcer.
        """
        self._enforcer = enforcer
        # Cache for authorization results: {(subject, resource, action): (result, timestamp)}
        self._cache: Dict[Tuple[str, str, str], Tuple[bool, float]] = {}
        self._cache_lock = asyncio.Lock()
        logger.info(
            "✔️  CasbinAdapter initialized with async thread pool execution and caching."
        )

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Performs the authorization check using the wrapped enforcer.
        Uses thread pool execution to prevent blocking the event loop and caches results.
        """
        cache_key = (subject, resource, action)
        current_time = time.time()

        # Check cache first
        async with self._cache_lock:
            if cache_key in self._cache:
                cached_result, cached_time = self._cache[cache_key]
                # Check if cache entry is still valid
                if current_time - cached_time < AUTHZ_CACHE_TTL:
                    logger.debug(
                        f"Authorization cache HIT for ({subject}, {resource}, {action})"
                    )
                    return cached_result
                # Cache expired, remove it
                del self._cache[cache_key]

        try:
            # The .enforce() method on AsyncEnforcer is synchronous and blocks the event loop.
            # Run it in a thread pool to prevent blocking.
            result = await asyncio.to_thread(
                self._enforcer.enforce, subject, resource, action
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

            return result
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.error(
                f"Casbin 'enforce' check failed for ({subject}, {resource}, {action}): {e}",
                exc_info=True,
            )
            return False

    async def clear_cache(self):
        """
        Clears the authorization cache. Useful when policies are updated.
        """
        async with self._cache_lock:
            self._cache.clear()
            logger.info("Authorization cache cleared.")

    async def add_policy(self, *params) -> bool:
        """Helper to pass-through policy additions for seeding."""
        try:
            result = await self._enforcer.add_policy(*params)
            # Clear cache when policies are modified
            if result:
                await self.clear_cache()
            return result
        except (ValueError, RuntimeError, AttributeError, TypeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to add policy: {e}", exc_info=True)
            return False

    async def add_role_for_user(self, *params) -> bool:
        """Helper to pass-through role additions for seeding."""
        try:
            result = await self._enforcer.add_role_for_user(*params)
            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to add role for user: {e}", exc_info=True)
            return False

    async def save_policy(self) -> bool:
        """Helper to pass-through policy saving for seeding."""
        try:
            result = await self._enforcer.save_policy()
            # Clear cache when policies are saved
            if result:
                await self.clear_cache()
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to save policy: {e}", exc_info=True)
            return False

    async def has_policy(self, *params) -> bool:
        """Check if a policy exists."""
        try:
            # Run in thread pool to prevent blocking
            result = await asyncio.to_thread(self._enforcer.has_policy, *params)
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to check policy: {e}", exc_info=True)
            return False

    async def has_role_for_user(self, *params) -> bool:
        """Check if a user has a role."""
        try:
            # Run in thread pool to prevent blocking
            result = await asyncio.to_thread(self._enforcer.has_role_for_user, *params)
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to check role for user: {e}", exc_info=True)
            return False

    async def remove_role_for_user(self, *params) -> bool:
        """Helper to pass-through role removal."""
        try:
            result = await self._enforcer.remove_role_for_user(*params)
            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
            return result
        except (ValueError, RuntimeError, AttributeError) as e:
            # Type 2: Recoverable - return False for Casbin operation errors
            logger.warning(f"Failed to remove role for user: {e}", exc_info=True)
            return False


class OsoAdapter:
    """
    Implements the AuthorizationProvider interface using OSO/Polar.
    Uses caching to improve performance and thread pool execution for blocking operations.
    """

    # Use a string literal for the type hint to prevent module-level import
    # Can accept either OSO Cloud client or OSO library client
    def __init__(self, oso_client: Any):
        """
        Initializes the adapter with a pre-configured OSO client.
        Can be either an OSO Cloud client or OSO library client.
        """
        self._oso = oso_client
        # Cache for authorization results: {(subject, resource, action): (result, timestamp)}
        self._cache: Dict[Tuple[str, str, str], Tuple[bool, float]] = {}
        self._cache_lock = asyncio.Lock()
        logger.info(
            "✔️  OsoAdapter initialized with async thread pool execution and caching."
        )

    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Performs the authorization check using OSO.
        Note: OSO's authorize method signature is: authorize(user, permission, resource)
        So we map: subject -> user, action -> permission, resource -> resource
        Uses thread pool execution to prevent blocking the event loop and caches results.
        """
        cache_key = (subject, resource, action)
        current_time = time.time()

        # Check cache first
        async with self._cache_lock:
            if cache_key in self._cache:
                cached_result, cached_time = self._cache[cache_key]
                # Check if cache entry is still valid
                if current_time - cached_time < AUTHZ_CACHE_TTL:
                    logger.debug(
                        f"Authorization cache HIT for ({subject}, {resource}, {action})"
                    )
                    return cached_result
                # Cache expired, remove it
                del self._cache[cache_key]

        try:
            # OSO Cloud's authorize method signature is: authorize(actor, action, resource)
            # OSO Cloud expects objects with .type and .id attributes, not dicts
            # Create simple objects with type and id attributes
            class TypedObject:
                def __init__(self, type_name, id_value):
                    self.type = type_name
                    self.id = id_value

            # Create typed objects for OSO Cloud
            if isinstance(subject, str):
                actor = TypedObject("User", subject)
            else:
                actor = subject

            if isinstance(resource, str):
                resource_obj = TypedObject("Document", resource)
            else:
                resource_obj = resource

            # Run in thread pool to prevent blocking the event loop
            result = await asyncio.to_thread(
                self._oso.authorize, actor, action, resource_obj
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

            return result
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.error(
                f"OSO 'authorize' check failed for ({subject}, {resource}, {action}): {e}",
                exc_info=True,
            )
            return False

    async def clear_cache(self):
        """
        Clears the authorization cache. Useful when policies are updated.
        """
        async with self._cache_lock:
            self._cache.clear()
            logger.info("Authorization cache cleared.")

    async def add_policy(self, *params) -> bool:
        """
        Adds a grants_permission fact in OSO.
        Maps Casbin policy (role, object, action) to OSO fact:
        grants_permission(role, action, object)
        """
        try:
            if len(params) != 3:
                logger.warning(
                    f"add_policy expects 3 params (role, object, action), got {len(params)}"
                )
                return False

            role, obj, act = params
            # OSO fact: grants_permission(role, action, object)
            # OSO Cloud SDK uses insert() method with a list
            if hasattr(self._oso, "insert"):
                # OSO Cloud client - insert fact as a list
                result = await asyncio.to_thread(
                    self._oso.insert, ["grants_permission", role, act, obj]
                )
            elif hasattr(self._oso, "tell"):
                # Legacy OSO Cloud SDK
                result = await asyncio.to_thread(
                    self._oso.tell, "grants_permission", role, act, obj
                )
            elif hasattr(self._oso, "register_constant"):
                # OSO library - we'd need to use a different approach
                logger.warning(
                    "OSO library mode: add_policy needs to be handled via policy files"
                )
                result = True  # Assume success for now
            else:
                logger.warning("OSO client doesn't support insert() or tell() method")
                result = False

            # Clear cache when policies are modified
            if result:
                await self.clear_cache()
            return result
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to add policy: {e}", exc_info=True)
            return False

    async def add_role_for_user(self, *params) -> bool:
        """
        Adds a has_role fact in OSO.
        Supports both global roles (2 params: user, role) and resource-based
        roles (3 params: user, role, resource).
        Maps to OSO fact: has_role(user, role) or has_role(user, role, resource)
        """
        try:
            if len(params) < 2 or len(params) > 3:
                logger.warning(
                    f"add_role_for_user expects 2-3 params "
                    f"(user, role, [resource]), got {len(params)}"
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
                    result = await asyncio.to_thread(
                        self._oso.tell, "has_role", user, role, resource
                    )
                else:
                    result = await asyncio.to_thread(
                        self._oso.tell, "has_role", user, role
                    )
            elif hasattr(self._oso, "register_constant"):
                # OSO library - we'd need to use a different approach
                logger.warning(
                    "OSO library mode: add_role_for_user needs to be handled via policy files"
                )
                result = True  # Assume success for now
            else:
                logger.warning("OSO client doesn't support insert() or tell() method")
                result = False

            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
            return result
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.warning(f"Failed to add role for user: {e}", exc_info=True)
            return False

    async def save_policy(self) -> bool:
        """
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
            return result
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Failed to save policy: {e}", exc_info=True)
            return False

    async def has_policy(self, *params) -> bool:
        """
        Check if a grants_permission fact exists in OSO.
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
                    lambda: list(
                        self._oso.query_rule(
                            "grants_permission", role, act, obj, accept_expression=True
                        )
                    )
                )
                return len(result) > 0
            else:
                # OSO Cloud - we'd need to use the API to check facts
                # For now, return True as a placeholder
                logger.debug("OSO Cloud: has_policy check not fully implemented")
                return True
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.warning(f"Failed to check policy: {e}", exc_info=True)
            return False

    async def has_role_for_user(self, *params) -> bool:
        """
        Check if a has_role fact exists in OSO.
        """
        try:
            if len(params) != 2:
                return False

            user, role = params
            # For OSO, we'd need to query facts
            if hasattr(self._oso, "query_rule"):
                # OSO library - query facts
                result = await asyncio.to_thread(
                    lambda: list(
                        self._oso.query_rule(
                            "has_role", user, role, accept_expression=True
                        )
                    )
                )
                return len(result) > 0
            elif hasattr(self._oso, "query"):
                # Alternative query method
                result = await asyncio.to_thread(
                    lambda: list(self._oso.query("has_role", user, role))
                )
                return len(result) > 0
            else:
                # OSO Cloud - we'd need to use the API to check facts
                # For now, return True as a placeholder
                logger.debug("OSO Cloud: has_role_for_user check not fully implemented")
                return True
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.warning(f"Failed to check role for user: {e}", exc_info=True)
            return False

    async def remove_role_for_user(self, *params) -> bool:
        """
        Removes a has_role fact in OSO.
        """
        try:
            if len(params) != 2:
                logger.warning(
                    f"remove_role_for_user expects 2 params (user, role), got {len(params)}"
                )
                return False

            user, role = params
            # OSO Cloud uses delete() method
            if hasattr(self._oso, "delete"):
                result = await asyncio.to_thread(
                    self._oso.delete, "has_role", user, role
                )
            else:
                logger.warning("OSO client doesn't support delete() method")
                result = False

            # Clear cache when roles are modified
            if result:
                await self.clear_cache()
            return result
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.warning(f"Failed to remove role for user: {e}", exc_info=True)
            return False
