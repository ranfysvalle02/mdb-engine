"""
Auto-CRUD router factory.

Generates RESTful CRUD endpoints for MongoDB collections defined in the
manifest ``collections`` key.  All operations go through
``ScopedCollectionWrapper``, so ``app_id`` scoping is enforced automatically.

MQL-as-DSL: collection config supports five declarative primitives
(``policy``, ``scopes``, ``pipelines``, ``defaults``, ``default_projection``)
whose values are native MongoDB Query Language expressions resolved at
runtime via ``{{user.*}}`` template placeholders.

Usage (manifest-driven — no user code):

    # manifest.json
    {
      "collections": {
        "tasks": {
          "schema": { "type": "object", "properties": { "title": { "type": "string" } } },
          "policy": { "read": { "team_id": "{{user.team_id}}" } },
          "scopes": { "active": { "status": { "$ne": "archived" } } },
          "pipelines": {
            "by_status": [
              { "$group": { "_id": "$status", "count": { "$sum": 1 } } }
            ]
          },
          "defaults": { "status": "pending" },
          "default_projection": { "internal_notes": 0 }
        }
      }
    }

Usage (programmatic):

    from mdb_engine.routing.auto_crud import create_auto_crud_router
    router = create_auto_crud_router("tasks", {"schema": {...}})
    app.include_router(router)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_scoped_db, require_role, require_user
from ._serialization import serialize_doc
from .query_parser import parse_query_params
from .template_resolver import merge_filters, resolve_template

logger = logging.getLogger(__name__)

BULK_INSERT_MAX = 1000


# ── Helpers ──────────────────────────────────────────────────────────────

_serialize_doc = serialize_doc


def _to_object_id(raw: str) -> ObjectId:
    """Convert a string to ObjectId, raising 400 on invalid format."""
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ID format: {raw}") from exc


def _validate_document(body: dict[str, Any], schema: dict[str, Any] | None, *, partial: bool = False) -> None:
    """Validate a document against the collection's JSON Schema."""
    if schema is None:
        return
    try:
        import jsonschema

        if partial:
            schema_copy = {**schema}
            schema_copy.pop("required", None)
            jsonschema.validate(instance=body, schema=schema_copy)
        else:
            jsonschema.validate(instance=body, schema=schema)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Validation error: {exc.message}") from exc


def _stamp_create(body: dict[str, Any]) -> None:
    """Inject ``created_at`` and ``updated_at`` for a new document."""
    now = datetime.now(timezone.utc)
    body.setdefault("created_at", now)
    body.setdefault("updated_at", now)


def _stamp_update(body: dict[str, Any]) -> None:
    """Force-set ``updated_at`` for a mutation."""
    body["updated_at"] = datetime.now(timezone.utc)


def _apply_soft_delete_filter(query_filter: dict[str, Any] | None) -> dict[str, Any]:
    """Merge ``{"deleted_at": None}`` into an existing filter."""
    base: dict[str, Any] = {"deleted_at": None}
    if not query_filter:
        return base
    return {"$and": [query_filter, base]}


def _get_user_from_request(request: Request) -> dict[str, Any] | None:
    """Read the user dict from request state (set by auth middleware)."""
    return getattr(request.state, "user", None)


# ── Collection context (holds parsed manifest config) ────────────────────


