"""
Metric export endpoints for MDB_ENGINE.

Provides a ``/metrics`` endpoint that serves Prometheus-compatible metrics
when the ``prometheus_client`` package is installed.  When the package is
absent the endpoint is simply not registered (graceful degradation).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def create_prometheus_endpoint(app: FastAPI) -> bool:
    """Register a ``/metrics`` endpoint on *app* that exports Prometheus format.

    Returns ``True`` if the endpoint was registered, ``False`` if
    ``prometheus_client`` is not installed.
    """
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        logger.debug("prometheus_client not installed — /metrics endpoint not registered")
        return False

    from fastapi.responses import Response

    @app.get("/metrics", include_in_schema=False, tags=["observability"])
    async def prometheus_metrics() -> Response:
        """Serve Prometheus-compatible metrics."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    logger.info("Prometheus /metrics endpoint registered")
    return True


def setup_otel_metrics_bridge(service_name: str = "mdb_engine") -> Any | None:
    """Create an OTel ``Meter`` and wire it into the global :class:`MetricsCollector`.

    Returns the ``Meter`` if OpenTelemetry is available, otherwise ``None``.
    """
    from .tracing import otel_available

    if not otel_available():
        return None

    try:
        from opentelemetry import metrics as otel_metrics

        meter = otel_metrics.get_meter(service_name)
        from .metrics import get_metrics_collector

        get_metrics_collector().configure_otel(meter)
        logger.info("OpenTelemetry metrics bridge configured (service=%s)", service_name)
        return meter
    except (ImportError, RuntimeError) as exc:
        logger.warning("Could not configure OTel metrics bridge: %s", exc)
        return None
