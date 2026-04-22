"""
Trash admin module.

Mounts quarantine/restore operations at ``/__mdb/trash/*``:

- ``GET  /trash?slug=``                          (scope: ``read``)
- ``GET  /trash/summary?slug=``                  (scope: ``read``)
- ``POST /trash/{id}/restore?slug=&dry_run=``    (scope: ``restore``, destructive)
- ``POST /trash/{id}/purge?slug=``               (scope: ``purge``, destructive)

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


class TrashAdminModule(AdminModule):
    name = "trash"

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        mr = ModuleRouter(self.name)

        async def trash_list(slug: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
            return await engine.trash_list(slug)

        async def trash_summary(slug: str = Query(..., min_length=1)) -> dict[str, Any]:
            return await engine.trash_summary(slug)

        async def trash_restore(
            request: Request,
            trash_id: str,
            slug: str = Query(..., min_length=1),
            dry_run: bool = Query(False),
        ) -> dict[str, Any]:
            async def _run() -> dict[str, Any]:
                try:
                    return await engine.trash_restore(slug, trash_id, dry_run=dry_run)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e

            return await replay_or_record(
                engine,
                request,
                module=self.name,
                endpoint="/trash/{id}/restore",
                run=_run,
            )

        async def trash_purge(
            request: Request,
            trash_id: str,
            slug: str = Query(..., min_length=1),
        ) -> dict[str, Any]:
            async def _run() -> dict[str, Any]:
                count = await engine.trash_purge(slug, expired_only=False, ids=[trash_id])
                return {"purged": count, "id": trash_id}

            return await replay_or_record(
                engine,
                request,
                module=self.name,
                endpoint="/trash/{id}/purge",
                run=_run,
            )

        mr.add("GET", "", endpoint=trash_list, scope="read", summary="List tombstones for a slug.")
        mr.add("GET", "/summary", endpoint=trash_summary, scope="read", summary="Aggregated stats for a slug's trash.")
        mr.add(
            "POST",
            "/{trash_id}/restore",
            endpoint=trash_restore,
            scope="restore",
            summary="Restore a tombstone; supports dry_run.",
            destructive=True,
        )
        mr.add(
            "POST",
            "/{trash_id}/purge",
            endpoint=trash_purge,
            scope="purge",
            summary="Hard-drop a single tombstone.",
            destructive=True,
        )
        return mr.router

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [
            ModuleEndpoint("GET", "/trash", "read", "List tombstones for a slug."),
            ModuleEndpoint("GET", "/trash/summary", "read", "Aggregated stats for a slug's trash."),
            ModuleEndpoint(
                "POST", "/trash/{id}/restore", "restore", "Restore a tombstone; supports dry_run.", destructive=True
            ),
            ModuleEndpoint("POST", "/trash/{id}/purge", "purge", "Hard-drop a single tombstone.", destructive=True),
        ]


__all__ = ["TrashAdminModule"]
