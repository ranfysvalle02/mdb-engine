"""
Admin plane composer.

:class:`AdminSurface` is the single integration point between the
engine and the admin HTTP plane. It:

1. Resolves each module's :class:`ModuleConfig` from the manifest's
   ``admin_api.modules`` block.
2. Mounts the union of their routers under ``<path_prefix>/<name>/``.
3. Applies the shared ``X-App-Token`` auth gate (unless the module
   opts out with ``public: true``).
4. Delegates **per-endpoint scope enforcement** to each module's
   route dependency (see :func:`mdb_engine.admin.routing.require_scope`).
5. Fires structured events and writes the audit row asynchronously
   via :meth:`persist_audit`.

A module implementation never talks to the secrets manager, the audit
collection, or the scope vocabulary directly. It just returns an
``APIRouter``; the surface handles the cross-cutting concerns.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from .audit import (
    AuditContext,
    write_audit_row,
)
from .base import (
    ADMIN_TOKEN_HEADER,
    WILDCARD_SCOPE,
    AdminModule,
    ModuleConfig,
)

if TYPE_CHECKING:
    from ..core.engine import MongoDBEngine

logger = logging.getLogger(__name__)


class _AuthResult:
    """In-memory result of the surface's auth gate for a request."""

    __slots__ = ("slug", "token_id", "label", "scopes")

    def __init__(
        self,
        slug: str,
        token_id: str | None,
        label: str | None,
        scopes: list[str],
    ):
        self.slug = slug
        self.token_id = token_id
        self.label = label
        self.scopes = scopes


