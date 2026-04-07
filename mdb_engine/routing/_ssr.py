"""
Manifest-driven server-side rendering (SSR) for zero-code apps.

Reads the ``ssr`` section from a manifest and registers FastAPI routes
that fetch data from MongoDB collections and render Jinja2 templates
server-side — complete with SEO meta tags, Open Graph, JSON-LD, caching,
pagination, relations ($lookup), computed fields, policy enforcement,
and auto-generated sitemaps.

Users write only ``manifest.json`` + ``templates/*.html``. No Python.

Features:
    * ``populate`` — $lookup joins for relations (reuses auto-CRUD infra)
    * ``computed`` — aggregation-based computed fields
    * ``policy.read`` — document-level access control enforcement
    * ``default_projection`` — hide sensitive fields from templates
    * ``cache`` — Cache-Control headers per route/scope
    * ``pagination`` — ?page= query param with total/pages in context
    * ``json_ld`` — auto-generated JSON-LD from manifest config
    * ``sitemap.xml`` — auto-generated from SSR routes
    * ``404.html / 500.html`` — custom error page templates
    * ``mdb_base.html`` — framework template inheritance
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from email.utils import format_datetime as _format_rfc822
from hashlib import md5 as _md5
from pathlib import Path
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse, Response

from ..dependencies import get_current_user, get_scoped_db
from ._serialization import serialize_doc
from .template_resolver import merge_filters, resolve_template

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")
_FW_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")


# ── SEO text transforms ─────────────────────────────────────────────────


def _strip_markdown(text: str) -> str:
    """Strip common markdown syntax, returning approximate plain text."""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(`{1,3}).*?\1", "", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*{1,2}|_{1,2})(.*?)\1", r"\2", text)
    text = re.sub(r"^[>\-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_datetime(val: str) -> datetime:
    """Best-effort parse of an ISO-ish or ``str(datetime)`` value to aware UTC."""
    s = str(val).strip()
    # Python str(datetime) uses space separator; normalise to 'T'
    s = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", r"\1T\2", s)
    # fromisoformat handles most variants including +00:00 / Z
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_rfc822(val: str, **_kw: Any) -> str:
    """Format a datetime string as RFC 822 (RSS ``pubDate``)."""
    if not val:
        return ""
    try:
        return _format_rfc822(_parse_datetime(val), usegmt=True)
    except (ValueError, TypeError):
        return str(val)


def _to_rfc3339(val: str, **_kw: Any) -> str:
    """Format a datetime string as RFC 3339 / ISO 8601 (Atom ``updated``)."""
    if not val:
        return ""
    try:
        return _parse_datetime(val).isoformat()
    except (ValueError, TypeError):
        return str(val)


_SEO_TRANSFORMS: dict[str, Any] = {
    "plain_text": lambda val, **_kw: _strip_markdown(str(val)) if val else "",
    "truncate": lambda val, length=160, **_kw: (
        (str(val)[: length - 1].rsplit(" ", 1)[0] + "\u2026") if val and len(str(val)) > length else str(val or "")
    ),
    "rfc822": _to_rfc822,
    "rfc3339": _to_rfc3339,
}


def _apply_seo_transforms(value: str, transforms: list[str]) -> str:
    """Apply a chain of ``name`` or ``name(arg)`` transforms to a string."""
    for expr in transforms:
        expr = expr.strip()
        match = re.match(r"(\w+)\((.+)\)", expr)
        if match:
            name, raw_arg = match.group(1), match.group(2)
            kwargs = {}
            try:
                kwargs["length"] = int(raw_arg)
            except ValueError:
                kwargs["length"] = raw_arg
        else:
            name = expr
            kwargs = {}
        fn = _SEO_TRANSFORMS.get(name)
        if fn:
            value = fn(value, **kwargs)
    return value


# ── SEO helpers ──────────────────────────────────────────────────────────


def _resolve_seo_placeholders(template: str, context: dict[str, Any]) -> str:
    """Resolve ``{{post.title}}`` style placeholders in SEO strings.

    Supports pipe transforms: ``{{post.body | plain_text | truncate(160)}}``.
    """

    def _replace(match: re.Match) -> str:
        raw = match.group(1)
        parts_pipe = [p.strip() for p in raw.split("|")]
        var_path = parts_pipe[0]
        transforms = parts_pipe[1:]

        segments = var_path.split(".")
        current: Any = context
        for seg in segments:
            if isinstance(current, dict):
                current = current.get(seg)
            else:
                return match.group(0)
        result = str(current) if current is not None else ""
        if transforms:
            result = _apply_seo_transforms(result, transforms)
        return result

    return _PLACEHOLDER_RE.sub(_replace, template)


def _resolve_seo_field(
    value: Any,
    data_context: dict[str, Any],
    *,
    skip_data_uri: bool = False,
) -> str:
    """Resolve a single SEO field value, supporting fallback chains.

    ``value`` may be:
    * A string template (legacy) — resolved directly.
    * A dict with ``"fallback"`` — an ordered list of templates; the first
      non-empty result wins.

    When *skip_data_uri* is True, resolved values starting with ``data:``
    are treated as empty so the chain falls through to the next candidate.
    This prevents multi-KB base64 strings from landing in ``og:image`` meta
    tags where social platforms cannot use them.
    """
    if isinstance(value, str):
        resolved = _resolve_seo_placeholders(value, data_context)
        if skip_data_uri and resolved.startswith("data:"):
            return ""
        return resolved
    if isinstance(value, dict) and "fallback" in value:
        for tpl in value["fallback"]:
            resolved = _resolve_seo_placeholders(tpl, data_context)
            if resolved and resolved.strip():
                if skip_data_uri and resolved.startswith("data:"):
                    continue
                return resolved
        return ""
    return str(value) if value is not None else ""


def _build_seo_context(
    seo_config: dict[str, Any],
    data_context: dict[str, Any],
    site_name: str,
) -> dict[str, Any]:
    """Build resolved SEO metadata from config + fetched data."""
    seo: dict[str, Any] = {"site_name": site_name}

    _SKIP_KEYS = {"json_ld", "breadcrumbs"}
    _IMAGE_SEO_KEYS = {"og_image", "og:image", "twitter_image", "twitter:image", "image"}
    for key, value in seo_config.items():
        if key in _SKIP_KEYS:
            continue
        seo[key] = _resolve_seo_field(
            value,
            data_context,
            skip_data_uri=key in _IMAGE_SEO_KEYS,
        )

    json_ld_config = seo_config.get("json_ld")
    breadcrumbs_config = seo_config.get("breadcrumbs")

    ld_objects: list[Any] = []
    if json_ld_config:
        ld_objects.append(_resolve_json_ld(json_ld_config, data_context))
    if breadcrumbs_config:
        ld_objects.append(_build_breadcrumb_ld(breadcrumbs_config, data_context))

    if len(ld_objects) == 1:
        seo["json_ld"] = json.dumps(ld_objects[0], default=str)
    elif len(ld_objects) > 1:
        seo["json_ld"] = json.dumps({"@context": "https://schema.org", "@graph": ld_objects}, default=str)

    return seo


def _build_breadcrumb_ld(
    breadcrumbs: list[dict[str, str]],
    data_context: dict[str, Any],
) -> dict[str, Any]:
    """Build a BreadcrumbList JSON-LD object from manifest shorthand."""
    items = []
    for i, crumb in enumerate(breadcrumbs, 1):
        name = _resolve_seo_placeholders(crumb.get("name", ""), data_context)
        url = _resolve_seo_placeholders(crumb.get("url", ""), data_context)
        items.append(
            {
                "@type": "ListItem",
                "position": i,
                "name": name,
                "item": url,
            }
        )
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }


def _resolve_json_ld(config: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve placeholders in a JSON-LD config object."""
    if isinstance(config, str):
        return _resolve_seo_placeholders(config, context)
    if isinstance(config, dict):
        return {k: _resolve_json_ld(v, context) for k, v in config.items()}
    if isinstance(config, list):
        return [_resolve_json_ld(item, context) for item in config]
    return config


