"""
Auto-registered graph service endpoints.

Included conditionally when ``graph_config.enabled`` is true.
Mounted under ``/_mdb/graph`` to avoid collisions with app routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..dependencies import get_graph_service_optional

router = APIRouter(prefix="/_mdb/graph", tags=["mdb-internal"])


@router.get("/stats")
async def graph_stats(graph: Any = Depends(get_graph_service_optional)) -> dict[str, Any]:
    """Return knowledge graph statistics (node/edge counts, types, etc.)."""
    if graph is None:
        return {"enabled": False, "error": "Graph service not available"}
    return await graph.get_stats()
