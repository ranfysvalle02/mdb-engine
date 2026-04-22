"""
Structured events emitted by the manifest reconciler.

Every lifecycle moment a production operator cares about gets a named
event:

- ``mdb.reconcile.plan_built`` — a plan was produced (or was a no-op).
- ``mdb.reconcile.op_applied`` — a single reconcile op finished.
- ``mdb.reconcile.quarantined`` — a collection/index moved to trash.
- ``mdb.reconcile.locked`` — lock acquired / contention observed.
- ``mdb.reconcile.confirmation_required`` — ``confirm_if`` gate tripped.
- ``mdb.trash.swept`` — the trash sweeper dropped a tombstone / coll.

Events are emitted two ways:

1. **Structured log line** via ``mdb.reconciler`` logger (always on).
   Attributes appear in the ``extra=`` dict for JSON log formatters.
2. **OTel span** (when the OTel SDK is configured). Span name equals
   event name; attributes become span attributes. Falls back to a no-op
   when OTel is not installed.

Design goal: **zero-cost when unused**. A production engine with no
OTel / JSON logging in place incurs ~a dict construction per event.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("mdb.reconciler")


EVENT_PLAN_BUILT = "mdb.reconcile.plan_built"
EVENT_OP_APPLIED = "mdb.reconcile.op_applied"
EVENT_QUARANTINED = "mdb.reconcile.quarantined"
EVENT_LOCKED = "mdb.reconcile.locked"
EVENT_CONFIRM_REQUIRED = "mdb.reconcile.confirmation_required"
EVENT_TRASH_SWEPT = "mdb.trash.swept"


def _safe_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Coerce attribute values to OTel-compatible primitives."""
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, str | bool | int | float):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def emit_event(name: str, **attrs: Any) -> None:
    """Emit a one-shot reconciler event (log + OTel span if available)."""
    cleaned = _safe_attrs(attrs)
    try:  # nosemgrep
        logger.info(name, extra={"event": name, **cleaned})
    except Exception:  # noqa: BLE001 - logging must never raise
        pass

    try:  # nosemgrep
        from ..observability.tracing import get_tracer, otel_available

        if not otel_available():
            return
        tracer = get_tracer("mdb.reconciler")
        with tracer.start_as_current_span(name, attributes=cleaned):
            pass
    except Exception:  # noqa: BLE001 - OTel is optional
        pass


@contextmanager
def trace_span(name: str, **attrs: Any) -> Iterator[None]:
    """Start a reconciler span covering a block of work (best-effort)."""
    cleaned = _safe_attrs(attrs)
    try:  # nosemgrep
        from ..observability.tracing import get_tracer, otel_available

        if otel_available():
            tracer = get_tracer("mdb.reconciler")
            with tracer.start_as_current_span(name, attributes=cleaned):
                yield
                return
    except Exception:  # noqa: BLE001 - OTel is optional
        pass

    yield


__all__ = [
    "emit_event",
    "trace_span",
    "EVENT_PLAN_BUILT",
    "EVENT_OP_APPLIED",
    "EVENT_QUARANTINED",
    "EVENT_LOCKED",
    "EVENT_CONFIRM_REQUIRED",
    "EVENT_TRASH_SWEPT",
]
