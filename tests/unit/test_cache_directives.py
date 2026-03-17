"""Tests for manifest-level cache directives."""

from __future__ import annotations

from mdb_engine.routing.auto_crud import _CollectionCtx, _parse_cache_ttl


class TestParseCacheTtl:
    def test_seconds(self):
        assert _parse_cache_ttl("30s") == 30

    def test_minutes(self):
        assert _parse_cache_ttl("5m") == 300

    def test_hours(self):
        assert _parse_cache_ttl("1h") == 3600

    def test_days(self):
        assert _parse_cache_ttl("7d") == 604800

    def test_empty(self):
        assert _parse_cache_ttl("") == 0

    def test_invalid(self):
        assert _parse_cache_ttl("xyz") == 0


class TestCacheControlHeader:
    def test_no_cache_config(self):
        ctx = _CollectionCtx(name="posts")
        assert ctx.cache_control_header() is None

    def test_default_ttl(self):
        ctx = _CollectionCtx(
            name="posts",
            cache_config={"default": {"ttl": "5m"}},
        )
        assert ctx.cache_control_header() == "max-age=300"

    def test_scope_specific(self):
        ctx = _CollectionCtx(
            name="posts",
            cache_config={
                "scope:published": {"ttl": "10m", "stale_while_revalidate": "30s"},
                "default": {"ttl": "0"},
            },
        )
        assert ctx.cache_control_header("published") == "max-age=600, stale-while-revalidate=30"

    def test_scope_falls_back_to_default(self):
        ctx = _CollectionCtx(
            name="posts",
            cache_config={"default": {"ttl": "2m"}},
        )
        assert ctx.cache_control_header("unknown_scope") == "max-age=120"

    def test_zero_ttl_returns_none(self):
        ctx = _CollectionCtx(
            name="posts",
            cache_config={"default": {"ttl": "0"}},
        )
        assert ctx.cache_control_header() is None

    def test_stale_while_revalidate(self):
        ctx = _CollectionCtx(
            name="posts",
            cache_config={"default": {"ttl": "1h", "stale_while_revalidate": "5m"}},
        )
        header = ctx.cache_control_header()
        assert header == "max-age=3600, stale-while-revalidate=300"
