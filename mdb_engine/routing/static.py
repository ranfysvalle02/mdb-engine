"""
Static file serving with Cache-Control headers, asset fingerprinting, and
optional CSS/JS minification.

Provides:
- ``CachedStaticFiles``: Starlette ``StaticFiles`` subclass that sets
  ``Cache-Control`` headers based on file extension category.
- ``AssetRegistry``: Startup file-hash registry that powers the
  ``asset_url()`` Jinja global for cache-busting query strings.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from starlette.datastructures import Headers
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

_FONT_EXTS = frozenset({".woff2", ".woff", ".ttf", ".otf", ".eot"})
_STYLE_EXTS = frozenset({".css"})
_SCRIPT_EXTS = frozenset({".js", ".mjs"})
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".avif"})

DEFAULT_CACHE_RULES: dict[str, str] = {
    "fonts": "max-age=31536000, immutable",
    "styles": "max-age=86400, stale-while-revalidate=3600",
    "scripts": "max-age=86400, stale-while-revalidate=3600",
    "images": "max-age=604800",
    "default": "max-age=3600",
}


def _category_for_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in _FONT_EXTS:
        return "fonts"
    if ext in _STYLE_EXTS:
        return "styles"
    if ext in _SCRIPT_EXTS:
        return "scripts"
    if ext in _IMAGE_EXTS:
        return "images"
    return "default"


class CachedStaticFiles(StaticFiles):
    """``StaticFiles`` with automatic ``Cache-Control`` headers and optional minification."""

    def __init__(
        self,
        *,
        directory: str | Path,
        cache_config: dict[str, Any] | None = None,
        minify: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(directory=directory, **kwargs)
        merged = dict(DEFAULT_CACHE_RULES)
        if cache_config:
            for key in ("fonts", "styles", "scripts", "images", "default"):
                if key in cache_config:
                    merged[key] = cache_config[key]
        self._cache_rules = merged
        self._minified: dict[str, bytes] = {}

        if minify:
            self._build_minified_cache(Path(directory))

    def _build_minified_cache(self, directory: Path) -> None:
        try:
            import csscompressor
            import rjsmin
        except ImportError:
            logger.warning(
                "Minification requested but rjsmin/csscompressor not installed. "
                "Install with: pip install mdb-engine[perf]"
            )
            return

        count = 0
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            rel = str(path.relative_to(directory))
            try:
                if ext == ".js" or ext == ".mjs":
                    original = path.read_text(encoding="utf-8")
                    minified = rjsmin.jsmin(original)
                    self._minified[rel] = minified.encode("utf-8")
                    count += 1
                elif ext == ".css":
                    original = path.read_text(encoding="utf-8")
                    minified = csscompressor.compress(original)
                    self._minified[rel] = minified.encode("utf-8")
                    count += 1
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                logger.warning("Failed to minify %s: %s", rel, exc)

        if count:
            logger.info("Minified %d static asset(s)", count)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return

        original_send = send
        request_path = scope.get("path", "")
        ext = Path(request_path).suffix
        category = _category_for_ext(ext)
        cache_value = self._cache_rules.get(category, self._cache_rules["default"])

        async def send_with_cache(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = Headers(raw=list(message.get("headers", [])))
                if "cache-control" not in headers:
                    raw_headers = list(message.get("headers", []))
                    raw_headers.append((b"cache-control", cache_value.encode("latin-1")))
                    message = {**message, "headers": raw_headers}
            await original_send(message)

        await super().__call__(scope, receive, send_with_cache)


class AssetRegistry:
    """Computes content hashes for static files at startup.

    Exposes ``get_hash(filename)`` and a ready-made Jinja global
    ``asset_url(filename)`` that appends ``?v=<hash>`` for cache busting.
    """

    def __init__(self, directory: str | Path, base_path: str = "") -> None:
        self._hashes: dict[str, str] = {}
        self._base_path = base_path.rstrip("/")
        self._scan(Path(directory))

    def _scan(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(directory))
            try:
                digest = hashlib.md5(path.read_bytes()).hexdigest()[:8]  # noqa: S324
                self._hashes[rel] = digest
            except OSError:
                pass
        if self._hashes:
            logger.info("AssetRegistry: hashed %d file(s) in %s", len(self._hashes), directory)

    def get_hash(self, filename: str) -> str | None:
        return self._hashes.get(filename)

    def asset_url(self, filename: str) -> str:
        """Jinja-friendly helper: ``{{ asset_url('style.css') }}``."""
        h = self.get_hash(filename)
        base = f"{self._base_path}/public/{filename}"
        return f"{base}?v={h}" if h else base