class AdminSurface:
    """Composes enabled admin modules into a single FastAPI router.

    Lifecycle:

    1. ``engine.admin_surface(cfg)`` constructs or returns the cached
       surface for ``cfg``.
    2. ``surface.register(module)`` adds modules. Default modules are
       registered automatically by :meth:`register_default_modules`.
    3. ``surface.build_router()`` returns an ``APIRouter`` that the
       engine mounts on the FastAPI app under ``cfg['path_prefix']``.

    The surface is stateless across requests: all state lives on the
    engine (modules) or on the request (auth result, audit context).
    """

    def __init__(self, engine: MongoDBEngine, cfg: dict[str, Any]):
        self.engine = engine
        self.cfg = cfg or {}
        self._modules: dict[str, AdminModule] = {}
        self._module_configs: dict[str, ModuleConfig] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, module: AdminModule) -> AdminSurface:
        """Add a module to the surface.

        Silently replaces any previously registered module with the
        same name so tests and third-party overrides compose cleanly.
        A warning is logged if the manifest's declared ``scopes`` vocabulary
        for this module references verbs the module itself never claims in
        :meth:`AdminModule.describe` — catching the classic
        ``"appply"`` typo at boot instead of at first 403.
        """
        self._modules[module.name] = module
        cfg = self._resolve_module_cfg(module.name)
        self._module_configs[module.name] = cfg
        self._validate_module_scopes(module, cfg)
        return self

    def register_default_modules(self) -> AdminSurface:
        """Wire the built-in modules (reconciler, trash, audit, secrets, health)."""
        from .modules.audit import AuditAdminModule
        from .modules.health import HealthAdminModule
        from .modules.reconciler import ReconcilerAdminModule
        from .modules.secrets import SecretsAdminModule
        from .modules.trash import TrashAdminModule

        for module in (
            HealthAdminModule(),
            ReconcilerAdminModule(),
            TrashAdminModule(),
            AuditAdminModule(),
            SecretsAdminModule(),
        ):
            self.register(module)
        return self

    def list_modules(self) -> list[tuple[AdminModule, ModuleConfig]]:
        """Return (module, resolved_cfg) pairs in registration order."""
        return [(self._modules[name], self._module_configs[name]) for name in self._modules]

    def get_module_config(self, name: str) -> ModuleConfig | None:
        return self._module_configs.get(name)

    # ------------------------------------------------------------------
    # Configuration resolution
    # ------------------------------------------------------------------

    def _validate_module_scopes(self, module: AdminModule, cfg: ModuleConfig) -> None:
        """Warn when a manifest references scopes the module doesn't expose.

        Declared scope vocabulary = the set of scope strings that actually
        appear in ``module.describe(cfg)``. If the manifest's
        ``admin_api.modules.<name>.scopes`` list mentions anything outside
        that vocabulary (plus the always-safe ``*`` / ``<name>:*`` /
        ``<name>:<verb>`` shapes), it's almost certainly a typo — surface
        it loudly at boot instead of letting it silently mint tokens that
        can never unlock anything.
        """
        if not cfg.enabled or cfg.public:
            return
        try:  # nosemgrep
            endpoints = list(module.describe(cfg))
        except Exception:  # noqa: BLE001 - never let describe() take boot down
            logger.debug("admin module %r describe() failed during validation", module.name, exc_info=True)
            return
        known_verbs = {e.scope for e in endpoints if e.scope}
        known_verbs.add(WILDCARD_SCOPE)
        acceptable = set(known_verbs)
        acceptable.add(f"{module.name}:*")
        for verb in list(known_verbs):
            acceptable.add(verb)
            acceptable.add(f"{module.name}:{verb}")
        unknown = [s for s in cfg.scopes if s and s not in acceptable]
        if unknown:
            logger.warning(
                "admin plane: module %r has unknown scope(s) %s in manifest; "
                "tokens carrying those scopes will never authorize. "
                "Known verbs: %s",
                module.name,
                sorted(unknown),
                sorted(known_verbs),
            )

    def _resolve_module_cfg(self, name: str) -> ModuleConfig:
        modules_cfg = self.cfg.get("modules") or {}
        raw = modules_cfg.get(name) or {}
        # A missing entry means "default on" so adopters don't have to
        # enumerate every built-in module explicitly.
        enabled = bool(raw.get("enabled", True))
        scopes_raw = raw.get("scopes") or [WILDCARD_SCOPE]
        scopes = [str(s) for s in scopes_raw] or [WILDCARD_SCOPE]
        public = bool(raw.get("public", False))
        extra = {k: v for k, v in raw.items() if k not in {"enabled", "scopes", "public"}}
        return ModuleConfig(name=name, enabled=enabled, scopes=scopes, public=public, extra=extra)

    # ------------------------------------------------------------------
    # Router construction
    # ------------------------------------------------------------------

    def build_router(self) -> APIRouter:
        """Compose the mounted router for the surface.

        Each module's router is prefixed with ``/<name>``. The auth +
        audit gates are attached at the module level; per-endpoint
        scope enforcement is already baked into each module's routes
        via :class:`mdb_engine.admin.routing.ModuleRouter`.

        The surface also exposes a single unauthenticated
        ``GET /health/live`` endpoint for infrastructure liveness probes
        (kube-probe, ALB/ELB, Nomad). It is intentionally cheap,
        slug-free, and body-stable so probes stay boring.
        """
        parent = APIRouter(tags=["mdb-admin"])

        # Liveness probe. Public by design, no slug / no token, returns
        # a stable JSON body so LB health checks don't need admin token
        # distribution. Kept on the parent router (not a module) so it
        # cannot be accidentally disabled by a misconfigured manifest.
        async def _liveness() -> dict[str, Any]:
            return {"ok": True}

        parent.add_api_route(
            "/health/live",
            _liveness,
            methods=["GET"],
            include_in_schema=False,
            name="mdb_admin_health_live",
        )

        for module in self._modules.values():
            cfg = self._module_configs[module.name]
            if not cfg.enabled:
                continue

            sub = module.build_router(self.engine, cfg)

            if cfg.public:
                parent.include_router(sub, prefix=f"/{module.name}")
                continue

            auth_gate = self._make_auth_gate(module.name, cfg)
            audit_gate = self._make_audit_gate(module.name)
            parent.include_router(
                sub,
                prefix=f"/{module.name}",
                dependencies=[auth_gate, audit_gate],
            )

        return parent

    # ------------------------------------------------------------------
    # Shared auth + audit
    # ------------------------------------------------------------------

    def _make_auth_gate(self, module_name: str, cfg: ModuleConfig):
        from fastapi import Depends

        async def _auth(
            request: Request,
            slug: str = Query(..., min_length=1),
            token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
        ) -> _AuthResult:
            result = await self._verify_token(slug, token)
            request.state.mdb_auth = result
            request.state.mdb_module = module_name
            return result

        return Depends(_auth)

    def _make_audit_gate(self, module_name: str):
        from fastapi import Depends

        async def _audit(request: Request) -> None:
            auth: _AuthResult | None = getattr(request.state, "mdb_auth", None)
            if auth is None:
                return None
            ctx = AuditContext(
                slug=auth.slug,
                module=module_name,
                endpoint=request.url.path,
                method=request.method,
            )
            request.state.mdb_audit = ctx
            return None

        return Depends(_audit)

    async def _verify_token(self, slug: str, token: str | None) -> _AuthResult:
        if not slug:
            raise HTTPException(status_code=400, detail="missing slug query param")
        if not token:
            self._emit_auth_failed(slug, reason="missing_token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"missing {ADMIN_TOKEN_HEADER} header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        mgr = getattr(self.engine, "_app_secrets_manager", None)
        if mgr is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="App secrets manager is not configured on the engine (encryption disabled).",
            )
        label: str | None = None
        token_id: str | None = None
        try:  # nosemgrep
            verify = getattr(mgr, "verify_app_token", None)
            if verify is not None:
                result = await verify(slug, token)
                valid = bool(result.valid)
                scopes = list(result.scopes or [WILDCARD_SCOPE])
                token_id = getattr(result, "token_id", None)
                label = getattr(result, "label", None)
            else:
                valid = await mgr.verify_app_secret(slug, token)
                scopes = [WILDCARD_SCOPE]
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Admin auth verify crashed for '%s'", slug, exc_info=True)
            self._emit_auth_failed(slug, reason="verify_error")
            raise HTTPException(status_code=500, detail="verify failed") from e
        if not valid:
            self._emit_auth_failed(slug, reason="invalid_token")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid app token",
            )
        # Fallback token_id when the secrets manager didn't compute one
        # (e.g. tests that stub verify_app_token without a token_id).
        if token_id is None:
            fingerprint_fn = getattr(mgr, "fingerprint", None)
            if callable(fingerprint_fn):
                try:  # nosemgrep
                    token_id = fingerprint_fn(token)
                except Exception:  # noqa: BLE001
                    token_id = None
        return _AuthResult(slug=slug, token_id=token_id, label=label, scopes=scopes)

    @staticmethod
    def _emit_auth_failed(slug: str, *, reason: str) -> None:
        try:  # nosemgrep
            from ..core.reconciler_events import emit_event
            from .events import EVENT_AUTH_FAILED

            emit_event(EVENT_AUTH_FAILED, slug=slug, reason=reason)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Audit persistence (called from response middleware)
    # ------------------------------------------------------------------

    async def persist_audit(
        self,
        request: Request,
        response: Any,
        *,
        request_summary: str = "",
    ) -> None:
        """Write the audit row for a completed request, if auth occurred.

        Called from the engine's HTTP middleware wrapper after each
        admin response is computed. The caller invokes this in a
        background task so the response is never blocked by a slow
        Mongo write.
        """
        if not (self.cfg.get("audit") or {}).get("enabled", True):
            return
        ctx: AuditContext | None = getattr(request.state, "mdb_audit", None)
        auth: _AuthResult | None = getattr(request.state, "mdb_auth", None)
        if ctx is None or auth is None:
            return
        db = getattr(self.engine, "_connection_manager", None)
        mongo_db = getattr(db, "mongo_db", None) if db else None
        status_code = getattr(response, "status_code", 0)
        resp_summary = ""
        if isinstance(response, JSONResponse):
            resp_summary = f"json body ({len(response.body) if response.body else 0}b)"
        # Telemetry fires regardless of whether the DB is reachable.
        try:  # nosemgrep
            from .events import emit_call

            emit_call(
                slug=auth.slug,
                module=ctx.module,
                endpoint=ctx.endpoint,
                method=ctx.method,
                status=int(status_code),
                duration_ms=ctx.elapsed_ms(),
                principal_label=auth.label,
                principal_token_id=auth.token_id,
            )
        except Exception:  # noqa: BLE001
            pass
        if mongo_db is None:
            return
        await write_audit_row(
            mongo_db,
            ctx=ctx,
            status_code=status_code,
            token_id=auth.token_id,
            label=auth.label,
            request_summary=request_summary,
            response_summary=resp_summary,
        )


__all__ = ["AdminSurface"]
