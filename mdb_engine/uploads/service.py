"""
GridFS-backed upload service for mdb-engine.

Provides content-addressed file storage scoped by app slug.  Files are
de-duplicated via SHA-256 hashing — uploading the same bytes twice returns
the existing URL without re-storing.

Each app gets its own GridFS bucket (``{slug}_uploads``) to maintain
the same data-isolation guarantee that ``ScopedMongoWrapper`` provides
for regular collections.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorGridFSBucket

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
DEFAULT_ALLOWED_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)

# Magic-byte signatures for common image types (defense-in-depth)
_MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # WebP starts with RIFF...WEBP
]


def _sniff_content_type(data: bytes) -> str | None:
    """Guess MIME type from leading magic bytes."""
    for magic, mime in _MAGIC_BYTES:
        if data[: len(magic)] == magic:
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    if data.lstrip().startswith((b"<svg", b"<?xml")):
        return "image/svg+xml"
    return None


def parse_size(value: str | int) -> int:
    """Parse a human-readable size string (e.g. ``'5MB'``) to bytes."""
    if isinstance(value, int):
        return value
    value = value.strip().upper()
    multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
    for suffix, mult in multipliers.items():
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * mult
    return int(value)


class UploadResult:
    """Value object returned after a successful upload."""

    __slots__ = ("url", "file_hash", "content_type", "size", "deduplicated")

    def __init__(
        self,
        url: str,
        file_hash: str,
        content_type: str,
        size: int,
        deduplicated: bool = False,
    ) -> None:
        self.url = url
        self.file_hash = file_hash
        self.content_type = content_type
        self.size = size
        self.deduplicated = deduplicated

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "hash": self.file_hash,
            "content_type": self.content_type,
            "size": self.size,
            "deduplicated": self.deduplicated,
        }


class UploadService:
    """Content-addressed GridFS upload service scoped to a single app."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        app_slug: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        config = config or {}
        self._app_slug = app_slug
        bucket_name = f"{app_slug}_uploads"
        self._bucket = AsyncIOMotorGridFSBucket(db, bucket_name=bucket_name)
        self._max_size = parse_size(config.get("max_size", DEFAULT_MAX_SIZE))
        raw_types = config.get("allowed_types")
        self._allowed_types: frozenset[str] = frozenset(raw_types) if raw_types else DEFAULT_ALLOWED_TYPES
        self._path_prefix = config.get("path_prefix", "/uploads").rstrip("/")

    @property
    def path_prefix(self) -> str:
        return self._path_prefix

    async def store(
        self,
        data: bytes,
        content_type: str,
        filename: str | None = None,
    ) -> UploadResult:
        """Store file bytes and return a stable, content-addressed URL.

        Raises:
            ValueError: If the file exceeds ``max_size`` or ``content_type``
                is not in the allow-list.
        """
        if not data:
            raise ValueError("Empty file")

        if len(data) > self._max_size:
            raise ValueError(f"File size {len(data)} exceeds maximum {self._max_size} bytes")

        ct_normalized = content_type.strip().lower().split(";")[0].strip()
        if ct_normalized not in self._allowed_types:
            raise ValueError(
                f"Content type '{ct_normalized}' is not allowed. " f"Allowed: {sorted(self._allowed_types)}"
            )

        sniffed = _sniff_content_type(data)
        if sniffed and sniffed != ct_normalized and ct_normalized != "image/svg+xml":
            logger.warning(
                "Declared content_type '%s' does not match detected '%s'",
                ct_normalized,
                sniffed,
            )

        file_hash = hashlib.sha256(data).hexdigest()
        ext = _ext_for_content_type(ct_normalized)

        existing = await self._find_by_hash(file_hash)
        if existing is not None:
            url = f"{self._path_prefix}/{file_hash}{ext}"
            return UploadResult(
                url=url,
                file_hash=file_hash,
                content_type=ct_normalized,
                size=len(data),
                deduplicated=True,
            )

        grid_filename = f"{file_hash}{ext}"
        metadata = {
            "hash": file_hash,
            "content_type": ct_normalized,
            "app_id": self._app_slug,
            "original_filename": filename or grid_filename,
        }
        await self._bucket.upload_from_stream(
            grid_filename,
            data,
            metadata=metadata,
        )
        logger.info(
            "Stored upload %s (%s, %d bytes) for app '%s'",
            grid_filename,
            ct_normalized,
            len(data),
            self._app_slug,
        )

        url = f"{self._path_prefix}/{file_hash}{ext}"
        return UploadResult(
            url=url,
            file_hash=file_hash,
            content_type=ct_normalized,
            size=len(data),
        )

    async def retrieve(self, file_hash: str, ext: str) -> tuple[bytes, str] | None:
        """Retrieve file bytes and content type by hash.

        Returns ``None`` if the file does not exist.
        """
        grid_in = await self._find_by_hash(file_hash)
        if grid_in is None:
            return None

        stream = await self._bucket.open_download_stream(grid_in["_id"])
        data = await stream.read()
        ct = grid_in.get("metadata", {}).get("content_type", "application/octet-stream")
        return data, ct

    async def delete(self, file_hash: str) -> bool:
        """Delete a file by its content hash. Returns True if deleted."""
        grid_in = await self._find_by_hash(file_hash)
        if grid_in is None:
            return False
        await self._bucket.delete(grid_in["_id"])
        logger.info("Deleted upload %s for app '%s'", file_hash, self._app_slug)
        return True

    async def _find_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        """Look up a GridFS file document by metadata.hash."""
        cursor = self._bucket.find({"metadata.hash": file_hash}, limit=1)
        async for doc in cursor:
            return doc
        return None


def _ext_for_content_type(content_type: str) -> str:
    """Map a MIME type to a file extension (with leading dot)."""
    ext = mimetypes.guess_extension(content_type, strict=False)
    if ext:
        if ext == ".jpe":
            return ".jpg"
        return ext
    _fallback = {
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    return _fallback.get(content_type, ".bin")
