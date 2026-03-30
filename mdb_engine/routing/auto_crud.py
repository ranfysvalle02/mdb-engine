"""
Auto-CRUD router factory.

Generates RESTful CRUD endpoints for MongoDB collections defined in the
manifest ``collections`` key.  All operations go through
``ScopedCollectionWrapper``, so ``app_id`` scoping is enforced automatically.

MQL-as-DSL: collection config supports declarative primitives
(``policy``, ``scopes``, ``pipelines``, ``defaults``, ``default_projection``,
``owner_field``, ``immutable_fields``, ``hooks``, ``relations``, ``computed``)
whose values are native MongoDB Query Language expressions resolved at
runtime via ``{{user.*}}`` / ``{{doc.*}}`` template placeholders.

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
          "default_projection": { "internal_notes": 0 },
          "owner_field": "created_by",
          "immutable_fields": ["created_by"],
          "hooks": {
            "after_create": [
              { "action": "insert", "collection": "audit_log", "document": {
                "event": "task_created", "entity_id": "{{doc._id}}", "timestamp": "$$NOW"
              }}
            ]
          },
          "relations": {
            "assignee": { "from": "users", "local_field": "assignee_id", "foreign_field": "_id", "single": true }
          },
          "computed": {
            "comment_count": {
              "pipeline": [
                { "$lookup": { "from": "comments", "localField": "_id", "foreignField": "task_id", "as": "_c" } },
                { "$addFields": { "comment_count": { "$size": "$_c" } } },
                { "$project": { "_c": 0 } }
              ]
            }
          }
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
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..dependencies import get_scoped_db, require_collection_permission, require_role, require_user
from ._hooks import BackgroundHookExecutor, HookExecutor
from ._rate_limit import create_collection_rate_limit_dependency
from ._serialization import serialize_doc
from ._validators import validate_schema_extensions
from .query_parser import parse_query_params
from .template_resolver import merge_filters, resolve_template

logger = logging.getLogger(__name__)

BULK_INSERT_MAX = 1000
MAX_BODY_BYTES_DEFAULT = 1_048_576  # 1 MB

_PROTECTED_FIELDS = frozenset(
    {
        "role",
        "roles",
        "password",
        "password_hash",
        "is_admin",
    }
)

_SENSITIVE_READ_FIELDS = frozenset(
    {
        "password",
        "password_hash",
    }
)


# ── Helpers ──────────────────────────────────────────────────────────────

_serialize_doc = serialize_doc


async def _enforce_body_limit(request: Request, max_bytes: int) -> None:
    """Reject requests whose body exceeds *max_bytes*."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (limit: {max_bytes} bytes)",
        )
    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (limit: {max_bytes} bytes)",
        )


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


def _user_is_admin(user: dict[str, Any] | None) -> bool:
    """Check if the user has the admin role."""
    if not user:
        return False
    role = str(user.get("role", ""))
    roles = set(user.get("roles", []))
    roles.add(role)
    return "admin" in roles