@dataclass
class _CollectionCtx:
    """Immutable bundle of parsed collection config passed to route builders."""

    name: str
    schema: dict[str, Any] | None = None
    read_only: bool = False
    timestamps: bool = True
    soft_delete: bool = False
    bulk_insert: bool = True
    policy: dict[str, Any] = field(default_factory=dict)
    scopes_config: dict[str, Any] = field(default_factory=dict)
    pipelines_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    defaults_config: dict[str, Any] = field(default_factory=dict)
    default_projection: dict[str, int] | None = None

    # ── filter helpers ────────────────────────────────────────────────

    def resolve_scopes(self, scope_names: list[str] | None, user: dict[str, Any] | None) -> dict[str, Any] | None:
        if not scope_names or not self.scopes_config:
            return None
        resolved: list[dict[str, Any]] = []
        for name in scope_names:
            scope_filter = self.scopes_config.get(name)
            if scope_filter is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown scope: '{name}'. Available: {list(self.scopes_config.keys())}",
                )
            resolved.append(resolve_template(scope_filter, user))
        return merge_filters(*resolved)

    def resolve_policy(self, operation: str, user: dict[str, Any] | None) -> dict[str, Any] | None:
        policy_filter = self.policy.get(operation)
        if policy_filter is None:
            if operation == "delete":
                policy_filter = self.policy.get("write")
            if policy_filter is None:
                return None
        return resolve_template(policy_filter, user)

    def read_filter(
        self,
        parsed_filter: dict[str, Any] | None,
        user: dict[str, Any] | None,
        scope_names: list[str] | None = None,
    ) -> dict[str, Any] | None:
        scope_filter = self.resolve_scopes(scope_names, user)
        policy_filter = self.resolve_policy("read", user)
        combined = merge_filters(parsed_filter, scope_filter, policy_filter)
        if self.soft_delete:
            return _apply_soft_delete_filter(combined)
        return combined

    def write_filter(self, oid: ObjectId, operation: str, user: dict[str, Any] | None) -> dict[str, Any]:
        base: dict[str, Any] = {"_id": oid}
        if self.soft_delete:
            base["deleted_at"] = None
        policy_filter = self.resolve_policy(operation, user)
        return merge_filters(base, policy_filter) or base

    def apply_defaults(self, body: dict[str, Any], user: dict[str, Any] | None) -> None:
        if not self.defaults_config:
            return
        resolved = resolve_template(self.defaults_config, user)
        for key, value in resolved.items():
            body.setdefault(key, value)

    def effective_projection(self, parsed_projection: dict[str, int] | None) -> dict[str, int] | None:
        if parsed_projection is not None:
            return parsed_projection
        return self.default_projection


# ── Route registration helpers ───────────────────────────────────────────


def _register_read_routes(router: APIRouter, ctx: _CollectionCtx) -> None:
    """Register GET endpoints (list, count, trash, get-by-id)."""

    @router.get("", summary=f"List {ctx.name}")
    async def list_documents(request: Request, db=Depends(get_scoped_db)):
        params = dict(request.query_params)
        parsed = parse_query_params(params)
        user = _get_user_from_request(request)
        collection = db[ctx.name]

        effective_filter = ctx.read_filter(parsed.filter, user, parsed.scope)
        projection = ctx.effective_projection(parsed.projection)
        cursor = collection.find(effective_filter, projection)

        if parsed.sort:
            cursor = cursor.sort(parsed.sort)

        cursor = cursor.skip(parsed.skip).limit(parsed.limit)
        docs = await cursor.to_list(length=parsed.limit)
        total = await collection.count_documents(effective_filter or {})

        return {
            "data": [_serialize_doc(d) for d in docs],
            "total": total,
            "skip": parsed.skip,
            "limit": parsed.limit,
        }

    @router.get("/_count", summary=f"Count {ctx.name}")
    async def count_documents(request: Request, db=Depends(get_scoped_db)):
        params = dict(request.query_params)
        parsed = parse_query_params(params)
        user = _get_user_from_request(request)
        effective_filter = ctx.read_filter(parsed.filter, user, parsed.scope)
        total = await db[ctx.name].count_documents(effective_filter or {})
        return {"count": total}

    if ctx.soft_delete:

        @router.get("/_trash", summary=f"List deleted {ctx.name}")
        async def list_trash(request: Request, db=Depends(get_scoped_db)):
            params = dict(request.query_params)
            parsed = parse_query_params(params)
            user = _get_user_from_request(request)

            scope_filter = ctx.resolve_scopes(parsed.scope, user)
            policy_filter = ctx.resolve_policy("read", user)
            trash_filter: dict[str, Any] = {"deleted_at": {"$ne": None}}
            combined = merge_filters(parsed.filter, scope_filter, policy_filter, trash_filter) or trash_filter

            collection = db[ctx.name]
            projection = ctx.effective_projection(parsed.projection)
            cursor = collection.find(combined, projection)
            if parsed.sort:
                cursor = cursor.sort(parsed.sort)
            cursor = cursor.skip(parsed.skip).limit(parsed.limit)
            docs = await cursor.to_list(length=parsed.limit)
            total = await collection.count_documents(combined)
            return {
                "data": [_serialize_doc(d) for d in docs],
                "total": total,
                "skip": parsed.skip,
                "limit": parsed.limit,
            }

    @router.get("/{document_id}", summary=f"Get {ctx.name} by ID")
    async def get_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        oid = _to_object_id(document_id)
        user = _get_user_from_request(request)
        collection = db[ctx.name]
        q = ctx.write_filter(oid, "read", user)
        projection = ctx.effective_projection(None)
        doc = await collection.find_one(q, projection)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"data": _serialize_doc(doc)}


