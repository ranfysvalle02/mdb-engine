"""Tests for mdb_engine.uploads (service + router)."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.uploads.service import (
    UploadService,
    _ext_for_content_type,
    _sniff_content_type,
    parse_size,
)

# ── Fixtures / helpers ───────────────────────────────────────────────────

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 100
GIF_MAGIC = b"GIF89a" + b"\x00" * 100
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100


class FakeGridFSBucket:
    """In-memory mock of AsyncIOMotorGridFSBucket."""

    def __init__(self):
        self._files: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def upload_from_stream(self, filename: str, data: bytes, metadata: dict | None = None):
        self._counter += 1
        file_id = f"oid_{self._counter}"
        self._files[file_id] = {
            "_id": file_id,
            "filename": filename,
            "data": data,
            "metadata": metadata or {},
        }
        return file_id

    def find(self, filter_spec: dict, limit: int = 0):
        return FakeGridFSCursor(self._files, filter_spec)

    async def open_download_stream(self, file_id: str):
        entry = self._files.get(file_id)
        if entry is None:
            raise Exception("File not found")
        return FakeDownloadStream(entry["data"])

    async def delete(self, file_id: str):
        if file_id in self._files:
            del self._files[file_id]


class FakeGridFSCursor:
    def __init__(self, files: dict, filter_spec: dict):
        self._results = []
        target_hash = filter_spec.get("metadata.hash")
        for _fid, entry in files.items():
            if entry["metadata"].get("hash") == target_hash:
                self._results.append(entry)

    def __aiter__(self):
        return FakeGridFSAsyncIter(self._results)


class FakeGridFSAsyncIter:
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class FakeDownloadStream:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


def _make_service(config: dict | None = None) -> tuple[UploadService, FakeGridFSBucket]:
    """Create an UploadService with a fake GridFS bucket."""
    bucket = FakeGridFSBucket()
    svc = UploadService.__new__(UploadService)
    svc._app_slug = "test_app"
    svc._bucket = bucket
    cfg = config or {}
    svc._max_size = parse_size(cfg.get("max_size", 5 * 1024 * 1024))
    raw = cfg.get("allowed_types")
    svc._allowed_types = frozenset(raw) if raw else frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
    svc._path_prefix = cfg.get("path_prefix", "/uploads")
    return svc, bucket


# ── parse_size ───────────────────────────────────────────────────────────


class TestParseSize:
    def test_kilobytes(self):
        assert parse_size("10KB") == 10 * 1024

    def test_megabytes(self):
        assert parse_size("5MB") == 5 * 1024 * 1024

    def test_gigabytes(self):
        assert parse_size("1GB") == 1024**3

    def test_integer_passthrough(self):
        assert parse_size(12345) == 12345

    def test_raw_string_number(self):
        assert parse_size("4096") == 4096

    def test_case_insensitive(self):
        assert parse_size("5mb") == 5 * 1024 * 1024


# ── _sniff_content_type ──────────────────────────────────────────────────


class TestSniffContentType:
    def test_png(self):
        assert _sniff_content_type(PNG_MAGIC) == "image/png"

    def test_jpeg(self):
        assert _sniff_content_type(JPEG_MAGIC) == "image/jpeg"

    def test_gif(self):
        assert _sniff_content_type(GIF_MAGIC) == "image/gif"

    def test_webp(self):
        assert _sniff_content_type(WEBP_MAGIC) == "image/webp"

    def test_svg(self):
        assert _sniff_content_type(b"<svg xmlns='...'") == "image/svg+xml"

    def test_unknown(self):
        assert _sniff_content_type(b"\x00\x01\x02\x03") is None


# ── _ext_for_content_type ────────────────────────────────────────────────


class TestExtForContentType:
    def test_jpeg(self):
        assert _ext_for_content_type("image/jpeg") == ".jpg"

    def test_png(self):
        assert _ext_for_content_type("image/png") == ".png"

    def test_webp(self):
        assert _ext_for_content_type("image/webp") == ".webp"

    def test_svg(self):
        assert _ext_for_content_type("image/svg+xml") == ".svg"

    def test_gif(self):
        assert _ext_for_content_type("image/gif") == ".gif"


# ── UploadService.store ──────────────────────────────────────────────────


class TestUploadServiceStore:
    @pytest.mark.asyncio
    async def test_stores_file_and_returns_url(self):
        svc, bucket = _make_service()
        result = await svc.store(PNG_MAGIC, "image/png", "test.png")
        expected_hash = hashlib.sha256(PNG_MAGIC).hexdigest()
        assert result.file_hash == expected_hash
        assert result.url == f"/uploads/{expected_hash}.png"
        assert result.content_type == "image/png"
        assert result.size == len(PNG_MAGIC)
        assert result.deduplicated is False
        assert len(bucket._files) == 1

    @pytest.mark.asyncio
    async def test_deduplicates_same_content(self):
        svc, bucket = _make_service()
        r1 = await svc.store(PNG_MAGIC, "image/png", "a.png")
        r2 = await svc.store(PNG_MAGIC, "image/png", "b.png")
        assert r1.url == r2.url
        assert r2.deduplicated is True
        assert len(bucket._files) == 1

    @pytest.mark.asyncio
    async def test_rejects_oversized_file(self):
        svc, _ = _make_service({"max_size": "1KB"})
        with pytest.raises(ValueError, match="exceeds maximum"):
            await svc.store(b"\x00" * 2048, "image/png")

    @pytest.mark.asyncio
    async def test_rejects_disallowed_type(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError, match="not allowed"):
            await svc.store(b"hello", "text/plain")

    @pytest.mark.asyncio
    async def test_strips_charset_from_content_type(self):
        svc, _ = _make_service()
        result = await svc.store(PNG_MAGIC, "image/png; charset=utf-8", "test.png")
        assert result.content_type == "image/png"

    @pytest.mark.asyncio
    async def test_rejects_empty_file(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError, match="Empty file"):
            await svc.store(b"", "image/png")


# ── UploadService.retrieve ───────────────────────────────────────────────


class TestUploadServiceRetrieve:
    @pytest.mark.asyncio
    async def test_retrieves_stored_file(self):
        svc, _ = _make_service()
        result = await svc.store(PNG_MAGIC, "image/png")
        retrieved = await svc.retrieve(result.file_hash, ".png")
        assert retrieved is not None
        data, ct = retrieved
        assert data == PNG_MAGIC
        assert ct == "image/png"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        svc, _ = _make_service()
        assert await svc.retrieve("nonexistent", ".png") is None


# ── UploadService.delete ─────────────────────────────────────────────────


class TestUploadServiceDelete:
    @pytest.mark.asyncio
    async def test_deletes_stored_file(self):
        svc, bucket = _make_service()
        result = await svc.store(PNG_MAGIC, "image/png")
        assert len(bucket._files) == 1
        deleted = await svc.delete(result.file_hash)
        assert deleted is True
        assert len(bucket._files) == 0

    @pytest.mark.asyncio
    async def test_returns_false_for_missing(self):
        svc, _ = _make_service()
        assert await svc.delete("nonexistent") is False


# ── Router tests ─────────────────────────────────────────────────────────


def _build_test_app(uploads_config: dict | None = None) -> tuple[FastAPI, FakeGridFSBucket]:
    """Build a minimal FastAPI app with upload routes mounted (auth disabled)."""
    from mdb_engine.uploads.router import mount_upload_routes

    app = FastAPI()
    svc, bucket = _make_service(uploads_config)
    app.state.upload_service = svc

    cfg = {**(uploads_config or {}), "auth": {"required": False}}
    mount_upload_routes(app, cfg, app_auth_enabled=False)

    return app, bucket


class TestUploadRouter:
    def test_multipart_upload(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/_uploads",
            files={"file": ("photo.png", PNG_MAGIC, "image/png")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["url"].startswith("/uploads/")
        assert body["url"].endswith(".png")
        assert body["content_type"] == "image/png"
        assert body["size"] == len(PNG_MAGIC)

    def test_base64_json_upload(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        b64 = base64.b64encode(PNG_MAGIC).decode()
        resp = client.post(
            "/api/_uploads",
            json={"data": b64, "content_type": "image/png", "filename": "paste.png"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["url"].endswith(".png")

    def test_base64_with_data_uri_prefix(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        b64 = "data:image/png;base64," + base64.b64encode(PNG_MAGIC).decode()
        resp = client.post(
            "/api/_uploads",
            json={"data": b64, "content_type": "image/png"},
        )
        assert resp.status_code == 201

    def test_serve_uploaded_file(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        upload_resp = client.post(
            "/api/_uploads",
            files={"file": ("photo.png", PNG_MAGIC, "image/png")},
        )
        url = upload_resp.json()["url"]
        serve_resp = client.get(url)
        assert serve_resp.status_code == 200
        assert serve_resp.content == PNG_MAGIC
        assert serve_resp.headers["content-type"] == "image/png"
        assert "immutable" in serve_resp.headers["cache-control"]

    def test_serve_missing_file_returns_404(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.get("/uploads/deadbeef.png")
        assert resp.status_code == 404

    def test_rejects_bad_content_type(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/_uploads",
            files={"file": ("evil.exe", b"\x00" * 100, "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_rejects_oversized_file(self):
        app, _ = _build_test_app({"max_size": "1KB"})
        client = TestClient(app)
        resp = client.post(
            "/api/_uploads",
            files={"file": ("big.png", b"\x00" * 2048, "image/png")},
        )
        assert resp.status_code == 400

    def test_dedup_returns_201_with_flag(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        client.post("/api/_uploads", files={"file": ("a.png", PNG_MAGIC, "image/png")})
        resp = client.post("/api/_uploads", files={"file": ("b.png", PNG_MAGIC, "image/png")})
        assert resp.status_code == 201
        assert resp.json()["deduplicated"] is True

    def test_missing_json_fields(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.post("/api/_uploads", json={"data": "abc"})
        assert resp.status_code == 400

    def test_invalid_base64(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/_uploads",
            json={"data": "not!valid!base64!!!", "content_type": "image/png"},
        )
        assert resp.status_code == 400

    def test_rejects_zero_byte_upload(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.post(
            "/api/_uploads",
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert resp.status_code == 400

    def test_serve_rejects_invalid_hash(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.get("/uploads/not-a-valid-hash.png")
        assert resp.status_code == 404

    def test_serve_rejects_nosql_injection_hash(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        resp = client.get('/uploads/{"$gt":""}.png')
        assert resp.status_code == 404

    def test_serve_returns_304_on_matching_etag(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        upload_resp = client.post(
            "/api/_uploads",
            files={"file": ("photo.png", PNG_MAGIC, "image/png")},
        )
        file_hash = upload_resp.json()["hash"]
        url = upload_resp.json()["url"]
        resp = client.get(url, headers={"If-None-Match": f'"{file_hash}"'})
        assert resp.status_code == 304
        assert resp.headers["etag"] == f'"{file_hash}"'

    def test_serve_returns_200_on_mismatched_etag(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        upload_resp = client.post(
            "/api/_uploads",
            files={"file": ("photo.png", PNG_MAGIC, "image/png")},
        )
        url = upload_resp.json()["url"]
        resp = client.get(url, headers={"If-None-Match": '"stale-etag"'})
        assert resp.status_code == 200

    def test_svg_not_in_default_allowed_types(self):
        from mdb_engine.uploads.service import DEFAULT_ALLOWED_TYPES

        assert "image/svg+xml" not in DEFAULT_ALLOWED_TYPES

    def test_svg_requires_explicit_opt_in(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        svg_data = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        resp = client.post(
            "/api/_uploads",
            files={"file": ("icon.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 400

    def test_svg_served_with_content_disposition_attachment(self):
        svg_data = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        app, _ = _build_test_app({"allowed_types": ["image/svg+xml"]})
        client = TestClient(app)
        upload_resp = client.post(
            "/api/_uploads",
            files={"file": ("icon.svg", svg_data, "image/svg+xml")},
        )
        assert upload_resp.status_code == 201
        url = upload_resp.json()["url"]
        serve_resp = client.get(url)
        assert serve_resp.status_code == 200
        assert serve_resp.headers["content-disposition"] == "attachment"

    def test_non_svg_served_without_content_disposition(self):
        app, _ = _build_test_app()
        client = TestClient(app)
        upload_resp = client.post(
            "/api/_uploads",
            files={"file": ("photo.png", PNG_MAGIC, "image/png")},
        )
        url = upload_resp.json()["url"]
        serve_resp = client.get(url)
        assert serve_resp.status_code == 200
        assert "content-disposition" not in serve_resp.headers