def _handle_duplicate_key(exc: DuplicateKeyError, collection_name: str) -> None:
    """Convert a DuplicateKeyError into a 409 HTTPException."""
    detail = f"Duplicate value in '{collection_name}'"
    key_pattern = getattr(exc, "details", {}).get("keyPattern", {})
    if key_pattern:
        fields = ", ".join(key_pattern.keys())
        detail = f"Duplicate value for unique field(s): {fields}"
    raise HTTPException(status_code=409, detail=detail) from exc


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
    max_body_bytes: int = MAX_BODY_BYTES_DEFAULT
    policy: dict[str, Any] = field(default_factory=dict)
    scopes_config: dict[str, Any] = field(default_factory=dict)
    pipelines_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    defaults_config: dict[str, Any] = field(default_factory=dict)
    default_projection: dict[str, int] | None = None
    owner_field: str | None = None
    immutable_fields: list[str] = field(default_factory=list)
    writable_fields: list[str] | dict[str, list[str]] | None = None
    hooks_config: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    relations_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    computed_config: dict[str, Any] = field(default_factory=dict)
    has_unique_fields: bool = False
    cascade_config: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    cache_config: dict[str, Any] = field(default_factory=dict)

    # ── filter helpers ────────────────────────────────────────────────

    def resolve_scopes(
        self,
        scope_names: list[str] | None,
        user: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Resolve named scopes into a merged MQL filter.

        Scopes support two formats:

        * **Plain filter:** ``{"status": "published"}``
        * **Extended:** ``{"filter": {"approved": false}, "auth": {"roles": ["admin"]}}``

        When a scope carries ``auth.roles``, the requesting user must have
        at least one of those roles or a 403 is raised.  ``auth.required``
        enforces authentication without checking a specific role.
        """
        if not scope_names or not self.scopes_config:
            return None
        resolved: list[dict[str, Any]] = []
        for name in scope_names:
            raw = self.scopes_config.get(name)
            if raw is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown scope: '{name}'. Available: {list(self.scopes_config.keys())}",
                )

            if isinstance(raw, dict) and "filter" in raw:
                scope_filter = raw["filter"]
                scope_auth = raw.get("auth", {})
            else:
                scope_filter = raw
                scope_auth = {}

            if scope_auth:
                required_roles = scope_auth.get("roles")
                if required_roles:
                    user_roles = set()
                    if user:
                        user_roles = {str(user.get("role", ""))}
                        user_roles |= set(user.get("roles", []))
                    if not any(r in user_roles for r in required_roles):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Required role: {' or '.join(required_roles)}",
                        )
                elif scope_auth.get("required") and not user:
                    raise HTTPException(status_code=401, detail="Authentication required")

            resolved.append(resolve_template(scope_filter, user))
        return merge_filters(*resolved)

    def resolve_policy(self, operation: str, user: dict[str, Any] | None) -> dict[str, Any] | None:
        policy_filter = self.policy.get(operation)
        if policy_filter is None:
            if operation == "delete":
                policy_filter = self.policy.get("write")
            if policy_filter is None:
                return None
        if self.owner_field and _user_is_admin(user):
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

    def sanitize_body(self, body: dict[str, Any], user: dict[str, Any] | None = None) -> None:
        """Strip disallowed fields from a create/update body.

        Enforces two layers:
        1. ``writable_fields`` allowlist — if set, only these fields survive.
           Accepts a flat list (all authenticated users) or a role map
           (``{"editor": ["title", "body"], "reader": ["body"]}``).
           Admin users bypass the allowlist when a role map is used.
        2. ``immutable_fields`` denylist — always stripped (includes
           ``_PROTECTED_FIELDS`` when app auth is enabled).
        """
        if self.writable_fields is not None:
            if isinstance(self.writable_fields, dict):
                if user and _user_is_admin(user):
                    allowed = None
                else:
                    role = user.get("role", "") if user else ""
                    roles = set(user.get("roles", [])) if user else set()
                    if role:
                        roles.add(role)
                    allowed: set[str] | None = set()
                    for r in roles:
                        allowed |= set(self.writable_fields.get(r, []))
            else:
                allowed = set(self.writable_fields)

            if allowed is not None:
                for key in list(body.keys()):
                    if key not in allowed:
                        body.pop(key)

        for f in self.immutable_fields:
            body.pop(f, None)

        # Strip any $-prefixed keys to prevent operator injection via
        # document field names (prevents storing operator-shaped keys).
        for key in list(body.keys()):
            if key.startswith("$"):
                body.pop(key)

    def effective_projection(self, parsed_projection: dict[str, int] | None) -> dict[str, int] | None:
        if parsed_projection is not None:
            return parsed_projection
        return self.default_projection

    def cache_control_header(self, scope: str | None = None) -> str | None:
        """Compute the Cache-Control header value for a read request."""
        if not self.cache_config:
            return None
        scope_key = f"scope:{scope}" if scope else "default"
        directive = self.cache_config.get(scope_key) or self.cache_config.get("default")
        if not directive:
            return None
        ttl = directive.get("ttl", "0")
        if ttl == "0":
            return None
        seconds = _parse_cache_ttl(ttl)
        if seconds <= 0:
            return None
        parts = [f"max-age={seconds}"]
        swr = directive.get("stale_while_revalidate")
        if swr:
            swr_secs = _parse_cache_ttl(swr)
            if swr_secs > 0:
                parts.append(f"stale-while-revalidate={swr_secs}")
        return ", ".join(parts)


def _parse_cache_ttl(value: str) -> int:
    """Parse a cache TTL string like '5m', '30s', '1h' into seconds."""
    if not value:
        return 0
    unit = value[-1].lower()
    try:
        num = int(value[:-1])
    except (ValueError, IndexError):
        return 0
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return num * multipliers.get(unit, 1)


# ── Route registration helpers ───────────────────────────────────────────


def _register_read_routes(
    router: APIRouter,
    ctx: _CollectionCtx,
    read_dependencies: list[Any] | None = None,
) -> None:
    """Register GET endpoints (list, count, trash, get-by-id)."""
    _r_deps = read_dependencies or []

    @router.get("", summary=f"List {ctx.name}", dependencies=_r_deps)
    async def list_documents(request: Request, response: Response, db=Depends(get_scoped_db)):
        params = dict(request.query_params)
        parsed = parse_query_params(params)
        user = _get_user_from_request(request)
        collection = db[ctx.name]

        effective_filter = ctx.read_filter(parsed.filter, user, parsed.scope)

        scope_name = parsed.scope[0] if parsed.scope else None
        cc = ctx.cache_control_header(scope_name)
        if cc:
            response.headers["Cache-Control"] = cc

        use_agg = bool(parsed.populate or parsed.computed)

        if use_agg:
            docs, total = await _aggregated_list(
                ctx,
                collection,
                effective_filter,
                parsed,
                user,
                db,
            )
        else:
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

    @router.get("/_count", summary=f"Count {ctx.name}", dependencies=_r_deps)
    async def count_documents(request: Request, db=Depends(get_scoped_db)):
        params = dict(request.query_params)
        parsed = parse_query_params(params)
        user = _get_user_from_request(request)
        effective_filter = ctx.read_filter(parsed.filter, user, parsed.scope)
        total = await db[ctx.name].count_documents(effective_filter or {})
        return {"count": total}

    if ctx.soft_delete:

        @router.get("/_trash", summary=f"List deleted {ctx.name}", dependencies=_r_deps)
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

    @router.get("/{document_id}", summary=f"Get {ctx.name} by ID", dependencies=_r_deps)
    async def get_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        oid = _to_object_id(document_id)
        user = _get_user_from_request(request)
        params = dict(request.query_params)
        parsed = parse_query_params(params)
        collection = db[ctx.name]

        use_agg = bool(parsed.populate or parsed.computed)

        if use_agg:
            q = ctx.write_filter(oid, "read", user)
            pipeline: list[dict[str, Any]] = [{"$match": q}]
            _inject_populate_stages(pipeline, parsed.populate, ctx, db=db)
            _inject_computed_stages(pipeline, parsed.computed, ctx, db=db)
            result = collection.aggregate(pipeline)
            if hasattr(result, "__await__"):
                result = await result
            docs = await result.to_list(length=1)
            if not docs:
                raise HTTPException(status_code=404, detail="Document not found")
            return {"data": _serialize_doc(docs[0])}

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


def _register_bulk_insert_route(
    router: APIRouter,
    ctx: _CollectionCtx,
    create_deps: list[Any],
    hook_exec: HookExecutor | None,
) -> None:
    """Register POST /_bulk endpoint for bulk document creation."""

    @router.post("/_bulk", status_code=201, summary=f"Bulk create {ctx.name}", dependencies=create_deps)
    async def bulk_create(request: Request, db=Depends(get_scoped_db)):
        await _enforce_body_limit(request, ctx.max_body_bytes)
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
            ctx.sanitize_body(doc, user)
            _validate_document(doc, ctx.schema)
            doc.pop("_id", None)
            ctx.apply_defaults(doc, user)
            if ctx.timestamps:
                _stamp_create(doc)
        collection = db[ctx.name]
        if hook_exec:
            for doc in body:
                await hook_exec.run_before("before_create", doc, user, db)
        try:
            result = await collection.insert_many(body)
        except DuplicateKeyError as exc:
            _handle_duplicate_key(exc, ctx.name)
        if hook_exec:
            for doc, inserted_id in zip(body, result.inserted_ids, strict=False):
                doc_out = {**doc, "_id": str(inserted_id)}
                await hook_exec.run("after_create", doc_out, user, db)
        return {"data": {"inserted": len(result.inserted_ids)}}


async def _execute_cascade(
    cascade_rules: list[dict[str, Any]],
    doc: dict[str, Any],
    db: Any,
    *,
    soft: bool = False,
) -> None:
    """Execute cascade rules after a delete/soft-delete."""
    for rule in cascade_rules:
        try:
            target = rule.get("collection", "")
            match_field = rule.get("match_field", "")
            action = rule.get("action", "delete")
            if not target or not match_field:
                continue
            doc_id = str(doc.get("_id", ""))
            target_col = db[target]
            filt = {match_field: doc_id}
            if action == "soft_delete" or soft:
                now = datetime.now(timezone.utc)
                await target_col.update_many(filt, {"$set": {"deleted_at": now}})
            else:
                await target_col.delete_many(filt)
        except (PyMongoError, OSError, RuntimeError):
            logger.exception("Cascade rule failed (target=%s)", rule.get("collection", "?"))


def _register_delete_routes(
    router: APIRouter,
    ctx: _CollectionCtx,
    mutate_deps: list[Any],
    hook_exec: HookExecutor | None,
) -> None:
    """Register DELETE and optional soft-delete _restore endpoints."""

    @router.delete("/{document_id}", summary=f"Delete {ctx.name}", dependencies=mutate_deps)
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
            if ctx.cascade_config.get("on_soft_delete"):
                await _execute_cascade(ctx.cascade_config["on_soft_delete"], doc, db, soft=True)
            elif ctx.cascade_config.get("on_delete"):
                await _execute_cascade(ctx.cascade_config["on_delete"], doc, db, soft=True)
            if hook_exec:
                await hook_exec.run("after_delete", _serialize_doc(doc), user, db)
            return {"data": {"_id": document_id, "deleted": True}}

        q = ctx.write_filter(oid, "delete", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        result = await collection.delete_one({"_id": oid})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Document not found")
        if ctx.cascade_config.get("on_delete"):
            await _execute_cascade(ctx.cascade_config["on_delete"], doc, db)
        if hook_exec:
            await hook_exec.run("after_delete", _serialize_doc(doc), user, db)
        return {"data": {"_id": document_id, "deleted": True}}

    if ctx.soft_delete:

        @router.post("/{document_id}/_restore", summary=f"Restore {ctx.name}", dependencies=mutate_deps)
        async def restore_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
            oid = _to_object_id(document_id)
            user = _get_user_from_request(request)
            collection = db[ctx.name]
            # write_filter adds "deleted_at: None" which contradicts restore's intent;
            # build the filter manually so we match soft-deleted docs.
            base: dict[str, Any] = {"_id": oid, "deleted_at": {"$ne": None}}
            policy_filter = ctx.resolve_policy("write", user)
            q = merge_filters(base, policy_filter) or base
            doc = await collection.find_one(q)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found or not deleted")
            await collection.update_one({"_id": oid}, {"$set": {"deleted_at": None}})
            return {"data": {"_id": document_id, "restored": True}}


def _register_write_routes(
    router: APIRouter,
    ctx: _CollectionCtx,
    create_dependencies: list[Any] | None = None,
    mutate_dependencies: list[Any] | None = None,
) -> None:
    """Register POST/PUT/PATCH/DELETE endpoints."""
    _c_deps = create_dependencies or []
    _m_deps = mutate_dependencies or []
    _hook_exec = BackgroundHookExecutor(ctx.hooks_config) if ctx.hooks_config else None

    @router.post("", status_code=201, summary=f"Create {ctx.name}", dependencies=_c_deps)
    async def create_document(request: Request, db=Depends(get_scoped_db)):
        await _enforce_body_limit(request, ctx.max_body_bytes)
        body = await request.json()
        user = _get_user_from_request(request)
        ctx.sanitize_body(body, user)
        _validate_document(body, ctx.schema)
        await validate_schema_extensions(body, ctx.schema, db)
        body.pop("_id", None)
        ctx.apply_defaults(body, user)
        if ctx.timestamps:
            _stamp_create(body)
        collection = db[ctx.name]
        if _hook_exec:
            await _hook_exec.run_before("before_create", body, user, db)
        try:
            result = await collection.insert_one(body)
        except DuplicateKeyError as exc:
            _handle_duplicate_key(exc, ctx.name)
        doc_out = {**body, "_id": str(result.inserted_id)}
        if _hook_exec:
            await _hook_exec.run("after_create", doc_out, user, db)
        return {"data": {"_id": str(result.inserted_id)}}

    if ctx.bulk_insert:
        _register_bulk_insert_route(router, ctx, _c_deps, _hook_exec)

    @router.put("/{document_id}", summary=f"Replace {ctx.name}", dependencies=_m_deps)
    async def replace_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        await _enforce_body_limit(request, ctx.max_body_bytes)
        oid = _to_object_id(document_id)
        body = await request.json()
        user = _get_user_from_request(request)
        ctx.sanitize_body(body, user)
        _validate_document(body, ctx.schema)
        await validate_schema_extensions(body, ctx.schema, db)
        body.pop("_id", None)
        if ctx.timestamps:
            _stamp_update(body)
        collection = db[ctx.name]
        q = ctx.write_filter(oid, "write", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if _hook_exec:
            await _hook_exec.run_before("before_update", body, user, db, prev=_serialize_doc(doc))
        try:
            result = await collection.update_one({"_id": oid}, {"$set": body})
        except DuplicateKeyError as exc:
            _handle_duplicate_key(exc, ctx.name)
        doc_out = {**body, "_id": document_id}
        if _hook_exec:
            prev_doc = _serialize_doc(doc)
            await _hook_exec.run("after_update", doc_out, user, db, prev=prev_doc)
        return {"data": {"_id": document_id, "modified": result.modified_count}}

    @router.patch("/{document_id}", summary=f"Update {ctx.name}", dependencies=_m_deps)
    async def patch_document(document_id: str, request: Request, db=Depends(get_scoped_db)):
        await _enforce_body_limit(request, ctx.max_body_bytes)
        oid = _to_object_id(document_id)
        body = await request.json()
        user = _get_user_from_request(request)
        ctx.sanitize_body(body, user)
        _validate_document(body, ctx.schema, partial=True)
        await validate_schema_extensions(body, ctx.schema, db, partial=True)
        body.pop("_id", None)
        if ctx.timestamps:
            _stamp_update(body)
        collection = db[ctx.name]
        q = ctx.write_filter(oid, "write", user)
        doc = await collection.find_one(q)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if _hook_exec:
            await _hook_exec.run_before("before_update", body, user, db, prev=_serialize_doc(doc))
        try:
            result = await collection.update_one({"_id": oid}, {"$set": body})
        except DuplicateKeyError as exc:
            _handle_duplicate_key(exc, ctx.name)
        doc_out = {**body, "_id": document_id}
        if _hook_exec:
            prev_doc = _serialize_doc(doc)
            await _hook_exec.run("after_update", doc_out, user, db, prev=prev_doc)
        return {"data": {"_id": document_id, "modified": result.modified_count}}

    _register_delete_routes(router, ctx, _m_deps, _hook_exec)


# ── Aggregation helpers (populate + computed) ────────────────────────────


def _get_db_prefix(db: Any) -> str:
    """Extract the slug prefix from a ScopedMongoWrapper for $lookup "from" fields."""
    scope = getattr(db, "_write_scope", None)
    return f"{scope}_" if scope else ""


def _inject_populate_stages(
    pipeline: list[dict[str, Any]],
    populate_names: list[str] | None,
    ctx: _CollectionCtx,
    db: Any = None,
) -> None:
    """Inject ``$lookup`` stages for requested relations."""
    if not populate_names or not ctx.relations_config:
        return
    prefix = _get_db_prefix(db) if db else ""
    for name in populate_names:
        rel = ctx.relations_config.get(name)
        if rel is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown relation: '{name}'. Available: {list(ctx.relations_config.keys())}",
            )
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
    ctx: _CollectionCtx,
    db: Any = None,
) -> None:
    """Inject aggregation stages for requested computed fields."""
    if not computed_names or not ctx.computed_config:
        return
    prefix = _get_db_prefix(db) if db else ""
    for name in computed_names:
        comp = ctx.computed_config.get(name)
        if comp is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown computed field: '{name}'. Available: {list(ctx.computed_config.keys())}",
            )
        if isinstance(comp, dict) and "pipeline" in comp:
            stages = comp["pipeline"]
            if prefix:
                stages = _prefix_lookup_stages(stages, prefix)
            pipeline.extend(stages)
        else:
            pipeline.append({"$addFields": {name: comp}})


def _prefix_lookup_stages(stages: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """Rewrite $lookup 'from' fields to use the scoped collection prefix."""
    import copy

    result = copy.deepcopy(stages)
    for stage in result:
        if "$lookup" in stage:
            lookup = stage["$lookup"]
            from_col = lookup.get("from", "")
            if from_col and not from_col.startswith(prefix):
                lookup["from"] = f"{prefix}{from_col}"
    return result


async def _aggregated_list(
    ctx: _CollectionCtx,
    collection: Any,
    effective_filter: dict[str, Any] | None,
    parsed: Any,
    user: dict[str, Any] | None,
    db: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Run an aggregation pipeline for list queries needing populate/computed."""
    total = await collection.count_documents(effective_filter or {})
    pipeline: list[dict[str, Any]] = []
    if effective_filter:
        pipeline.append({"$match": effective_filter})

    _inject_populate_stages(pipeline, parsed.populate, ctx, db=db)
    _inject_computed_stages(pipeline, parsed.computed, ctx, db=db)

    if parsed.sort:
        sort_doc = {k: v for k, v in parsed.sort}
        pipeline.append({"$sort": sort_doc})
    pipeline.append({"$skip": parsed.skip})
    pipeline.append({"$limit": parsed.limit})

    projection = ctx.effective_projection(parsed.projection)
    if projection:
        pipeline.append({"$project": projection})

    result = collection.aggregate(pipeline)
    if hasattr(result, "__await__"):
        result = await result
    docs = await result.to_list(length=parsed.limit)
    return docs, total


# ── Router factory ───────────────────────────────────────────────────────


def create_auto_crud_router(
    collection_name: str,
    config: dict[str, Any] | None = None,
    *,
    tags: list[str] | None = None,
    app_auth_enabled: bool = False,
) -> APIRouter:
    """Create a FastAPI router with CRUD endpoints for a single collection.

    Args:
        collection_name: MongoDB collection base name (without app prefix).
        config: Collection config from the manifest ``collections`` dict.
        tags: Optional OpenAPI tags for the generated endpoints.
        app_auth_enabled: When ``True`` (i.e. ``auth.users.enabled`` at app
            level), every endpoint requires authentication at minimum.
            Per-collection ``auth`` config can only *tighten* (e.g. require
            specific roles), never loosen.

    Returns:
        A ``fastapi.APIRouter`` ready to be included in an app.
    """
    config = config or {}
    route_tags = tags or [collection_name]
    prefix = f"/api/{collection_name}"

    owner_field = config.get("owner_field")

    defaults = dict(config.get("defaults", {}))
    policy = dict(config.get("policy", {}))

    if owner_field:
        defaults.setdefault(owner_field, "{{user._id}}")
        owner_policy = {owner_field: "{{user._id}}"}
        if "write" not in policy:
            policy["write"] = owner_policy
        if "delete" not in policy:
            policy["delete"] = owner_policy

    has_unique = False
    schema = config.get("schema")
    if schema and isinstance(schema.get("properties"), dict):
        for _prop_name, prop_def in schema["properties"].items():
            if isinstance(prop_def, dict) and prop_def.get("x-unique"):
                has_unique = True
                break

    immutable = list(config.get("immutable_fields", []))
    if app_auth_enabled:
        immutable = list(set(immutable) | _PROTECTED_FIELDS)

    # Auto-hide sensitive fields on reads when auth is enabled
    projection = dict(config.get("default_projection") or {})
    if app_auth_enabled:
        for sf in _SENSITIVE_READ_FIELDS:
            projection.setdefault(sf, 0)

    ctx = _CollectionCtx(
        name=collection_name,
        schema=schema,
        read_only=config.get("read_only", False),
        timestamps=config.get("timestamps", True),
        soft_delete=config.get("soft_delete", False),
        bulk_insert=config.get("bulk_insert", True),
        max_body_bytes=int(config.get("max_body_bytes", MAX_BODY_BYTES_DEFAULT)),
        policy=policy,
        scopes_config=config.get("scopes", {}),
        pipelines_config=config.get("pipelines", {}),
        defaults_config=defaults,
        default_projection=projection or None,
        owner_field=owner_field,
        immutable_fields=immutable,
        writable_fields=config.get("writable_fields"),
        hooks_config=config.get("hooks", {}),
        relations_config=config.get("relations", {}),
        computed_config=config.get("computed", {}),
        has_unique_fields=has_unique,
        cascade_config=config.get("cascade", {}),
        cache_config=config.get("cache", {}),
    )

    auth_config = config.get("auth", {})

    # Secure-by-default: when the app has auth.users.enabled, every
    # collection endpoint requires at least an authenticated user.
    # Per-collection auth can only tighten (specific roles), never loosen.
    _auth_baseline = app_auth_enabled
    _public_read = bool(auth_config.get("public_read", False))

    # --- auth gate wiring ---------------------------------------------------
    # When an authz_provider is available (Casbin/OSO), delegates to it via
    # require_collection_permission.  Falls back to the legacy inline
    # require_role/require_user path when no provider is configured.
    _has_roles = bool(auth_config.get("roles"))
    _has_write_roles = bool(auth_config.get("write_roles"))
    _has_create_roles = bool(auth_config.get("create_roles"))
    _use_provider = _has_roles or _has_write_roles or _has_create_roles

    _all_roles = auth_config.get("roles", [])
    _write_roles_list = auth_config.get("write_roles", [])
    _create_roles_list = auth_config.get("create_roles", [])

    # Router-level auth gates ALL endpoints (reads + writes) by default.
    router_dependencies: list[Any] = []
    if _use_provider and _has_roles:
        router_dependencies.append(
            Depends(require_collection_permission(collection_name, "read", fallback_roles=_all_roles))
        )
    elif _has_roles:
        router_dependencies.append(Depends(require_role(*_all_roles)))
    elif auth_config.get("required") or _auth_baseline:
        router_dependencies.append(Depends(require_user()))

    # Per-collection rate limiting (reads + writes)
    _rate_limits_config = config.get("rate_limits", {})
    _read_rate_dep = create_collection_rate_limit_dependency(collection_name, "reads", _rate_limits_config)
    _write_rate_dep = create_collection_rate_limit_dependency(collection_name, "writes", _rate_limits_config)

    _read_rate_deps = [Depends(_read_rate_dep)]

    # public_read: split into two routers — public reads, auth-gated writes.
    if _public_read and _auth_baseline:
        if _use_provider:
            read_router = APIRouter(
                prefix=prefix,
                tags=route_tags,
                dependencies=[Depends(require_collection_permission(collection_name, "read"))],
            )
        else:
            read_router = APIRouter(prefix=prefix, tags=route_tags)
        write_router = APIRouter(prefix=prefix, tags=route_tags, dependencies=[Depends(require_user())])
    else:
        read_router = APIRouter(prefix=prefix, tags=route_tags, dependencies=router_dependencies)
        write_router = read_router

    _register_read_routes(read_router, ctx, read_dependencies=_read_rate_deps)
    _register_pipeline_routes(read_router, ctx)
    if not ctx.read_only:
        create_roles = auth_config.get("create_roles")
        create_required = auth_config.get("create_required", False) or _auth_baseline
        create_deps: list[Any] = []
        _create_fb = _create_roles_list or _write_roles_list or None
        if _use_provider and (create_roles or _has_write_roles):
            create_deps.append(
                Depends(require_collection_permission(collection_name, "create", fallback_roles=_create_fb))
            )
        elif create_roles:
            create_deps.append(Depends(require_role(*create_roles)))
        elif create_required:
            create_deps.append(Depends(require_user()))

        write_roles = auth_config.get("write_roles")
        write_required = auth_config.get("write_required", False) or _auth_baseline
        write_deps: list[Any] = []
        _write_fb = _write_roles_list or _all_roles or None
        if _use_provider and (_has_write_roles or _has_roles):
            write_deps.append(
                Depends(require_collection_permission(collection_name, "write", fallback_roles=_write_fb))
            )
        elif write_roles:
            write_deps.append(Depends(require_role(*write_roles)))
        elif write_required:
            write_deps.append(Depends(require_user()))

        _wr_dep = [Depends(_write_rate_dep)]
        _register_write_routes(
            write_router,
            ctx,
            create_dependencies=(create_deps or write_deps) + _wr_dep,
            mutate_dependencies=write_deps + _wr_dep,
        )

    if read_router is write_router:
        return read_router

    # Return both routers wrapped in a parent so include_router works
    parent = APIRouter()
    parent.include_router(read_router)
    parent.include_router(write_router)
    return parent


def mount_auto_crud_routes(
    app: Any,
    collections: dict[str, dict[str, Any]],
    *,
    app_auth_enabled: bool = False,
    auth_users_collection: str = "users",
) -> None:
    """Mount auto-CRUD routers for all eligible collections onto an app.

    Iterates the ``collections`` dict from the manifest and includes
    a router for each collection where ``auto_crud`` is enabled
    (defaults to ``True`` when omitted).

    Args:
        app: FastAPI application instance.
        collections: The ``collections`` section of the manifest.
        app_auth_enabled: When ``True``, all endpoints require
            authentication (secure-by-default when the app has auth).
        auth_users_collection: Name of the auth users collection.
            Auto-CRUD is blocked on this collection when auth is enabled
            to prevent privilege escalation via direct CRUD.
    """
    for name, config in collections.items():
        if not config.get("auto_crud", True):
            logger.info(f"Skipping auto-CRUD for collection '{name}' (auto_crud=false)")
            continue
        if app_auth_enabled and name == auth_users_collection:
            logger.error(
                "BLOCKED: Collection '%s' is the auth users collection — "
                "auto-CRUD will not be mounted. Manage users via /auth/* endpoints.",
                name,
            )
            continue
        router = create_auto_crud_router(name, config, app_auth_enabled=app_auth_enabled)
        app.include_router(router)
        _auth_label = " (auth enforced)" if app_auth_enabled else " (public — no auth)"
        logger.info(f"Mounted auto-CRUD routes for collection '{name}' at /api/{name}{_auth_label}")
