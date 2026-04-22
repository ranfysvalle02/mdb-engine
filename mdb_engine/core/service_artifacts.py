"""
Service artifact listers for the manifest reconciler.

When a manifest disables a previously-enabled service (memory / graph /
OSI / profile), the reconciler needs to know which physical collections
that service used to own so it can quarantine them. Listers defined here
inspect the previous manifest and return ``ArtifactListEntry`` dicts
that the reconciler consumes.

Listers are purely informational — they do not drop or modify anything
themselves. They use naming conventions that match the initializers in
:mod:`mdb_engine.core.service_initialization` so the reconciler can
safely quarantine the right collections.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


def _prefix(slug: str, name: str) -> str:
    if name.startswith(f"{slug}_"):
        return name
    return f"{slug}_{name}"


def _memory_collection_name(slug: str, prev: dict[str, Any] | None) -> str:
    """Best-effort physical memory collection name for a slug."""
    cfg: Any = (prev or {}).get("memory_config")
    if isinstance(cfg, dict):
        name = cfg.get("collection_name")
        if isinstance(name, str) and name:
            return _prefix(slug, name)
    return f"{slug}_memories"


def _graph_collection_name(slug: str, prev: dict[str, Any] | None) -> str:
    cfg: Any = (prev or {}).get("graph_config")
    if isinstance(cfg, dict):
        name = cfg.get("collection_name")
        if isinstance(name, str) and name:
            return _prefix(slug, name)
    return f"{slug}_kg"


def _osi_collection_name(slug: str) -> str:
    return f"{slug}_osi_models"


def _profile_collection_name(slug: str, prev: dict[str, Any] | None) -> str:
    cfg: Any = (prev or {}).get("profile_config")
    if isinstance(cfg, dict):
        name = cfg.get("collection_name")
        if isinstance(name, str) and name:
            return _prefix(slug, name)
    return f"{slug}_user_profiles"


async def _collection_exists(db: AsyncIOMotorDatabase, name: str) -> bool:
    try:  # nosemgrep
        names = await db.list_collection_names(filter={"name": name})
        return name in names
    except Exception:  # noqa: BLE001
        return False


def make_memory_lister(db: AsyncIOMotorDatabase):
    async def list_memory_artifacts(slug: str, prev: dict[str, Any] | None) -> list[dict[str, Any]]:
        coll = _memory_collection_name(slug, prev)
        if not await _collection_exists(db, coll):
            return []
        return [
            {
                "artifact_type": "service_collection",
                "collection": coll,
                "name": coll,
                "spec": {"source": "memory_config"},
                "service": "memory",
            }
        ]

    return list_memory_artifacts


def make_graph_lister(db: AsyncIOMotorDatabase):
    async def list_graph_artifacts(slug: str, prev: dict[str, Any] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # Main graph (kg) collection
        coll = _graph_collection_name(slug, prev)
        if await _collection_exists(db, coll):
            out.append(
                {
                    "artifact_type": "service_collection",
                    "collection": coll,
                    "name": coll,
                    "spec": {"source": "graph_config"},
                    "service": "graph",
                }
            )
        # Communities collection (GraphRAG), if present
        communities = f"{coll}_communities"
        if await _collection_exists(db, communities):
            out.append(
                {
                    "artifact_type": "service_collection",
                    "collection": communities,
                    "name": communities,
                    "spec": {"source": "graph_config.communities"},
                    "service": "graph",
                }
            )
        return out

    return list_graph_artifacts


def make_osi_lister(db: AsyncIOMotorDatabase):
    async def list_osi_artifacts(slug: str, prev: dict[str, Any] | None) -> list[dict[str, Any]]:
        coll = _osi_collection_name(slug)
        if not await _collection_exists(db, coll):
            return []
        return [
            {
                "artifact_type": "service_collection",
                "collection": coll,
                "name": coll,
                "spec": {"source": "osi_config"},
                "service": "osi",
            }
        ]

    return list_osi_artifacts


def make_profile_lister(db: AsyncIOMotorDatabase):
    async def list_profile_artifacts(slug: str, prev: dict[str, Any] | None) -> list[dict[str, Any]]:
        coll = _profile_collection_name(slug, prev)
        if not await _collection_exists(db, coll):
            return []
        return [
            {
                "artifact_type": "service_collection",
                "collection": coll,
                "name": coll,
                "spec": {"source": "profile_config"},
                "service": "profile",
            }
        ]

    return list_profile_artifacts


def _make_single_collection_rename_detector(
    name_fn,
    service: str,
    source_label: str,
):
    """Build a rename detector that compares prev/new ``collection_name``.

    Covers the common case for memory / graph / profile where the
    service owns exactly one collection whose physical name is derived
    from ``<service>_config.collection_name``.
    """

    async def detect_renames(
        slug: str,
        prev: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not prev:
            return []
        prev_name = name_fn(slug, prev)
        new_name = name_fn(slug, new)
        if not prev_name or not new_name or prev_name == new_name:
            return []
        return [
            {
                "collection": new_name,
                "rename_from": prev_name,
                "service": service,
                "spec": {"source": source_label, "rename": True},
                "reason": f"{service}_config.collection_name changed: {prev_name} -> {new_name}",
            }
        ]

    return detect_renames


def make_memory_rename_detector(db: AsyncIOMotorDatabase):
    return _make_single_collection_rename_detector(_memory_collection_name, "memory", "memory_config")


def make_graph_rename_detector(db: AsyncIOMotorDatabase):
    return _make_single_collection_rename_detector(_graph_collection_name, "graph", "graph_config")


def make_profile_rename_detector(db: AsyncIOMotorDatabase):
    return _make_single_collection_rename_detector(_profile_collection_name, "profile", "profile_config")


def register_default_listers(reconciler: Any, db: AsyncIOMotorDatabase) -> None:
    """Register the built-in memory / graph / OSI / profile listers + renamers."""
    reconciler.register_service_lister("memory", make_memory_lister(db))
    reconciler.register_service_lister("graph", make_graph_lister(db))
    reconciler.register_service_lister("osi", make_osi_lister(db))
    reconciler.register_service_lister("profile", make_profile_lister(db))

    reconciler.register_service_rename_detector("memory", make_memory_rename_detector(db))
    reconciler.register_service_rename_detector("graph", make_graph_rename_detector(db))
    reconciler.register_service_rename_detector("profile", make_profile_rename_detector(db))


__all__ = [
    "make_memory_lister",
    "make_graph_lister",
    "make_osi_lister",
    "make_profile_lister",
    "make_memory_rename_detector",
    "make_graph_rename_detector",
    "make_profile_rename_detector",
    "register_default_listers",
]
