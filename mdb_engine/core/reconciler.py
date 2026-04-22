"""
Manifest reconciler — declarative startup cleanup for mdb-engine apps.

The reconciler compares a freshly-loaded manifest to the last-applied state
(recorded in ``_mdb_owned_artifacts`` and ``apps_config._applied_hash``) and
produces a :class:`ReconcilePlan`. Applying a plan brings the database in
line with the manifest by:

- Creating newly-declared collections and indexes.
- Updating changed index specs.
- **Quarantining** removed collections and indexes by renaming collections
  into a hidden ``_mdb_trash__*`` namespace and tombstoning index specs in
  the ``_mdb_trash`` collection. Quarantined artifacts are auto-purged
  after ``manifest_tracking.retention.trash_ttl_days`` days by the
  built-in ``trash_sweeper``.
- Delegating service-owned artifacts (memory / graph / OSI collections) to
  the respective service listers via :class:`ServiceArtifactLister`.

Three modes control how aggressive cleanup is:

- ``safe`` (default): additions and updates only; removals are logged.
- ``reconcile``: removals are quarantined (never hard-dropped).
- ``strict``: same as ``reconcile``, but empty collections may skip
  quarantine when ``allow_immediate_drop`` is true.

Beyond the declarative diff the reconciler also owns:

- **Atomic persistence**: ``apps_config`` is updated inside the same lock
  as revision + ledger writes, so a crash mid-apply can never leave the
  three sources of truth disagreeing.
- **Fencing-token advisory locks**: prevent two replicas from applying
  simultaneously and survive pid/namespace reuse.
- **Quarantine with rich metadata**: tombstones record the removed
  subtree, the committing user/SHA (when provided), a JSON Patch of the
  manifest change, and the revision number.
- **Safety gates** (``manifest_tracking.confirm_if``): block plans that
  look "too big" unless explicitly confirmed.
- **Structured observability** events (``mdb.reconcile.*``) wired through
  :mod:`mdb_engine.core.reconciler_events`.

Never hard-drops user-populated collections unless explicitly told to.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING
from pymongo.errors import (
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from ..constants import (
    DEFAULT_MAX_REVISION_AGE_DAYS,
    DEFAULT_MAX_REVISIONS,
    DEFAULT_RECONCILE_MODE,
    DEFAULT_TRASH_TTL_DAYS,
    MANIFEST_REVISIONS_COLLECTION,
    OWNED_ARTIFACTS_COLLECTION,
    RESERVED_COLLECTION_NAMES,
    RESERVED_TRASH_PREFIX,
    SUPPORTED_RECONCILE_MODES,
    TRASH_COLLECTION,
)
from .manifest_diff import filter_patch_by_prefix, manifest_patch
from .manifest_hash import compute_manifest_hash, compute_schema_hash, is_current_version
from .reconciler_events import (
    EVENT_CONFIRM_REQUIRED,
    EVENT_LOCKED,
    EVENT_OP_APPLIED,
    EVENT_PLAN_BUILT,
    EVENT_QUARANTINED,
    emit_event,
    trace_span,
)
from .reconciler_gates import GateResult, evaluate_gates
from .reconciler_store import (
    RECONCILER_INTERNAL_COLLECTIONS,
    acquire_lock,
    ensure_collection_exists,
    make_holder_id,
    next_revision_number,
    release_lock,
)

logger = logging.getLogger(__name__)


# ----- Types --------------------------------------------------------------


OpKind = Literal[
    "add_collection",
    "drop_collection",
    "rename_collection",
    "add_index",
    "drop_index",
    "update_index",
    "disable_service",
    "enable_service",
]

ArtifactType = Literal[
    "collection",
    "index",
    "service_collection",
    "search_index",
]


_VALID_BASE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")
"""Allowed characters in a declared collection or index base name.

Excludes anything that could collide with engine-internal prefixes
(``_mdb_`` / ``system.`` / ``_mdb_trash__``) or MongoDB reserved
namespaces.
"""


@dataclass
class ReconcileOp:
    """A single operation within a reconcile plan."""

    op: OpKind
    collection: str = ""
    name: str = ""  # For indexes: the base (unprefixed) index name.
    spec: dict[str, Any] = field(default_factory=dict)
    previous_spec: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    # Optional rename metadata: when op == "rename_collection".
    rename_from: str = ""
    # Marks ops that were planned but skipped (protect list, safe mode, etc.).
    skipped: bool = False
    # Human-readable skip reason when skipped is True.
    skipped_reason: str = ""
    # For service-owned ops: the owner service name ("memory" | "graph" | "osi").
    service: str = ""
    artifact_type: ArtifactType = "collection"
    # JSON Patch entries (subset of the full manifest diff) that caused
    # this op. Useful for audit / `trash ls` output.
    patch: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconcilePlan:
    """A structured plan describing desired state transitions."""

    slug: str
    mode: str
    from_hash: str | None
    to_hash: str
    schema_hash: str
    ops: list[ReconcileOp] = field(default_factory=list)
    # Full JSON Patch from the previous manifest to the new one (runtime
    # fields already stripped by ``canonicalize_manifest``). Empty when
    # there was no previous manifest.
    patch: list[dict[str, Any]] = field(default_factory=list)

    # Set when plan was built but nothing needs to change.
    is_noop: bool = False
    # Populated by ``apply()`` when confirm_if gates trip.
    gate_result: GateResult | None = None

    def add(self, op: ReconcileOp) -> None:
        self.ops.append(op)

    @property
    def is_destructive(self) -> bool:
        destructive_ops = {"drop_collection", "drop_index", "disable_service"}
        return any(op.op in destructive_ops and not op.skipped for op in self.ops)

    @property
    def summary(self) -> dict[str, int]:
        buckets: dict[str, int] = {}
        for op in self.ops:
            key = f"{op.op}{'_skipped' if op.skipped else ''}"
            buckets[key] = buckets.get(key, 0) + 1
        return buckets

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "mode": self.mode,
            "from_hash": self.from_hash,
            "to_hash": self.to_hash,
            "schema_hash": self.schema_hash,
            "is_noop": self.is_noop,
            "is_destructive": self.is_destructive,
            "summary": self.summary,
            "ops": [op.to_dict() for op in self.ops],
            "patch": list(self.patch),
            "gate_result": self.gate_result.to_dict() if self.gate_result else None,
        }


# ----- Service artifact lister protocol ----------------------------------


ArtifactListEntry = dict[str, Any]  # {artifact_type, collection, name, spec, service}

ServiceArtifactLister = Callable[[str, dict[str, Any] | None], Awaitable[list[ArtifactListEntry]]]
"""A coroutine ``async def list_owned_artifacts(slug, prev_manifest)``.

Registered by memory / graph / OSI initializers so the reconciler can
quarantine the physical collections each service used to own, when that
service is disabled in the new manifest. ``prev_manifest`` is the last
applied manifest (or ``None`` on first registration); listers typically
use it to recover custom collection names.
"""


RenameOp = dict[str, Any]
"""Rename descriptor returned by ``ServiceRenameDetector``.

Shape: ``{"collection": "<prefixed>", "rename_from": "<prefixed>",
"service": "<name>", "reason": "<human>"}``. The reconciler treats
this as a ``rename_collection`` op instead of a
disable-plus-re-enable pair, so no data is quarantined.
"""


ServiceRenameDetector = Callable[
    [str, dict[str, Any] | None, dict[str, Any]],
    Awaitable[list[RenameOp]],
]
"""Optional sibling of ``ServiceArtifactLister`` for in-place renames.

