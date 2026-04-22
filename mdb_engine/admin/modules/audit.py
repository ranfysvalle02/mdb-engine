"""
Audit read-back module.

Closes the loop on the admin plane's audit log: every authenticated
admin call is written to ``_mdb_admin_audit`` by the surface, and
this module exposes a tiny, scoped query API on top of it:

- ``GET /audit?slug=&module=&since=&until=&status_gte=&cursor=&limit=``
  Paginated time-descending listing with opaque cursor pagination.
- ``GET /audit/recent?slug=&limit=&after=``
  Cheap tail used by ``mdb-engine admin audit tail`` and the SPA
  audit tab. Supports ``?after=<ISO8601>`` for incremental polling.
- ``GET /audit/stats?slug=``
  Counts by module, status bucket, and rolling window (1h / 24h / 7d).
  Cheap single-aggregation answer for "is anything on fire right now?"
  — deeper forensics go through ``/audit`` with filters.

All endpoints require scope ``read``.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, status

from ..audit import ADMIN_AUDIT_COLLECTION
from ..base import AdminModule, ModuleConfig, ModuleEndpoint
from ..routing import ModuleRouter

if TYPE_CHECKING:
    from ...core.engine import MongoDBEngine

logger = logging.getLogger(__name__)

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50


def _encode_cursor(at: datetime, _id: Any) -> str:
    payload = {"at": at.isoformat(), "id": str(_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:  # nosemgrep
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        at = datetime.fromisoformat(payload["at"])
        return at, payload["id"]
    except Exception:  # noqa: BLE001
        return None


def _shape_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("_id")),
        "slug": row.get("slug"),
        "module": row.get("module"),
        "endpoint": row.get("endpoint"),
        "method": row.get("method"),
        "status": row.get("status"),
        "duration_ms": row.get("duration_ms"),
        "principal_token_id": row.get("principal_token_id"),
        "principal_label": row.get("principal_label"),
        "request_summary": row.get("request_summary"),
        "response_summary": row.get("response_summary"),
        "extra": row.get("extra"),
        "at": row.get("at"),
    }


class AuditAdminModule(AdminModule):
    name = "audit"

    def build_router(self, engine: MongoDBEngine, cfg: ModuleConfig) -> APIRouter:
        mr = ModuleRouter(self.name)

        async def _coll():
            conn = getattr(engine, "_connection_manager", None)
            mongo_db = getattr(conn, "mongo_db", None) if conn else None
            if mongo_db is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="audit collection unavailable (no database connection)",
                )
            return mongo_db[ADMIN_AUDIT_COLLECTION]

        async def audit_list(
            slug: str = Query(..., min_length=1),
            module: str | None = Query(None),
            since: datetime | None = Query(None),
            until: datetime | None = Query(None),
            status_gte: int | None = Query(None, ge=0, le=599),
            cursor: str | None = Query(None),
            limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
        ) -> dict[str, Any]:
            coll = await _coll()
            query: dict[str, Any] = {"slug": slug}
            if module:
                query["module"] = module
            if status_gte is not None:
                query["status"] = {"$gte": status_gte}
            at_filter: dict[str, Any] = {}
            if since:
                at_filter["$gte"] = since
            if until:
                at_filter["$lt"] = until
            if cursor:
                decoded = _decode_cursor(cursor)
                if decoded is None:
                    raise HTTPException(status_code=400, detail="invalid cursor")
                at_cur, id_cur = decoded
                at_filter["$lte"] = at_cur
                query["$or"] = [
                    {"at": {"$lt": at_cur}},
                    {"at": at_cur, "_id": {"$lt": id_cur}},
                ]
            if at_filter and "$or" not in query:
                query["at"] = at_filter

            sort = [("at", -1), ("_id", -1)]
            rows = await coll.find(query).sort(sort).limit(limit + 1).to_list(length=limit + 1)
            next_cursor: str | None = None
            if len(rows) > limit:
                overflow = rows[limit]
                next_cursor = _encode_cursor(overflow["at"], overflow["_id"])
                rows = rows[:limit]
            return {
                "slug": slug,
                "items": [_shape_row(r) for r in rows],
                "next_cursor": next_cursor,
                "limit": limit,
            }

        async def audit_recent(
            slug: str = Query(..., min_length=1),
            limit: int = Query(50, ge=1, le=200),
            module: str | None = Query(None),
            after: datetime | None = Query(None),
        ) -> dict[str, Any]:
            coll = await _coll()
            query: dict[str, Any] = {"slug": slug}
            if module:
                query["module"] = module
            if after:
                query["at"] = {"$gt": after}
            rows = await coll.find(query).sort([("at", -1), ("_id", -1)]).limit(limit).to_list(length=limit)
            return {
                "slug": slug,
                "items": [_shape_row(r) for r in rows],
            }

        async def audit_stats(slug: str = Query(..., min_length=1)) -> dict[str, Any]:
            coll = await _coll()
            now = datetime.now(timezone.utc)
            one_hour = now - timedelta(hours=1)
            one_day = now - timedelta(days=1)
            seven_days = now - timedelta(days=7)

            pipeline = [
                {"$match": {"slug": slug, "at": {"$gte": seven_days}}},
                {
                    "$facet": {
                        "by_module": [
                            {"$group": {"_id": "$module", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                        ],
                        "by_status_bucket": [
                            {
                                "$group": {
                                    "_id": {
                                        "$switch": {
                                            "branches": [
                                                {"case": {"$lt": ["$status", 300]}, "then": "2xx"},
                                                {"case": {"$lt": ["$status", 400]}, "then": "3xx"},
                                                {"case": {"$lt": ["$status", 500]}, "then": "4xx"},
                                            ],
                                            "default": "5xx",
                                        }
                                    },
                                    "count": {"$sum": 1},
                                }
                            }
                        ],
                        "windows": [
                            {
                                "$group": {
                                    "_id": None,
                                    "last_7d": {"$sum": 1},
                                    "last_24h": {"$sum": {"$cond": [{"$gte": ["$at", one_day]}, 1, 0]}},
                                    "last_1h": {"$sum": {"$cond": [{"$gte": ["$at", one_hour]}, 1, 0]}},
                                }
                            }
                        ],
                    }
                },
            ]
            rows = await coll.aggregate(pipeline).to_list(length=1)
            if not rows:
                return {
                    "slug": slug,
                    "by_module": {},
                    "by_status_bucket": {},
                    "windows": {"last_1h": 0, "last_24h": 0, "last_7d": 0},
                }
            facets = rows[0]
            windows_list = facets.get("windows") or []
            windows = windows_list[0] if windows_list else {}
            return {
                "slug": slug,
                "by_module": {r["_id"]: r["count"] for r in facets.get("by_module", []) if r.get("_id")},
                "by_status_bucket": {
                    r["_id"]: r["count"] for r in facets.get("by_status_bucket", []) if r.get("_id") is not None
                },
                "windows": {
                    "last_1h": windows.get("last_1h", 0),
                    "last_24h": windows.get("last_24h", 0),
                    "last_7d": windows.get("last_7d", 0),
                },
            }

        mr.add("GET", "", endpoint=audit_list, scope="read", summary="Paginated audit log (cursor-based, descending).")
        mr.add(
            "GET",
            "/recent",
            endpoint=audit_recent,
            scope="read",
            summary="Cheap tail of recent audit rows; supports ?after=ISO.",
        )
        mr.add(
            "GET",
            "/stats",
            endpoint=audit_stats,
            scope="read",
            summary="Audit counts by module / status bucket / window.",
        )
        return mr.router

    def describe(self, cfg: ModuleConfig) -> list[ModuleEndpoint]:
        return [
            ModuleEndpoint("GET", "/audit", "read", "Paginated audit log."),
            ModuleEndpoint("GET", "/audit/recent", "read", "Recent audit rows for tailing."),
            ModuleEndpoint("GET", "/audit/stats", "read", "Audit counts by module / status / window."),
        ]


__all__ = ["AuditAdminModule"]
