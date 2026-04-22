"""
Admin plane base protocols and shared types.

The admin plane is a first-class concern of mdb-engine: it exposes
pluggable, auth-gated HTTP modules under ``/__mdb/*``. Each module is a
tiny object that returns a FastAPI router and declares its scope
vocabulary; :class:`AdminSurface` composes the set of enabled modules
into a single router with shared auth + audit dependencies.

Third parties can ship their own module by implementing
:class:`AdminModule` and registering it on the engine's
:attr:`MongoDBEngine.admin_surface`.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import APIRouter

    from ..core.engine import MongoDBEngine


ADMIN_TOKEN_HEADER = "X-App-Token"
"""Header name carrying the per-app secret for admin endpoints."""

WILDCARD_SCOPE = "*"
"""Special scope that matches every module action (legacy default)."""


@dataclass(frozen=True)
class ModuleEndpoint:
    """A single endpoint exposed by an :class:`AdminModule`.

    The ``scope`` field is load-bearing: the :class:`AdminSurface`
    enforces it per-route (so a ``reconciler:read`` token cannot call
    ``POST /reconciler/apply``). It's also returned by
    ``GET /__mdb/health/modules`` so CLI + UI can render themselves
    generically.
    """

    method: str
    path: str
    scope: str = WILDCARD_SCOPE
    summary: str = ""
    destructive: bool = False
    """True when the endpoint mutates durable state and should honour
    ``Idempotency-Key`` replay semantics."""


@dataclass
class ModuleConfig:
    """Per-module configuration resolved from the manifest."""

    name: str
    enabled: bool = True
    scopes: list[str] = field(default_factory=lambda: [WILDCARD_SCOPE])
    public: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AdminModule(Protocol):
    """Contract every admin plane module implements.

    Modules are stateless factories: :meth:`build_router` is called once
    per :meth:`AdminSurface.build_router` invocation. Shared state (DB
    handles, reconciler references, etc.) is fetched from the engine on
    demand rather than captured at module-registration time, so the
    same module object can serve multiple engines if needed.
    """

    name: str
    """Short, URL-safe module name (e.g. ``"reconciler"``)."""

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        """Return a router mounted under ``/<name>/`` by the surface."""
        ...

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        """Return the endpoints the module exposes (for introspection)."""
        ...


__all__ = [
    "ADMIN_TOKEN_HEADER",
    "WILDCARD_SCOPE",
    "AdminModule",
    "ModuleConfig",
    "ModuleEndpoint",
]
