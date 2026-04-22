"""
Structured diffing of canonicalized manifests (RFC-6902 JSON Patch).

The reconciler records *what* changed between revisions via
``ReconcilePlan`` ops, but those ops describe database-level effects
(add_collection / drop_index / ...). To answer "*why* did this plan pick
this op?" we need a manifest-level diff — a list of structural changes
between the previous and the new manifest.

This module implements a small, pure subset of RFC 6902 JSON Patch:

- ``add``: a key/index that didn't exist before now does
- ``remove``: a key/index that existed before no longer does
- ``replace``: the value at that path changed (primitive or structural)

We do **not** implement ``move`` / ``copy`` / ``test`` — they're not
useful for our review workflow and their semantics complicate the diff.
Paths are JSON Pointers (RFC 6901): ``/collections/leads/auth``.

The diff runs over the canonicalized manifest (runtime-only fields
already stripped by :mod:`mdb_engine.core.manifest_hash`) so a CORS
tweak doesn't pollute the patch with noise.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import Any

from .manifest_hash import canonicalize_manifest

JsonPatchOp = dict[str, Any]
"""A single RFC-6902 patch op: ``{"op": "...", "path": "...", ...}``."""


def _escape_pointer_component(token: str) -> str:
    """Escape a JSON-Pointer component per RFC 6901 (``~`` and ``/``)."""
    return token.replace("~", "~0").replace("/", "~1")


def _join(prefix: str, token: Any) -> str:
    """Append ``token`` to a JSON Pointer ``prefix``."""
    if isinstance(token, int):
        return f"{prefix}/{token}"
    return f"{prefix}/{_escape_pointer_component(str(token))}"


def _diff(a: Any, b: Any, path: str, out: list[JsonPatchOp]) -> None:
    """Recursively emit add/remove/replace ops describing ``a`` -> ``b``."""
    if a == b:
        return

    if isinstance(a, dict) and isinstance(b, dict):
        a_keys = set(a.keys())
        b_keys = set(b.keys())
        for key in sorted(b_keys - a_keys):
            out.append({"op": "add", "path": _join(path, key), "value": b[key]})
        for key in sorted(a_keys - b_keys):
            out.append({"op": "remove", "path": _join(path, key), "previous": a[key]})
        for key in sorted(a_keys & b_keys):
            _diff(a[key], b[key], _join(path, key), out)
        return

    if isinstance(a, list) and isinstance(b, list):
        # Simple per-index diff. For our use-case (manifests, not arbitrary
        # documents) lists are small and position-stable so we don't need
        # Myers-style LCS. Extra elements become adds/removes.
        common = min(len(a), len(b))
        for i in range(common):
            _diff(a[i], b[i], _join(path, i), out)
        if len(b) > len(a):
            for i in range(len(a), len(b)):
                out.append({"op": "add", "path": _join(path, i), "value": b[i]})
        elif len(a) > len(b):
            for i in range(len(b), len(a)):
                out.append({"op": "remove", "path": _join(path, i), "previous": a[i]})
        return

    # Scalars, or a type mismatch: single replace.
    out.append({"op": "replace", "path": path or "", "previous": a, "value": b})


def manifest_patch(
    prev: dict[str, Any] | None,
    new: dict[str, Any] | None,
    *,
    canonical: bool = True,
) -> list[JsonPatchOp]:
    """Compute an RFC-6902 patch transforming ``prev`` into ``new``.

    Args:
        prev: Previous manifest (or ``None`` on first apply).
        new: New manifest (or ``None`` on delete; both None is a no-op).
        canonical: When True (default), both inputs are canonicalized
            first so runtime-only fields don't appear in the patch.

    Returns:
        A list of RFC-6902 patch ops with optional ``previous`` fields
        on ``replace`` / ``remove`` entries for reviewability.
    """
    a = prev or {}
    b = new or {}
    if canonical:
        a = canonicalize_manifest(a) if a else {}
        b = canonicalize_manifest(b) if b else {}
    out: list[JsonPatchOp] = []
    _diff(a, b, "", out)
    return out


def filter_patch_by_prefix(patch: list[JsonPatchOp], prefix: str) -> list[JsonPatchOp]:
    """Return only the patch ops whose path starts with ``prefix``.

    Used when writing tombstones to capture only the subtree that was
    removed for a specific collection or index.
    """
    return [op for op in patch if str(op.get("path", "")).startswith(prefix)]


def format_patch_markdown(patch: list[JsonPatchOp]) -> str:
    """Render a patch as a GitHub-flavored Markdown bullet list.

    Designed for PR comments: each op becomes one bullet, sorted by op
    kind (removes first, then replaces, then adds) so reviewers see
    destructive changes up top.
    """
    if not patch:
        return "_(no structural changes)_"
    order = {"remove": 0, "replace": 1, "add": 2}
    sorted_ops = sorted(patch, key=lambda o: (order.get(o.get("op", "replace"), 3), o.get("path", "")))
    lines: list[str] = []
    for op in sorted_ops:
        kind = op.get("op", "?")
        path = op.get("path", "")
        if kind == "add":
            lines.append(f"- `+` `{path}`")
        elif kind == "remove":
            lines.append(f"- `-` `{path}`")
        else:
            lines.append(f"- `~` `{path}`")
    return "\n".join(lines)


__all__ = [
    "manifest_patch",
    "filter_patch_by_prefix",
    "format_patch_markdown",
    "JsonPatchOp",
]
