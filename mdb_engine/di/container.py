"""
Dependency Injection Container

A lightweight, FastAPI-native DI container with proper service lifetimes.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any, Optional, TypeVar

from .providers import Provider
from .scopes import Scope

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Container:
    """
    Dependency Injection Container with proper service lifetimes.

    Supports three scopes:
    - SINGLETON: One instance for app lifetime
    - REQUEST: One instance per HTTP request
    - TRANSIENT: New instance on every resolve

    Usage:
        container = Container()

        # Register with auto-detection
        container.register(UserService)  # Singleton by default
        container.register(RequestContext, scope=Scope.REQUEST)

        # Register with factory
        container.register_factory(
            Database,
            lambda c: Database(c.resolve(Config).db_url),
            scope=Scope.SINGLETON
        )

        # Register instance directly
        container.register_instance(Config, config_instance)

        # Resolve
        user_svc = container.resolve(UserService)
    """

    _global_instance: Optional["Container"] = None
    _global_lock = threading.Lock()

    def __init__(self):
        self._providers: dict[type, Provider] = {}
        self._instances: dict[type, Any] = {}  # For register_instance

    @classmethod
    def get_global(cls) -> "Container":
        """Get the global container instance (thread-safe)."""
        if cls._global_instance is not None:  # Fast path
            return cls._global_instance
        with cls._global_lock:  # Double-check
            if cls._global_instance is None:
                cls._global_instance = Container()
            return cls._global_instance

    @classmethod
    def set_global(cls, container: "Container") -> None:
        """Set the global container instance."""
        cls._global_instance = container

    @classmethod
    def reset_global(cls) -> None:
        """Reset the global container (useful for testing)."""
        cls._global_instance = None

    def register(
        self,
        service_type: type[T],
        implementation: type[T] | None = None,
        scope: Scope = Scope.SINGLETON,
    ) -> "Container":
        """
        Register a service type with the container.

        Args:
            service_type: The type to register (interface or concrete class)
            implementation: Optional implementation class (defaults to service_type)
            scope: Service lifetime scope

        Returns:
            Self for chaining

        Example:
            container.register(UserService)
            container.register(IRepository, MongoRepository, Scope.REQUEST)
        """
        from .providers import RequestProvider, SingletonProvider, TransientProvider

        impl = implementation or service_type

        if scope == Scope.SINGLETON:
            self._providers[service_type] = SingletonProvider(service_type, impl)
        elif scope == Scope.REQUEST:
            self._providers[service_type] = RequestProvider(service_type, impl)
        else:
            self._providers[service_type] = TransientProvider(service_type, impl)

        logger.debug(f"Registered {service_type.__name__} as {scope.value}")
        return self

    def register_factory(
        self,
        service_type: type[T],
        factory: Callable[["Container"], T],
        scope: Scope = Scope.SINGLETON,
    ) -> "Container":
        """
        Register a service with a custom factory function.

        The factory receives the container and can resolve dependencies manually.

        Args:
            service_type: The type to register
            factory: Factory function (container) -> instance
            scope: Service lifetime scope

        Returns:
            Self for chaining

        Example:
            container.register_factory(
                Database,
                lambda c: Database(c.resolve(Config).connection_string),
                Scope.SINGLETON
            )
        """
        from .providers import FactoryProvider

        self._providers[service_type] = FactoryProvider(service_type, factory, scope)
        logger.debug(f"Registered factory for {service_type.__name__} as {scope.value}")
        return self

    def register_instance(self, service_type: type[T], instance: T) -> "Container":
        """
        Register an existing instance as a singleton.

        Useful for configuration objects or externally created instances.

        Args:
            service_type: The type to register
            instance: The instance to use

        Returns:
            Self for chaining
        """
        self._instances[service_type] = instance
        logger.debug(f"Registered instance for {service_type.__name__}")
        return self

    def resolve(self, service_type: type[T]) -> T:
        """
        Resolve a service instance.

        Args:
            service_type: The type to resolve

        Returns:
            Service instance

        Raises:
            KeyError: If service is not registered
        """
        # Check direct instances first
        if service_type in self._instances:
            return self._instances[service_type]

        # Check providers
        if service_type not in self._providers:
            raise KeyError(
                f"Service {service_type.__name__} is not registered. "
                f"Call container.register({service_type.__name__}) first."
            )

        return self._providers[service_type].get(self)

    def try_resolve(self, service_type: type[T]) -> T | None:
        """
        Try to resolve a service, returning None if not registered.

        Args:
            service_type: The type to resolve

        Returns:
            Service instance or None
        """
        try:
            return self.resolve(service_type)
        except KeyError:
            return None

    def is_registered(self, service_type: type) -> bool:
        """Check if a service type is registered."""
        return service_type in self._providers or service_type in self._instances

    def reset(self) -> None:
        """
        Reset all registrations and cached instances.

        Useful for testing.
        """
        for provider in self._providers.values():
            if hasattr(provider, "reset"):
                provider.reset()
        self._providers.clear()
        self._instances.clear()
        logger.debug("Container reset")

    def __contains__(self, service_type: type) -> bool:
        """Support 'in' operator for checking registration."""
        return self.is_registered(service_type)


# FastAPI integration helpers
def inject(service_type: type[T]) -> T:
    """
    FastAPI dependency that resolves a service from the global container.

    Usage:
        @app.get("/users")
        async def get_users(user_svc: UserService = Depends(inject(UserService))):
            return await user_svc.list_all()

    Or with the shorthand:
        @app.get("/users")
        async def get_users(user_svc: UserService = Inject(UserService)):
            return await user_svc.list_all()
    """
    from fastapi import Request

    async def _dependency(request: Request) -> T:
        # Get container from app state or use global
        container = getattr(request.app.state, "container", None)
        if container is None:
            container = Container.get_global()
        return container.resolve(service_type)

    return _dependency


# Alias for cleaner syntax
Inject = inject
