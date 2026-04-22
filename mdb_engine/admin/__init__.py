"""
First-class admin plane for mdb-engine.

The admin plane is a top-level manifest concern: enable it with an
``admin_api`` block at the root of ``manifest.json`` and the engine
mounts a :class:`AdminSurface` at ``admin_api.path_prefix``
(default ``/__mdb``). Every authenticated call is scope-checked per
endpoint and audited to ``_mdb_admin_audit`` via a non-blocking
middleware.

Built-in modules: :class:`HealthAdminModule`,
:class:`ReconcilerAdminModule`, :class:`TrashAdminModule`,
:class:`AuditAdminModule`, :class:`SecretsAdminModule`. Register
third-party modules via
``engine.admin_surface().register(my_module)``.
"""

from .audit import (
    ADMIN_AUDIT_COLLECTION,
    DEFAULT_AUDIT_RETENTION_DAYS,
    AuditContext,
    bootstrap_admin_collections,
    fingerprint_token,
    write_audit_row,
)
from .base import (
    ADMIN_TOKEN_HEADER,
    WILDCARD_SCOPE,
    AdminModule,
    ModuleConfig,
    ModuleEndpoint,
)
from .events import (
    EVENT_AUTH_FAILED,
    EVENT_CALL,
    EVENT_IDEMPOTENCY_REPLAY,
    EVENT_RATE_LIMITED,
    EVENT_SCOPE_DENIED,
)
from .idempotency import (
    IDEMPOTENCY_COLLECTION,
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_TTL_SECONDS,
    bootstrap_idempotency_collection,
    replay_or_record,
)
from .modules import (
    AuditAdminModule,
    HealthAdminModule,
    ReconcilerAdminModule,
    SecretsAdminModule,
    TrashAdminModule,
)
from .routing import ModuleRouter, require_scope
from .surface import AdminSurface

__all__ = [
    "ADMIN_AUDIT_COLLECTION",
    "ADMIN_TOKEN_HEADER",
    "AdminModule",
    "AdminSurface",
    "AuditAdminModule",
    "AuditContext",
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "EVENT_AUTH_FAILED",
    "EVENT_CALL",
    "EVENT_IDEMPOTENCY_REPLAY",
    "EVENT_RATE_LIMITED",
    "EVENT_SCOPE_DENIED",
    "HealthAdminModule",
    "IDEMPOTENCY_COLLECTION",
    "IDEMPOTENCY_HEADER",
    "IDEMPOTENCY_TTL_SECONDS",
    "ModuleConfig",
    "ModuleEndpoint",
    "ModuleRouter",
    "ReconcilerAdminModule",
    "SecretsAdminModule",
    "TrashAdminModule",
    "WILDCARD_SCOPE",
    "bootstrap_admin_collections",
    "bootstrap_idempotency_collection",
    "fingerprint_token",
    "replay_or_record",
    "require_scope",
    "write_audit_row",
]
