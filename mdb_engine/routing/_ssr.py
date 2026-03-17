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
from pathlib import Path
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from ..dependencies import get_current_user, get_scoped_db
from ._serialization import serialize_doc
from .template_resolver import merge_filters, resolve_template

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")
_FW_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")


# ── SEO helpers ──────────────────────────────────────────────────────────


def _resolve_seo_placeholders(template: str, context: dict[str, Any]) -> str:
    """Resolve ``{{post.title}}`` style placeholders in SEO strings."""

    def _replace(match: re.Match) -> str:
        path = match.group(1)
        parts = path.split(".")
        current: Any = context
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return match.group(0)
        return str(current) if current is not None else ""

    return _PLACEHOLDER_RE.sub(_replace, template)


def _build_seo_context(
    seo_config: dict[str, Any],
    data_context: dict[str, Any],
    site_name: str,
) -> dict[str, Any]:
    """Build resolved SEO metadata from config + fetched data."""
    seo: dict[str, Any] = {"site_name": site_name}

    for key, template in seo_config.items():
        if key == "json_ld":
            continue
        if isinstance(template, str):
            seo[key] = _resolve_seo_placeholders(template, data_context)

    json_ld_config = seo_config.get("json_ld")
    if json_ld_config:
        resolved_ld = _resolve_json_ld(json_ld_config, data_context)
        seo["json_ld"] = json.dumps(resolved_ld, default=str)

    return seo


def _resolve_json_ld(config: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve placeholders in a JSON-LD config object."""
    if isinstance(config, str):
        return _resolve_seo_placeholders(config, context)
    if isinstance(config, dict):
        return {k: _resolve_json_ld(v, context) for k, v in config.items()}
    if isinstance(config, list):
        return [_resolve_json_ld(item, context) for item in config]
    return config


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
    parts = [f"max-age={seconds}"]
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
    """Fetch a single document by ID with optional populate/computed."""
    doc_id = path_params.get(id_param)
    if not doc_id:
        raise HTTPException(status_code=404, detail="Not found")
    oid = _to_object_id(doc_id)

    filter_spec: dict[str, Any] = {"_id": oid}
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


def _register_sitemap_route(
    router: APIRouter,
    ssr_config: dict[str, Any],
    collections_config: dict[str, Any],
    base_url: str,
) -> None:
    """Register a ``/sitemap.xml`` route that lists all SSR-eligible URLs."""
    routes = ssr_config.get("routes", {})

    @router.get("/sitemap.xml", response_class=Response, include_in_schema=False)
    async def sitemap(request: Request, db=Depends(get_scoped_db)) -> Response:
        host = base_url or str(request.base_url).rstrip("/")
        urls: list[str] = []

        for pattern, route_config in routes.items():
            if route_config.get("auth"):
                continue

            if "{" not in pattern and ":" not in pattern:
                urls.append(f"  <url><loc>{host}{pattern}</loc></url>")
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

                    cursor = col.find(filter_spec or {}, {"_id": 1})
                    docs = await cursor.to_list(length=10000)
                    param = source["id_param"]
                    for doc in docs:
                        doc_id = str(doc["_id"])
                        url_path = _convert_route_pattern(pattern).replace(f"{{{param}}}", doc_id)
                        urls.append(f"  <url><loc>{host}{url_path}</loc></url>")
                    break

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>"
        )
        return Response(content=xml, media_type="application/xml")


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


# ── Main mount function ──────────────────────────────────────────────────


def mount_ssr_routes(
    app: Any,
    templates_dir: Path,
    ssr_config: dict[str, Any],
    collections_config: dict[str, Any] | None = None,
) -> None:
    """Register SSR routes on a FastAPI app from manifest config.

    Args:
        app: FastAPI application instance.
        templates_dir: Path to the Jinja2 templates directory.
        ssr_config: The ``ssr`` section of the manifest.
        collections_config: The ``collections`` section (for scopes, policy,
            relations, computed, projection resolution).
    """
    from jinja2 import ChoiceLoader, Environment, FileSystemLoader

    collections_config = collections_config or {}
    routes = ssr_config.get("routes", {})
    site_name = ssr_config.get("site_name", "")
    site_description = ssr_config.get("site_description", "")
    base_url = ssr_config.get("base_url", "")

    loaders = [FileSystemLoader(str(templates_dir))]
    if Path(_FW_TEMPLATES_DIR).exists():
        loaders.append(FileSystemLoader(_FW_TEMPLATES_DIR))

    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=True,
    )

    error_handler = _ErrorPageHandler(env)
    router = APIRouter(include_in_schema=False)

    if ssr_config.get("sitemap", True) and routes:
        _register_sitemap_route(router, ssr_config, collections_config, base_url)
        logger.info("Registered /sitemap.xml")

    for pattern, route_config in routes.items():
        fastapi_path = _convert_route_pattern(pattern)
        template_name = route_config.get("template", "")
        data_sources = route_config.get("data", {})
        seo_config = route_config.get("seo", {})
        auth_required = route_config.get("auth", False)
        cache_config = route_config.get("cache")

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
        )
        logger.info("Registered SSR route: %s -> %s", pattern, template_name)

    app.include_router(router)
    logger.info("Mounted %d SSR route(s)", len(routes))


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
) -> None:
    """Register a single SSR route handler."""
    from jinja2.exceptions import TemplateError as _TemplateError

    cache_header = _build_cache_header(cache_config)

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

        try:
            template = jinja_env.get_template(template_name)
            html = template.render(
                request=request,
                seo=seo,
                user=user,
                **data_context,
            )
        except _TemplateError:
            logger.exception("SSR template render failed: %s", template_name)
            err_page = error_handler.render_500(request)
            if err_page:
                return err_page
            raise

        resp = HTMLResponse(content=html)
        if cache_header:
            resp.headers["Cache-Control"] = cache_header
        return resp
