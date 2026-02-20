"""
MDB Engine Dependency Injection Module

Enterprise-grade DI container with proper service lifetimes:
- SINGLETON: One instance per application lifetime
- REQUEST: One instance per HTTP request
- TRANSIENT: New instance on every injection

Usage:
    from mdb_engine.di import Container, Scope, inject

    # Register services
    container = Container()
    container.register(DatabaseService, scope=Scope.SINGLETON)
    container.register(UserService, scope=Scope.REQUEST)

    # In FastAPI routes
    @app.get("/users")
    async def get_users(user_svc: UserService = inject(UserService)):
        return await user_svc.list_all()
"""

from .container import Container
from .providers import FactoryProvider, Provider, SingletonProvider
from .scopes import Scope, ScopeManager

__all__ = [
    "Container",
    "Scope",
    "ScopeManager",
    "Provider",
    "FactoryProvider",
    "SingletonProvider",
]