# ── Pagination SEO ───────────────────────────────────────────────────────


def _inject_pagination_seo(
    seo: dict[str, Any],
    data_context: dict[str, Any],
    request: Request,
) -> None:
    """Add ``pagination_prev`` / ``pagination_next`` to the SEO context.

    Scans data_context for ``{name}_pagination`` entries and uses the first
    one found to compute rel=prev / rel=next URLs.
    """
    for key, val in data_context.items():
        if not key.endswith("_pagination") or not isinstance(val, dict):
            continue
        page = val.get("page", 1)
        total_pages = val.get("total_pages", 1)
        base = str(request.url).split("?")[0]
        if page > 1:
            prev_page = page - 1
            seo["pagination_prev"] = f"{base}?page={prev_page}" if prev_page > 1 else base
        if page < total_pages:
            seo["pagination_next"] = f"{base}?page={page + 1}"
        break


# ── Cache helpers ────────────────────────────────────────────────────────


def _parse_cache_ttl(value: str) -> int:
    if not value:
        return 0
    unit = value[-1].lower()
    try:
        num = int(value[:-1])
    except (ValueError, IndexError):
        return 0
    return num * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 1)


def _build_cache_header(cache_config: dict[str, Any] | None) -> str | None:
    """Build a Cache-Control header from route cache config."""
    if not cache_config:
        return None
    ttl = cache_config.get("ttl", "0")
    seconds = _parse_cache_ttl(ttl)
    if seconds <= 0:
        return None
    scope = cache_config.get("scope", "public")
    parts = [scope, f"max-age={seconds}"]
    swr = cache_config.get("stale_while_revalidate")
    if swr:
        swr_secs = _parse_cache_ttl(swr)
        if swr_secs > 0:
            parts.append(f"stale-while-revalidate={swr_secs}")
    return ", ".join(parts)


# ── Data fetching ────────────────────────────────────────────────────────


