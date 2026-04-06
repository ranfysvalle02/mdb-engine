"""
FastAPI routes for the mdb-engine upload service.

Provides:
    POST /api/_uploads  — upload a file (multipart or base64 JSON)
    GET  {path_prefix}/{hash}.{ext} — serve a stored file
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..dependencies import require_role, require_user

logger = logging.getLogger(__name__)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _get_upload_service(request: Request):
    """Resolve the UploadService from app state."""
    service = getattr(request.app.state, "upload_service", None)
    if service is None:
        raise HTTPException(
            503,
            "Upload service not configured. " "Enable it with uploads.enabled=true in your manifest.",
        )
    return service


def mount_upload_routes(
    app: Any,
    uploads_config: dict[str, Any],
    *,
    app_auth_enabled: bool = False,
) -> None:
    """Register upload + serve routes on a FastAPI app.

    Args:
        app: FastAPI application instance.
        uploads_config: The ``uploads`` section from the manifest.
        app_auth_enabled: Whether app-level auth is active (controls
            whether the upload endpoint requires authentication).
    """
    path_prefix = uploads_config.get("path_prefix", "/uploads").rstrip("/")
    auth_config = uploads_config.get("auth", {})
    auth_required = auth_config.get("required", True)
    auth_roles = auth_config.get("roles")

    upload_deps: list[Any] = []
    if auth_required or app_auth_enabled:
        if auth_roles:
            upload_deps.append(Depends(require_role(*auth_roles)))
        else:
            upload_deps.append(Depends(require_user()))

    upload_router = APIRouter(tags=["uploads"])
    serve_router = APIRouter(include_in_schema=False)

    @upload_router.post(
        "/api/_uploads",
        summary="Upload a file",
        dependencies=upload_deps,
    )
    async def upload_file(
        request: Request,
        file: UploadFile | None = None,
    ) -> JSONResponse:
        """Upload a file via multipart form or base64 JSON body.

        **Multipart:** Send as ``file`` field in ``multipart/form-data``.

        **Base64 JSON:** Send ``{"data": "<base64>", "content_type": "image/png"}``.
        Optional ``filename`` field.
        """
        service = _get_upload_service(request)

        if file is not None and file.filename:
            data = await file.read()
            content_type = file.content_type or "application/octet-stream"
            filename = file.filename
        else:
            try:
                body = await request.json()
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                raise HTTPException(
                    400,
                    "Expected multipart file upload or JSON body with 'data' and 'content_type'.",
                ) from None

            b64_data = body.get("data")
            content_type = body.get("content_type")
            if not b64_data or not content_type:
                raise HTTPException(
                    400,
                    "JSON body must include 'data' (base64) and 'content_type'.",
                )
            try:
                if "," in b64_data:
                    b64_data = b64_data.split(",", 1)[1]
                data = base64.b64decode(b64_data)
            except (ValueError, TypeError):
                raise HTTPException(400, "Invalid base64 data.") from None
            filename = body.get("filename")

        try:
            result = await service.store(data, content_type, filename)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

        return JSONResponse(result.to_dict(), status_code=201)

    @serve_router.get(f"{path_prefix}/{{file_hash}}.{{ext}}")
    async def serve_file(
        file_hash: str,
        ext: str,
        request: Request,
    ) -> Response:
        """Serve an uploaded file by its content hash."""
        if not _HASH_RE.match(file_hash):
            raise HTTPException(404, "File not found")

        etag = f'"{file_hash}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip('"') == file_hash:
            return Response(status_code=304, headers={"ETag": etag})

        service = _get_upload_service(request)
        result = await service.retrieve(file_hash, ext)
        if result is None:
            raise HTTPException(404, "File not found")

        file_bytes, content_type = result
        headers: dict[str, str] = {
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
        }
        if content_type == "image/svg+xml":
            headers["Content-Disposition"] = "attachment"

        return Response(
            content=file_bytes,
            media_type=content_type,
            headers=headers,
        )

    app.include_router(upload_router)
    app.include_router(serve_router)
    logger.info("Mounted upload routes (POST /api/_uploads, GET %s/{{hash}}.{{ext}})", path_prefix)
