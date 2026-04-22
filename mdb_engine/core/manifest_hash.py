"""
Manifest canonicalization and hashing for reconciler support.

Provides deterministic canonical serialization and hashing of manifests so
the reconciler can detect changes between app startups.

Two hashes are computed:

- ``compute_manifest_hash``: SHA-256 over the fully canonicalized manifest
  (with volatile / runtime-only fields stripped). This is what the engine
  compares against ``apps_config._applied_hash`` to decide whether to run
  reconciliation at all.
- ``compute_schema_hash``: SHA-256 over only the schema-affecting subset
  (collections, managed_indexes, memory_config, graph_config, osi_config,
  encrypted_fields, collection_settings). This lets the reconciler tell
  "runtime config only changed" apart from "schema/storage changed".

Hashes are versioned (``sha256:v2:<digest>``) so that whenever the set of
fields we hash changes we can invalidate previously stored hashes exactly
once and force a single re-reconcile, without requiring any manual
migration.

These helpers are intentionally pure-Python and side-effect free.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Current hash schema version. Incrementing this forces all previously
# stored ``_applied_hash`` values to be treated as stale, causing the
# reconciler to run exactly once against existing apps. Bump when the set
# of keys hashed (``_RUNTIME_ONLY_KEYS`` / ``_RUNTIME_ONLY_POINTERS`` /
# ``_SCHEMA_AFFECTING_KEYS``) changes in a semantically meaningful way.
HASH_SCHEMA_VERSION: int = 2
"""Version marker embedded in every computed hash (``sha256:v{N}:...``)."""

# Top-level fields whose value never affects database schema / storage.
# They are stripped before hashing so e.g. a CORS tweak doesn't create a
# new manifest revision row.
_RUNTIME_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "_id",
        "_source_path",
        "_applied_hash",
        "_applied_schema_hash",
        "_applied_revision",
        "_applied_at",
        "_created",
        "_updated",
        "url",
        "cors",
        "compression",
        "static_cache",
        "observability",  # full block is runtime-only (sampling, endpoints, ...)
        "prompt_safety",
        "ssr",  # purely presentational: SSR routes/templates
    }
)

# Nested runtime-only paths, expressed as JSON-Pointer-ish path tuples.
# ``None`` acts as a wildcard matching every key at that level.
#
# For example ``("ssr", "routes", None, "cache")`` strips the ``cache``
# sub-tree out of every route under ``ssr.routes.*``. These are applied
# *after* the top-level strip so nested runtime tweaks on otherwise
# schema-affecting subtrees don't force new revisions.
_RUNTIME_ONLY_POINTERS: tuple[tuple[Any, ...], ...] = (
    ("ssr", "routes", None, "cache"),
    ("observability", None, "sampling"),
    ("collections", None, "_runtime"),
)

# The schema-affecting subset used by ``compute_schema_hash``. These are the
# keys that, when mutated, may require database-side reconciliation (new
# collections, new indexes, removed collections, etc.). ``initial_data`` is
# intentionally **excluded**: seed rows are orthogonal to schema and a
# one-character tweak to a seed value should not spawn a schema revision.
_SCHEMA_AFFECTING_KEYS: tuple[str, ...] = (
    "collections",
    "managed_indexes",
    "collection_settings",
    "memory_config",
    "graph_config",
    "graphrag_config",
    "osi_config",
    "profile_config",
    "encrypted_fields",
    "encryption_config",
)


def _normalize(obj: Any) -> Any:
    """Recursively normalize a value for deterministic JSON serialization.

    - Tuples become lists.
    - Sets / frozensets become sorted lists.
    - Dict keys are serialized in sorted order at ``json.dumps`` time.
    - All other primitives are passed through unchanged.
    """
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_normalize(v) for v in obj]
    if isinstance(obj, set | frozenset):
        return sorted((_normalize(v) for v in obj), key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return obj


def _strip_pointer(obj: Any, pointer: tuple[Any, ...]) -> Any:
    """Return a copy of ``obj`` with the subtree at ``pointer`` removed.

    Pointer components are dict keys; ``None`` matches every key at that
    level (wildcard). Non-matching paths are left untouched.
    """
    if not pointer:
        return obj
    head, *rest = pointer

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if head is None or k == head:
                if not rest:
                    # Leaf match: drop this key entirely.
                    continue
                out[k] = _strip_pointer(v, tuple(rest))
            else:
                out[k] = v
        return out

    if isinstance(obj, list):
        # Wildcard traversal into list elements for future-proofing.
        if head is None:
            return [_strip_pointer(item, tuple(rest)) for item in obj]
        return obj

    return obj


def canonicalize_manifest(
    manifest: dict[str, Any],
    *,
    strip_runtime: bool = True,
) -> dict[str, Any]:
    """Return a canonicalized copy of the manifest.

    Args:
        manifest: The manifest dictionary to canonicalize.
        strip_runtime: If True (default), remove volatile / runtime-only
            keys so hashes are stable across non-schema changes. Both the
            top-level ``_RUNTIME_ONLY_KEYS`` and nested
            ``_RUNTIME_ONLY_POINTERS`` paths are stripped.

    Returns:
        A new dictionary suitable for deterministic ``json.dumps(..., sort_keys=True)``.
    """
    if not isinstance(manifest, dict):
        raise TypeError(f"canonicalize_manifest expects dict, got {type(manifest).__name__}")

    normalized = _normalize(manifest)

    if strip_runtime:
        normalized = {k: v for k, v in normalized.items() if k not in _RUNTIME_ONLY_KEYS}
        for pointer in _RUNTIME_ONLY_POINTERS:
            normalized = _strip_pointer(normalized, pointer)

    return normalized


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON string: sorted keys, no whitespace noise."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _format_hash(digest: str) -> str:
    """Wrap a hex digest in the canonical versioned hash format."""
    return f"sha256:v{HASH_SCHEMA_VERSION}:{digest}"


def compute_manifest_hash(manifest: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash of the full canonical manifest.

    The hash is prefixed with ``sha256:v{N}:`` so it is self-describing in
    logs and database documents; the ``v{N}`` segment reflects
    :data:`HASH_SCHEMA_VERSION`.
    """
    canonical = canonicalize_manifest(manifest)
    payload = _canonical_json(canonical)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return _format_hash(digest)


def compute_schema_hash(manifest: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash over the schema-affecting subset only.

    Useful for distinguishing "only runtime config changed" from
    "storage layout might have changed". Always prefixed with
    ``sha256:v{N}:``.
    """
    canonical = canonicalize_manifest(manifest)
    subset = {k: canonical[k] for k in _SCHEMA_AFFECTING_KEYS if k in canonical}
    payload = _canonical_json(subset)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return _format_hash(digest)


def is_current_version(hash_value: str | None) -> bool:
    """Return True if ``hash_value`` was produced by the current hash schema.

    Used by the reconciler's startup path to detect legacy hashes and
    force a one-shot re-plan without treating the change as destructive.
    """
    if not isinstance(hash_value, str):
        return False
    return hash_value.startswith(f"sha256:v{HASH_SCHEMA_VERSION}:")


__all__ = [
    "canonicalize_manifest",
    "compute_manifest_hash",
    "compute_schema_hash",
    "is_current_version",
    "HASH_SCHEMA_VERSION",
]