``async def detect_renames(slug, prev_manifest, new_manifest) -> list[RenameOp]``
"""


# ----- Helpers -----------------------------------------------------------


def _prefixed_collection(slug: str, base: str) -> str:
    """Return the physical (slug-prefixed) collection name."""
    if base.startswith(f"{slug}_"):
        return base
    return f"{slug}_{base}"


def _base_collection(slug: str, physical: str) -> str:
    """Return the unprefixed base collection name, or the physical name as-is."""
    prefix = f"{slug}_"
    if physical.startswith(prefix):
        return physical[len(prefix) :]
    return physical


def _normalize_index_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned copy of an index spec for storage and diffing.

    Mongo's ``listIndexes`` output includes fields like ``ns`` / ``v`` that
    aren't part of our managed spec; we drop them so diffs don't oscillate.
    """
    ignored = {"ns", "v", "wait_for_ready"}
    return {k: v for k, v in spec.items() if k not in ignored}


def _spec_hash(spec: dict[str, Any]) -> str:
    """Deterministic hash of a spec (used for "changed?" detection)."""
    import hashlib

    from .manifest_hash import _canonical_json  # type: ignore[attr-defined]

    return hashlib.sha256(_canonical_json(_normalize_index_spec(spec)).encode()).hexdigest()[:16]


def _trash_name(slug: str, kind: str, original_name: str, revision: int) -> str:
    """Build a unique trash name under the ``_mdb_trash__`` prefix.

    Keeps MongoDB namespace length comfortably under the 120-char ceiling
    by trimming the original component when needed.
    """
    ts = int(time.time())
    base = f"{RESERVED_TRASH_PREFIX}{slug}__{kind}__{original_name}__r{revision}__{ts}"
    # Leave room for db.name; trim the middle if it's too long.
    if len(base) > 110:
        head = f"{RESERVED_TRASH_PREFIX}{slug}__{kind}__"
        tail = f"__r{revision}__{ts}"
        allowed = 110 - len(head) - len(tail)
        original_name = original_name[: max(4, allowed)]
        base = f"{head}{original_name}{tail}"
    return base


def _retention(manifest: dict[str, Any]) -> dict[str, int]:
    tracking = manifest.get("manifest_tracking") or {}
    retention = tracking.get("retention") or {}
    return {
        "max_revisions": int(retention.get("max_revisions", DEFAULT_MAX_REVISIONS)),
        "max_age_days": int(retention.get("max_age_days", DEFAULT_MAX_REVISION_AGE_DAYS)),
        "trash_ttl_days": int(retention.get("trash_ttl_days", DEFAULT_TRASH_TTL_DAYS)),
    }


def _mode(manifest: dict[str, Any]) -> str:
    tracking = manifest.get("manifest_tracking") or {}
    mode = tracking.get("mode", DEFAULT_RECONCILE_MODE)
    if mode not in SUPPORTED_RECONCILE_MODES:
        logger.warning(f"Unknown manifest_tracking.mode={mode!r}; falling back to {DEFAULT_RECONCILE_MODE}")
        return DEFAULT_RECONCILE_MODE
    return mode


def _protected(manifest: dict[str, Any]) -> set[str]:
    tracking = manifest.get("manifest_tracking") or {}
    return set(tracking.get("protect_collections") or [])


def _allow_immediate_drop(manifest: dict[str, Any]) -> bool:
    tracking = manifest.get("manifest_tracking") or {}
    return bool(tracking.get("allow_immediate_drop", False))


