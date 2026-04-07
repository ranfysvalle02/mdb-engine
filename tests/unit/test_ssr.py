"""
Tests for manifest-driven server-side rendering (SSR) v2.

Covers:
- Basic SSR route rendering (list + single doc)
- Populate/relations ($lookup) in SSR data fetching
- Computed fields in SSR data fetching
- Policy.read enforcement
- default_projection field hiding
- Soft-delete exclusion
- Pagination (?page= with total/pages in template context)
- Cache-Control headers
- JSON-LD auto-generation from seo.json_ld
- SEO placeholder resolution
- Custom error pages (404.html, 500.html)
- Sitemap.xml auto-generation
- Auth-gated SSR routes
- Express-style route patterns
- mdb_base.html template inheritance
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("jinja2", reason="SSR tests require jinja2")

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.dependencies import get_current_user, get_scoped_db
from mdb_engine.routing._ssr import (
    _build_cache_header,
    _build_seo_context,
    _convert_route_pattern,
    _resolve_json_ld,
    _resolve_seo_placeholders,
    _to_rfc822,
    _to_rfc3339,
    mount_ssr_routes,
)

# ── Helpers ──────────────────────────────────────────────────────────────


class FakeAggResult:
    """Fake aggregation cursor."""

    def __init__(self, docs: list[dict]):
        self._docs = docs

    def __await__(self):
        async def _noop():
            return self

        return _noop().__await__()

    async def to_list(self, length=None):
        return self._docs[:length] if length else self._docs


class FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)
        self._skip = 0
        self._limit = 1000
        self._projection = None

    def sort(self, spec):
        return self

    def skip(self, n):
        self._skip = n
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        end = self._skip + (length or self._limit)
        return self._docs[self._skip : end]


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None):
        self._docs = docs or []

    async def find_one(self, filter_=None):
        if not filter_:
            return self._docs[0] if self._docs else None
        for doc in self._docs:
            match = True
            for k, v in filter_.items():
                if k in ("$and",):
                    for sub in v:
                        for sk, sv in sub.items():
                            if doc.get(sk) != sv:
                                match = False
                    continue
                if doc.get(k) != v:
                    match = False
            if match:
                return doc
        return None

    def find(self, filter_=None, projection=None):
        if not filter_:
            return FakeCursor(self._docs)
        matched = []
        for doc in self._docs:
            ok = True
            for k, v in filter_.items():
                if k in ("$and",):
                    continue
                if isinstance(v, dict):
                    continue
                if doc.get(k) != v:
                    ok = False
            if ok:
                matched.append(doc)
        return FakeCursor(matched)

    async def count_documents(self, filter_=None):
        if not filter_:
            return len(self._docs)
        count = 0
        for doc in self._docs:
            ok = True
            for k, v in filter_.items():
                if k in ("$and",):
                    continue
                if isinstance(v, dict):
                    continue
                if doc.get(k) != v:
                    ok = False
            if ok:
                count += 1
        return count

    def aggregate(self, pipeline):
        return FakeAggResult(self._docs)


class FakeScopedDB:
    def __init__(self, collections: dict[str, FakeCollection] | None = None):
        self._cols = collections or {}
        self._write_scope = None

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.get(name, FakeCollection())

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.get(name, FakeCollection())


def _build_ssr_app(
    templates: dict[str, str],
    ssr_config: dict[str, Any],
    collections_config: dict[str, Any] | None = None,
    db_collections: dict[str, FakeCollection] | None = None,
    user: dict[str, Any] | None = None,
    base_path: str = "",
) -> tuple[TestClient, Path]:
    """Build a test app with SSR routes and return (client, templates_dir)."""
    app = FastAPI()
    fake_db = FakeScopedDB(db_collections or {})
    _user = user

    async def override_db():
        return fake_db

    async def override_user():
        return _user

    app.dependency_overrides[get_scoped_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    tmpdir = Path(tempfile.mkdtemp())
    for name, content in templates.items():
        tpl_path = tmpdir / name
        tpl_path.parent.mkdir(parents=True, exist_ok=True)
        tpl_path.write_text(content)

    mount_ssr_routes(app, tmpdir, ssr_config, collections_config or {}, base_path=base_path)
    return TestClient(app), tmpdir


# ═════════════════════════════════════════════════════════════════════════
# SEO Placeholder Resolution
# ═════════════════════════════════════════════════════════════════════════


class TestSeoPlaceholders:
    def test_simple_replacement(self):
        result = _resolve_seo_placeholders(
            "{{post.title}} - My Blog",
            {"post": {"title": "Hello World"}},
        )
        assert result == "Hello World - My Blog"

    def test_nested_path(self):
        result = _resolve_seo_placeholders(
            "By {{post.author.name}}",
            {"post": {"author": {"name": "Alice"}}},
        )
        assert result == "By Alice"

    def test_missing_value_empty_string(self):
        result = _resolve_seo_placeholders(
            "{{post.missing}} fallback",
            {"post": {"title": "Hello"}},
        )
        assert result == " fallback"

    def test_no_placeholders(self):
        result = _resolve_seo_placeholders("Static title", {})
        assert result == "Static title"


class TestBuildSeoContext:
    def test_resolves_all_fields(self):
        seo = _build_seo_context(
            {"title": "{{post.title}}", "description": "{{post.excerpt}}"},
            {"post": {"title": "Hello", "excerpt": "A post"}},
            "My Blog",
        )
        assert seo["title"] == "Hello"
        assert seo["description"] == "A post"
        assert seo["site_name"] == "My Blog"

    def test_json_ld_generation(self):
        seo = _build_seo_context(
            {
                "title": "{{post.title}}",
                "json_ld": {
                    "@context": "https://schema.org",
                    "@type": "BlogPosting",
                    "headline": "{{post.title}}",
                    "author": {"@type": "Person", "name": "{{post.author_name}}"},
                },
            },
            {"post": {"title": "My Post", "author_name": "Alice"}},
            "Blog",
        )
        assert "json_ld" in seo
        ld = json.loads(seo["json_ld"])
        assert ld["headline"] == "My Post"
        assert ld["author"]["name"] == "Alice"
        assert ld["@context"] == "https://schema.org"


class TestResolveJsonLd:
    def test_recursive_resolution(self):
        config = {
            "@type": "Article",
            "name": "{{post.title}}",
            "tags": ["{{post.tag1}}", "{{post.tag2}}"],
        }
        result = _resolve_json_ld(config, {"post": {"title": "Hi", "tag1": "a", "tag2": "b"}})
        assert result["name"] == "Hi"
        assert result["tags"] == ["a", "b"]


class TestConvertRoutePattern:
    def test_fastapi_style_unchanged(self):
        assert _convert_route_pattern("/posts/{id}") == "/posts/{id}"

    def test_express_style_converted(self):
        assert _convert_route_pattern("/posts/:id") == "/posts/{id}"

    def test_multiple_params(self):
        assert _convert_route_pattern("/users/:uid/posts/:pid") == "/users/{uid}/posts/{pid}"


class TestBuildCacheHeader:
    def test_none_when_no_config(self):
        assert _build_cache_header(None) is None

    def test_ttl(self):
        assert _build_cache_header({"ttl": "5m"}) == "public, max-age=300"

    def test_ttl_with_swr(self):
        h = _build_cache_header({"ttl": "1h", "stale_while_revalidate": "5m"})
        assert h == "public, max-age=3600, stale-while-revalidate=300"

    def test_zero_ttl_returns_none(self):
        assert _build_cache_header({"ttl": "0s"}) is None

    def test_private_scope(self):
        h = _build_cache_header({"ttl": "5m", "scope": "private"})
        assert h == "private, max-age=300"

    def test_public_scope_explicit(self):
        h = _build_cache_header({"ttl": "10m", "scope": "public"})
        assert h == "public, max-age=600"


# ═════════════════════════════════════════════════════════════════════════
# Basic SSR Rendering
# ═════════════════════════════════════════════════════════════════════════


class TestSSRListRoute:
    def test_renders_list_of_posts(self):
        oid1, oid2 = ObjectId(), ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "index.html": (
                    "<title>{{ seo.title }}</title>" "{% for p in posts %}<h2>{{ p.title }}</h2>{% endfor %}"
                ),
            },
            ssr_config={
                "enabled": True,
                "site_name": "Test Blog",
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Test Blog"},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection(
                    [
                        {"_id": oid1, "title": "First Post"},
                        {"_id": oid2, "title": "Second Post"},
                    ]
                ),
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "First Post" in resp.text
        assert "Second Post" in resp.text


class TestSSRSingleDocRoute:
    def test_renders_single_post(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "post.html": "<h1>{{ post.title }}</h1><p>{{ post.body }}</p>",
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "My Post", "body": "Content"}]),
            },
        )
        resp = client.get(f"/posts/{oid}")
        assert resp.status_code == 200
        assert "My Post" in resp.text

    def test_404_for_missing_document(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": "<h1>{{ post.title }}</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": oid, "title": "X"}])},
        )
        resp = client.get(f"/posts/{ObjectId()}")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Pagination
# ═════════════════════════════════════════════════════════════════════════


class TestSSRPagination:
    def test_pagination_context_available(self):
        docs = [{"_id": ObjectId(), "title": f"Post {i}"} for i in range(25)]
        client, _ = _build_ssr_app(
            templates={
                "index.html": (
                    "Page {{ posts_pagination.page }} of {{ posts_pagination.total_pages }} "
                    "({{ posts_pagination.total }} total)"
                    "{% for p in posts %}<h2>{{ p.title }}</h2>{% endfor %}"
                ),
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                    }
                },
            },
            db_collections={"posts": FakeCollection(docs)},
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Page 1 of 3" in resp.text
        assert "25 total" in resp.text

    def test_page_query_param(self):
        docs = [{"_id": ObjectId(), "title": f"Post {i}"} for i in range(25)]
        client, _ = _build_ssr_app(
            templates={
                "index.html": "Page {{ posts_pagination.page }}",
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                    }
                },
            },
            db_collections={"posts": FakeCollection(docs)},
        )
        resp = client.get("/?page=2")
        assert resp.status_code == 200
        assert "Page 2" in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Cache-Control Headers
# ═════════════════════════════════════════════════════════════════════════


class TestSSRCacheHeaders:
    def test_cache_header_set(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "cached"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "cache": {"ttl": "5m", "stale_while_revalidate": "30s"},
                    }
                },
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "public" in cc
        assert "max-age=300" in cc
        assert "stale-while-revalidate=30" in cc

    def test_no_cache_by_default(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "no cache"},
            ssr_config={
                "enabled": True,
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/")
        assert "cache-control" not in resp.headers


# ═════════════════════════════════════════════════════════════════════════
# JSON-LD in Templates
# ═════════════════════════════════════════════════════════════════════════


class TestSSRJsonLd:
    def test_json_ld_in_template(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "post.html": (
                    '<script type="application/ld+json">{{ seo.json_ld | safe }}</script>' "<h1>{{ post.title }}</h1>"
                ),
            },
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "json_ld": {
                                "@context": "https://schema.org",
                                "@type": "BlogPosting",
                                "headline": "{{post.title}}",
                            },
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "SSR Post"}]),
            },
        )
        resp = client.get(f"/posts/{oid}")
        assert resp.status_code == 200
        assert "application/ld+json" in resp.text
        assert "SSR Post" in resp.text
        assert "schema.org" in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Policy Enforcement
# ═════════════════════════════════════════════════════════════════════════


class TestSSRPolicyEnforcement:
    def test_scope_filter_applied(self):
        client, _ = _build_ssr_app(
            templates={
                "index.html": "{% for p in posts %}<h2>{{ p.title }}</h2>{% endfor %}",
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "scope": "published"}},
                    }
                },
            },
            collections_config={
                "posts": {"scopes": {"published": {"status": "published"}}},
            },
            db_collections={
                "posts": FakeCollection(
                    [
                        {"_id": ObjectId(), "title": "Published", "status": "published"},
                        {"_id": ObjectId(), "title": "Draft", "status": "draft"},
                    ]
                ),
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Published" in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Auth
# ═════════════════════════════════════════════════════════════════════════


class TestSSRAuth:
    def test_auth_required_blocks_anonymous(self):
        client, _ = _build_ssr_app(
            templates={"dashboard.html": "<h1>Dashboard</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {"/dashboard": {"template": "dashboard.html", "auth": True}},
            },
            user=None,
        )
        resp = client.get("/dashboard")
        assert resp.status_code == 401

    def test_auth_required_allows_authenticated(self):
        client, _ = _build_ssr_app(
            templates={"dashboard.html": "<h1>Hello {{ user.name }}</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {"/dashboard": {"template": "dashboard.html", "auth": True}},
            },
            user={"_id": "u1", "name": "Alice"},
        )
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "Hello Alice" in resp.text

    def test_public_route_works_without_auth(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "<h1>Public</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {"/": {"template": "index.html"}},
            },
            user=None,
        )
        resp = client.get("/")
        assert resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════
# Error Pages
# ═════════════════════════════════════════════════════════════════════════


class TestSSRErrorPages:
    def test_custom_404_page(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "post.html": "<h1>{{ post.title }}</h1>",
                "404.html": "<h1>Not Found</h1><p>The page you requested does not exist.</p>",
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": oid, "title": "Only"}])},
        )
        resp = client.get(f"/posts/{ObjectId()}")
        assert resp.status_code == 404
        assert "The page you requested does not exist" in resp.text

    def test_no_custom_404_falls_through(self):
        client, _ = _build_ssr_app(
            templates={"post.html": "<h1>{{ post.title }}</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={"posts": FakeCollection()},
        )
        resp = client.get(f"/posts/{ObjectId()}")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Sitemap
# ═════════════════════════════════════════════════════════════════════════


class TestSSRSitemap:
    def test_sitemap_includes_static_routes(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home", "about.html": "about"},
            ssr_config={
                "enabled": True,
                "base_url": "https://myblog.com",
                "routes": {
                    "/": {"template": "index.html"},
                    "/about": {"template": "about.html"},
                },
            },
        )
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]
        assert "https://myblog.com/" in resp.text
        assert "https://myblog.com/about" in resp.text

    def test_sitemap_includes_dynamic_routes(self):
        oid1, oid2 = ObjectId(), ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": "post"},
            ssr_config={
                "enabled": True,
                "base_url": "https://myblog.com",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection(
                    [
                        {"_id": oid1, "title": "Post 1"},
                        {"_id": oid2, "title": "Post 2"},
                    ]
                ),
            },
        )
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert str(oid1) in resp.text
        assert str(oid2) in resp.text

    def test_sitemap_excludes_auth_routes(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home", "dash.html": "dash"},
            ssr_config={
                "enabled": True,
                "base_url": "https://myblog.com",
                "routes": {
                    "/": {"template": "index.html"},
                    "/dashboard": {"template": "dash.html", "auth": True},
                },
            },
        )
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "https://myblog.com/" in resp.text
        assert "dashboard" not in resp.text

    def test_sitemap_disabled(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home"},
            ssr_config={
                "enabled": True,
                "sitemap": False,
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Projection / Field Hiding
# ═════════════════════════════════════════════════════════════════════════


class TestSSRProjection:
    def test_default_projection_hides_fields(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "post.html": ("<h1>{{ post.title }}</h1>" "{% if post.internal_notes %}LEAKED{% endif %}"),
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            collections_config={
                "posts": {
                    "default_projection": {"internal_notes": 0, "password_hash": 0},
                },
            },
            db_collections={
                "posts": FakeCollection(
                    [
                        {"_id": oid, "title": "Post", "internal_notes": "secret", "password_hash": "hash"},
                    ]
                ),
            },
        )
        resp = client.get(f"/posts/{oid}")
        assert resp.status_code == 200
        assert "Post" in resp.text
        assert "LEAKED" not in resp.text
        assert "secret" not in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Related Data (filter with params)
# ═════════════════════════════════════════════════════════════════════════


class TestSSRRelatedData:
    def test_filter_with_path_params(self):
        post_oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={
                "post.html": ("<h1>{{ post.title }}</h1>" "{% for c in comments %}<p>{{ c.body }}</p>{% endfor %}"),
            },
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {
                            "post": {"collection": "posts", "id_param": "id"},
                            "comments": {
                                "collection": "comments",
                                "filter": {"post_id": "{{params.id}}"},
                            },
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": post_oid, "title": "My Post"}]),
                "comments": FakeCollection(
                    [
                        {"_id": ObjectId(), "post_id": str(post_oid), "body": "Great!"},
                        {"_id": ObjectId(), "post_id": "other", "body": "Wrong post"},
                    ]
                ),
            },
        )
        resp = client.get(f"/posts/{post_oid}")
        assert resp.status_code == 200
        assert "Great!" in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Express-Style Routes
# ═════════════════════════════════════════════════════════════════════════


class TestSSRExpressRoutes:
    def test_express_style_route(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": "<h1>{{ post.title }}</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/posts/:id": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Express"}]),
            },
        )
        resp = client.get(f"/posts/{oid}")
        assert resp.status_code == 200
        assert "Express" in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═════════════════════════════════════════════════════════════════════════


class TestSSREdgeCases:
    def test_no_routes_does_not_crash(self):
        app = FastAPI()

        async def override_db():
            return FakeScopedDB()

        app.dependency_overrides[get_scoped_db] = override_db
        tmpdir = Path(tempfile.mkdtemp())
        mount_ssr_routes(app, tmpdir, {"enabled": True, "routes": {}})

    def test_missing_template_key_skipped(self):
        app = FastAPI()

        async def override_db():
            return FakeScopedDB()

        app.dependency_overrides[get_scoped_db] = override_db
        tmpdir = Path(tempfile.mkdtemp())
        mount_ssr_routes(app, tmpdir, {"enabled": True, "routes": {"/broken": {}}})

    def test_empty_data_sources(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "<h1>No data</h1>"},
            ssr_config={
                "enabled": True,
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "No data" in resp.text

    def test_seo_fallback_to_site_description(self):
        client, _ = _build_ssr_app(
            templates={
                "index.html": '<meta name="description" content="{{ seo.description }}">',
            },
            ssr_config={
                "enabled": True,
                "site_description": "Default description.",
                "routes": {"/": {"template": "index.html", "seo": {"title": "Home"}}},
            },
        )
        resp = client.get("/")
        assert "Default description." in resp.text


# ═════════════════════════════════════════════════════════════════════════
# Auto SEO Meta Tags (mdb_base.html)
# ═════════════════════════════════════════════════════════════════════════

# Template that extends the framework base — the whole point is that it
# needs ZERO manual meta tags to get full OG/Twitter/JSON-LD output.
_BASE_CHILD = """\
{% extends "mdb_base.html" %}
{% block content %}<h1>{{ post.title }}</h1>{% endblock %}
"""

_BASE_CHILD_LIST = """\
{% extends "mdb_base.html" %}
{% block content %}<ul>{% for p in posts %}<li>{{ p.title }}</li>{% endfor %}</ul>{% endblock %}
"""

_BASE_CHILD_CUSTOM_SEO = """\
{% extends "mdb_base.html" %}
{% block seo_meta %}
<meta property="og:title" content="Custom Override">
{% endblock %}
{% block content %}<h1>Custom</h1>{% endblock %}
"""


class TestAutoSeoMetaTags:
    """Verify mdb_base.html automatically renders OG, Twitter Card,
    and JSON-LD tags from the seo context — zero template boilerplate."""

    def test_og_tags_rendered_with_image(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "My Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}} — My Blog",
                            "description": "{{post.excerpt}}",
                            "og_type": "article",
                            "og_image": "{{post.cover}}",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection(
                    [
                        {
                            "_id": oid,
                            "title": "Test Post",
                            "excerpt": "A great post",
                            "cover": "https://img.example.com/hero.jpg",
                        }
                    ]
                ),
            },
        )
        resp = client.get(f"/posts/{oid}")
        html = resp.text
        assert resp.status_code == 200
        assert '<meta property="og:title" content="Test Post — My Blog">' in html
        assert '<meta property="og:description" content="A great post">' in html
        assert '<meta property="og:type" content="article">' in html
        assert '<meta property="og:site_name" content="My Blog">' in html
        assert '<meta property="og:url"' in html
        assert '<meta property="og:image" content="https://img.example.com/hero.jpg">' in html

    def test_twitter_card_large_image_when_og_image(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "desc",
                            "og_image": "{{post.img}}",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Img Post", "img": "https://img.example.com/a.jpg"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta name="twitter:card" content="summary_large_image">' in html
        assert '<meta name="twitter:title" content="Img Post">' in html
        assert '<meta name="twitter:description" content="desc">' in html
        assert '<meta name="twitter:image" content="https://img.example.com/a.jpg">' in html

    def test_twitter_card_summary_without_image(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "No image here",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Text Post"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta name="twitter:card" content="summary">' in html
        assert "twitter:image" not in html

    def test_json_ld_rendered_in_base_template(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "A post",
                            "json_ld": {
                                "@context": "https://schema.org",
                                "@type": "Article",
                                "headline": "{{post.title}}",
                            },
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "LD Post"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<script type="application/ld+json">' in html
        ld = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
        assert ld["@type"] == "Article"
        assert ld["headline"] == "LD Post"

    def test_title_auto_filled_from_seo(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {"title": "{{post.title}} — Blog"},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Title Test"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert "<title>Title Test — Blog</title>" in html

    def test_no_seo_config_still_renders_structural_tags(self):
        """Routes without explicit seo config still get structural meta tags
        (the SSR handler always builds a seo context with at least site_name)."""
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                "enabled": True,
                "site_name": "My Site",
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": ObjectId(), "title": "A"}])},
        )
        html = client.get("/").text
        assert '<meta property="og:site_name" content="My Site">' in html
        assert '<meta name="twitter:card" content="summary">' in html
        assert "og:image" not in html

    def test_seo_meta_block_overridable(self):
        """Apps can override {% block seo_meta %} for full control."""
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD_CUSTOM_SEO},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "Should not appear",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Overridden"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta property="og:title" content="Custom Override">' in html
        assert "og:description" not in html
        assert "twitter:card" not in html

    def test_og_type_defaults_to_website(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {"title": "{{post.title}}", "description": "d"},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Default OG"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta property="og:type" content="website">' in html

    def test_empty_og_image_field_no_image_tags(self):
        """When og_image resolves to empty string, treat as no image."""
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "d",
                            "og_image": "{{post.cover}}",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "No Cover"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta name="twitter:card" content="summary">' in html
        assert "og:image" not in html
        assert "twitter:image" not in html


# ═════════════════════════════════════════════════════════════════════════
# RSS / Atom Feeds
# ═════════════════════════════════════════════════════════════════════════

_FEED_SSR_CONFIG: dict[str, Any] = {
    "enabled": True,
    "site_name": "Test Blog",
    "site_description": "A test blog",
    "base_url": "https://example.com",
    "routes": {
        "/": {"template": "index.html"},
    },
    "feeds": {
        "/feed.xml": {
            "format": "rss",
            "collection": "posts",
            "scope": "published",
            "sort": {"created_at": -1},
            "limit": 20,
            "title": "{{site_name}} RSS",
            "description": "{{site_description}}",
            "item": {
                "title": "{{doc.title}}",
                "link": "{{base_url}}/posts/{{doc.slug}}",
                "description": "{{doc.excerpt}}",
                "pubDate": "{{doc.created_at}}",
            },
        },
    },
}

_FEED_POSTS = [
    {
        "_id": ObjectId(),
        "title": "First Post",
        "slug": "first",
        "excerpt": "Hello",
        "created_at": "2026-04-01T12:00:00+00:00",
        "status": "published",
    },
    {
        "_id": ObjectId(),
        "title": "Second Post",
        "slug": "second",
        "excerpt": "World",
        "created_at": "2026-03-15T09:00:00+00:00",
        "status": "published",
    },
]


class TestSSRFeeds:
    """Tests for manifest-driven RSS/Atom feed generation."""

    def _app(
        self,
        ssr_config: dict[str, Any] | None = None,
        collections_config: dict[str, Any] | None = None,
        posts: list[dict] | None = None,
        base_path: str = "",
    ) -> TestClient:
        config = ssr_config or _FEED_SSR_CONFIG
        cols_cfg = collections_config or {
            "posts": {"scopes": {"published": {"status": "published"}}},
        }
        client, _ = _build_ssr_app(
            templates={"index.html": "home"},
            ssr_config=config,
            collections_config=cols_cfg,
            db_collections={"posts": FakeCollection(posts or _FEED_POSTS)},
            base_path=base_path,
        )
        return client

    # ── RSS basics ────────────────────────────────────────────────────

    def test_rss_feed_returns_200_with_correct_content_type(self):
        client = self._app()
        resp = client.get("/feed.xml")
        assert resp.status_code == 200
        assert "application/rss+xml" in resp.headers["content-type"]

    def test_rss_feed_contains_items(self):
        client = self._app()
        xml = client.get("/feed.xml").text
        assert "<item>" in xml
        assert "<title>First Post</title>" in xml
        assert "<title>Second Post</title>" in xml

    def test_rss_feed_has_self_link(self):
        client = self._app()
        xml = client.get("/feed.xml").text
        assert 'rel="self"' in xml
        assert 'type="application/rss+xml"' in xml
        assert "https://example.com/feed.xml" in xml

    def test_rss_feed_has_last_build_date(self):
        client = self._app()
        xml = client.get("/feed.xml").text
        assert "<lastBuildDate>" in xml

    def test_rss_feed_has_generator(self):
        client = self._app()
        xml = client.get("/feed.xml").text
        assert "<generator>mdb-engine</generator>" in xml

    # ── Atom basics ───────────────────────────────────────────────────

    def test_atom_feed_returns_200_with_correct_content_type(self):
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {
                "/atom.xml": {
                    **_FEED_SSR_CONFIG["feeds"]["/feed.xml"],
                    "format": "atom",
                },
            },
        }
        client = self._app(ssr_config=cfg)
        resp = client.get("/atom.xml")
        assert resp.status_code == 200
        assert "application/atom+xml" in resp.headers["content-type"]

    def test_atom_feed_has_updated(self):
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {
                "/atom.xml": {
                    **_FEED_SSR_CONFIG["feeds"]["/feed.xml"],
                    "format": "atom",
                },
            },
        }
        client = self._app(ssr_config=cfg)
        xml = client.get("/atom.xml").text
        assert "<updated>" in xml

    def test_atom_feed_has_generator(self):
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {
                "/atom.xml": {
                    **_FEED_SSR_CONFIG["feeds"]["/feed.xml"],
                    "format": "atom",
                },
            },
        }
        client = self._app(ssr_config=cfg)
        xml = client.get("/atom.xml").text
        assert "<generator>mdb-engine</generator>" in xml

    # ── Scope filtering ──────────────────────────────────────────────

    def test_scope_filters_items(self):
        posts = [
            *_FEED_POSTS,
            {
                "_id": ObjectId(),
                "title": "Draft",
                "slug": "draft",
                "excerpt": "WIP",
                "created_at": "2026-04-02",
                "status": "draft",
            },
        ]
        client = self._app(posts=posts)
        xml = client.get("/feed.xml").text
        assert "First Post" in xml
        assert "Draft" not in xml

    # ── Soft-delete exclusion ────────────────────────────────────────

    def test_soft_delete_excluded(self):
        """Soft-deleted docs are excluded via ``deleted_at: null`` filter."""
        posts = [
            {
                "_id": ObjectId(),
                "title": "Live Post",
                "slug": "live",
                "excerpt": "Visible",
                "created_at": "2026-04-01",
                "deleted_at": None,
            },
            {
                "_id": ObjectId(),
                "title": "Deleted Post",
                "slug": "del",
                "excerpt": "Gone",
                "created_at": "2026-04-03",
                "deleted_at": "2026-04-04",
            },
        ]
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {
                "/feed.xml": {
                    "format": "rss",
                    "collection": "posts",
                    "sort": {"created_at": -1},
                    "limit": 20,
                    "item": {"title": "{{doc.title}}"},
                },
            },
        }
        cols_cfg = {"posts": {"soft_delete": True}}
        client = self._app(ssr_config=cfg, collections_config=cols_cfg, posts=posts)
        xml = client.get("/feed.xml").text
        assert "Live Post" in xml
        assert "Deleted Post" not in xml

    # ── Limit ────────────────────────────────────────────────────────

    def test_limit_respected(self):
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {
                "/feed.xml": {
                    **_FEED_SSR_CONFIG["feeds"]["/feed.xml"],
                    "limit": 1,
                },
            },
        }
        client = self._app(ssr_config=cfg)
        xml = client.get("/feed.xml").text
        assert xml.count("<item>") == 1

    # ── Caching / Conditional GET ────────────────────────────────────

    def test_etag_and_cache_control_headers(self):
        client = self._app()
        resp = client.get("/feed.xml")
        assert "ETag" in resp.headers
        assert resp.headers["Cache-Control"] == "public, max-age=3600"

    def test_if_none_match_returns_304(self):
        client = self._app()
        resp1 = client.get("/feed.xml")
        etag = resp1.headers["ETag"]
        resp2 = client.get("/feed.xml", headers={"If-None-Match": etag})
        assert resp2.status_code == 304

    # ── <link rel="alternate"> injection ─────────────────────────────

    def test_feed_link_injected_into_ssr_page_head(self):
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                **_FEED_SSR_CONFIG,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Home", "description": "desc"},
                    },
                },
            },
            collections_config={
                "posts": {"scopes": {"published": {"status": "published"}}},
            },
            db_collections={"posts": FakeCollection(_FEED_POSTS)},
        )
        html = client.get("/").text
        assert 'rel="alternate"' in html
        assert 'type="application/rss+xml"' in html
        assert 'href="/feed.xml"' in html
        assert "Test Blog RSS" in html

    def test_feed_link_href_includes_base_path(self):
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                **_FEED_SSR_CONFIG,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Home", "description": "desc"},
                    },
                },
            },
            collections_config={
                "posts": {"scopes": {"published": {"status": "published"}}},
            },
            db_collections={"posts": FakeCollection(_FEED_POSTS)},
            base_path="/blog",
        )
        html = client.get("/").text
        assert 'href="/blog/feed.xml"' in html

    # ── Missing collection warning ───────────────────────────────────

    def test_feed_with_no_collection_skipped(self):
        cfg = {
            **_FEED_SSR_CONFIG,
            "feeds": {"/bad.xml": {"format": "rss"}},
        }
        client = self._app(ssr_config=cfg)
        resp = client.get("/bad.xml")
        assert resp.status_code == 404

    # ── rfc822 / rfc3339 pipe transforms ─────────────────────────────

    def test_rfc822_transform_iso_input(self):
        assert _to_rfc822("2026-04-01T12:00:00+00:00") == "Wed, 01 Apr 2026 12:00:00 GMT"

    def test_rfc822_transform_python_str_input(self):
        assert _to_rfc822("2026-04-01 12:00:00+00:00") == "Wed, 01 Apr 2026 12:00:00 GMT"

    def test_rfc3339_transform_iso_input(self):
        result = _to_rfc3339("2026-04-01T12:00:00+00:00")
        assert result == "2026-04-01T12:00:00+00:00"

    def test_rfc822_transform_empty_string(self):
        assert _to_rfc822("") == ""

    def test_rfc3339_transform_empty_string(self):
        assert _to_rfc3339("") == ""

    def test_rfc822_transform_invalid_input(self):
        assert _to_rfc822("not-a-date") == "not-a-date"

    def test_rfc3339_transform_invalid_input(self):
        assert _to_rfc3339("not-a-date") == "not-a-date"

    def test_rfc822_transform_z_suffix(self):
        assert _to_rfc822("2026-04-01T12:00:00Z") == "Wed, 01 Apr 2026 12:00:00 GMT"


# ═════════════════════════════════════════════════════════════════════════
# Per-Route Meta Robots
# ═════════════════════════════════════════════════════════════════════════


class TestMetaRobots:
    def test_robots_meta_tag_rendered(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "desc",
                            "robots": "noindex, follow",
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Hidden Post"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<meta name="robots" content="noindex, follow">' in html

    def test_robots_meta_tag_absent_when_not_set(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {"title": "{{post.title}}", "description": "desc"},
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "Visible Post"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert 'name="robots"' not in html


# ═════════════════════════════════════════════════════════════════════════
# Configurable <html lang>
# ═════════════════════════════════════════════════════════════════════════


class TestHtmlLang:
    def test_custom_lang_attribute(self):
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                "enabled": True,
                "site_name": "Mi Blog",
                "lang": "es",
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Inicio", "description": "desc"},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": ObjectId(), "title": "A"}])},
        )
        html = client.get("/").text
        assert '<html lang="es">' in html

    def test_default_lang_is_en(self):
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Home", "description": "desc"},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": ObjectId(), "title": "A"}])},
        )
        html = client.get("/").text
        assert '<html lang="en">' in html


# ═════════════════════════════════════════════════════════════════════════
# Manifest-Driven Redirects
# ═════════════════════════════════════════════════════════════════════════


class TestSSRRedirects:
    def test_static_301_redirect(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home", "new.html": "new page"},
            ssr_config={
                "enabled": True,
                "redirects": {
                    "/old": {"to": "/new", "status": 301},
                },
                "routes": {
                    "/": {"template": "index.html"},
                    "/new": {"template": "new.html"},
                },
            },
        )
        resp = client.get("/old", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/new"

    def test_parameterized_redirect(self):
        client, _ = _build_ssr_app(
            templates={"post.html": "<h1>post</h1>"},
            ssr_config={
                "enabled": True,
                "redirects": {
                    "/blog/:slug": {"to": "/posts/:slug", "status": 301},
                },
                "routes": {
                    "/posts/{slug}": {"template": "post.html"},
                },
            },
        )
        resp = client.get("/blog/hello-world", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/posts/hello-world"

    def test_302_redirect(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home"},
            ssr_config={
                "enabled": True,
                "redirects": {
                    "/temp": {"to": "/", "status": 302},
                },
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/temp", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"


# ═════════════════════════════════════════════════════════════════════════
# Preconnect / DNS-Prefetch in Preload
# ═════════════════════════════════════════════════════════════════════════

from mdb_engine.routing._ssr import _build_preload_link_parts


class TestPreconnectPreload:
    def test_preconnect_link_header(self):
        parts = _build_preload_link_parts(
            [
                {"href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": True},
            ]
        )
        assert len(parts) == 1
        assert parts[0] == "<https://fonts.googleapis.com>; rel=preconnect; crossorigin"

    def test_dns_prefetch_link_header(self):
        parts = _build_preload_link_parts(
            [
                {"href": "https://cdn.example.com", "rel": "dns-prefetch"},
            ]
        )
        assert len(parts) == 1
        assert parts[0] == "<https://cdn.example.com>; rel=dns-prefetch"

    def test_preload_still_works(self):
        parts = _build_preload_link_parts(
            [
                {"href": "/public/style.css", "as": "style"},
            ]
        )
        assert len(parts) == 1
        assert parts[0] == "</public/style.css>; rel=preload; as=style"

    def test_preload_without_as_skipped(self):
        parts = _build_preload_link_parts(
            [
                {"href": "/public/style.css"},
            ]
        )
        assert len(parts) == 0

    def test_mixed_preload_and_preconnect(self):
        parts = _build_preload_link_parts(
            [
                {"href": "/public/style.css", "as": "style"},
                {"href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": True},
                {"href": "https://cdn.example.com", "rel": "dns-prefetch"},
            ]
        )
        assert len(parts) == 3
        assert "rel=preload" in parts[0]
        assert "rel=preconnect" in parts[1]
        assert "rel=dns-prefetch" in parts[2]

    def test_preconnect_link_tag_in_html(self):
        client, _ = _build_ssr_app(
            templates={"index.html": _BASE_CHILD_LIST},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "preload": [
                    {"href": "https://fonts.googleapis.com", "rel": "preconnect", "crossorigin": True},
                    {"href": "/public/style.css", "as": "style"},
                ],
                "routes": {
                    "/": {
                        "template": "index.html",
                        "data": {"posts": {"collection": "posts", "limit": 10}},
                        "seo": {"title": "Home", "description": "desc"},
                    }
                },
            },
            db_collections={"posts": FakeCollection([{"_id": ObjectId(), "title": "A"}])},
        )
        html = client.get("/").text
        assert '<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>' in html
        assert '<link rel="preload" href="/public/style.css" as="style">' in html


# ═════════════════════════════════════════════════════════════════════════
# Vary Header
# ═════════════════════════════════════════════════════════════════════════


class TestSSRVaryHeader:
    def test_explicit_vary_header(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "cached"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "cache": {"ttl": "5m", "vary": ["Cookie", "Accept-Language"]},
                    }
                },
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers.get("vary") == "Cookie, Accept-Language"

    def test_auto_vary_cookie_on_auth_route(self):
        client, _ = _build_ssr_app(
            templates={"dash.html": "dashboard"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/dashboard": {
                        "template": "dash.html",
                        "auth": True,
                        "cache": {"ttl": "1m"},
                    }
                },
            },
            user={"_id": "u1", "name": "Alice"},
        )
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert resp.headers.get("vary") == "Cookie"

    def test_no_vary_on_public_route_without_config(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home"},
            ssr_config={
                "enabled": True,
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/")
        assert "vary" not in resp.headers


# ═════════════════════════════════════════════════════════════════════════
# Trailing-Slash Normalization
# ═════════════════════════════════════════════════════════════════════════


class TestSSRTrailingSlash:
    def test_strip_redirects_trailing_slash(self):
        client, _ = _build_ssr_app(
            templates={"about.html": "about"},
            ssr_config={
                "enabled": True,
                "trailing_slash": "strip",
                "routes": {"/about": {"template": "about.html"}},
            },
        )
        resp = client.get("/about/", follow_redirects=False)
        assert resp.status_code == 301
        location = resp.headers["location"]
        assert location.endswith("/about")
        assert not location.endswith("/about/")

    def test_strip_does_not_redirect_root(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "home"},
            ssr_config={
                "enabled": True,
                "trailing_slash": "strip",
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200

    def test_ensure_adds_trailing_slash(self):
        client, _ = _build_ssr_app(
            templates={"about.html": "about"},
            ssr_config={
                "enabled": True,
                "trailing_slash": "ensure",
                "routes": {"/about/": {"template": "about.html"}},
            },
        )
        resp = client.get("/about", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"].endswith("/about/")

    def test_ignore_no_redirect(self):
        client, _ = _build_ssr_app(
            templates={"about.html": "about"},
            ssr_config={
                "enabled": True,
                "trailing_slash": "ignore",
                "routes": {"/about": {"template": "about.html"}},
            },
        )
        resp = client.get("/about", follow_redirects=False)
        assert resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════
# ETag / Conditional GET on SSR Pages
# ═════════════════════════════════════════════════════════════════════════


class TestSSRETag:
    def test_etag_header_present(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "static content"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "cache": {"ttl": "5m", "etag": True},
                    }
                },
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ETag" in resp.headers
        assert resp.headers["ETag"].startswith('"')
        assert resp.headers["ETag"].endswith('"')

    def test_304_on_matching_if_none_match(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "static content"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "cache": {"ttl": "5m", "etag": True},
                    }
                },
            },
        )
        resp1 = client.get("/")
        etag = resp1.headers["ETag"]
        resp2 = client.get("/", headers={"If-None-Match": etag})
        assert resp2.status_code == 304

    def test_no_etag_when_not_enabled(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "no etag"},
            ssr_config={
                "enabled": True,
                "routes": {
                    "/": {
                        "template": "index.html",
                        "cache": {"ttl": "5m"},
                    }
                },
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ETag" not in resp.headers

    def test_no_etag_without_cache_config(self):
        client, _ = _build_ssr_app(
            templates={"index.html": "no cache"},
            ssr_config={
                "enabled": True,
                "routes": {"/": {"template": "index.html"}},
            },
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ETag" not in resp.headers


# ═════════════════════════════════════════════════════════════════════════
# Breadcrumb JSON-LD Shorthand
# ═════════════════════════════════════════════════════════════════════════


class TestBreadcrumbJsonLd:
    def test_breadcrumb_ld_generated(self):
        seo = _build_seo_context(
            {
                "title": "Blog Post",
                "breadcrumbs": [
                    {"name": "Home", "url": "https://example.com/"},
                    {"name": "Blog", "url": "https://example.com/blog"},
                    {"name": "{{post.title}}", "url": "https://example.com/posts/{{post.slug}}"},
                ],
            },
            {"post": {"title": "My Post", "slug": "my-post"}},
            "Blog",
        )
        assert "json_ld" in seo
        ld = json.loads(seo["json_ld"])
        assert ld["@type"] == "BreadcrumbList"
        assert len(ld["itemListElement"]) == 3
        assert ld["itemListElement"][0]["position"] == 1
        assert ld["itemListElement"][0]["name"] == "Home"
        assert ld["itemListElement"][2]["name"] == "My Post"
        assert ld["itemListElement"][2]["item"] == "https://example.com/posts/my-post"

    def test_breadcrumbs_merged_with_json_ld(self):
        seo = _build_seo_context(
            {
                "title": "Post",
                "json_ld": {
                    "@context": "https://schema.org",
                    "@type": "Article",
                    "headline": "{{post.title}}",
                },
                "breadcrumbs": [
                    {"name": "Home", "url": "/"},
                    {"name": "{{post.title}}", "url": "/posts/{{post.slug}}"},
                ],
            },
            {"post": {"title": "My Post", "slug": "my-post"}},
            "Blog",
        )
        ld = json.loads(seo["json_ld"])
        assert "@graph" in ld
        assert len(ld["@graph"]) == 2
        types = {item["@type"] for item in ld["@graph"]}
        assert "Article" in types
        assert "BreadcrumbList" in types

    def test_no_breadcrumbs_no_json_ld(self):
        seo = _build_seo_context(
            {"title": "Home"},
            {},
            "Blog",
        )
        assert "json_ld" not in seo

    def test_breadcrumb_in_template(self):
        oid = ObjectId()
        client, _ = _build_ssr_app(
            templates={"post.html": _BASE_CHILD},
            ssr_config={
                "enabled": True,
                "site_name": "Blog",
                "routes": {
                    "/posts/{id}": {
                        "template": "post.html",
                        "data": {"post": {"collection": "posts", "id_param": "id"}},
                        "seo": {
                            "title": "{{post.title}}",
                            "description": "desc",
                            "breadcrumbs": [
                                {"name": "Home", "url": "https://example.com/"},
                                {"name": "{{post.title}}", "url": "https://example.com/posts/{{post.id}}"},
                            ],
                        },
                    }
                },
            },
            db_collections={
                "posts": FakeCollection([{"_id": oid, "title": "BC Post"}]),
            },
        )
        html = client.get(f"/posts/{oid}").text
        assert '<script type="application/ld+json">' in html
        assert "BreadcrumbList" in html
        assert "BC Post" in html
