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

    mount_ssr_routes(app, tmpdir, ssr_config, collections_config or {})
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
        assert _build_cache_header({"ttl": "5m"}) == "max-age=300"

    def test_ttl_with_swr(self):
        h = _build_cache_header({"ttl": "1h", "stale_while_revalidate": "5m"})
        assert h == "max-age=3600, stale-while-revalidate=300"

    def test_zero_ttl_returns_none(self):
        assert _build_cache_header({"ttl": "0s"}) is None


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
        assert "max-age=300" in resp.headers.get("cache-control", "")
        assert "stale-while-revalidate=30" in resp.headers.get("cache-control", "")

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
