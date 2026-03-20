"""
Policy Compiler — Manifest DSL to AuthZ Provider

Reads declarative collection-auth config from a manifest and seeds the
corresponding policies/facts into whatever ``AuthorizationProvider`` is
active (Casbin, OSO, or custom).

Split-compilation model:
  * **Permission gates** (boolean allow/deny) → compiled here → provider
  * **Data scoping** (MQL query filters)     → stays in auto-CRUD unchanged

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Actions that map to CRUD operations
ALL_ACTIONS = ("read", "write", "create", "delete")


async def compile_manifest_policies(
    provider: Any,
    manifest: dict[str, Any],
    app_slug: str,
) -> int:
    """Compile collection auth config into authz policies.

    Reads ``collections.*.auth`` and ``auth.users.role_hierarchy`` from the
    manifest and calls ``provider.add_policy()`` / ``provider.add_role_for_user()``
    to seed the corresponding rules.  Works with any backend that implements
    the ``AuthorizationProvider`` protocol.

    Returns:
        Number of policies successfully added.
    """
    count = 0

    # --- role hierarchy → grouping rules (g, parent, child) ---------------
    auth_cfg = manifest.get("auth", {})
    users_cfg = auth_cfg.get("users", {})
    hierarchy: dict[str, list[str]] = users_cfg.get("role_hierarchy", {})

    for parent_role, children in hierarchy.items():
        for child_role in children:
            try:
                added = await provider.add_role_for_user(parent_role, child_role)
                if added:
                    count += 1
                    logger.debug("Compiled role hierarchy: g(%s, %s)", parent_role, child_role)
            except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
                logger.warning(
                    "Failed to compile role hierarchy g(%s, %s): %s",
                    parent_role,
                    child_role,
                    exc,
                )

    # --- collection-level auth → policy rules (p, role, resource, action) --
    collections: dict[str, Any] = manifest.get("collections", {})

    for col_name, col_cfg in collections.items():
        if not isinstance(col_cfg, dict):
            continue

        col_auth: dict[str, Any] = col_cfg.get("auth", {})
        if not col_auth:
            continue

        # public_read → wildcard subject
        if col_auth.get("public_read"):
            count += await _add_policy_safe(provider, "*", col_name, "read")

        # roles → full access for listed roles
        for role in col_auth.get("roles", []):
            for action in ALL_ACTIONS:
                count += await _add_policy_safe(provider, role, col_name, action)

        # write_roles → write + delete
        for role in col_auth.get("write_roles", []):
            for action in ("write", "create", "delete"):
                count += await _add_policy_safe(provider, role, col_name, action)

        # create_roles → create only
        for role in col_auth.get("create_roles", []):
            count += await _add_policy_safe(provider, role, col_name, "create")

    logger.info("Policy compilation complete for '%s': %d rules seeded", app_slug, count)
    return count


def has_collection_auth(manifest: dict[str, Any]) -> bool:
    """Return *True* if any collection in the manifest has an ``auth`` block."""
    collections: dict[str, Any] = manifest.get("collections", {})
    for col_cfg in collections.values():
        if isinstance(col_cfg, dict) and col_cfg.get("auth"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


async def _add_policy_safe(provider: Any, role: str, resource: str, action: str) -> int:
    """Add a single policy, swallowing errors.  Returns 1 on success, 0 on failure."""
    try:
        added = await provider.add_policy(role, resource, action)
        if added:
            logger.debug("Compiled policy: p(%s, %s, %s)", role, resource, action)
            return 1
    except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
        logger.warning(
            "Failed to compile policy p(%s, %s, %s): %s",
            role,
            resource,
            action,
            exc,
        )
    return 0
