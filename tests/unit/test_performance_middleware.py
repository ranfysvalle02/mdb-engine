"""
Tests for the 0.12.x performance middleware suite.

Covers:
- GZip / Brotli compression middleware selection
- CachedStaticFiles Cache-Control headers
- AssetRegistry content-hash fingerprinting
- Markdown Jinja filter (with and without deps)
- Link preload header building
- Extension-to-category mapping
- Minification integration (mocked)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ════════════════════════════════════════════════════════════════════════════
# Static file utilities — _category_for_ext, DEFAULT_CACHE_RULES
# ════════════════════════════════════════════════════════════════════════════


class TestCategoryForExt:
    """Test file extension categorization."""

    def test_font_extensions(self):
        from mdb_engine.routing.static import _category_for_ext

        for ext in (".woff2", ".woff", ".ttf", ".otf", ".eot"):
            assert _category_for_ext(ext) == "fonts", f"Expected 'fonts' for {ext}"

    def test_style_extensions(self):
        from mdb_engine.routing.static import _category_for_ext

        assert _category_for_ext(".css") == "styles"

    def test_script_extensions(self):
        from mdb_engine.routing.static import _category_for_ext

        assert _category_for_ext(".js") == "scripts"
        assert _category_for_ext(".mjs") == "scripts"

    def test_image_extensions(self):
        from mdb_engine.routing.static import _category_for_ext

        for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".avif"):
            assert _category_for_ext(ext) == "images", f"Expected 'images' for {ext}"

    def test_unknown_extension(self):
        from mdb_engine.routing.static import _category_for_ext

        assert _category_for_ext(".txt") == "default"
        assert _category_for_ext(".json") == "default"
        assert _category_for_ext(".xml") == "default"

    def test_case_insensitive(self):
        from mdb_engine.routing.static import _category_for_ext

        assert _category_for_ext(".CSS") == "styles"
        assert _category_for_ext(".WOFF2") == "fonts"
        assert _category_for_ext(".PNG") == "images"


class TestDefaultCacheRules:
    """Ensure default cache rules are sensible."""

    def test_fonts_immutable(self):
        from mdb_engine.routing.static import DEFAULT_CACHE_RULES

        assert "immutable" in DEFAULT_CACHE_RULES["fonts"]
        assert "31536000" in DEFAULT_CACHE_RULES["fonts"]

    def test_styles_have_swr(self):
        from mdb_engine.routing.static import DEFAULT_CACHE_RULES

        assert "stale-while-revalidate" in DEFAULT_CACHE_RULES["styles"]

    def test_all_categories_present(self):
        from mdb_engine.routing.static import DEFAULT_CACHE_RULES

        for key in ("fonts", "styles", "scripts", "images", "default"):
            assert key in DEFAULT_CACHE_RULES


# ════════════════════════════════════════════════════════════════════════════
# AssetRegistry
# ════════════════════════════════════════════════════════════════════════════


class TestAssetRegistry:
    """Test content-hash asset fingerprinting."""

    def test_hashes_files_in_directory(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        (tmp_path / "style.css").write_text("body { color: red; }")
        (tmp_path / "app.js").write_text("console.log('hi');")

        registry = AssetRegistry(directory=tmp_path)
        assert registry.get_hash("style.css") is not None
        assert registry.get_hash("app.js") is not None
        assert len(registry.get_hash("style.css")) == 8

    def test_unknown_file_returns_none(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        registry = AssetRegistry(directory=tmp_path)
        assert registry.get_hash("nonexistent.css") is None

    def test_asset_url_with_hash(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        (tmp_path / "style.css").write_text("body { color: red; }")
        registry = AssetRegistry(directory=tmp_path)

        url = registry.asset_url("style.css")
        assert url.startswith("/public/style.css?v=")
        assert len(url.split("?v=")[1]) == 8

    def test_asset_url_without_hash(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        registry = AssetRegistry(directory=tmp_path)
        url = registry.asset_url("missing.css")
        assert url == "/public/missing.css"

    def test_asset_url_with_base_path(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        (tmp_path / "app.js").write_text("var x = 1;")
        registry = AssetRegistry(directory=tmp_path, base_path="/my-blog")

        url = registry.asset_url("app.js")
        assert url.startswith("/my-blog/public/app.js?v=")

    def test_hash_changes_with_content(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        f = tmp_path / "data.txt"
        f.write_text("version 1")
        reg1 = AssetRegistry(directory=tmp_path)
        hash1 = reg1.get_hash("data.txt")

        f.write_text("version 2")
        reg2 = AssetRegistry(directory=tmp_path)
        hash2 = reg2.get_hash("data.txt")

        assert hash1 != hash2

    def test_scans_subdirectories(self, tmp_path: Path):
        from mdb_engine.routing.static import AssetRegistry

        sub = tmp_path / "fonts"
        sub.mkdir()
        (sub / "inter.woff2").write_bytes(b"\x00\x01\x02")

        registry = AssetRegistry(directory=tmp_path)
        assert registry.get_hash("fonts/inter.woff2") is not None

    def test_nonexistent_directory(self):
        from mdb_engine.routing.static import AssetRegistry

        registry = AssetRegistry(directory="/nonexistent/path")
        assert registry.get_hash("anything") is None


# ════════════════════════════════════════════════════════════════════════════
# CachedStaticFiles
# ════════════════════════════════════════════════════════════════════════════


class TestCachedStaticFiles:
    """Test CachedStaticFiles configuration and cache rule merging."""

    def test_default_rules_applied(self, tmp_path: Path):
        from mdb_engine.routing.static import DEFAULT_CACHE_RULES, CachedStaticFiles

        csf = CachedStaticFiles(directory=str(tmp_path))
        assert csf._cache_rules == DEFAULT_CACHE_RULES

    def test_custom_rules_override(self, tmp_path: Path):
        from mdb_engine.routing.static import CachedStaticFiles

        custom = {"fonts": "no-cache", "scripts": "max-age=0"}
        csf = CachedStaticFiles(directory=str(tmp_path), cache_config=custom)
        assert csf._cache_rules["fonts"] == "no-cache"
        assert csf._cache_rules["scripts"] == "max-age=0"
        assert "stale-while-revalidate" in csf._cache_rules["styles"]

    def test_partial_override_preserves_defaults(self, tmp_path: Path):
        from mdb_engine.routing.static import DEFAULT_CACHE_RULES, CachedStaticFiles

        csf = CachedStaticFiles(directory=str(tmp_path), cache_config={"images": "max-age=999"})
        assert csf._cache_rules["images"] == "max-age=999"
        assert csf._cache_rules["fonts"] == DEFAULT_CACHE_RULES["fonts"]

    def test_minify_without_deps_logs_warning(self, tmp_path: Path):
        from mdb_engine.routing.static import CachedStaticFiles

        (tmp_path / "app.js").write_text("var x = 1;")

        with patch.dict("sys.modules", {"rjsmin": None, "csscompressor": None}):
            csf = CachedStaticFiles(directory=str(tmp_path), minify=True)
            assert len(csf._minified) == 0


# ════════════════════════════════════════════════════════════════════════════
# Compression middleware selection
# ════════════════════════════════════════════════════════════════════════════


class TestCompressionMiddleware:
    """Test _add_compression_middleware selection logic."""

    def _make_mixin(self):
        from mdb_engine.core.fastapi_app import FastAPIAppMixin

        mixin = FastAPIAppMixin.__new__(FastAPIAppMixin)
        mixin._connection_manager = MagicMock()
        return mixin

    def test_gzip_added_by_default(self):
        mixin = self._make_mixin()
        app = MagicMock()

        with patch.dict("sys.modules", {"brotli_asgi": None}):
            mixin._add_compression_middleware(app, "test", None)

        app.add_middleware.assert_called_once()
        args = app.add_middleware.call_args
        assert "GZipMiddleware" in str(args)

    def test_compression_disabled_via_manifest(self):
        mixin = self._make_mixin()
        app = MagicMock()
        manifest = {"compression": {"enabled": False}}

        mixin._add_compression_middleware(app, "test", manifest)
        app.add_middleware.assert_not_called()

    def test_custom_minimum_size(self):
        mixin = self._make_mixin()
        app = MagicMock()
        manifest = {"compression": {"minimum_size": 1024}}

        with patch.dict("sys.modules", {"brotli_asgi": None}):
            mixin._add_compression_middleware(app, "test", manifest)

        args = app.add_middleware.call_args
        assert args[1]["minimum_size"] == 1024

    def test_brotli_preferred_when_available(self):
        mixin = self._make_mixin()
        app = MagicMock()

        mock_brotli_module = MagicMock()
        mock_brotli_module.BrotliMiddleware = MagicMock()

        with patch.dict("sys.modules", {"brotli_asgi": mock_brotli_module}):
            mixin._add_compression_middleware(app, "test", None)

        app.add_middleware.assert_called_once_with(mock_brotli_module.BrotliMiddleware, minimum_size=500)

    def test_no_manifest_uses_defaults(self):
        mixin = self._make_mixin()
        app = MagicMock()

        with patch.dict("sys.modules", {"brotli_asgi": None}):
            mixin._add_compression_middleware(app, "test", {})

        app.add_middleware.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# Markdown Jinja filter
# ════════════════════════════════════════════════════════════════════════════


class TestMarkdownFilter:
    """Test _make_markdown_filter and its sanitization."""

    def test_returns_none_without_deps(self):
        from mdb_engine.routing._ssr import _make_markdown_filter

        with patch.dict("sys.modules", {"mistune": None, "nh3": None}):
            result = _make_markdown_filter()
            assert result is None

    def test_renders_basic_markdown(self):
        mistune = pytest.importorskip("mistune", reason="mistune required")
        nh3 = pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        assert md_filter is not None

        result = md_filter("**bold** and *italic*")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    def test_renders_headings(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter("# Hello World")
        assert "<h1>" in result
        assert "Hello World" in result

    def test_renders_links(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter("[click here](https://example.com)")
        assert 'href="https://example.com"' in result
        assert "click here" in result

    def test_links_do_not_crash_nh3(self):
        """Regression: nh3 raises ValueError if 'rel' is in allowed attrs
        while link_rel is set (the default). Ensure links render without error.
        See: https://github.com/ranfysvalle02/mdb-engine/issues/XXX
        """
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        body = (
            "Check out [Google](https://google.com) and "
            "[GitHub](https://github.com) for more info.\n\n"
            "Also see [docs](/docs)."
        )
        result = md_filter(body)
        assert "https://google.com" in result
        assert "https://github.com" in result
        assert "noopener" in result  # nh3 default link_rel applies

    def test_strips_script_tags(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "alert" not in result or "&lt;script&gt;" in result

    def test_strips_event_handlers(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter('<img src="x" onerror="alert(1)">')
        assert "onerror" not in result

    def test_empty_input(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        assert md_filter("") == ""
        assert md_filter(None) == ""

    def test_renders_code_blocks(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter("```python\nprint('hello')\n```")
        assert "<code" in result
        assert "<pre" in result

    def test_renders_tables(self):
        pytest.importorskip("mistune", reason="mistune required")
        pytest.importorskip("nh3", reason="nh3 required")

        from mdb_engine.routing._ssr import _make_markdown_filter

        md_filter = _make_markdown_filter()
        result = md_filter("| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in result
        assert "<td>" in result


# ════════════════════════════════════════════════════════════════════════════
# Link preload headers
# ════════════════════════════════════════════════════════════════════════════


class TestPreloadLinkParts:
    """Test _build_preload_link_parts for Link header construction."""

    def test_empty_list(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        assert _build_preload_link_parts([]) == []

    def test_single_resource(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts([{"href": "/public/style.css", "as": "style"}])
        assert len(parts) == 1
        assert parts[0] == "</public/style.css>; rel=preload; as=style"

    def test_crossorigin_attribute(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts([{"href": "/public/font.woff2", "as": "font", "crossorigin": True}])
        assert parts[0] == "</public/font.woff2>; rel=preload; as=font; crossorigin"

    def test_crossorigin_false(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts([{"href": "/public/style.css", "as": "style", "crossorigin": False}])
        assert "crossorigin" not in parts[0]

    def test_multiple_resources(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts(
            [
                {"href": "/a.css", "as": "style"},
                {"href": "/b.js", "as": "script"},
                {"href": "/c.woff2", "as": "font", "crossorigin": True},
            ]
        )
        assert len(parts) == 3
        assert "as=style" in parts[0]
        assert "as=script" in parts[1]
        assert "crossorigin" in parts[2]

    def test_missing_href_skipped(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts([{"as": "style"}])
        assert parts == []

    def test_missing_as_skipped(self):
        from mdb_engine.routing._ssr import _build_preload_link_parts

        parts = _build_preload_link_parts([{"href": "/style.css"}])
        assert parts == []


# ════════════════════════════════════════════════════════════════════════════
# Manifest schema validation for new keys
# ════════════════════════════════════════════════════════════════════════════


class TestManifestPerformanceKeys:
    """Test that the new manifest keys validate correctly."""

    @pytest.fixture
    def _run_validate(self):
        import asyncio

        from mdb_engine.core.manifest import validate_manifest

        def _validate(manifest: dict[str, Any]) -> tuple[bool, str | None, list[str] | None]:
            return asyncio.run(validate_manifest(manifest, use_cache=False))

        return _validate

    def test_compression_valid(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "compression": {"enabled": True, "minimum_size": 500},
            }
        )
        assert result[0] is True

    def test_compression_disabled(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "compression": {"enabled": False},
            }
        )
        assert result[0] is True

    def test_compression_invalid_key(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "compression": {"enabled": True, "bogus_key": 42},
            }
        )
        assert result[0] is False

    def test_static_cache_valid(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "static_cache": {
                    "fonts": "max-age=31536000, immutable",
                    "styles": "max-age=86400",
                    "minify": True,
                },
            }
        )
        assert result[0] is True

    def test_static_cache_invalid_key(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "static_cache": {"invalid_category": "max-age=100"},
            }
        )
        assert result[0] is False

    def test_ssr_preload_global(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "ssr": {
                    "enabled": True,
                    "preload": [
                        {"href": "/style.css", "as": "style"},
                        {"href": "/font.woff2", "as": "font", "crossorigin": True},
                    ],
                },
            }
        )
        assert result[0] is True

    def test_ssr_preload_per_route(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "ssr": {
                    "enabled": True,
                    "routes": {
                        "/": {
                            "template": "index.html",
                            "preload": [{"href": "/app.js", "as": "script"}],
                        }
                    },
                },
            }
        )
        assert result[0] is True

    def test_ssr_preload_missing_required_fields(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "ssr": {
                    "enabled": True,
                    "preload": [{"href": "/style.css"}],
                },
            }
        )
        assert result[0] is False

    def test_ssr_preload_invalid_as_type(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "ssr": {
                    "enabled": True,
                    "preload": [{"href": "/x.css", "as": "banana"}],
                },
            }
        )
        assert result[0] is False

    def test_all_perf_keys_together(self, _run_validate):
        result = _run_validate(
            {
                "schema_version": "2.0",
                "slug": "test",
                "name": "Test",
                "compression": {"enabled": True, "minimum_size": 300},
                "static_cache": {
                    "fonts": "max-age=31536000, immutable",
                    "minify": True,
                },
                "ssr": {
                    "enabled": True,
                    "preload": [{"href": "/style.css", "as": "style"}],
                    "routes": {
                        "/": {
                            "template": "index.html",
                            "preload": [{"href": "/hero.webp", "as": "image"}],
                        }
                    },
                },
            }
        )
        assert result[0] is True
