"""
Health + introspection module.

Mounts two always-on endpoints:

- ``GET /health`` — cheap liveness probe. Public by default so
  Kubernetes / load-balancers can call it without provisioning a token.
- ``GET /health/modules`` — returns the enabled modules with their
  endpoints and declared scopes. Clients (CLI, business_spa UI) use
  this to render themselves generically, so adding a new module to
  the manifest automatically makes it visible in the dashboard.

``GET /health/modules`` is authenticated — the listing leaks
information about what's configured, which we never want on an
unauthenticated surface.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException, Query, status

from ..base import ADMIN_TOKEN_HEADER, AdminModule, ModuleConfig, ModuleEndpoint
from ..routing import ModuleRouter

if TYPE_CHECKING:
    from ...core.engine import MongoDBEngine


class HealthAdminModule(AdminModule):
    """The only always-on module. Exposes liveness + module introspection."""

    name = "health"

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        mr = ModuleRouter(self.name)

        async def health() -> dict[str, Any]:
            return {"ok": True, "admin_api": True}

        async def list_modules(
            slug: str = Query(..., min_length=1),
            token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
        ) -> dict[str, Any]:
            surface = engine.admin_surface_cached()
            if surface is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="admin surface unavailable",
                )
            await surface._verify_token(slug, token)  # noqa: SLF001
            modules_out: list[dict[str, Any]] = []
            for module, module_cfg in surface.list_modules():
                if not module_cfg.enabled:
                    continue
                try:  # nosemgrep
                    endpoints = [asdict(e) for e in module.describe(module_cfg)]
                except Exception:  # noqa: BLE001
                    endpoints = []
                modules_out.append(
                    {
                        "name": module.name,
                        "enabled": module_cfg.enabled,
                        "public": module_cfg.public,
                        "scopes": module_cfg.scopes,
                        "endpoints": endpoints,
                    }
                )
            audit_cfg = (surface.cfg.get("audit") or {}) if surface else {}
            rate_limit_cfg = (surface.cfg.get("rate_limits") or {}) if surface else {}
            return {
                "slug": slug,
                "modules": modules_out,
                "audit": {"enabled": bool(audit_cfg.get("enabled", True))},
                "rate_limits": dict(rate_limit_cfg),
            }

        mr.add("GET", "", endpoint=health, scope="*", summary="Liveness probe.")
        mr.add(
            "GET",
            "/modules",
            endpoint=list_modules,
            scope="read",
            summary="List enabled modules + endpoints + declared scopes.",
        )
        return mr.router

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [
            ModuleEndpoint("GET", "/health", "*", "Liveness probe (public)."),
            ModuleEndpoint("GET", "/health/modules", "read", "List enabled modules + endpoints."),
        ]


__all__ = ["HealthAdminModule"]