def _to_object_id(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Not found") from None


def _apply_policy_filter(
    filter_spec: dict[str, Any] | None,
    col_config: dict[str, Any],
    user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge collection policy.read filter into the query."""
    policy = col_config.get("policy", {})
    read_policy = policy.get("read")
    if not read_policy:
        return filter_spec
    try:
        resolved_policy = resolve_template(read_policy, user)
    except HTTPException:
        resolved_policy = None
    return merge_filters(filter_spec, resolved_policy)


def _apply_soft_delete_filter(filter_spec: dict[str, Any] | None) -> dict[str, Any]:
    """Exclude soft-deleted documents."""
    sd = {"deleted_at": None}
    if filter_spec:
        return {"$and": [filter_spec, sd]}
    return sd


def _apply_projection(doc: dict[str, Any], projection: dict[str, int] | None) -> dict[str, Any]:
    """Apply a MongoDB-style projection (exclusion only) to a document."""
    if not projection:
        return doc
    excluded = {k for k, v in projection.items() if v == 0}
    if not excluded:
        return doc
    return {k: v for k, v in doc.items() if k not in excluded}


async def _fetch_data_source(
    source_config: dict[str, Any],
    db: Any,
    path_params: dict[str, str],
    query_params: dict[str, str],
    user: dict[str, Any] | None,
    collections_config: dict[str, Any],
) -> Any:
    """Fetch data for a single SSR data source from MongoDB.

    Supports: id_param (single doc), list queries with scope/filter/sort/
    limit/pagination, populate ($lookup), computed fields, policy.read
    enforcement, soft_delete exclusion, and default_projection.
    """
    collection_name = source_config.get("collection", "")
    if not collection_name:
        return None

    collection = db[collection_name]
    col_config = collections_config.get(collection_name, {})
    projection = col_config.get("default_projection")

    exclude_fields = source_config.get("exclude_fields")
    if exclude_fields:
        extra_exclusion = {f: 0 for f in exclude_fields}
        projection = {**(projection or {}), **extra_exclusion}

    populate_names = source_config.get("populate")
    computed_names = source_config.get("computed")
    needs_aggregation = bool(populate_names or computed_names)

    id_param = source_config.get("id_param")
    if id_param:
        return await _fetch_single_doc(
            collection,
            col_config,
            db,
            path_params,
            user,
            id_param,
            populate_names,
            computed_names,
            projection,
        )

    return await _fetch_list(
        collection,
        col_config,
        db,
        source_config,
        path_params,
        query_params,
        user,
        populate_names,
        computed_names,
        projection,
        needs_aggregation,
    )


def _detect_slug_field(col_config: dict[str, Any]) -> str | None:
    """Return the name of the first ``x-slug`` field in the collection schema, if any."""
    schema = col_config.get("schema", {})
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for name, prop_def in props.items():
        if isinstance(prop_def, dict) and "x-slug" in prop_def:
            return name
    return None


async def _fetch_single_doc(
    collection: Any,
    col_config: dict[str, Any],
    db: Any,
    path_params: dict[str, str],
    user: dict[str, Any] | None,
    id_param: str,
    populate_names: list[str] | None,
    computed_names: list[str] | None,
    projection: dict[str, int] | None,
) -> dict[str, Any]:
    """Fetch a single document by ID or slug with optional populate/computed."""
    doc_id = path_params.get(id_param)
    if not doc_id:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        oid = ObjectId(doc_id)
        filter_spec: dict[str, Any] = {"_id": oid}
    except (InvalidId, TypeError):
        slug_field = _detect_slug_field(col_config)
        if slug_field:
            filter_spec = {slug_field: doc_id}
        else:
            raise HTTPException(status_code=404, detail="Not found") from None
    filter_spec = _apply_policy_filter(filter_spec, col_config, user)
    if col_config.get("soft_delete"):
        filter_spec = _apply_soft_delete_filter(filter_spec)

    if populate_names or computed_names:
        pipeline: list[dict[str, Any]] = [{"$match": filter_spec}]
        _inject_populate_stages(pipeline, populate_names, col_config, db)
        _inject_computed_stages(pipeline, computed_names, col_config, db)
        if projection:
            pipeline.append({"$project": projection})
        pipeline.append({"$limit": 1})
        result = collection.aggregate(pipeline)
        if hasattr(result, "__await__"):
            result = await result
        docs = await result.to_list(length=1)
        if not docs:
            raise HTTPException(status_code=404, detail="Not found")
        return serialize_doc(docs[0])

    doc = await collection.find_one(filter_spec)
    if doc is None:
        raise HTTPException(status_code=404, detail="Not found")
    doc = _apply_projection(doc, projection)
    return serialize_doc(doc)


async def _fetch_list(
    collection: Any,
    col_config: dict[str, Any],
    db: Any,
    source_config: dict[str, Any],
    path_params: dict[str, str],
    query_params: dict[str, str],
    user: dict[str, Any] | None,
    populate_names: list[str] | None,
    computed_names: list[str] | None,
    projection: dict[str, int] | None,
    needs_aggregation: bool,
) -> dict[str, Any]:
    """Fetch a list of documents with pagination metadata.

    Returns ``{"items": [...], "total": N, "page": N, "total_pages": N, "limit": N}``
    so templates can render pagination.
    """
    filter_spec = _build_list_filter(source_config, col_config, path_params, user)

    sort_spec = source_config.get("sort")
    limit = source_config.get("limit", 20)

    page_str = query_params.get("page", "1")
    try:
        page = max(1, int(page_str))
    except (ValueError, TypeError):
        page = 1
    skip = (page - 1) * limit

    total = await collection.count_documents(filter_spec or {})
    total_pages = max(1, math.ceil(total / limit))

    if needs_aggregation:
        pipeline: list[dict[str, Any]] = []
        if filter_spec:
            pipeline.append({"$match": filter_spec})
        _inject_populate_stages(pipeline, populate_names, col_config, db)
        _inject_computed_stages(pipeline, computed_names, col_config, db)
        if sort_spec:
            pipeline.append({"$sort": dict(sort_spec) if not isinstance(sort_spec, dict) else sort_spec})
        pipeline.append({"$skip": skip})
        pipeline.append({"$limit": limit})
        if projection:
            pipeline.append({"$project": projection})
        result = collection.aggregate(pipeline)
        if hasattr(result, "__await__"):
            result = await result
        docs = await result.to_list(length=limit)
    else:
        cursor = collection.find(filter_spec or {}, projection)
        if sort_spec:
            cursor = cursor.sort(list(sort_spec.items()) if isinstance(sort_spec, dict) else sort_spec)
        cursor = cursor.skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)

    return {
        "items": [serialize_doc(d) for d in docs],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "limit": limit,
    }


def _build_list_filter(
    source_config: dict[str, Any],
    col_config: dict[str, Any],
    path_params: dict[str, str],
    user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the combined filter for a list query (scope + filter + policy + soft_delete)."""
    filter_spec = dict(source_config.get("filter", {}))
    for key, value in list(filter_spec.items()):
        if isinstance(value, str) and value.startswith("{{params."):
            param_name = value[len("{{params.") : -2]
            filter_spec[key] = path_params.get(param_name, "")

    scope_name = source_config.get("scope")
    if scope_name:
        scope_filter = col_config.get("scopes", {}).get(scope_name, {})
        if isinstance(scope_filter, dict) and "filter" in scope_filter:
            scope_filter = scope_filter["filter"]
        if scope_filter:
            filter_spec = merge_filters(filter_spec or None, scope_filter) or {}

    combined = _apply_policy_filter(filter_spec or None, col_config, user)
    if col_config.get("soft_delete"):
        combined = _apply_soft_delete_filter(combined)
    return combined


# ── Populate + Computed (reusing auto-CRUD patterns) ─────────────────────


def _get_db_prefix(db: Any) -> str:
    scope = getattr(db, "_write_scope", None)
    return f"{scope}_" if scope else ""


def _inject_populate_stages(
    pipeline: list[dict[str, Any]],
    populate_names: list[str] | None,
    col_config: dict[str, Any],
    db: Any,
) -> None:
    """Inject $lookup stages for relations defined in the collection config."""
    if not populate_names:
        return
    relations = col_config.get("relations", {})
    if not relations:
        return
    prefix = _get_db_prefix(db)
    for name in populate_names:
        rel = relations.get(name)
        if rel is None:
            logger.warning("SSR populate: unknown relation '%s', skipping", name)
            continue
        as_field = rel.get("as", name)
        foreign_field = rel.get("foreign_field", "_id")
        from_collection = f"{prefix}{rel['from']}"

        if foreign_field == "_id":
            pipeline.append(
                {
                    "$lookup": {
                        "from": from_collection,
                        "let": {"local_val": f"${rel['local_field']}"},
                        "pipeline": [
                            {"$match": {"$expr": {"$eq": ["$_id", {"$toObjectId": "$$local_val"}]}}},
                        ],
                        "as": as_field,
                    },
                }
            )
        else:
            pipeline.append(
                {
                    "$lookup": {
                        "from": from_collection,
                        "localField": rel["local_field"],
                        "foreignField": foreign_field,
                        "as": as_field,
                    },
                }
            )

        if rel.get("single", False):
            pipeline.append(
                {
                    "$unwind": {"path": f"${as_field}", "preserveNullAndEmptyArrays": True},
                }
            )


def _inject_computed_stages(
    pipeline: list[dict[str, Any]],
    computed_names: list[str] | None,
    col_config: dict[str, Any],
    db: Any,
) -> None:
    """Inject aggregation stages for computed fields."""
    if not computed_names:
        return
    computed_config = col_config.get("computed", {})
    if not computed_config:
        return

    prefix = _get_db_prefix(db)
    for name in computed_names:
        comp = computed_config.get(name)
        if comp is None:
            logger.warning("SSR computed: unknown field '%s', skipping", name)
            continue
        if isinstance(comp, dict) and "pipeline" in comp:
            stages = comp["pipeline"]
            if prefix:
                stages = _prefix_lookup_stages(stages, prefix)
            pipeline.extend(stages)
        else:
            pipeline.append({"$addFields": {name: comp}})


def _prefix_lookup_stages(stages: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    import copy

    result = copy.deepcopy(stages)
    for stage in result:
        if "$lookup" in stage:
            lookup = stage["$lookup"]
            from_col = lookup.get("from", "")
            if from_col and not from_col.startswith(prefix):
                lookup["from"] = f"{prefix}{from_col}"
    return result


# ── Route pattern conversion ─────────────────────────────────────────────


def _convert_route_pattern(pattern: str) -> str:
    """Convert ``/posts/:id`` (Express-style) to ``/posts/{id}`` (FastAPI)."""
    return re.sub(r":(\w+)", r"{\1}", pattern)


# ── Sitemap generation ───────────────────────────────────────────────────


def _format_sitemap_url(
    loc: str,
    meta: dict[str, Any] | None = None,
    doc: dict[str, Any] | None = None,
) -> str:
    """Format a single ``<url>`` element with optional lastmod/changefreq/priority."""
    parts = [f"  <url>\n    <loc>{loc}</loc>"]
    if meta:
        lastmod = meta.get("lastmod")
        if lastmod and doc:
            resolved = _resolve_seo_placeholders(lastmod, {"doc": doc})
            if resolved:
                parts.append(f"    <lastmod>{resolved}</lastmod>")
        elif lastmod and "{{" not in lastmod:
            parts.append(f"    <lastmod>{lastmod}</lastmod>")
        changefreq = meta.get("changefreq")
        if changefreq:
            parts.append(f"    <changefreq>{changefreq}</changefreq>")
        priority = meta.get("priority")
        if priority is not None:
            parts.append(f"    <priority>{priority}</priority>")
    parts.append("  </url>")
    return "\n".join(parts)


def _build_sitemap_xml(urls: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>"
    )


def _build_sitemap_index_xml(sitemap_urls: list[str]) -> str:
    entries = "\n".join(f"  <sitemap><loc>{u}</loc></sitemap>" for u in sitemap_urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + entries + "\n</sitemapindex>"
    )


def _register_sitemap_route(
    router: APIRouter,
    ssr_config: dict[str, Any],
    collections_config: dict[str, Any],
    base_url: str,
) -> None:
    """Register ``/sitemap.xml`` (with optional index splitting) from SSR config.

    When ``sitemap`` is an object, it may carry per-route metadata
    (``lastmod``, ``changefreq``, ``priority``) and ``max_urls_per_file``
    for automatic index splitting.  The legacy ``"sitemap": true`` form
    is fully backward-compatible.
    """
    routes = ssr_config.get("routes", {})
    sitemap_cfg = ssr_config.get("sitemap", True)
    route_meta: dict[str, dict[str, Any]] = {}
    max_per_file = 50000
    if isinstance(sitemap_cfg, dict):
        route_meta = sitemap_cfg.get("routes", {})
        max_per_file = sitemap_cfg.get("max_urls_per_file", 50000)

    async def _collect_urls(request: Request, db: Any) -> list[str]:
        host = base_url or str(request.base_url).rstrip("/")
        urls: list[str] = []

        for pattern, route_config in routes.items():
            if route_config.get("auth"):
                continue
            meta = route_meta.get(pattern)

            if "{" not in pattern and ":" not in pattern:
                urls.append(_format_sitemap_url(f"{host}{pattern}", meta))
                continue

            data_sources = route_config.get("data", {})
            for _name, source in data_sources.items():
                if source.get("id_param"):
                    col_name = source.get("collection", "")
                    if not col_name:
                        continue
                    col = db[col_name]
                    col_config = collections_config.get(col_name, {})

                    filter_spec: dict[str, Any] | None = None
                    scope_name = source.get("scope")
                    if scope_name:
                        scope_filter = col_config.get("scopes", {}).get(scope_name, {})
                        if isinstance(scope_filter, dict) and "filter" in scope_filter:
                            scope_filter = scope_filter["filter"]
                        filter_spec = scope_filter or None
                    if col_config.get("soft_delete"):
                        filter_spec = _apply_soft_delete_filter(filter_spec)

                    projection: dict[str, int] = {"_id": 1}
                    if meta and meta.get("lastmod"):
                        lastmod_tpl = meta["lastmod"]
                        lm_match = _PLACEHOLDER_RE.search(lastmod_tpl)
                        if lm_match:
                            field_path = lm_match.group(1)
                            if field_path.startswith("doc."):
                                projection[field_path[4:]] = 1

                    cursor = col.find(filter_spec or {}, projection)
                    docs = await cursor.to_list(length=100000)
                    param = source["id_param"]
                    for doc in docs:
                        doc_id = str(doc["_id"])
                        url_path = _convert_route_pattern(pattern).replace(f"{{{param}}}", doc_id)
                        doc_ser = serialize_doc(doc)
                        urls.append(_format_sitemap_url(f"{host}{url_path}", meta, doc_ser))
                    break

        return urls

    @router.get("/sitemap.xml", response_class=Response, include_in_schema=False)
    async def sitemap(request: Request, db=Depends(get_scoped_db)) -> Response:
        urls = await _collect_urls(request, db)
        if len(urls) <= max_per_file:
            return Response(content=_build_sitemap_xml(urls), media_type="application/xml")
        host = base_url or str(request.base_url).rstrip("/")
        num_files = math.ceil(len(urls) / max_per_file)
        index_urls = [f"{host}/sitemap-{i + 1}.xml" for i in range(num_files)]
        return Response(content=_build_sitemap_index_xml(index_urls), media_type="application/xml")

    @router.get("/sitemap-{page_num}.xml", response_class=Response, include_in_schema=False)
    async def sitemap_page(page_num: int, request: Request, db=Depends(get_scoped_db)) -> Response:
        if page_num < 1:
            raise HTTPException(status_code=404, detail="Not found")
        urls = await _collect_urls(request, db)
        start = (page_num - 1) * max_per_file
        if start >= len(urls):
            raise HTTPException(status_code=404, detail="Not found")
        page_urls = urls[start : start + max_per_file]
        return Response(content=_build_sitemap_xml(page_urls), media_type="application/xml")


# ── RSS / Atom feed generation ───────────────────────────────────────────


def _build_rss_xml(
    feed_cfg: dict[str, Any],
    docs: list[dict[str, Any]],
    host: str,
    feed_path: str,
    site_name: str,
    site_description: str,
) -> str:
    """Build an RSS 2.0 XML string from feed config and documents."""
    title = _resolve_seo_placeholders(
        feed_cfg.get("title", "{{site_name}}"),
        {"site_name": site_name},
    )
    desc = _resolve_seo_placeholders(
        feed_cfg.get("description", "{{site_description}}"),
        {"site_name": site_name, "site_description": site_description},
    )
    item_tpl = feed_cfg.get("item", {})
    now_rfc822 = _format_rfc822(datetime.now(timezone.utc), usegmt=True)
    self_url = f"{host}{feed_path}"

    items: list[str] = []
    for doc in docs:
        ctx = {"doc": doc, "base_url": host, "site_name": site_name}
        parts = ["    <item>"]
        for tag, tpl in item_tpl.items():
            resolved = _resolve_seo_placeholders(tpl, ctx)
            parts.append(f"      <{tag}>{_xml_escape(resolved)}</{tag}>")
        parts.append("    </item>")
        items.append("\n".join(parts))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{_xml_escape(title)}</title>\n"
        f"    <description>{_xml_escape(desc)}</description>\n"
        f"    <link>{host}</link>\n"
        f'    <atom:link href="{_xml_escape(self_url)}" rel="self"'
        f' type="application/rss+xml"/>\n'
        f"    <lastBuildDate>{now_rfc822}</lastBuildDate>\n"
        "    <generator>mdb-engine</generator>\n" + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>"
    )


def _build_atom_xml(
    feed_cfg: dict[str, Any],
    docs: list[dict[str, Any]],
    host: str,
    feed_path: str,
    site_name: str,
    site_description: str,
) -> str:
    """Build an Atom 1.0 XML string from feed config and documents."""
    title = _resolve_seo_placeholders(
        feed_cfg.get("title", "{{site_name}}"),
        {"site_name": site_name},
    )
    subtitle = _resolve_seo_placeholders(
        feed_cfg.get("description", "{{site_description}}"),
        {"site_name": site_name, "site_description": site_description},
    )
    item_tpl = feed_cfg.get("item", {})

    atom_tag_map = {
        "title": "title",
        "link": "id",
        "description": "summary",
        "pubDate": "updated",
        "author": "author",
    }

    entries: list[str] = []
    for doc in docs:
        ctx = {"doc": doc, "base_url": host, "site_name": site_name}
        parts = ["    <entry>"]
        for rss_tag, tpl in item_tpl.items():
            atom_tag = atom_tag_map.get(rss_tag, rss_tag)
            resolved = _resolve_seo_placeholders(tpl, ctx)
            if atom_tag == "id":
                parts.append(f"      <id>{_xml_escape(resolved)}</id>")
                parts.append(f'      <link href="{_xml_escape(resolved)}"/>')
            elif atom_tag == "author":
                parts.append(f"      <author><name>{_xml_escape(resolved)}</name></author>")
            else:
                parts.append(f"      <{atom_tag}>{_xml_escape(resolved)}</{atom_tag}>")
        parts.append("    </entry>")
        entries.append("\n".join(parts))

    now_rfc3339 = datetime.now(timezone.utc).isoformat()

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{_xml_escape(title)}</title>\n"
        f"  <subtitle>{_xml_escape(subtitle)}</subtitle>\n"
        f'  <link href="{host}{feed_path}" rel="self"/>\n'
        f'  <link href="{host}"/>\n'
        f"  <id>{host}{feed_path}</id>\n"
        f"  <updated>{now_rfc3339}</updated>\n"
        "  <generator>mdb-engine</generator>\n" + "\n".join(entries) + "\n"
        "</feed>"
    )


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _register_feed_routes(
    router: APIRouter,
    feeds_config: dict[str, dict[str, Any]],
    collections_config: dict[str, Any],
    base_url: str,
    site_name: str,
    site_description: str,
) -> None:
    """Register RSS/Atom feed routes from ``ssr.feeds`` config."""
    from hashlib import md5

    for feed_path, feed_cfg in feeds_config.items():
        fmt = feed_cfg.get("format", "rss")
        col_name = feed_cfg.get("collection", "")
        scope_name = feed_cfg.get("scope")
        sort_spec = feed_cfg.get("sort", {"created_at": -1})
        limit = feed_cfg.get("limit", 20)

        if not col_name:
            logger.warning("Feed %s has no collection, skipping", feed_path)
            continue

        def _make_handler(
            _path: str,
            _cfg: dict[str, Any],
            _fmt: str,
            _col: str,
            _scope: str | None,
            _sort: Any,
            _limit: int,
        ):  # type: ignore[no-untyped-def]
            @router.get(_path, response_class=Response, include_in_schema=False)
            async def feed_handler(request: Request, db=Depends(get_scoped_db)) -> Response:
                host = base_url or str(request.base_url).rstrip("/")
                col_config = collections_config.get(_col, {})
                collection = db[_col]

                filter_spec: dict[str, Any] | None = None
                if _scope:
                    scope_filter = col_config.get("scopes", {}).get(_scope, {})
                    if isinstance(scope_filter, dict) and "filter" in scope_filter:
                        scope_filter = scope_filter["filter"]
                    filter_spec = scope_filter or None
                if col_config.get("soft_delete"):
                    filter_spec = _apply_soft_delete_filter(filter_spec)

                cursor = collection.find(filter_spec or {})
                if _sort:
                    cursor = cursor.sort(
                        list(_sort.items()) if isinstance(_sort, dict) else _sort,
                    )
                cursor = cursor.limit(_limit)
                docs = await cursor.to_list(length=_limit)
                serialized = [serialize_doc(d) for d in docs]

                if _fmt == "atom":
                    xml = _build_atom_xml(_cfg, serialized, host, _path, site_name, site_description)
                    content_type = "application/atom+xml; charset=utf-8"
                else:
                    xml = _build_rss_xml(_cfg, serialized, host, _path, site_name, site_description)
                    content_type = "application/rss+xml; charset=utf-8"

                etag = md5(xml.encode()).hexdigest()  # noqa: S324
                etag_header = f'"{etag}"'

                if_none_match = request.headers.get("if-none-match")
                if if_none_match and if_none_match.strip() == etag_header:
                    return Response(
                        status_code=304,
                        headers={
                            "ETag": etag_header,
                            "Cache-Control": "public, max-age=3600",
                        },
                    )

                resp = Response(content=xml, media_type=content_type)
                resp.headers["ETag"] = etag_header
                resp.headers["Cache-Control"] = "public, max-age=3600"
                return resp

        _make_handler(feed_path, feed_cfg, fmt, col_name, scope_name, sort_spec, limit)


# ── robots.txt generation ────────────────────────────────────────────────


def _register_robots_route(
    router: APIRouter,
    robots_config: dict[str, Any],
    base_url: str,
) -> None:
    """Register a ``/robots.txt`` route from manifest ``ssr.robots`` config."""
    allow_paths = robots_config.get("allow", [])
    disallow_paths = robots_config.get("disallow", [])
    sitemap_url = robots_config.get("sitemap", "")

    @router.get("/robots.txt", response_class=Response, include_in_schema=False)
    async def robots_txt(request: Request) -> Response:
        host = base_url or str(request.base_url).rstrip("/")
        lines = ["User-agent: *"]
        for path in allow_paths:
            lines.append(f"Allow: {path}")
        for path in disallow_paths:
            lines.append(f"Disallow: {path}")
        sm = sitemap_url.replace("{{base_url}}", host) if sitemap_url else f"{host}/sitemap.xml"
        lines.append(f"Sitemap: {sm}")
        return Response(content="\n".join(lines) + "\n", media_type="text/plain")


# ── Error page rendering ────────────────────────────────────────────────


class _ErrorPageHandler:
    """Render custom error pages from templates if they exist."""

    def __init__(self, jinja_env: Any) -> None:
        self._env = jinja_env
        self._has_404 = self._template_exists("404.html")
        self._has_500 = self._template_exists("500.html")

    def _template_exists(self, name: str) -> bool:
        from jinja2.exceptions import TemplateNotFound

        try:
            self._env.get_template(name)
            return True
        except TemplateNotFound:
            return False

    def render_404(self, request: Request) -> HTMLResponse | None:
        if not self._has_404:
            return None
        tpl = self._env.get_template("404.html")
        html = tpl.render(request=request, seo={"title": "Page Not Found"})
        return HTMLResponse(content=html, status_code=404)

    def render_500(self, request: Request) -> HTMLResponse | None:
        if not self._has_500:
            return None
        tpl = self._env.get_template("500.html")
        html = tpl.render(request=request, seo={"title": "Server Error"})
        return HTMLResponse(content=html, status_code=500)


# ── Redirects ────────────────────────────────────────────────────────────


def _register_redirect_routes(
    router: APIRouter,
    redirects_config: dict[str, dict[str, Any]],
) -> None:
    """Register 301/302 redirect routes from ``ssr.redirects``."""
    for from_pattern, redirect_cfg in redirects_config.items():
        from_path = _convert_route_pattern(from_pattern)
        to_pattern = redirect_cfg.get("to", "")
        status_code = redirect_cfg.get("status", 301)
        if not to_pattern:
            logger.warning("Redirect %s has no 'to', skipping", from_pattern)
            continue

        to_fastapi = _convert_route_pattern(to_pattern)

        def _make_handler(_to: str, _status: int):  # type: ignore[no-untyped-def]
            async def handler(request: Request) -> RedirectResponse:
                resolved = _to
                for param, value in request.path_params.items():
                    resolved = resolved.replace(f"{{{param}}}", value)
                return RedirectResponse(url=resolved, status_code=_status)

            return handler

        router.add_api_route(
            from_path,
            _make_handler(to_fastapi, status_code),
            methods=["GET"],
            response_class=RedirectResponse,
            include_in_schema=False,
        )
        logger.info("Registered redirect: %s -> %s (%d)", from_pattern, to_pattern, status_code)


# ── Trailing-slash normalisation ─────────────────────────────────────────


def _register_trailing_slash_middleware(
    app: Any,
    policy: str,
) -> None:
    """Add trailing-slash redirect middleware to the app."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class TrailingSlashMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            path = request.url.path
            if policy == "strip" and path != "/" and path.endswith("/"):
                new_url = str(request.url).replace(path, path.rstrip("/"), 1)
                return RedirectResponse(url=new_url, status_code=301)
            if policy == "ensure" and not path.endswith("/"):
                new_url = str(request.url).replace(path, path + "/", 1)
                return RedirectResponse(url=new_url, status_code=301)
            return await call_next(request)

    app.add_middleware(TrailingSlashMiddleware)


# ── Main mount function ──────────────────────────────────────────────────


def _make_markdown_filter() -> Any:
    """Build a Jinja filter that renders Markdown to sanitized HTML.

    Returns ``None`` when the required libraries are not installed so
    callers can skip registration gracefully.
    """
    try:
        import mistune
        import nh3
    except ImportError:
        logger.debug(
            "mistune/nh3 not installed — |markdown Jinja filter unavailable. "
            "Install with: pip install mdb-engine[markdown]"
        )
        return None

    _ALLOWED_TAGS = {
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "br", "hr",
        "ul", "ol", "li",
        "a", "strong", "em", "code", "pre", "blockquote",
        "img", "table", "thead", "tbody", "tr", "th", "td",
        "del", "sup", "sub", "details", "summary",
        "div", "span",
    }  # fmt: skip

    _ALLOWED_ATTRS: dict[str, set[str]] = {
        "a": {"href", "title"},
        "img": {"src", "alt", "title", "width", "height", "loading"},
        "td": {"align"},
        "th": {"align"},
        "code": {"class"},
        "pre": {"class"},
        "div": {"class"},
        "span": {"class"},
    }

    _URL_SCHEMES = {"http", "https", "mailto", "data"}

    # data: URIs are allowed in url_schemes so <img src="data:..."> works,
    # but they're dangerous in <a href="data:text/html;..."> (XSS via
    # navigation).  Post-process to neutralize them on anchors only.
    _DATA_HREF_RE = re.compile(
        r"""(<a\s[^>]*?)href\s*=\s*"data:[^"]*\"""",
        re.IGNORECASE,
    )

    def _markdown_filter(text: str | None) -> str:
        if not text:
            return ""
        raw_html = mistune.html(str(text))
        cleaned = nh3.clean(
            raw_html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            url_schemes=_URL_SCHEMES,
        )
        return _DATA_HREF_RE.sub(r'\1href="#"', cleaned)

    return _markdown_filter


def _strip_data_uris_filter(value: str | None) -> str:
    """Jinja filter: return empty string for ``data:`` URIs, pass through otherwise."""
    if value and isinstance(value, str) and value.startswith("data:"):
        return ""
    return value or ""


def mount_ssr_routes(
    app: Any,
    templates_dir: Path,
    ssr_config: dict[str, Any],
    collections_config: dict[str, Any] | None = None,
    base_path: str = "",
    asset_registry: Any | None = None,
) -> None:
    """Register SSR routes on a FastAPI app from manifest config.

    Args:
        app: FastAPI application instance.
        templates_dir: Path to the Jinja2 templates directory.
        ssr_config: The ``ssr`` section of the manifest.
        collections_config: The ``collections`` section (for scopes, policy,
            relations, computed, projection resolution).
        base_path: URL path prefix for the app (e.g. ``/tech-blog``).
            Injected into templates as ``{{ base_path }}`` so links work
            correctly in multi-app deployments.  Empty string for single-app.
        asset_registry: Optional ``AssetRegistry`` for ``{{ asset_url() }}``
            cache-busting in templates.
    """
    from jinja2 import ChoiceLoader, Environment, FileSystemLoader

    collections_config = collections_config or {}
    routes = ssr_config.get("routes", {})
    site_name = ssr_config.get("site_name", "")
    site_description = ssr_config.get("site_description", "")
    base_url = ssr_config.get("base_url", "")
    global_preload: list[dict[str, Any]] = ssr_config.get("preload", [])

    loaders = [FileSystemLoader(str(templates_dir))]
    if Path(_FW_TEMPLATES_DIR).exists():
        loaders.append(FileSystemLoader(_FW_TEMPLATES_DIR))

    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=True,
    )
    env.globals["base_path"] = base_path
    env.globals["lang"] = ssr_config.get("lang", "en")

    md_filter = _make_markdown_filter()
    if md_filter is not None:
        env.filters["markdown"] = md_filter
    env.filters["strip_data_uris"] = _strip_data_uris_filter

    if asset_registry is not None:
        env.globals["asset_url"] = asset_registry.asset_url

    error_handler = _ErrorPageHandler(env)
    router = APIRouter(include_in_schema=False)

    robots_config = ssr_config.get("robots")
    if robots_config:
        _register_robots_route(router, robots_config, base_url)
        logger.info("Registered /robots.txt")

    if ssr_config.get("sitemap", True) and routes:
        _register_sitemap_route(router, ssr_config, collections_config, base_url)
        logger.info("Registered /sitemap.xml")

    feeds_config = ssr_config.get("feeds", {})
    feed_links: list[dict[str, str]] = []
    _seo_ctx = {"site_name": site_name, "site_description": site_description}
    for feed_path, feed_cfg in feeds_config.items():
        fmt = feed_cfg.get("format", "rss")
        mime = "application/atom+xml" if fmt == "atom" else "application/rss+xml"
        feed_links.append(
            {
                "href": f"{base_path}{feed_path}",
                "type": mime,
                "title": _resolve_seo_placeholders(
                    feed_cfg.get("title", site_name),
                    _seo_ctx,
                ),
            }
        )

    if feeds_config:
        _register_feed_routes(router, feeds_config, collections_config, base_url, site_name, site_description)
        logger.info("Registered %d feed route(s)", len(feeds_config))

    og_config = ssr_config.get("og_image_fallback")
    if og_config and og_config.get("enabled"):
        from ._og_image import register_og_image_route

        register_og_image_route(router, og_config, collections_config, base_url)
        logger.info("Registered OG image fallback route")

    redirects_config = ssr_config.get("redirects", {})
    if redirects_config:
        _register_redirect_routes(router, redirects_config)

    trailing_slash = ssr_config.get("trailing_slash", "ignore")
    if trailing_slash != "ignore":
        _register_trailing_slash_middleware(app, trailing_slash)
        logger.info("Registered trailing-slash policy: %s", trailing_slash)

    for pattern, route_config in routes.items():
        fastapi_path = _convert_route_pattern(pattern)
        template_name = route_config.get("template", "")
        data_sources = route_config.get("data", {})
        seo_config = route_config.get("seo", {})
        auth_required = route_config.get("auth", False)
        cache_config = route_config.get("cache")
        route_preload: list[dict[str, Any]] = route_config.get("preload", [])
        merged_preload = global_preload + route_preload

        if not template_name:
            logger.warning("SSR route %s has no template, skipping", pattern)
            continue

        _register_ssr_route(
            router=router,
            path=fastapi_path,
            template_name=template_name,
            data_sources=data_sources,
            seo_config=seo_config,
            site_name=site_name,
            site_description=site_description,
            jinja_env=env,
            collections_config=collections_config,
            auth_required=auth_required,
            cache_config=cache_config,
            error_handler=error_handler,
            feed_links=feed_links or None,
            preload=merged_preload or None,
        )
        logger.info("Registered SSR route: %s -> %s", pattern, template_name)

    app.include_router(router)
    logger.info("Mounted %d SSR route(s)", len(routes))


def _normalize_preload_links(preload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise preload items for template rendering."""
    links: list[dict[str, Any]] = []
    for item in preload:
        href = item.get("href", "")
        if not href:
            continue
        rel = item.get("rel", "preload")
        link: dict[str, Any] = {"href": href, "rel": rel}
        if item.get("as"):
            link["as"] = item["as"]
        if item.get("crossorigin"):
            link["crossorigin"] = True
        links.append(link)
    return links


