"""
Reconciler admin module.

Mounts the reconciler's planning/history surface at
``/__mdb/reconciler/*``:

- ``GET  /reconciler/plan?slug=``               (scope: ``read``)
- ``POST /reconciler/apply?slug=&...``           (scope: ``apply``, destructive)
- ``GET  /reconciler/manifest/history?slug=``    (scope: ``read``)
- ``GET  /reconciler/manifest/diff?slug=``       (scope: ``read``)

Every endpoint is authenticated and audited by :class:`AdminSurface`;
this module only contains the dispatch logic and per-route scope
declaration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..base import AdminModule, ModuleConfig, ModuleEndpoint
from ..idempotency import replay_or_record
from ..routing import ModuleRouter

if TYPE_CHECKING:
    from ...core.engine import MongoDBEngine


class ReconcilerAdminModule(AdminModule):
    name = "reconciler"

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        mr = ModuleRouter(self.name)

        async def plan(slug: str = Query(..., min_length=1)) -> dict[str, Any]:
            try:
                return await engine.manifest_diff(slug)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e

        async def apply_op(
            request: Request,
            slug: str = Query(..., min_length=1),
            dry_run: bool = Query(False),
            yes: bool = Query(False),
            expected_head: str | None = Query(None),
        ) -> dict[str, Any]:
            async def _run() -> dict[str, Any]:
                try:
                    return await engine.reconcile(
                        slug,
                        dry_run=dry_run,
                        confirm=yes,
                        expected_head=expected_head,
                    )
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e

            return await replay_or_record(
                engine,
                request,
                module=self.name,
                endpoint="/reconciler/apply",
                run=_run,
            )

        async def manifest_history(
            slug: str = Query(..., min_length=1),
            limit: int = Query(20, ge=1, le=500),
        ) -> list[dict[str, Any]]:
            return await engine.manifest_history(slug, limit=limit)

        async def manifest_diff(slug: str = Query(..., min_length=1)) -> dict[str, Any]:
            try:
                return await engine.manifest_diff(slug)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e

        mr.add("GET", "/plan", endpoint=plan, scope="read", summary="Show the plan the reconciler would apply.")
        mr.add(
            "POST",
            "/apply",
            endpoint=apply_op,
            scope="apply",
            summary="Apply the plan (respects confirm_if).",
            destructive=True,
        )
        mr.add(
            "GET", "/manifest/history", endpoint=manifest_history, scope="read", summary="List revisions for a slug."
        )
        mr.add(
            "GET", "/manifest/diff", endpoint=manifest_diff, scope="read", summary="Alias for /plan; structural diff."
        )
        return mr.router

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [
            ModuleEndpoint("GET", "/reconciler/plan", "read", "Show the plan the reconciler would apply."),
            ModuleEndpoint(
                "POST", "/reconciler/apply", "apply", "Apply the plan (respects confirm_if).", destructive=True
            ),
            ModuleEndpoint("GET", "/reconciler/manifest/history", "read", "List revisions for a slug."),
            ModuleEndpoint("GET", "/reconciler/manifest/diff", "read", "Alias for /plan; structural diff."),
        ]


__all__ = ["ReconcilerAdminModule"]
