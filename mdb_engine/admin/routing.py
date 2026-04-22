"""
Admin module routing helpers.

Modules declare per-endpoint scopes via :class:`ModuleEndpoint`, but
they also need a tiny convenience for attaching the matching scope
dependency when registering the route. :class:`ModuleRouter` is a
thin wrapper around :class:`fastapi.APIRouter` that keeps the two in
sync — the scope string passed to :meth:`ModuleRouter.add` is the
single source of truth for both enforcement and introspection.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request, status

from .base import WILDCARD_SCOPE


def require_scope(module: str, required: str) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency that enforces *required* on the token.

    The surface's auth gate attaches :class:`_AuthResult` to
    ``request.state.mdb_auth`` before any route runs; this dependency
    reads it and decides whether the granted scopes satisfy
    ``required``.

    A missing ``mdb_auth`` means the gate didn't run (public module);
    we fall through without checking, matching legacy behaviour.
    """

    async def _check(request: Request) -> None:
        auth = getattr(request.state, "mdb_auth", None)
        if auth is None:
            return None
        granted = list(getattr(auth, "scopes", None) or [])
        if _scope_allows(module, required, granted):
            return None
        # Centralize telemetry on denials — resolve lazily so the
        # submodule has no import-time dep on core reconciler events.
        try:  # nosemgrep
            from ..core.reconciler_events import emit_event
            from .events import EVENT_SCOPE_DENIED

            emit_event(
                EVENT_SCOPE_DENIED,
                slug=getattr(auth, "slug", None),
                module=module,
                required=required,
                granted=",".join(granted) if granted else "",
                endpoint=request.url.path,
                method=request.method.upper(),
            )
        except Exception:  # noqa: BLE001 - telemetry must never break auth
            pass
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(f"token for module '{module}' is missing required scope " f"'{required}'"),
        )

    return _check


def _scope_allows(module: str, required: str, granted: list[str]) -> bool:
    """Return ``True`` when ``granted`` satisfies ``required``.

    Matching rules (in order of generality):

    1. ``"*"`` in granted                    — superuser, matches anything.
    2. ``"<module>:*"`` in granted           — full access to the module.
    3. ``"<module>:<required>"`` in granted  — exact match.
    4. Bare ``<required>`` in granted        — legacy unqualified scopes.

    A required scope of ``"*"`` means "no specific scope needed beyond
    authentication" and always passes.
    """
    if required in (WILDCARD_SCOPE, "", None):
        return True
    if WILDCARD_SCOPE in granted:
        return True
    module_wildcard = f"{module}:*"
    qualified = f"{module}:{required}"
    for g in granted:
        if g in (module_wildcard, qualified, required):
            return True
    return False


class ModuleRouter:
    """Thin convenience over :class:`APIRouter` that keeps each route's
    ``ModuleEndpoint.scope`` in sync with its FastAPI dependency.

    Example::

        mr = ModuleRouter(module_name="reconciler")
        mr.add(
            "GET", "/plan", scope="read",
            endpoint=plan_handler, summary="Show the plan.",
        )
        router = mr.router

    The returned router is what :meth:`AdminModule.build_router` hands
    back to :class:`AdminSurface`.
    """

    def __init__(self, module_name: str, *, tags: list[str] | None = None):
        self._module = module_name
        self.router = APIRouter(tags=tags or ["mdb-admin", module_name])

    def add(
        self,
        method: str,
        path: str,
        *,
        endpoint: Callable[..., Any],
        scope: str = WILDCARD_SCOPE,
        summary: str = "",
        destructive: bool = False,
        status_code: int | None = None,
    ) -> None:
        """Register a route with scope enforcement attached."""
        deps = [Depends(require_scope(self._module, scope))]
        self.router.add_api_route(
            path,
            endpoint,
            methods=[method.upper()],
            dependencies=deps,
            summary=summary,
            name=f"{self._module}_{method.lower()}_{path.strip('/').replace('/', '_') or 'root'}",
            **({"status_code": status_code} if status_code else {}),
        )
        # Record metadata on the route for introspection + idempotency.
        # The ``_mdb_*`` attributes are deliberate internal API markers on
        # ModuleRouter-authored endpoints — consumers read them through
        # :func:`route_scope` / :func:`route_destructive` helpers, not by
        # reaching in here. The SLF001 suppressions are intentional.
        self.router.routes[-1].endpoint._mdb_scope = scope  # type: ignore[attr-defined]  # noqa: SLF001
        self.router.routes[-1].endpoint._mdb_destructive = destructive  # type: ignore[attr-defined]  # noqa: SLF001


__all__ = ["ModuleRouter", "require_scope"]
