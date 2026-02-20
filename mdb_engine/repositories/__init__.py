"""
MDB Engine Repository Pattern

Provides abstract repository interfaces and MongoDB implementations
for clean data access patterns.

Usage:
    from mdb_engine.repositories import Repository, MongoRepository

    # In domain services
    class UserService:
        def __init__(self, users: Repository[User]):
            self._users = users

        async def get_user(self, id: str) -> User:
            return await self._users.get(id)

    # In FastAPI routes using UnitOfWork
    @app.get("/users/{user_id}")
    async def get_user(user_id: str, ctx: RequestContext = Depends(get_request_context)):
        user = await ctx.uow.users.get(user_id)
        return user
"""

from .base import Entity, Repository
from .mongo import MongoRepository
from .unit_of_work import UnitOfWork

__all__ = [
    "Repository",
    "Entity",
    "MongoRepository",
    "UnitOfWork",
]