def _register_pipeline_routes(router: APIRouter, ctx: _CollectionCtx) -> None:
    """Register GET /_agg/{name} endpoints for each named pipeline."""
    for pipeline_name, pipeline_stages in ctx.pipelines_config.items():

        @router.get(f"/_agg/{pipeline_name}", summary=f"Aggregate {ctx.name}: {pipeline_name}")
        async def run_pipeline(
            request: Request,
            db=Depends(get_scoped_db),
            _stages: list[dict[str, Any]] = pipeline_stages,
            _name: str = pipeline_name,
        ):
            user = _get_user_from_request(request)
            resolved_stages = resolve_template(_stages, user)
            collection = db[ctx.name]
            result = collection.aggregate(resolved_stages)
            if hasattr(result, "__await__"):
                result = await result
            results = await result.to_list(length=None)
            return {"data": [_serialize_doc(d) for d in results]}


def _register_write_routes(router: APIRouter, ctx: _CollectionCtx) -> None:
    """Register POST/PUT/PATCH/DELETE endpoints."""

    @router.post("", status_code=201, summary=f"Create {ctx.name}")
    async def create_document(request: Request, db=Depends(get_scoped_db)):
        body = await request.json()
        _validate_document(body, ctx.schema)
        body.pop("_id", None)
        user = _get_user_from_request(request)
        ctx.apply_defaults(body, user)
        if ctx.timestamps:
            _stamp_create(body)
        collection = db[ctx.name]
        result = await collection.insert_one(body)
        return {"data": {"_id": str(result.inserted_id)}}

    if ctx.bulk_insert:

        @router.post("/_bulk", status_code=201, summary=f"Bulk create {ctx.name}")
        async def bulk_create(request: Request, db=Depends(get_scoped_db)):
            body = await request.json()
            if not isinstance(body, list):
                raise HTTPException(status_code=400, detail="Body must be a JSON array of documents")
            if len(body) > BULK_INSERT_MAX:
                raise HTTPException(
                    status_code=400,
                    detail=f"Bulk insert limited to {BULK_INSERT_MAX} documents per request",
                )
            user = _get_user_from_request(request)
            for doc in body:
                if not isinstance(doc, dict):
                    raise HTTPException(status_code=400, detail="Each item must be a JSON object")
                _validate_document(doc, ctx.schema)
                doc.pop("_id", None)
                ctx.apply_defaults(doc, user)
                if ctx.timestamps:
                    _stamp_create(doc)
            collection = db[ctx.name]
            result = await collection.insert_many(body)
            return {"data": {"inserted": len(result.inserted_ids)}}

    @router.put("/{document_id}", summary=f"Replace {ctx.name}")
    async def replace_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        oid = _to_object_id(document_id)
        body = await request.json()
        _validate_document(body, ctx.schema)
        body.pop("_id", None)
        if ctx.timestamps:
            _stamp_update(body)
        user = _get_user_from_request(request)
        collection = db[ctx.name]
        q = ctx.write_filter(oid, "write", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        result = await collection.update_one({"_id": oid}, {"$set": body})
        return {"data": {"_id": document_id, "modified": result.modified_count}}

    @router.patch("/{document_id}", summary=f"Update {ctx.name}")
    async def patch_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        oid = _to_object_id(document_id)
        body = await request.json()
        _validate_document(body, ctx.schema, partial=True)
        body.pop("_id", None)
        if ctx.timestamps:
            _stamp_update(body)
        user = _get_user_from_request(request)
        collection = db[ctx.name]
        q = ctx.write_filter(oid, "write", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        result = await collection.update_one({"_id": oid}, {"$set": body})
        return {"data": {"_id": document_id, "modified": result.modified_count}}

    @router.delete("/{document_id}", summary=f"Delete {ctx.name}")
    async def delete_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        oid = _to_object_id(document_id)
        user = _get_user_from_request(request)
        collection = db[ctx.name]

        if ctx.soft_delete:
            q = ctx.write_filter(oid, "delete", user)
            doc = await collection.find_one(q)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            now = datetime.now(timezone.utc)
            await collection.update_one({"_id": oid}, {"$set": {"deleted_at": now}})
            return {"data": {"_id": document_id, "deleted": True}}

        q = ctx.write_filter(oid, "delete", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        result = await collection.delete_one({"_id": oid})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"data": {"_id": document_id, "deleted": True}}

    if ctx.soft_delete:

        @router.post("/{document_id}/_restore", summary=f"Restore {ctx.name}")
        async def restore_document(document_id: str, db=Depends(get_scoped_db)):
            oid = _to_object_id(document_id)
            collection = db[ctx.name]
            doc = await collection.find_one({"_id": oid, "deleted_at": {"$ne": None}})
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found or not deleted")
            await collection.update_one({"_id": oid}, {"$set": {"deleted_at": None}})
            return {"data": {"_id": document_id, "restored": True}}


# ── Router factory ───────────────────────────────────────────────────────


def create_auto_crud_router(
    collection_name: str,
    config: dict[str, Any] | None = None,
    *,
    tags: list[str] | None = None,
) -> APIRouter:
    """Create a FastAPI router with CRUD endpoints for a single collection.

    Args:
        collection_name: MongoDB collection base name (without app prefix).
        config: Collection config from the manifest ``collections`` dict.
            Supported keys: ``auto_crud``, ``schema``, ``read_only``,
            ``timestamps``, ``soft_delete``, ``bulk_insert``, ``auth``,
            ``realtime``, ``policy``, ``scopes``, ``pipelines``,
            ``defaults``, ``default_projection``.
        tags: Optional OpenAPI tags for the generated endpoints.

    Returns:
        A ``fastapi.APIRouter`` ready to be included in an app.
    """
    config = config or {}
    route_tags = tags or [collection_name]
    prefix = f"/api/{collection_name}"

    ctx = _CollectionCtx(
        name=collection_name,
        schema=config.get("schema"),
        read_only=config.get("read_only", False),
        timestamps=config.get("timestamps", True),
        soft_delete=config.get("soft_delete", False),
        bulk_insert=config.get("bulk_insert", True),
        policy=config.get("policy", {}),
        scopes_config=config.get("scopes", {}),
        pipelines_config=config.get("pipelines", {}),
        defaults_config=config.get("defaults", {}),
        default_projection=config.get("default_projection"),
    )

    auth_config = config.get("auth", {})
    router_dependencies: list[Any] = []
    if auth_config.get("roles"):
        router_dependencies.append(Depends(require_role(*auth_config["roles"])))
    elif auth_config.get("required"):
        router_dependencies.append(Depends(require_user()))

    router = APIRouter(prefix=prefix, tags=route_tags, dependencies=router_dependencies)

    _register_read_routes(router, ctx)
    _register_pipeline_routes(router, ctx)
    if not ctx.read_only:
        _register_write_routes(router, ctx)

    return router


def mount_auto_crud_routes(
    app: Any,
    collections: dict[str, dict[str, Any]],
) -> None:
    """Mount auto-CRUD routers for all eligible collections onto an app.

    Iterates the ``collections`` dict from the manifest and includes
    a router for each collection where ``auto_crud`` is enabled
    (defaults to ``True`` when omitted).

    Args:
        app: FastAPI application instance.
        collections: The ``collections`` section of the manifest.
    """
    for name, config in collections.items():
        if not config.get("auto_crud", True):
            logger.info(f"Skipping auto-CRUD for collection '{name}' (auto_crud=false)")
            continue
        router = create_auto_crud_router(name, config)
        app.include_router(router)
        logger.info(f"Mounted auto-CRUD routes for collection '{name}' at /api/{name}")
