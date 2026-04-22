"""
Secrets admin module.

Exposes per-app token management. Two endpoints:

- ``GET /secrets/current`` — return non-sensitive metadata about the
  currently active token (label, scopes, ``token_id`` fingerprint of
  the *presenting* token, rotation count, created/updated timestamps).
  Requires scope ``secrets:read``.
- ``POST /secrets/rotate`` — generate a fresh token, replace the
  stored one, and return the new plaintext *exactly once*. Rotating
  immediately invalidates the previous token, so this is also the
  revocation path. Requires scope ``secrets:rotate``.

Rotation body (all optional)::

    {
        "label": "ci-gha",
        "scopes": ["reconciler:read", "trash:read"],
        "overlap_seconds": 300
    }

``overlap_seconds`` enables **graceful rotation**: the previous token
stays valid for that many seconds after rotation so a fleet of
callers can roll their cached credential without a 401 storm. Capped
by ``AppSecretsManager.MAX_OVERLAP_SECONDS`` (1 hour). The rotation
response echoes the effective (possibly-clamped) value back as
``overlap_seconds`` and includes ``previous_expires_at`` when the
overlap is non-zero.

The rotation response is never cached: both ``Cache-Control: no-store``
and ``Pragma: no-cache`` are set. Nothing else in the admin plane
ever returns a plaintext token.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..base import ADMIN_TOKEN_HEADER, AdminModule, ModuleConfig, ModuleEndpoint
from ..idempotency import replay_or_record
from ..routing import ModuleRouter

if TYPE_CHECKING:
    from ...core.engine import MongoDBEngine


class _RotateBody(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    scopes: list[str] | None = None
    overlap_seconds: int = Field(default=0, ge=0, le=3600)
    """Grace window during which the *previous* token remains valid.

    ``0`` (the default) preserves the legacy immediate-revocation
    behaviour. Must be in ``[0, 3600]``; values outside the range are
    rejected by Pydantic with a 422 before touching the DB.
    """


class SecretsAdminModule(AdminModule):
    name = "secrets"

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        mr = ModuleRouter(self.name)

        async def current(
            slug: str = Query(..., min_length=1),
            token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
        ) -> dict[str, Any]:
            """Return non-sensitive metadata about the currently stored token.

            Useful for operators who want to verify *which* token they
            are presenting (via ``token_id`` fingerprint) and confirm
            its scopes / label before taking destructive actions.

            We deliberately do NOT return the plaintext token — that's
            only available on the rotation response, exactly once.
            """
            mgr = getattr(engine, "_app_secrets_manager", None)
            if mgr is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="App secrets manager is not configured on the engine.",
                )
            get_metadata = getattr(mgr, "get_app_secret_metadata", None)
            if not callable(get_metadata):
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail="Installed secrets manager does not expose metadata.",
                )
            meta = await get_metadata(slug)
            if not meta:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"no app secret found for slug={slug!r}",
                )
            # Attach the fingerprint of the *presenting* token so the
            # caller can correlate this response with their audit rows
            # without us ever touching the stored plaintext.
            token_id: str | None = None
            fingerprint_fn = getattr(mgr, "fingerprint", None)
            if callable(fingerprint_fn):
                try:  # nosemgrep
                    token_id = fingerprint_fn(token)
                except Exception:  # noqa: BLE001
                    token_id = None
            return {
                "slug": meta.get("slug", slug),
                "label": meta.get("label"),
                "scopes": list(meta.get("scopes") or []),
                "rotation_count": meta.get("rotation_count", 0),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "presenting_token_id": token_id,
            }

        async def rotate(
            request: Request,
            slug: str = Query(..., min_length=1),
            body: _RotateBody | None = None,
        ) -> Any:
            mgr = getattr(engine, "_app_secrets_manager", None)
            if mgr is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="App secrets manager is not configured on the engine.",
                )
            new_scopes = body.scopes if body else None
            new_label = body.label if body else None
            overlap_seconds = int(body.overlap_seconds) if body else 0

            async def _run() -> dict[str, Any]:
                try:
                    new_token = await mgr.rotate_app_secret(
                        slug,
                        scopes=new_scopes,
                        label=new_label,
                        overlap_seconds=overlap_seconds,
                    )
                except TypeError:
                    # Legacy shim: older managers ignore scopes/label/overlap.
                    try:
                        new_token = await mgr.rotate_app_secret(slug, scopes=new_scopes, label=new_label)
                    except TypeError:
                        new_token = await mgr.rotate_app_secret(slug)
                except ValueError as e:
                    raise HTTPException(status_code=404, detail=str(e)) from e
                token_id: str | None = None
                fingerprint_fn = getattr(mgr, "fingerprint", None)
                if callable(fingerprint_fn):
                    try:  # nosemgrep
                        token_id = fingerprint_fn(new_token)
                    except Exception:  # noqa: BLE001
                        token_id = None
                # Fetch the effective scopes/label post-rotation so the
                # response reflects what was actually stored.
                effective_scopes: list[str] = []
                effective_label: str | None = None
                get_metadata = getattr(mgr, "get_app_secret_metadata", None)
                if callable(get_metadata):
                    try:  # nosemgrep
                        meta = await get_metadata(slug)
                        effective_scopes = list(meta.get("scopes") or [])
                        effective_label = meta.get("label")
                    except Exception:  # noqa: BLE001
                        effective_scopes = list(new_scopes or [])
                        effective_label = new_label
                else:
                    effective_scopes = list(new_scopes or [])
                    effective_label = new_label
                # Clamp the echoed overlap to the manager's max so
                # clients see the *effective* grace window, not what
                # they asked for.
                max_overlap = int(getattr(mgr, "MAX_OVERLAP_SECONDS", 3600))
                effective_overlap = max(0, min(overlap_seconds, max_overlap))
                payload: dict[str, Any] = {
                    "slug": slug,
                    "rotated": True,
                    "token": new_token,
                    "token_id": token_id,
                    "label": effective_label,
                    "scopes": effective_scopes,
                    "overlap_seconds": effective_overlap,
                    "notice": "Store this token immediately — it cannot be retrieved again.",
                }
                if effective_overlap > 0:
                    from datetime import datetime, timedelta, timezone

                    payload["previous_expires_at"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=effective_overlap)
                    ).isoformat()
                return payload

            payload = await replay_or_record(
                engine,
                request,
                module=self.name,
                endpoint="/secrets/rotate",
                run=_run,
                never_cache=True,
            )
            resp = JSONResponse(content=payload)
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Pragma"] = "no-cache"
            return resp

        mr.add(
            "GET",
            "/current",
            endpoint=current,
            scope="read",
            summary="Read non-sensitive metadata about the current token.",
        )
        mr.add(
            "POST",
            "/rotate",
            endpoint=rotate,
            scope="rotate",
            summary="Rotate the per-app token; returns the new plaintext once.",
            destructive=True,
        )
        return mr.router

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [
            ModuleEndpoint(
                "GET",
                "/secrets/current",
                "read",
                "Read non-sensitive metadata about the currently stored token.",
            ),
            ModuleEndpoint(
                "POST",
                "/secrets/rotate",
                "rotate",
                "Rotate the per-app token; returns the new plaintext once.",
                destructive=True,
            ),
        ]


__all__ = ["SecretsAdminModule"]
