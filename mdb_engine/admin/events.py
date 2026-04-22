"""
Structured events emitted by the admin plane.

Every lifecycle moment a production operator cares about gets a
named event:

- ``mdb.admin.call`` — an authenticated admin call completed.
- ``mdb.admin.auth_failed`` — a request failed authentication (401/403).
- ``mdb.admin.scope_denied`` — a token lacked a required scope.
- ``mdb.admin.rate_limited`` — a request was rate-limited (429).
- ``mdb.admin.idempotency_replay`` — a cached idempotent response was served.

These are plain constants consumed by callers via
:func:`mdb_engine.core.reconciler_events.emit_event`, so existing
OpenTelemetry / JSON log pipelines pick them up with zero extra
configuration. The single helper below (:func:`emit_call`) lives here
because it has *actual* normalization logic (method uppercase, rounded
duration); the other events are trivial pass-throughs and are emitted
directly at the call site.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import Any

from ..core.reconciler_events import emit_event

EVENT_CALL = "mdb.admin.call"
EVENT_AUTH_FAILED = "mdb.admin.auth_failed"
EVENT_SCOPE_DENIED = "mdb.admin.scope_denied"
EVENT_RATE_LIMITED = "mdb.admin.rate_limited"
EVENT_IDEMPOTENCY_REPLAY = "mdb.admin.idempotency_replay"


def emit_call(
    *,
    slug: str,
    module: str,
    endpoint: str,
    method: str,
    status: int,
    duration_ms: float,
    principal_label: str | None = None,
    principal_token_id: str | None = None,
    **extra: Any,
) -> None:
    """Emit ``mdb.admin.call`` with normalized method + duration.

    The only event helper that survived the trim — the others were
    trivial ``emit_event(EVENT_X, **kwargs)`` passthroughs and now call
    :func:`emit_event` directly at their one site each.
    """
    emit_event(
        EVENT_CALL,
        slug=slug,
        module=module,
        endpoint=endpoint,
        method=method.upper(),
        status=status,
        duration_ms=round(float(duration_ms), 2),
        principal_label=principal_label,
        principal_token_id=principal_token_id,
        **extra,
    )


__all__ = [
    "EVENT_AUTH_FAILED",
    "EVENT_CALL",
    "EVENT_IDEMPOTENCY_REPLAY",
    "EVENT_RATE_LIMITED",
    "EVENT_SCOPE_DENIED",
    "emit_call",
]
