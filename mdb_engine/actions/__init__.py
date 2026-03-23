"""
Manifest-driven Actions for mdb-engine.

Actions are single-file Python handlers auto-discovered from an ``actions/``
directory next to the manifest.  Each file exports an async ``handler``
function that receives an :class:`ActionContext` with access to all engine
services (scoped DB, auth, memory, LLM, embeddings).

Three trigger types are supported:

* **http** — mounted as ``POST /actions/v1/<name>`` (default)
* **schedule** — run on a recurring interval via the ``recurring_task`` loop
* **event** — fired by collection hooks (``after_create``, ``after_update``,
  ``after_delete``)

Quick example (``actions/send-welcome-email.py``)::

    from mdb_engine.actions import ActionContext

    async def handler(ctx: ActionContext):
        user = ctx.require_user()
        body = await ctx.json()
        db = await ctx.get_db()
        await db.email_queue.insert_one({"to": body["email"], "user_id": user["_id"]})
        return ctx.json_response({"queued": True})
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.responses import Response

if TYPE_CHECKING:
    from fastapi import Request

    from ..core.engine import MongoDBEngine
    from ..database.scoped_wrapper import ScopedMongoWrapper
    from ..embeddings.service import EmbeddingService
    from ..llm.service import LLMService
    from ..memory import BaseMemoryService
    from ..repositories import UnitOfWork

logger = logging.getLogger(__name__)

ActionResponse = JSONResponse | Response | dict


class ActionContext:
    """All-in-one context injected into every action handler.

    Provides lazy access to the scoped database, authentication helpers,
    AI services, and request data.  For HTTP-triggered actions the full
    :class:`~starlette.requests.Request` is available; for schedule and
    event triggers ``request`` is ``None``.
    """

    def __init__(
        self,
        engine: MongoDBEngine,
        slug: str,
        *,
        request: Request | None = None,
        event_doc: dict[str, Any] | None = None,
        event_user: dict[str, Any] | None = None,
        event_name: str | None = None,
        event_prev: dict[str, Any] | None = None,
    ) -> None:
        self._engine = engine
        self._slug = slug
        self._request = request
        self._event_doc = event_doc
        self._event_user = event_user
        self._event_name = event_name
        self._event_prev = event_prev
        self._db: ScopedMongoWrapper | None = None
        self._uow: UnitOfWork | None = None

    # ------------------------------------------------------------------
    # Factory for event triggers
    # ------------------------------------------------------------------

    @classmethod
    def from_event(
        cls,
        engine: MongoDBEngine,
        slug: str,
        *,
        doc: dict[str, Any],
        user: dict[str, Any] | None = None,
        event: str = "",
        prev: dict[str, Any] | None = None,
    ) -> ActionContext:
        """Build a context for an event-triggered action."""
        return cls(
            engine=engine,
            slug=slug,
            event_doc=doc,
            event_user=user,
            event_name=event,
            event_prev=prev,
        )

    # ------------------------------------------------------------------
    # Request helpers (HTTP triggers only)
    # ------------------------------------------------------------------

    @property
    def request(self) -> Request | None:
        return self._request

    async def json(self) -> Any:
        """Parse the request body as JSON (HTTP triggers only)."""
        if self._request is None:
            raise RuntimeError("json() is only available for HTTP-triggered actions")
        return await self._request.json()

    async def text(self) -> str:
        """Read the request body as text (HTTP triggers only)."""
        if self._request is None:
            raise RuntimeError("text() is only available for HTTP-triggered actions")
        return (await self._request.body()).decode()

    @property
    def method(self) -> str:
        """HTTP method of the incoming request."""
        if self._request is None:
            return ""
        return self._request.method

    @property
    def headers(self) -> dict[str, str]:
        """Request headers as a plain dict."""
        if self._request is None:
            return {}
        return dict(self._request.headers)

    @property
    def query_params(self) -> dict[str, str]:
        """Query parameters as a plain dict."""
        if self._request is None:
            return {}
        return dict(self._request.query_params)

    # ------------------------------------------------------------------
    # Event helpers (event triggers only)
    # ------------------------------------------------------------------

    @property
    def event_doc(self) -> dict[str, Any] | None:
        """The document that triggered the event (event triggers only)."""
        return self._event_doc

    @property
    def event_prev(self) -> dict[str, Any] | None:
        """Previous document state before an update (event triggers only)."""
        return self._event_prev

    @property
    def event_name(self) -> str | None:
        """Name of the event that triggered this action."""
        return self._event_name

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    @property
    def user(self) -> dict[str, Any] | None:
        """Current authenticated user (from middleware or event context)."""
        if self._request is not None:
            return getattr(self._request.state, "user", None)
        return self._event_user

    def require_user(self) -> dict[str, Any]:
        """Require an authenticated user; raises 401 if absent."""
        u = self.user
        if not u:
            raise HTTPException(status_code=401, detail="Authentication required")
        return u

    def require_role(self, *roles: str) -> dict[str, Any]:
        """Require the user to hold at least one of *roles*; raises 403."""
        user = self.require_user()
        user_roles: set[str] = set()
        single = user.get("role")
        if single:
            user_roles.add(str(single))
        multi = user.get("roles", [])
        if isinstance(multi, list | tuple | set | frozenset):
            user_roles |= {str(r) for r in multi if r}
        if not any(r in user_roles for r in roles):
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {' or '.join(roles)}",
            )
        return user

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    @property
    def engine(self) -> MongoDBEngine:
        return self._engine

    @property
    def slug(self) -> str:
        return self._slug

    async def get_db(self) -> ScopedMongoWrapper:
        """Get the scoped database wrapper (cached per context)."""
        if self._db is None:
            self._db = await self._engine.get_scoped_db(self._slug)
        return self._db

    async def get_uow(self) -> UnitOfWork:
        """Get a Unit of Work for repository-style access (cached)."""
        if self._uow is None:
            from ..repositories import UnitOfWork

            db = await self.get_db()
            self._uow = UnitOfWork(db)
        return self._uow

    # ------------------------------------------------------------------
    # AI services (None when not configured)
    # ------------------------------------------------------------------

    @property
    def memory(self) -> BaseMemoryService | None:
        """Memory service for the current app (None if not configured)."""
        return self._engine.get_memory_service(self._slug)

    @property
    def llm(self) -> LLMService | None:
        """LLM service for the current app (None if not configured)."""
        return self._engine.get_llm_service(self._slug)

    @property
    def embedding(self) -> EmbeddingService | None:
        """Embedding service for the current app (None if not configured)."""
        return self._engine.get_embedding_service(self._slug)

    # ------------------------------------------------------------------
    # Response helpers (HTTP triggers)
    # ------------------------------------------------------------------

    @staticmethod
    def json_response(data: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
        """Build a JSON response."""
        return JSONResponse(content=data, status_code=status, **kwargs)

    @staticmethod
    def text_response(text: str, status: int = 200) -> Response:
        """Build a plain-text response."""
        return Response(content=text, status_code=status, media_type="text/plain")

    @staticmethod
    def error(status: int, detail: str) -> HTTPException:
        """Create an HTTPException (raise the return value)."""
        return HTTPException(status_code=status, detail=detail)


__all__ = ["ActionContext", "ActionResponse"]
