"""
Auto-generated Open Graph social preview images.

Renders a 1200x630 PNG "title card" for documents that lack a cover image,
using configurable colors, font, and optional logo.  Images are cached in
an LRU to avoid re-rendering on every request.

Requires the ``Pillow`` package (optional dependency).
"""

from __future__ import annotations

import io
import logging
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import Response

from ..dependencies import get_scoped_db

logger = logging.getLogger(__name__)

OG_WIDTH = 1200
OG_HEIGHT = 630


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except ImportError:
        return False


@lru_cache(maxsize=256)
def _render_og_image(
    title: str,
    author: str,
    background: str,
    text_color: str,
    font_name: str,
    logo_path: str | None,
) -> bytes:
    """Render a 1200x630 PNG with title, author, and optional logo."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), color=background)
    draw = ImageDraw.Draw(img)

    title_size = 52
    author_size = 28
    try:
        title_font = ImageFont.truetype(font_name, title_size)
    except OSError:
        title_font = ImageFont.load_default()
    try:
        author_font = ImageFont.truetype(font_name, author_size)
    except OSError:
        author_font = ImageFont.load_default()

    if logo_path:
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((120, 120))
            img.paste(logo, (60, 60), logo)
        except (OSError, ValueError):
            logger.debug("Could not load OG logo: %s", logo_path)

    wrapped = textwrap.fill(title, width=36)
    y_offset = OG_HEIGHT // 2 - 80
    draw.text((80, y_offset), wrapped, fill=text_color, font=title_font)

    if author:
        draw.text((80, OG_HEIGHT - 100), author, fill=text_color, font=author_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def register_og_image_route(
    router: APIRouter,
    og_config: dict[str, Any],
    collections_config: dict[str, Any],
    base_url: str,
) -> None:
    """Register ``GET /og/{collection}/{doc_id}.png`` for auto-generated previews."""
    if not _pillow_available():
        logger.warning("OG image generation requires Pillow — install with: pip install mdb-engine[og-image]")
        return

    background = og_config.get("background", "#1a1a2e")
    text_color = og_config.get("text_color", "#ffffff")
    font = og_config.get("font", "")
    logo = og_config.get("logo")
    title_field = og_config.get("title_field", "title")
    author_field = og_config.get("author_field", "author")
    cover_field = og_config.get("cover_field", "cover_image")

    logo_abs: str | None = None
    if logo:
        p = Path(logo)
        if p.exists():
            logo_abs = str(p.resolve())

    @router.get("/og/{collection}/{doc_id}.png", response_class=Response, include_in_schema=False)
    async def og_image(collection: str, doc_id: str, request: Request, db=Depends(get_scoped_db)) -> Response:
        from bson import ObjectId
        from bson.errors import InvalidId

        col = db[collection]
        try:
            oid = ObjectId(doc_id)
            doc = await col.find_one({"_id": oid})
        except (InvalidId, TypeError):
            col_config = collections_config.get(collection, {})
            schema = col_config.get("schema", {})
            slug_field = None
            for name, prop in schema.get("properties", {}).items():
                if isinstance(prop, dict) and "x-slug" in prop:
                    slug_field = name
                    break
            if slug_field:
                doc = await col.find_one({slug_field: doc_id})
            else:
                raise HTTPException(status_code=404, detail="Not found") from None

        if doc is None:
            raise HTTPException(status_code=404, detail="Not found")

        existing_cover = doc.get(cover_field)
        if existing_cover and not existing_cover.startswith("data:"):
            host = base_url or str(request.base_url).rstrip("/")
            if existing_cover.startswith(("http://", "https://")):
                redirect_url = existing_cover
            else:
                redirect_url = f"{host}{existing_cover}"
            return Response(status_code=302, headers={"Location": redirect_url})

        title = str(doc.get(title_field, ""))
        author = str(doc.get(author_field, ""))

        png_bytes = _render_og_image(title, author, background, text_color, font, logo_abs)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