def _validate_base_name(kind: str, name: str) -> None:
    """Reject reserved / malformed declared names at planning time.

    Prevents a manifest from being able to drive renames into engine
    reserved namespaces (``_mdb_trash__...``) or from declaring
    collections that collide with engine internals.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"{kind} name must be a non-empty string, got {name!r}")
    if name.startswith("_mdb_") or name.startswith(RESERVED_TRASH_PREFIX):
        raise ValueError(f"{kind} {name!r} uses the reserved '_mdb_' prefix; this namespace is engine-owned")
    if name in RESERVED_COLLECTION_NAMES:
        raise ValueError(f"{kind} {name!r} is reserved for engine internals")
    if not _VALID_BASE_NAME.match(name):
        raise ValueError(f"{kind} {name!r} must match /^[A-Za-z][A-Za-z0-9_-]*$/ (got unsupported characters)")


def _env_confirm_flag() -> bool:
    """True when the operator opted in to bypass ``confirm_if`` via env."""
    raw = os.environ.get("MDB_CONFIRM", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ----- The Reconciler ---------------------------------------------------


class Reconciler:
    """Diff a manifest against the engine-owned artifact ledger and apply the delta."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        service_listers: dict[str, ServiceArtifactLister] | None = None,
        service_rename_detectors: dict[str, ServiceRenameDetector] | None = None,
    ) -> None:
        self._db = db
        self._service_listers: dict[str, ServiceArtifactLister] = dict(service_listers or {})
        self._service_rename_detectors: dict[str, ServiceRenameDetector] = dict(service_rename_detectors or {})

    # -- Service registration --------------------------------------------

    def register_service_lister(self, service: str, lister: ServiceArtifactLister) -> None:
        """Register a coroutine that lists artifacts owned by a service.

        ``service`` is the logical name (``"memory"``, ``"graph"``, ``"osi"``).
        The coroutine should return a list of dicts:
        ``{"artifact_type": "service_collection", "collection": "<prefixed>",
          "name": "<prefixed>", "spec": {...}, "service": service}``.
        """
        self._service_listers[service] = lister

    def register_service_rename_detector(
        self,
        service: str,
        detector: ServiceRenameDetector,
    ) -> None:
        """Register a coroutine that detects in-place service renames.

        When this returns a non-empty list, the reconciler plans a
        ``rename_collection`` op instead of ``disable_service`` +
        ``enable_service``, preserving data without a trash cycle.
        """
        self._service_rename_detectors[service] = detector

    # -- Plan ------------------------------------------------------------

    async def plan(
        self,
        slug: str,
        desired_manifest: dict[str, Any],
        *,
        prev_hash: str | None,
        prev_manifest: dict[str, Any] | None = None,
    ) -> ReconcilePlan:
        """Produce a :class:`ReconcilePlan` without touching the database.

        ``prev_manifest`` is the previously applied manifest, used by
        service listers to recover custom collection names when the
        service is being disabled.
        """
        t_start = time.monotonic()
        mode = _mode(desired_manifest)
        to_hash = compute_manifest_hash(desired_manifest)
        schema_hash = compute_schema_hash(desired_manifest)

        # If the stored hash was produced by a prior hash schema version
        # we can't trust the equality check — force a plan so we re-walk
        # the ledger once. The plan is usually a no-op in practice
        # because the ledger already matches, and the new hash gets
        # persisted on the resulting revision.
        effective_prev_hash = prev_hash if is_current_version(prev_hash) else None

        plan = ReconcilePlan(
            slug=slug,
            mode=mode,
            from_hash=prev_hash,
            to_hash=to_hash,
            schema_hash=schema_hash,
        )

        # Structural JSON patch — cheap and independent of ledger state.
        try:  # nosemgrep
            plan.patch = manifest_patch(prev_manifest, desired_manifest)
        except Exception as e:  # noqa: BLE001
            logger.debug("[%s] manifest_patch failed (non-fatal): %s", slug, e)
            plan.patch = []

        if effective_prev_hash == to_hash:
            plan.is_noop = True
            emit_event(
                EVENT_PLAN_BUILT,
                slug=slug,
                n_ops=0,
                mode=mode,
                from_hash=prev_hash,
                to_hash=to_hash,
                destructive=False,
                is_noop=True,
                duration_ms=int((time.monotonic() - t_start) * 1000),
            )
            return plan

        desired = self._extract_desired(slug, desired_manifest)
        current = await self._load_ledger(slug)

        self._plan_collections(plan, slug, desired, current, desired_manifest)
        self._plan_indexes(plan, slug, desired, current, desired_manifest)
        await self._plan_services(plan, slug, desired_manifest, prev_manifest)

        # Attach per-op patch slices so tombstones can record the exact
        # removed subtree. We do this last so every op is already in place.
        self._annotate_ops_with_patch(plan)

        emit_event(
            EVENT_PLAN_BUILT,
            slug=slug,
            n_ops=len(plan.ops),
            mode=mode,
            from_hash=prev_hash,
            to_hash=to_hash,
            destructive=plan.is_destructive,
            is_noop=False,
            duration_ms=int((time.monotonic() - t_start) * 1000),
        )
        return plan

    # -- Apply -----------------------------------------------------------

    async def apply(
        self,
        plan: ReconcilePlan,
        *,
        manifest: dict[str, Any],
        applied_by: str = "startup",
        dry_run: bool = False,
        persist_manifest: bool = True,
        confirm: bool = False,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> dict[str, Any]:
        """Execute a plan. Returns the recorded revision document (or draft).

        Args:
            plan: The plan to apply (produced by :meth:`plan`).
            manifest: Desired manifest to persist to ``apps_config``.
            applied_by: Audit marker for the revision row.
            dry_run: When True, evaluate the plan + gates but do not
                mutate any state. Returns ``status="dry_run"``.
            persist_manifest: When True (default), ``apps_config`` is
                atomically replaced inside the reconciler lock so
                ledger + revision + apps_config advance together.
                Set False to opt out (used by legacy callers that
                persist externally).
            confirm: When True, bypass ``manifest_tracking.confirm_if``
                safety gates. Equivalent to ``MDB_CONFIRM=1`` or
                ``--yes`` on the CLI.
            caused_by_commit: Optional git SHA / build tag; also read
                from ``GIT_COMMIT`` / ``MDB_GIT_COMMIT`` env if not
                supplied.
            caused_by_user: Optional operator identity; also read from
                ``MDB_APPLIED_BY`` / ``USER`` env if not supplied.
        """
        if plan.is_noop:
            logger.info(f"[{plan.slug}] Reconciler: no-op (hash unchanged)")
            if persist_manifest and not dry_run:
                # Even a no-op persists fresh _applied_* metadata so
                # startup is idempotent across hash schema bumps.
                await self._persist_manifest(
                    plan.slug,
                    manifest,
                    revision=None,
                    to_hash=plan.to_hash,
                    schema_hash=plan.schema_hash,
                )
            return {"status": "noop", "revision": None, "plan": plan.to_dict()}

        # --- Safety gates (confirm_if) -----------------------------------
        env_confirm = confirm or _env_confirm_flag()
        gate_result: GateResult = await evaluate_gates(
            db=self._db,
            slug=plan.slug,
            manifest=manifest,
            plan=plan,
            env_confirm=env_confirm,
        )
        plan.gate_result = gate_result
        if gate_result.blocked:
            emit_event(
                EVENT_CONFIRM_REQUIRED,
                slug=plan.slug,
                reasons="; ".join(gate_result.reasons),
                destructive_ops=gate_result.destructive_ops,
                docs_at_risk=gate_result.docs_at_risk,
            )
            logger.warning(
                "[%s] Reconciler: confirm_if tripped (%s); refusing to apply",
                plan.slug,
                "; ".join(gate_result.reasons),
            )
            return {
                "status": "confirmation_required",
                "revision": None,
                "plan": plan.to_dict(),
                "reasons": list(gate_result.reasons),
            }

        started = time.monotonic()
        holder = make_holder_id(f"reconcile:{plan.slug}")
        locked = False
        if not dry_run:
            locked = await acquire_lock(self._db, plan.slug, holder=holder)
            if not locked:
                emit_event(EVENT_LOCKED, slug=plan.slug, holder=holder, acquired=False)
                logger.warning(
                    f"[{plan.slug}] Reconciler: could not acquire lock; another worker is applying a revision"
                )
                return {"status": "locked", "revision": None, "plan": plan.to_dict()}
            emit_event(EVENT_LOCKED, slug=plan.slug, holder=holder, acquired=True)

        revision_number = 0
        commit = caused_by_commit or os.environ.get("MDB_GIT_COMMIT") or os.environ.get("GIT_COMMIT")
        user = caused_by_user or os.environ.get("MDB_APPLIED_BY") or os.environ.get("USER")
        try:
            if not dry_run:
                revision_number = await next_revision_number(self._db, plan.slug)

            executed_ops: list[ReconcileOp] = []
            for op in plan.ops:
                if op.skipped:
                    executed_ops.append(op)
                    continue
                t_op = time.monotonic()
                try:  # nosemgrep
                    if dry_run:
                        executed_ops.append(op)
                        continue
                    with trace_span(
                        "mdb.reconcile.op",
                        slug=plan.slug,
                        op=op.op,
                        collection=op.collection,
                        op_name=op.name,
                    ):
                        await self._execute_op(
                            plan.slug,
                            op,
                            revision_number,
                            manifest,
                            caused_by_commit=commit,
                            caused_by_user=user,
                        )
                    emit_event(
                        EVENT_OP_APPLIED,
                        slug=plan.slug,
                        op=op.op,
                        collection=op.collection,
                        op_name=op.name,
                        duration_ms=int((time.monotonic() - t_op) * 1000),
                    )
                    executed_ops.append(op)
                except Exception as e:  # noqa: BLE001  (record + continue; plan-level revision still wins)
                    logger.exception(f"[{plan.slug}] Reconciler: op {op.op} {op.collection}/{op.name} failed: {e}")
                    op_copy = ReconcileOp(**op.to_dict())
                    op_copy.skipped = True
                    op_copy.skipped_reason = f"exec_error: {e}"
                    executed_ops.append(op_copy)
            plan.ops = executed_ops

            revision_doc: dict[str, Any] = {}
            if not dry_run:
                revision_doc = await self._record_revision(
                    plan=plan,
                    manifest=manifest,
                    revision_number=revision_number,
                    applied_by=applied_by,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    caused_by_commit=commit,
                    caused_by_user=user,
                )

                if persist_manifest:
                    # CRITICAL: persist inside the lock so ledger +
                    # revision + apps_config either all advance together
                    # or none of them do. A crash between steps leaves
                    # ledger/revisions ahead of apps_config; next boot
                    # re-plans and converges (idempotent).
                    await self._persist_manifest(
                        plan.slug,
                        manifest,
                        revision=revision_number,
                        to_hash=plan.to_hash,
                        schema_hash=plan.schema_hash,
                    )

                await self._gc_revisions(plan.slug, manifest)

            return {
                "status": "applied" if not dry_run else "dry_run",
                "revision": revision_doc,
                "plan": plan.to_dict(),
            }
        finally:
            if locked:
                await release_lock(self._db, plan.slug, holder=holder)

    # -- Extraction (desired state from manifest) -----------------------

    def _extract_desired(self, slug: str, manifest: dict[str, Any]) -> dict[str, Any]:
        """Project the manifest into {collections, indexes} keyed by name.

        Raises ``ValueError`` when the manifest declares reserved names
        or attempts to rename from engine-internal namespaces.
        """
        collections: dict[str, dict[str, Any]] = {}
        for coll_name, coll_cfg in (manifest.get("collections") or {}).items():
            _validate_base_name("collection", coll_name)
            rename_sources = list((coll_cfg or {}).get("rename_from") or [])
            for src in rename_sources:
                if not isinstance(src, str) or not src:
                    raise ValueError(f"collection {coll_name!r}: rename_from entries must be non-empty strings")
                # rename_from uses base names too; same reserved-name rules.
                _validate_base_name("rename_from", src)
            collections[coll_name] = {
                "rename_from": rename_sources,
                "spec": coll_cfg,
            }

        indexes: dict[tuple[str, str], dict[str, Any]] = {}
        for coll_name, defs in (manifest.get("managed_indexes") or {}).items():
            # managed_indexes may reference collections not in `collections`
            # (legacy manifests). Still validate the collection name.
            _validate_base_name("managed_indexes collection", coll_name)
            if not isinstance(defs, list):
                continue
            for idx_def in defs:
                name = idx_def.get("name")
                if not name:
                    continue
                _validate_base_name("index", name)
                rename_sources = list(idx_def.get("rename_from") or [])
                for src in rename_sources:
                    _validate_base_name("index rename_from", src)
                indexes[(coll_name, name)] = {
                    "rename_from": rename_sources,
                    "spec": idx_def,
                }
        return {"collections": collections, "indexes": indexes}

    # -- Ledger loading --------------------------------------------------

    async def _load_ledger(self, slug: str) -> dict[str, Any]:
        """Load current ledger rows for a slug, keyed by artifact identity."""
        cursor = self._db[OWNED_ARTIFACTS_COLLECTION].find({"slug": slug})
        rows = await cursor.to_list(length=None)

        collections: dict[str, dict[str, Any]] = {}
        indexes: dict[tuple[str, str], dict[str, Any]] = {}

        for row in rows:
            atype = row.get("artifact_type")
            coll = row.get("collection", "")
            name = row.get("name", "")
            if atype == "collection":
                collections[coll] = row
            elif atype == "index":
                indexes[(coll, name)] = row

        return {"collections": collections, "indexes": indexes}

    # -- Planning: collections ------------------------------------------

    def _plan_collections(
        self,
        plan: ReconcilePlan,
        slug: str,
        desired: dict[str, Any],
        current: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        desired_cols: dict[str, dict[str, Any]] = desired["collections"]
        current_cols: dict[str, dict[str, Any]] = current["collections"]
        protected = _protected(manifest)
        mode = plan.mode

        # Add / keep
        # Track rename_from handling so we don't double-plan ops.
        consumed_current: set[str] = set()
        for name, desired_entry in desired_cols.items():
            if name in current_cols:
                consumed_current.add(name)
                continue

            # Try to recover via rename_from
            renamed_source = next(
                (src for src in desired_entry["rename_from"] if src in current_cols and src not in consumed_current),
                None,
            )
            if renamed_source:
                consumed_current.add(renamed_source)
                plan.add(
                    ReconcileOp(
                        op="rename_collection",
                        collection=name,
                        name=name,
                        spec=desired_entry["spec"],
                        rename_from=renamed_source,
                        reason=f"rename_from: {renamed_source} -> {name}",
                    )
                )
                continue

            plan.add(
                ReconcileOp(
                    op="add_collection",
                    collection=name,
                    name=name,
                    spec=desired_entry["spec"],
                    reason="new collection in manifest",
                )
            )

        # Drops (anything in current but not consumed)
        for name, current_row in current_cols.items():
            if name in desired_cols or name in consumed_current:
                continue

            op = ReconcileOp(
                op="drop_collection",
                collection=name,
                name=name,
                previous_spec=current_row.get("spec") or {},
                reason="collection no longer declared in manifest",
            )

            if name in protected:
                op.skipped = True
                op.skipped_reason = "collection listed in manifest_tracking.protect_collections"
            elif mode == "safe":
                op.skipped = True
                op.skipped_reason = "manifest_tracking.mode=safe; removals are logged only"

            plan.add(op)

    # -- Planning: indexes ----------------------------------------------

    def _plan_indexes(
        self,
        plan: ReconcilePlan,
        slug: str,
        desired: dict[str, Any],
        current: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        desired_idx: dict[tuple[str, str], dict[str, Any]] = desired["indexes"]
        current_idx: dict[tuple[str, str], dict[str, Any]] = current["indexes"]
        mode = plan.mode

        consumed_current: set[tuple[str, str]] = set()
        for (coll, name), entry in desired_idx.items():
            current_key = (coll, name)
            if current_key in current_idx:
                # Compare spec hash; if different, update.
                current_row = current_idx[current_key]
                cur_hash = current_row.get("spec_hash")
                new_hash = _spec_hash(entry["spec"])
                if cur_hash != new_hash:
                    plan.add(
                        ReconcileOp(
                            op="update_index",
                            collection=coll,
                            name=name,
                            spec=entry["spec"],
                            previous_spec=current_row.get("spec") or {},
                            reason="index spec changed",
                        )
                    )
                consumed_current.add(current_key)
                continue

            # rename_from check
            renamed = next(
                (
                    (coll, old_name)
                    for old_name in entry["rename_from"]
                    if (coll, old_name) in current_idx and (coll, old_name) not in consumed_current
                ),
                None,
            )
            if renamed:
                consumed_current.add(renamed)
                plan.add(
                    ReconcileOp(
                        op="drop_index",
                        collection=coll,
                        name=renamed[1],
                        previous_spec=current_idx[renamed].get("spec") or {},
                        reason=f"rename_from source: {renamed[1]} -> {name} (drop old, add new)",
                    )
                )
                plan.add(
                    ReconcileOp(
                        op="add_index",
                        collection=coll,
                        name=name,
                        spec=entry["spec"],
                        reason=f"rename_from target: {renamed[1]} -> {name}",
                    )
                )
                continue

            plan.add(
                ReconcileOp(
                    op="add_index",
                    collection=coll,
                    name=name,
                    spec=entry["spec"],
                    reason="new index in manifest",
                )
            )

        # Drops (anything in current but not consumed)
        for (coll, name), current_row in current_idx.items():
            if (coll, name) in desired_idx or (coll, name) in consumed_current:
                continue

            op = ReconcileOp(
                op="drop_index",
                collection=coll,
                name=name,
                previous_spec=current_row.get("spec") or {},
                reason="index no longer declared in manifest",
            )
            if mode == "safe":
                op.skipped = True
                op.skipped_reason = "manifest_tracking.mode=safe; removals are logged only"
            plan.add(op)

    # -- Planning: services (memory/graph/osi) --------------------------

    async def _plan_services(
        self,
        plan: ReconcilePlan,
        slug: str,
        manifest: dict[str, Any],
        prev_manifest: dict[str, Any] | None,
    ) -> None:
        """Detect services disabled in the new manifest and quarantine their artifacts."""
        service_flags = {
            "memory": self._service_enabled(manifest.get("memory_config")),
            "graph": self._service_enabled(manifest.get("graph_config")),
            "osi": self._service_enabled(manifest.get("osi_config")),
        }
        prev_flags = {}
        if prev_manifest:
            prev_flags = {
                "memory": self._service_enabled(prev_manifest.get("memory_config")),
                "graph": self._service_enabled(prev_manifest.get("graph_config")),
                "osi": self._service_enabled(prev_manifest.get("osi_config")),
            }

        mode = plan.mode

        # First, give each service a chance to declare an in-place rename
        # instead of disable+enable so data is preserved.
        rename_handled_services: set[str] = set()
        for service, detector in self._service_rename_detectors.items():
            if not service_flags.get(service, False):
                continue
            if prev_manifest is not None and not prev_flags.get(service, False):
                continue
            try:  # nosemgrep
                renames = await detector(slug, prev_manifest, manifest)
            except Exception as e:  # noqa: BLE001
                logger.debug("[%s] Service rename detector '%s' failed: %s", slug, service, e)
                renames = []
            for r in renames or []:
                src = r.get("rename_from", "")
                dst = r.get("collection", "")
                if not src or not dst or src == dst:
                    continue
                plan.add(
                    ReconcileOp(
                        op="rename_collection",
                        collection=dst,
                        name=dst,
                        spec=r.get("spec") or {},
                        rename_from=src,
                        reason=r.get("reason") or f"service {service}: rename {src} -> {dst}",
                        service=service,
                        artifact_type="service_collection",
                    )
                )
                rename_handled_services.add(service)

        for service, enabled in service_flags.items():
            if enabled:
                continue
            # Only quarantine when the service was previously enabled, OR on
            # first run (prev_manifest is None) the reconciler still inspects
            # the DB via the lister in case a previous engine left artifacts.
            if prev_manifest is not None and not prev_flags.get(service):
                continue
            if service in rename_handled_services:
                continue
            lister = self._service_listers.get(service)
            if not lister:
                continue
            try:  # nosemgrep
                artifacts = await lister(slug, prev_manifest)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{slug}] Service lister '{service}' failed: {e}")
                continue
            for entry in artifacts:
                op = ReconcileOp(
                    op="disable_service",
                    collection=entry.get("collection", ""),
                    name=entry.get("name", ""),
                    previous_spec=entry.get("spec") or {},
                    reason=f"service '{service}' disabled in manifest",
                    service=service,
                    artifact_type=entry.get("artifact_type", "service_collection"),
                )
                if mode == "safe":
                    op.skipped = True
                    op.skipped_reason = "manifest_tracking.mode=safe"
                plan.add(op)

    @staticmethod
    def _service_enabled(cfg: Any, *, default: bool = False) -> bool:
        if cfg is True:
            return True
        if isinstance(cfg, str) and cfg:
            return True
        if isinstance(cfg, dict):
            return bool(cfg.get("enabled", default)) or "preset" in cfg
        return False

    # -- Planning: patch slicing ----------------------------------------

    def _annotate_ops_with_patch(self, plan: ReconcilePlan) -> None:
        """Attach per-op JSON Patch slices for audit / tombstones.

        Each op is matched to the patch entries that describe its
        collection or index subtree. Best-effort: falls back to an empty
        slice when the plan has no patch (e.g. prev_manifest was None).
        """
        if not plan.patch:
            return
        for op in plan.ops:
            prefix = self._patch_prefix_for_op(op)
            if not prefix:
                continue
            op.patch = filter_patch_by_prefix(plan.patch, prefix)

    @staticmethod
    def _patch_prefix_for_op(op: ReconcileOp) -> str:
        if op.op in ("add_collection", "drop_collection", "rename_collection"):
            return f"/collections/{op.collection}"
        if op.op in ("add_index", "drop_index", "update_index"):
            return f"/managed_indexes/{op.collection}"
        if op.op == "disable_service":
            # Service ops are driven by *_config blocks at the root.
            return f"/{op.service}_config" if op.service else ""
        return ""

    # -- Execution ------------------------------------------------------

    async def _execute_op(
        self,
        slug: str,
        op: ReconcileOp,
        revision: int,
        manifest: dict[str, Any],
        *,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> None:
        """Dispatch a single op to its executor."""
        if op.op == "add_collection":
            await self._op_add_collection(slug, op)
        elif op.op == "rename_collection":
            await self._op_rename_collection(slug, op)
        elif op.op == "drop_collection":
            await self._op_drop_collection(
                slug, op, revision, manifest, caused_by_commit=caused_by_commit, caused_by_user=caused_by_user
            )
        elif op.op == "add_index":
            await self._op_add_index(slug, op)
        elif op.op == "update_index":
            await self._op_update_index(slug, op)
        elif op.op == "drop_index":
            await self._op_drop_index(
                slug, op, revision, manifest, caused_by_commit=caused_by_commit, caused_by_user=caused_by_user
            )
        elif op.op == "disable_service":
            await self._op_disable_service(
                slug, op, revision, manifest, caused_by_commit=caused_by_commit, caused_by_user=caused_by_user
            )
        else:
            logger.warning(f"[{slug}] Unknown reconcile op: {op.op}")

    async def _op_add_collection(self, slug: str, op: ReconcileOp) -> None:
        physical = _prefixed_collection(slug, op.collection)
        await ensure_collection_exists(self._db, physical)
        await self._ledger_upsert(
            slug,
            artifact_type="collection",
            collection=op.collection,
            name=op.collection,
            spec=op.spec,
        )

    async def _op_rename_collection(self, slug: str, op: ReconcileOp) -> None:
        """Rename an existing collection to match a new manifest name."""
        # Service-owned renames pass pre-prefixed physical names; declared
        # collection renames use base names.
        if op.service and op.artifact_type == "service_collection":
            src_physical = op.rename_from
            dst_physical = op.collection
        else:
            src_physical = _prefixed_collection(slug, op.rename_from)
            dst_physical = _prefixed_collection(slug, op.collection)
        try:
            await self._db[src_physical].rename(dst_physical, dropTarget=False)
        except OperationFailure as e:
            if "source namespace does not exist" in str(e).lower():
                await ensure_collection_exists(self._db, dst_physical)
            else:
                raise

        if not op.service:
            # Ledger: remove old, upsert new.
            await self._db[OWNED_ARTIFACTS_COLLECTION].delete_one(
                {"slug": slug, "artifact_type": "collection", "collection": op.rename_from, "name": op.rename_from}
            )
            await self._ledger_upsert(
                slug,
                artifact_type="collection",
                collection=op.collection,
                name=op.collection,
                spec=op.spec,
            )

    async def _op_drop_collection(
        self,
        slug: str,
        op: ReconcileOp,
        revision: int,
        manifest: dict[str, Any],
        *,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> None:
        """Quarantine a collection via rename (or hard-drop in strict-empty mode)."""
        physical = _prefixed_collection(slug, op.collection)
        mode = _mode(manifest)
        allow_immediate = _allow_immediate_drop(manifest)

        # Decide quarantine vs immediate drop.
        try:
            doc_count = await self._db[physical].estimated_document_count()
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
            doc_count = -1  # unknown -> be safe

        if mode == "strict" and allow_immediate and doc_count == 0:
            try:
                await self._db.drop_collection(physical)
            except OperationFailure as e:
                logger.debug(f"[{slug}] Immediate drop of {physical} failed: {e}")
        else:
            trash_name = _trash_name(slug, "collection", op.collection, revision)
            try:
                await self._db[physical].rename(trash_name, dropTarget=False)
            except OperationFailure as e:
                if "source namespace does not exist" in str(e).lower():
                    logger.debug(f"[{slug}] Collection {physical} already gone; skipping quarantine.")
                else:
                    raise
            else:
                await self._write_tombstone(
                    slug=slug,
                    kind="collection",
                    original_name=op.collection,
                    trash_name=trash_name,
                    spec=op.previous_spec,
                    doc_count=doc_count,
                    revision=revision,
                    manifest=manifest,
                    removed_subtree=op.patch,
                    caused_by_commit=caused_by_commit,
                    caused_by_user=caused_by_user,
                )

        # Remove ledger entry regardless.
        await self._db[OWNED_ARTIFACTS_COLLECTION].delete_one(
            {"slug": slug, "artifact_type": "collection", "collection": op.collection, "name": op.collection}
        )

    async def _op_add_index(self, slug: str, op: ReconcileOp) -> None:
        await self._apply_index_spec(slug, op.collection, op.spec)
        await self._ledger_upsert(
            slug,
            artifact_type="index",
            collection=op.collection,
            name=op.name,
            spec=op.spec,
        )

    async def _op_update_index(self, slug: str, op: ReconcileOp) -> None:
        # Drop + re-create via same path used by apply; underlying
        # AsyncAtlasIndexManager handles idempotency + atlas search cases.
        await self._apply_index_spec(slug, op.collection, op.spec)
        await self._ledger_upsert(
            slug,
            artifact_type="index",
            collection=op.collection,
            name=op.name,
            spec=op.spec,
        )

    async def _op_drop_index(
        self,
        slug: str,
        op: ReconcileOp,
        revision: int,
        manifest: dict[str, Any],
        *,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> None:
        physical = _prefixed_collection(slug, op.collection)
        prefixed_idx = f"{slug}_{op.name}"

        # Best-effort drop; regular indexes use drop_index, search indexes
        # have their own API via AsyncAtlasIndexManager.
        dropped = False
        try:
            await self._db[physical].drop_index(prefixed_idx)
            dropped = True
        except OperationFailure as e:
            # Code 27 = IndexNotFound — that's fine, treat as already dropped.
            if getattr(e, "code", None) == 27 or "index not found" in str(e).lower():
                dropped = True
            else:
                logger.debug(f"[{slug}] drop_index({prefixed_idx}) failed, will try search-index path: {e}")

        if not dropped:
            # Try search-index drop (Atlas Search / Vector Search).
            try:  # nosemgrep
                from ..database.scoped_wrapper import AsyncAtlasIndexManager

                mgr = AsyncAtlasIndexManager(self._db[physical])
                await mgr.drop_search_index(prefixed_idx)
                dropped = True
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{slug}] drop_search_index({prefixed_idx}) failed: {e}")

        # Always write tombstone so rollback can recreate from spec.
        await self._write_tombstone(
            slug=slug,
            kind="index",
            original_name=f"{op.collection}::{op.name}",
            trash_name="",  # physical was dropped; no rename for indexes
            spec=op.previous_spec,
            doc_count=0,
            revision=revision,
            manifest=manifest,
            removed_subtree=op.patch,
            caused_by_commit=caused_by_commit,
            caused_by_user=caused_by_user,
            extra={"collection": op.collection, "index_name": op.name, "recreate_on_restore": True},
        )
        await self._db[OWNED_ARTIFACTS_COLLECTION].delete_one(
            {"slug": slug, "artifact_type": "index", "collection": op.collection, "name": op.name}
        )

    async def _op_disable_service(
        self,
        slug: str,
        op: ReconcileOp,
        revision: int,
        manifest: dict[str, Any],
        *,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> None:
        """Quarantine a service-owned collection."""
        physical = op.collection  # service listers return pre-prefixed names
        trash_name = _trash_name(slug, f"service_{op.service}", op.name, revision)
        try:
            await self._db[physical].rename(trash_name, dropTarget=False)
        except OperationFailure as e:
            if "source namespace does not exist" in str(e).lower():
                logger.debug(f"[{slug}] Service collection {physical} already gone.")
                return
            raise

        try:
            doc_count = await self._db[trash_name].estimated_document_count()
        except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
            doc_count = -1

        await self._write_tombstone(
            slug=slug,
            kind="service_collection",
            original_name=physical,
            trash_name=trash_name,
            spec=op.previous_spec,
            doc_count=doc_count,
            revision=revision,
            manifest=manifest,
            removed_subtree=op.patch,
            caused_by_commit=caused_by_commit,
            caused_by_user=caused_by_user,
            extra={"service": op.service},
        )

    # -- Index application (delegates to existing indexes/manager) -----

    async def _apply_index_spec(self, slug: str, collection: str, spec: dict[str, Any]) -> None:
        """Create or update a single index via the existing index manager."""
        from ..indexes import run_index_creation_for_collection

        physical = _prefixed_collection(slug, collection)
        name = spec.get("name", "")
        prefixed = dict(spec)
        if name and not name.startswith(f"{slug}_"):
            prefixed["name"] = f"{slug}_{name}"
        await run_index_creation_for_collection(
            db=self._db,
            slug=slug,
            collection_name=physical,
            index_definitions=[prefixed],
        )

    # -- Ledger / tombstone / manifest persistence ---------------------

    async def _ledger_upsert(
        self,
        slug: str,
        *,
        artifact_type: str,
        collection: str,
        name: str,
        spec: dict[str, Any],
    ) -> None:
        await self._db[OWNED_ARTIFACTS_COLLECTION].update_one(
            {
                "slug": slug,
                "artifact_type": artifact_type,
                "collection": collection,
                "name": name,
            },
            {
                "$set": {
                    "spec": spec,
                    "spec_hash": _spec_hash(spec),
                    "last_seen_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {
                    "slug": slug,
                    "artifact_type": artifact_type,
                    "collection": collection,
                    "name": name,
                    "created_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    async def _write_tombstone(
        self,
        *,
        slug: str,
        kind: str,
        original_name: str,
        trash_name: str,
        spec: dict[str, Any],
        doc_count: int,
        revision: int,
        manifest: dict[str, Any],
        removed_subtree: list[dict[str, Any]] | None = None,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        retention = _retention(manifest)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=retention["trash_ttl_days"])
        doc: dict[str, Any] = {
            "slug": slug,
            "kind": kind,
            "original_name": original_name,
            "trash_name": trash_name,
            "spec": spec,
            "doc_count": doc_count,
            "quarantined_at": now,
            "quarantined_in_revision": revision,
            "expires_at": expires_at,
            "removed_subtree": list(removed_subtree or []),
            "caused_by_commit": caused_by_commit,
            "caused_by_user": caused_by_user,
        }
        if extra:
            doc.update(extra)
        try:
            await self._db[TRASH_COLLECTION].insert_one(doc)
        except OperationFailure as e:
            logger.warning(f"[{slug}] Failed to write trash tombstone: {e}")
        emit_event(
            EVENT_QUARANTINED,
            slug=slug,
            kind=kind,
            original_name=original_name,
            trash_name=trash_name,
            doc_count=doc_count,
            revision=revision,
            caused_by_commit=caused_by_commit,
        )

    async def _persist_manifest(
        self,
        slug: str,
        manifest: dict[str, Any],
        *,
        revision: int | None,
        to_hash: str,
        schema_hash: str,
    ) -> None:
        """Atomically replace ``apps_config[slug]`` with reconciler metadata.

        Always called inside the reconciler advisory lock so ledger,
        revision, and app config advance together. Uses
        ``writeConcern={"w": "majority", "j": True}`` so the write is
        durable before we release the lock.
        """
        doc = dict(manifest)
        doc["_applied_hash"] = to_hash
        doc["_applied_schema_hash"] = schema_hash
        doc["_applied_at"] = datetime.now(timezone.utc)
        if revision is not None:
            doc["_applied_revision"] = revision
        collection = self._db.apps_config.with_options(
            write_concern=_majority_write_concern(),
        )
        try:
            await collection.replace_one({"slug": slug}, doc, upsert=True)
        except OperationFailure as e:
            logger.warning(f"[{slug}] apps_config persist failed inside lock: {e}")

    # -- Revision recording --------------------------------------------

    async def _record_revision(
        self,
        *,
        plan: ReconcilePlan,
        manifest: dict[str, Any],
        revision_number: int,
        applied_by: str,
        duration_ms: int,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> dict[str, Any]:
        doc = {
            "slug": plan.slug,
            "revision": revision_number,
            "hash": plan.to_hash,
            "schema_hash": plan.schema_hash,
            "parent_hash": plan.from_hash,
            "mode": plan.mode,
            "schema_version": manifest.get("schema_version"),
            "manifest": manifest,
            "applied_at": datetime.now(timezone.utc),
            "applied_by": applied_by,
            "duration_ms": duration_ms,
            "status": "applied",
            "summary": plan.summary,
            "is_destructive": plan.is_destructive,
            "changeset": [op.to_dict() for op in plan.ops],
            "patch": list(plan.patch),
            "caused_by_commit": caused_by_commit,
            "caused_by_user": caused_by_user,
        }
        collection = self._db[MANIFEST_REVISIONS_COLLECTION].with_options(
            write_concern=_majority_write_concern(),
        )
        try:
            await collection.insert_one(doc)
        except OperationFailure as e:
            logger.warning(f"[{plan.slug}] Failed to insert revision row: {e}")
        return doc

    async def _gc_revisions(self, slug: str, manifest: dict[str, Any]) -> None:
        retention = _retention(manifest)
        # Age-based pruning
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention["max_age_days"])
        try:
            await self._db[MANIFEST_REVISIONS_COLLECTION].delete_many({"slug": slug, "applied_at": {"$lt": cutoff}})
        except OperationFailure:
            pass

        # Count-based pruning: keep the newest N
        try:
            cursor = (
                self._db[MANIFEST_REVISIONS_COLLECTION]
                .find({"slug": slug}, {"_id": 1})
                .sort("revision", DESCENDING)
                .skip(retention["max_revisions"])
            )
            old_ids = [doc["_id"] for doc in await cursor.to_list(length=None)]
            if old_ids:
                await self._db[MANIFEST_REVISIONS_COLLECTION].delete_many({"_id": {"$in": old_ids}})
        except OperationFailure:
            pass

    # -- History / diff / trash API -------------------------------------

    async def get_history(self, slug: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent revision documents for a slug, newest first."""
        cursor = self._db[MANIFEST_REVISIONS_COLLECTION].find({"slug": slug}).sort("revision", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    async def head_revision(self, slug: str) -> dict[str, Any] | None:
        """Return the most recent revision doc for ``slug`` (or None)."""
        history = await self.get_history(slug, limit=1)
        return history[0] if history else None

    async def trash_list(self, slug: str | None = None) -> list[dict[str, Any]]:
        """List trash entries. ``slug=None`` returns every slug's trash."""
        q: dict[str, Any] = {}
        if slug:
            q["slug"] = slug
        cursor = self._db[TRASH_COLLECTION].find(q).sort("quarantined_at", DESCENDING)
        return await cursor.to_list(length=None)

    async def trash_summary(self) -> list[dict[str, Any]]:
        """Aggregate trash counts + estimated doc counts per slug."""
        pipeline = [
            {
                "$group": {
                    "_id": "$slug",
                    "n": {"$sum": 1},
                    "total_docs": {"$sum": {"$ifNull": ["$doc_count", 0]}},
                    "next_expires_at": {"$min": "$expires_at"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        out: list[dict[str, Any]] = []
        async for row in self._db[TRASH_COLLECTION].aggregate(pipeline):
            out.append(
                {
                    "slug": row.get("_id"),
                    "n": row.get("n", 0),
                    "total_docs": row.get("total_docs", 0),
                    "next_expires_at": row.get("next_expires_at"),
                }
            )
        return out

    async def trash_restore_plan(self, slug: str, trash_id: Any) -> dict[str, Any]:
        """Preview a restore without mutating anything.

        Returns ``{"can_restore": bool, "reasons": [...], "tombstone": {...}}``.
        """
        from bson import ObjectId

        if not isinstance(trash_id, ObjectId):
            try:  # nosemgrep
                trash_id = ObjectId(str(trash_id))
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"Invalid trash_id: {trash_id!r}") from e

        tombstone = await self._db[TRASH_COLLECTION].find_one({"_id": trash_id, "slug": slug})
        if not tombstone:
            return {
                "can_restore": False,
                "reasons": [f"no trash entry found for slug={slug!r} id={trash_id}"],
                "tombstone": None,
            }

        kind = tombstone.get("kind")
        reasons: list[str] = []
        if kind in ("collection", "service_collection"):
            trash_name = tombstone.get("trash_name") or ""
            original = tombstone.get("original_name") or ""
            if not trash_name:
                reasons.append("tombstone has no trash_name (possibly already purged)")
            else:
                try:
                    names = await self._db.list_collection_names(filter={"name": trash_name})
                    if trash_name not in names:
                        reasons.append(f"physical collection {trash_name!r} is missing (already swept?)")
                except OperationFailure as e:
                    reasons.append(f"could not verify physical collection presence: {e}")

            if kind == "collection":
                dst_physical = _prefixed_collection(slug, original)
            else:
                dst_physical = original
            try:
                names = await self._db.list_collection_names(filter={"name": dst_physical})
                if dst_physical in names:
                    reasons.append(f"destination {dst_physical!r} already exists")
            except OperationFailure as e:
                reasons.append(f"could not verify destination: {e}")
        elif kind == "index":
            coll = tombstone.get("collection")
            name = tombstone.get("index_name")
            spec = tombstone.get("spec") or {}
            if not coll or not name:
                reasons.append("tombstone missing collection / index_name fields")
            if not isinstance(spec, dict) or not spec:
                reasons.append("tombstone has no usable index spec to recreate")
            # A full dry-apply of the index is expensive; a simpler sanity
            # check is to require the base collection exists.
            if coll:
                physical = _prefixed_collection(slug, coll)
                try:
                    names = await self._db.list_collection_names(filter={"name": physical})
                    if physical not in names:
                        reasons.append(f"base collection {physical!r} is missing; create it before restoring the index")
                except OperationFailure as e:
                    reasons.append(f"could not verify base collection: {e}")
        else:
            reasons.append(f"unsupported trash kind: {kind!r}")

        return {
            "can_restore": not reasons,
            "reasons": reasons,
            "tombstone": tombstone,
        }

    async def trash_restore(
        self,
        slug: str,
        trash_id: Any,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restore a quarantined artifact back to its original name.

        Verifies the restore can succeed (via :meth:`trash_restore_plan`)
        *before* renaming anything. When ``dry_run=True``, only the
        preview is returned.
        """
        preview = await self.trash_restore_plan(slug, trash_id)
        if not preview["can_restore"]:
            if dry_run:
                return {"restored": False, "dry_run": True, **preview}
            raise ValueError("Cannot restore from trash: " + "; ".join(preview["reasons"]))
        if dry_run:
            return {"restored": False, "dry_run": True, **preview}

        from bson import ObjectId

        if not isinstance(trash_id, ObjectId):
            trash_id = ObjectId(str(trash_id))
        tombstone = preview["tombstone"]
        kind = tombstone.get("kind")

        if kind in ("collection", "service_collection"):
            trash_name = tombstone["trash_name"]
            original = tombstone["original_name"]
            if kind == "collection":
                dst_physical = _prefixed_collection(slug, original)
            else:
                dst_physical = original

            await self._db[trash_name].rename(dst_physical, dropTarget=False)

            if kind == "collection":
                await self._ledger_upsert(
                    slug,
                    artifact_type="collection",
                    collection=original,
                    name=original,
                    spec=tombstone.get("spec") or {},
                )

        elif kind == "index":
            coll = tombstone.get("collection")
            name = tombstone.get("index_name")
            spec = tombstone.get("spec") or {}
            await self._apply_index_spec(slug, coll, spec)
            await self._ledger_upsert(
                slug,
                artifact_type="index",
                collection=coll,
                name=name,
                spec=spec,
            )

        await self._db[TRASH_COLLECTION].delete_one({"_id": trash_id})
        return {"restored": True, "id": str(trash_id), "kind": kind}

    async def trash_purge(
        self,
        slug: str | None,
        *,
        expired_only: bool = True,
        ids: list[Any] | None = None,
    ) -> int:
        """Hard-drop trashed artifacts matching the filter. Returns count."""
        from bson import ObjectId

        q: dict[str, Any] = {}
        if slug:
            q["slug"] = slug
        if expired_only:
            q["expires_at"] = {"$lt": datetime.now(timezone.utc)}
        if ids:
            oids = []
            for i in ids:
                try:  # nosemgrep
                    oids.append(i if isinstance(i, ObjectId) else ObjectId(str(i)))
                except Exception:  # noqa: BLE001
                    continue
            q["_id"] = {"$in": oids}

        tombstones = await self._db[TRASH_COLLECTION].find(q).to_list(length=None)
        n = 0
        for t in tombstones:
            trash_name = t.get("trash_name") or ""
            if trash_name:
                try:
                    await self._db.drop_collection(trash_name)
                except OperationFailure as e:
                    logger.debug(f"[{t.get('slug')}] drop_collection({trash_name}) failed: {e}")
            await self._db[TRASH_COLLECTION].delete_one({"_id": t["_id"]})
            n += 1
        return n

    async def adopt(self, slug: str) -> dict[str, Any]:
        """Seed the ledger from existing ``<slug>_*`` collections + indexes.

        Useful on first upgrade for an app that already has physical
        state: without this, subsequent reconciles look like "add
        everything" against an empty ledger and can duplicate indexes.

        Returns ``{"adopted_collections": N, "adopted_indexes": N,
        "skipped": [...]}``.
        """
        adopted_cols = 0
        adopted_idx = 0
        skipped: list[str] = []

        prefix = f"{slug}_"
        try:
            names = await self._db.list_collection_names()
        except OperationFailure as e:
            raise RuntimeError(f"Could not list collections: {e}") from e

        for physical in names:
            if not physical.startswith(prefix):
                continue
            if physical.startswith(RESERVED_TRASH_PREFIX) or physical in RECONCILER_INTERNAL_COLLECTIONS:
                continue
            base = _base_collection(slug, physical)
            try:
                _validate_base_name("collection", base)
            except ValueError:
                skipped.append(physical)
                continue
            await self._ledger_upsert(
                slug,
                artifact_type="collection",
                collection=base,
                name=base,
                spec={"adopted": True},
            )
            adopted_cols += 1
            # Existing indexes — index names on disk carry the `<slug>_`
            # prefix, strip it when unprefixing.
            try:
                existing = await self._db[physical].index_information()
            except OperationFailure:
                continue
            for idx_name, idx_info in existing.items():
                if idx_name == "_id_":
                    continue
                base_idx = idx_name[len(prefix) :] if idx_name.startswith(prefix) else idx_name
                spec = {
                    "name": base_idx,
                    "keys": {k: v for k, v in (idx_info.get("key") or [])},
                    "unique": bool(idx_info.get("unique", False)),
                }
                await self._ledger_upsert(
                    slug,
                    artifact_type="index",
                    collection=base,
                    name=base_idx,
                    spec=spec,
                )
                adopted_idx += 1
        return {
            "adopted_collections": adopted_cols,
            "adopted_indexes": adopted_idx,
            "skipped": skipped,
        }

    # -- Change stream ---------------------------------------------------

    async def watch_revisions(
        self,
        slug: str,
        callback: Callable[[dict[str, Any]], Awaitable[None] | None],
        *,
        resume_after: Any = None,
    ) -> None:
        """Tail ``_mdb_manifest_revisions`` for a slug, invoking ``callback``.

        This helper blocks until cancelled. Intended for long-running
        tasks / daemons that need "a new revision was applied" signals
        without polling. The engine registers its own wrapper that
        integrates with the app lifecycle; most callers want
        :meth:`MongoDBEngine.watch_revisions` instead.
        """
        pipeline = [
            {
                "$match": {
                    "operationType": "insert",
                    "fullDocument.slug": slug,
                }
            }
        ]
        kwargs: dict[str, Any] = {"full_document": "updateLookup"}
        if resume_after is not None:
            kwargs["resume_after"] = resume_after
        async with self._db[MANIFEST_REVISIONS_COLLECTION].watch(pipeline, **kwargs) as stream:
            async for change in stream:
                doc = change.get("fullDocument") or {}
                try:  # nosemgrep
                    result = callback(doc)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:  # noqa: BLE001 - callbacks must not break the stream
                    logger.warning("[%s] watch_revisions callback raised: %s", slug, e)


def _majority_write_concern():
    """Build a ``w: 'majority'`` / ``j: True`` write concern (lazy import)."""
    try:  # nosemgrep
        from pymongo import WriteConcern  # type: ignore[attr-defined]

        return WriteConcern(w="majority", j=True)
    except Exception:  # noqa: BLE001 - fall back to driver default
        from pymongo.write_concern import WriteConcern

        return WriteConcern(w="majority", j=True)


def is_reconciler_internal_collection(name: str) -> bool:
    """True if ``name`` is a reconciler-owned internal collection or trash namespace."""
    return name in RECONCILER_INTERNAL_COLLECTIONS or name.startswith(RESERVED_TRASH_PREFIX)


__all__ = [
    "Reconciler",
    "ReconcilePlan",
    "ReconcileOp",
    "OpKind",
    "ArtifactType",
    "ServiceArtifactLister",
    "ServiceRenameDetector",
    "RenameOp",
    "is_reconciler_internal_collection",
]