def _build_preload_link_parts(preload: list[dict[str, Any]]) -> list[str]:
    """Build ``Link`` header parts for resource preloading / preconnect."""
    parts: list[str] = []
    for item in preload:
        href = item.get("href", "")
        if not href:
            continue
        rel = item.get("rel", "preload")
        if rel == "preload":
            as_type = item.get("as", "")
            if not as_type:
                continue
            part = f"<{href}>; rel=preload; as={as_type}"
        else:
            part = f"<{href}>; rel={rel}"
        if item.get("crossorigin"):
            part += "; crossorigin"
        parts.append(part)
    return parts


def _register_ssr_route(  # noqa: PLR0913
    router: APIRouter,
    path: str,
    template_name: str,
    data_sources: dict[str, dict[str, Any]],
    seo_config: dict[str, Any],
    site_name: str,
    site_description: str,
    jinja_env: Any,
    collections_config: dict[str, Any],
    auth_required: bool,
    cache_config: dict[str, Any] | None,
    error_handler: _ErrorPageHandler,
    feed_links: list[dict[str, str]] | None = None,
    preload: list[dict[str, Any]] | None = None,
) -> None:
    """Register a single SSR route handler."""
    from jinja2.exceptions import TemplateError as _TemplateError

    cache_header = _build_cache_header(cache_config)
    preload_link_parts = _build_preload_link_parts(preload) if preload else []
    cache_vary: list[str] = list((cache_config or {}).get("vary", []))
    if auth_required and not cache_vary:
        cache_vary = ["Cookie"]
    etag_enabled = bool((cache_config or {}).get("etag"))

    @router.get(path, response_class=HTMLResponse)
    async def ssr_handler(
        request: Request,
        db=Depends(get_scoped_db),
        user=Depends(get_current_user),
    ) -> HTMLResponse:
        if auth_required and not user:
            raise HTTPException(status_code=401, detail="Authentication required")

        path_params = dict(request.path_params)
        query_params = dict(request.query_params)
        data_context: dict[str, Any] = {}

        try:
            for name, source_config in data_sources.items():
                result = await _fetch_data_source(
                    source_config,
                    db,
                    path_params,
                    query_params,
                    user,
                    collections_config,
                )
                if isinstance(result, dict) and "items" in result:
                    data_context[name] = result["items"]
                    data_context[f"{name}_pagination"] = {
                        "total": result["total"],
                        "page": result["page"],
                        "total_pages": result["total_pages"],
                        "limit": result["limit"],
                    }
                else:
                    data_context[name] = result
        except HTTPException as exc:
            if exc.status_code == 404:
                err_page = error_handler.render_404(request)
                if err_page:
                    return err_page
            raise

        seo = _build_seo_context(seo_config, data_context, site_name)
        if not seo.get("description"):
            seo["description"] = site_description

        url_str = str(request.url)
        seo["canonical"] = url_str.split("?")[0]
        if feed_links:
            seo["feed_links"] = feed_links

        _inject_pagination_seo(seo, data_context, request)

        cache_context = {"is_stale": False, "cached_at": None}

        preload_links = _normalize_preload_links(preload) if preload else []

        try:
            template = jinja_env.get_template(template_name)
            html = template.render(
                request=request,
                seo=seo,
                user=user,
                cache=cache_context,
                preload_links=preload_links,
                **data_context,
            )
        except _TemplateError:
            logger.exception("SSR template render failed: %s", template_name)
            err_page = error_handler.render_500(request)
            if err_page:
                return err_page
            raise

        if etag_enabled:
            etag_value = _md5(html.encode()).hexdigest()  # noqa: S324
            etag_header = f'"{etag_value}"'
            if_none_match = request.headers.get("if-none-match")
            if if_none_match and if_none_match.strip() == etag_header:
                resp_304 = Response(status_code=304)
                resp_304.headers["ETag"] = etag_header
                if cache_header:
                    resp_304.headers["Cache-Control"] = cache_header
                return resp_304

        resp = HTMLResponse(content=html)
        resp.headers["X-Cache-Status"] = "MISS"
        if cache_header:
            resp.headers["Cache-Control"] = cache_header
            resp.headers["X-Cache-Age"] = "0"
        if etag_enabled:
            resp.headers["ETag"] = etag_header
        if cache_vary:
            resp.headers["Vary"] = ", ".join(cache_vary)
        link_parts: list[str] = list(preload_link_parts)
        if seo.get("pagination_prev"):
            link_parts.append(f'<{seo["pagination_prev"]}>; rel="prev"')
        if seo.get("pagination_next"):
            link_parts.append(f'<{seo["pagination_next"]}>; rel="next"')
        if link_parts:
            resp.headers["Link"] = ", ".join(link_parts)
        return resp
