"""
Service Providers for Dependency Injection

Providers are responsible for creating and managing service instances
according to their configured scope.
"""

import inspect
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .scopes import Scope, ScopeManager

if TYPE_CHECKING:
    from .container import Container

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Provider(ABC, Generic[T]):
    """
    Abstract base class for service providers.

    Providers know how to create instances of a service and manage
    their lifecycle according to the configured scope.
    """

    def __init__(
        self,
        service_type: type[T],
        scope: Scope,
        factory: Callable[..., T] | None = None,
    ):
        self.service_type = service_type
        self.scope = scope
        self._factory = factory or service_type

    @abstractmethod
    def get(self, container: "Container") -> T:
        """
        Get or create a service instance.

        Args:
            container: The DI container for resolving dependencies

        Returns:
            Service instance
        """
        pass

    def _create_instance(self, container: "Container") -> T:
        """
        Create a new instance, injecting dependencies.

        Inspects the factory/constructor signature and resolves
        any type-hinted parameters from the container.
        """
        # Get constructor signature
        sig = inspect.signature(self._factory)
        kwargs: dict[str, Any] = {}

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            # Check if parameter has a type annotation
            if param.annotation != inspect.Parameter.empty:
                param_type = param.annotation

                # Skip primitive types and Optional markers
                if param_type in (str, int, float, bool, type(None)):
                    continue

                # Handle Optional[X] - extract X
                origin = getattr(param_type, "__origin__", None)
                if origin is type(None):
                    continue

                # Try to resolve from container
                try:
                    kwargs[param_name] = container.resolve(param_type)
                except KeyError:
                    # If not registered and has default, skip
                    if param.default != inspect.Parameter.empty:
                        continue
                    # If not registered and no default, re-raise
                    raise

        return self._factory(**kwargs)


class SingletonProvider(Provider[T]):
    """
    Provider that creates a single instance shared across the application.

    The instance is created lazily on first request and cached forever.
    """

    def __init__(
        self,
        service_type: type[T],
        factory: Callable[..., T] | None = None,
    ):
        super().__init__(service_type, Scope.SINGLETON, factory)
        self._instance: T | None = None
        self._lock = threading.Lock()

    def get(self, container: "Container") -> T:
        if self._instance is not None:  # Fast path (no lock)
            return self._instance
        with self._lock:  # Slow path (double-check)
            if self._instance is None:
                self._instance = self._create_instance(container)
                logger.debug(f"Created singleton: {self.service_type.__name__}")
            return self._instance

    def reset(self) -> None:
        """Reset the singleton (useful for testing)."""
        with self._lock:
            self._instance = None


class RequestProvider(Provider[T]):
    """
    Provider that creates one instance per request.

    Uses ScopeManager to cache instances within the request scope.
    """

    def __init__(
        self,
        service_type: type[T],
        factory: Callable[..., T] | None = None,
    ):
        super().__init__(service_type, Scope.REQUEST, factory)

    def get(self, container: "Container") -> T:
        return ScopeManager.get_or_create(self.service_type, lambda: self._create_instance(container))


class TransientProvider(Provider[T]):
    """
    Provider that creates a new instance every time.
    """

    def __init__(
        self,
        service_type: type[T],
        factory: Callable[..., T] | None = None,
    ):
        super().__init__(service_type, Scope.TRANSIENT, factory)

    def get(self, container: "Container") -> T:
        instance = self._create_instance(container)
        logger.debug(f"Created transient: {self.service_type.__name__}")
        return instance


class FactoryProvider(Provider[T]):
    """
    Provider that uses a custom factory function.

    The factory is called with the container as the first argument,
    allowing manual dependency resolution.

    Usage:
        def create_user_service(container: Container) -> UserService:
            db = container.resolve(Database)
            return UserService(db, custom_config=...)

        container.register_factory(UserService, create_user_service, Scope.REQUEST)
    """

    def __init__(
        self,
        service_type: type[T],
        factory: Callable[["Container"], T],
        scope: Scope,
    ):
        super().__init__(service_type, scope, None)
        self._custom_factory = factory
        self._singleton_instance: T | None = None
        self._lock = threading.Lock()

    def get(self, container: "Container") -> T:
        if self.scope == Scope.SINGLETON:
            if self._singleton_instance is not None:  # Fast path
                return self._singleton_instance
            with self._lock:  # Double-check
                if self._singleton_instance is None:
                    self._singleton_instance = self._custom_factory(container)
                return self._singleton_instance

        elif self.scope == Scope.REQUEST:
            return ScopeManager.get_or_create(self.service_type, lambda: self._custom_factory(container))

        else:  # TRANSIENT
            return self._custom_factory(container)


__all__ = [
    "Provider",
    "SingletonProvider",
    "RequestProvider",
    "TransientProvider",
    "FactoryProvider",
]
