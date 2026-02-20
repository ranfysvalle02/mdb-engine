"""FastAPI dependency injection for OSI services."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .registry import OsiModelRegistry

logger = logging.getLogger(__name__)


async def get_osi_registry(request: Any) -> OsiModelRegistry | None:
    """FastAPI dependency to retrieve the OSI model registry for the current app.

    Checks (in order):
    1. ``app.state.osi_registry`` (cached from previous request)
    2. ``app.state.engine.get_osi_registry(slug)`` (from service initializer)

    Returns:
        The OsiModelRegistry if available, else None.
    """
    app = request.app

    # 1. Check app state cache
    if hasattr(app.state, "osi_registry") and app.state.osi_registry is not None:
        return app.state.osi_registry

    # 2. Try to get from engine
    if hasattr(app.state, "engine") and app.state.engine is not None:
        engine = app.state.engine
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug and hasattr(engine, "_service_initializer"):
            initializer = engine._service_initializer  # noqa: SLF001
            if hasattr(initializer, "get_osi_registry"):
                registry = initializer.get_osi_registry(slug)
                if registry:
                    app.state.osi_registry = registry
                    return registry

    return None
