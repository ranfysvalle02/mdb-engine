"""
Authorization Engine Base Classes

Defines the abstract contract for authorization providers using the Adapter Pattern.
This ensures type safety, fail-closed security, and proper abstraction.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from ..exceptions import MongoDBEngineError

logger = logging.getLogger(__name__)


class AuthorizationError(MongoDBEngineError):
    """
    Base exception for authorization failures.

    Ensures abstraction doesn't leak - application code doesn't need to know
    if the failure came from Casbin, OSO, or any other engine.
    """

    pass


class BaseAuthorizationProvider(abc.ABC):
    """
    Abstract Base Class defining the contract for authorization providers.

    Design Principles:
    1. Interface Segregation - Application only needs 'check', not engine internals
    2. Fail-Closed Security - Errors deny access, never grant it
    3. Adapter Pattern - Wraps third-party libraries without modifying them
    4. Type Safety - Clear contracts with proper type hints

    This ABC ensures that all authorization providers:
    - Have a consistent interface
    - Fail securely (deny on error)
    - Provide observability (structured logging)
    - Handle edge cases gracefully
    """

    def __init__(self, engine_name: str):
        """
        Initialize the base provider.

        Args:
            engine_name: Human-readable name of the engine (e.g., "Casbin", "OSO Cloud")
        """
        self._engine_name = engine_name
        self._initialized = False
        logger.info(f"Initializing {engine_name} authorization provider")

    @property
    def engine_name(self) -> str:
        """Get the name of the authorization engine."""
        return self._engine_name

    @property
    def is_initialized(self) -> bool:
        """Check if the provider has been properly initialized."""
        return self._initialized

    @abc.abstractmethod
    async def check(
        self,
        subject: str,
        resource: str,
        action: str,
        user_object: dict[str, Any] | None = None,
    ) -> bool:
        """
        Check if a subject is allowed to perform an action on a resource.

        This is the primary authorization decision method. All implementations
        must follow fail-closed security: if evaluation fails, deny access.

        Args:
            subject: Who is making the request (typically email or user ID)
            resource: What resource they're accessing (e.g., "documents", "clicks")
            action: What action they want to perform (e.g., "read", "write", "delete")
            user_object: Optional user object with additional context

        Returns:
            True if authorized, False otherwise (including on error - fail-closed)

        Raises:
            AuthorizationError: Only for configuration/initialization errors,
                not evaluation failures
        """
        pass

    @abc.abstractmethod
    async def add_policy(self, *params: Any) -> bool:
        """
        Add a policy rule to the authorization engine.

        Args:
            *params: Policy parameters (format depends on engine)

        Returns:
            True if policy was added successfully, False otherwise
        """
        pass

    @abc.abstractmethod
    async def add_role_for_user(self, *params: Any) -> bool:
        """
        Assign a role to a user.

        Args:
            *params: Role assignment parameters (format depends on engine)

        Returns:
            True if role was assigned successfully, False otherwise
        """
        pass

    @abc.abstractmethod
    async def save_policy(self) -> bool:
        """
        Persist policy changes to storage.

        Returns:
            True if saved successfully, False otherwise
        """
        pass

    @abc.abstractmethod
    async def has_policy(self, *params: Any) -> bool:
        """
        Check if a policy exists.

        Args:
            *params: Policy parameters to check

        Returns:
            True if policy exists, False otherwise
        """
        pass

    @abc.abstractmethod
    async def has_role_for_user(self, *params: Any) -> bool:
        """
        Check if a user has a specific role.

        Args:
            *params: User and role parameters

        Returns:
            True if user has the role, False otherwise
        """
        pass

    @abc.abstractmethod
    async def clear_cache(self) -> None:
        """
        Clear the authorization cache.

        Should be called when policies or roles are modified to ensure
        fresh authorization decisions.
        """
        pass

    def _mark_initialized(self) -> None:
        """Mark the provider as initialized (internal use only)."""
        self._initialized = True
        logger.info(f"{self._engine_name} authorization provider initialized successfully")

    def is_casbin(self) -> bool:
        """
        Check if this provider is a Casbin adapter.

        Returns:
            True if this is a CasbinAdapter, False otherwise
        """
        return hasattr(self, "_enforcer")

    def is_oso(self) -> bool:
        """
        Check if this provider is an OSO adapter.

        Returns:
            True if this is an OsoAdapter, False otherwise
        """
        return hasattr(self, "_oso")

    def _handle_evaluation_error(
        self,
        subject: str,
        resource: str,
        action: str,
        error: Exception,
        context: str | None = None,
    ) -> bool:
        """
        Handle authorization evaluation errors with fail-closed security.

        Design Principle: Fail-Closed Security
        - If the authorization engine crashes or errors, we MUST deny access
        - Logging the error is critical for observability
        - Never raise exceptions from evaluation errors (only from config errors)

        Args:
            subject: Subject that was being checked
            resource: Resource that was being checked
            action: Action that was being checked
            error: The exception that occurred
            context: Optional context string for logging

        Returns:
            False (deny access - fail-closed)
        """
        context_str = f" ({context})" if context else ""
        logger.critical(
            f"{self._engine_name} authorization evaluation failed{context_str}: "
            f"subject={subject}, resource={resource}, action={action}, "
            f"error={type(error).__name__}: {error}",
            exc_info=True,
        )
        return False

    def _handle_operation_error(
        self,
        operation: str,
        error: Exception,
        *params: Any,
    ) -> bool:
        """
        Handle policy/role operation errors.

        These are non-critical operations (adding policies, roles, etc.)
        so we log warnings but don't fail-closed (return False).

        Args:
            operation: Name of the operation (e.g., "add_policy")
            error: The exception that occurred
            *params: Parameters that were passed to the operation

        Returns:
            False (operation failed)
        """
        logger.warning(
            f"{self._engine_name} {operation} failed: " f"params={params}, error={type(error).__name__}: {error}",
            exc_info=True,
        )
        return False
